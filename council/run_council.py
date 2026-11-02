# Council runner. Repaired 14 Oct 2026: every seat must answer or say why it could not.
# T1 = free voices (Groq, Google, Mistral, OpenRouter). T2 = Faris's paid: IlmuClaw + Codex (the arbiter).
#
# What a seat does now:
#   1. Tries the full brief. A rate limit retries up to 3 times with backoff (the provider's own wait time if it says one).
#   2. If the brief is over the seat's input limit, it reads the brief in parts, writes notes per part,
#      then gives one verdict from its own notes. The output starts with a SEAT NOTE saying so.
#   3. If the model is out of quota or gone, it falls back to a sibling model on the same key and says so.
#   4. Nothing is dropped silently: a seat that still fails records every attempt and the reason.
# Command line unchanged: --images, --out, --tier2, --t1-only, --vision-only. New optional: --prompt FILE.
import argparse, base64, json, mimetypes, os, re, shutil, subprocess, tempfile, time, concurrent.futures, threading, sys

KEYS = {}
# Keys come from a KEY=VALUE file (COUNCIL_KEYS_FILE, default ~/.model-keys.env) and/or plain environment variables.
_KEYS_FILE = os.environ.get("COUNCIL_KEYS_FILE", os.path.expanduser("~/.model-keys.env"))
if os.path.exists(_KEYS_FILE):
    for line in open(_KEYS_FILE, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); KEYS.setdefault(k.strip(), v.strip())
for _k in ("GROQ_API_KEY", "OPENROUTER_API_KEY", "OPENROUTER_API_KEY_AITKL", "MISTRAL_API_KEY", "GEMINI_API_KEY", "ILMU_API_KEY", "ILMU_BASE_URL", "OPENAI_API_KEY_CODEX", "OPENAI_ORG_ID"):
    if os.environ.get(_k):
        KEYS.setdefault(_k, os.environ[_k])

parser = argparse.ArgumentParser(description="Run Council; --images switches to rendered deck review.")
parser.add_argument("--images", nargs="+", metavar="IMAGE", help="Rendered slides/pages for visual review")
parser.add_argument("--out", help="Write results here instead of council_out.json")
parser.add_argument("--tier2", action="store_true", help="Include paid Tier 2; never the default")
parser.add_argument("--t1-only", action="store_true", help="Deprecated compatibility flag; Tier 1 is now the default")
parser.add_argument("--vision-only", action="store_true", help="Run only free image-capable seats (requires --images)")
parser.add_argument("--prompt", help="Brief file to judge (default: prompt.txt next to this script)")
parser.add_argument("--no-brand", action="store_true", help="Do not prepend the deck/brand standard files; use for plain text review prompts")
args = parser.parse_args()

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT_PATH = args.prompt or os.path.join(HERE, "prompt.txt")
PROMPT = open(PROMPT_PATH, encoding="utf-8-sig").read()  # -sig: PowerShell-written prompts start with a BOM, which hid every VERDICT label
ROOT = os.path.dirname(HERE)
if not args.no_brand:
    for filename, heading in (("RIZQEY_BRAND.md", "OUR BRAND AND QUALITY STANDARD"), ("COUNCIL_STANDARDS.md", "COUNCIL REVIEW STANDARD")):
        path = os.path.join(ROOT, filename)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                PROMPT = heading + ": apply this to every review.\n\n" + f.read() + "\n\n" + PROMPT

IMAGE_PARTS = []
for image_path in args.images or []:
    if not os.path.isfile(image_path):
        parser.error("image not found: " + image_path)
    mime = mimetypes.guess_type(image_path)[0] or "image/png"
    with open(image_path, "rb") as f:
        IMAGE_PARTS.append({"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (mime, base64.b64encode(f.read()).decode("ascii"))}})
if IMAGE_PARTS:
    PROMPT += "\n\nATTACHED SLIDES REPLACE THE TEXT-ONLY DECISION TASK ABOVE. Review every visible slide against the Council standard. Return exactly these fields:\nVISUAL VERDICT: PASS or FAIL or ABSTAIN\nVISUAL DEFECTS: slide/page, severity, observed problem, exact fix\nBRAND: PASS or FAIL with observed evidence\nREADABILITY: PASS or FAIL with observed evidence\nDo not guess about an image you cannot see."
if args.vision_only and not IMAGE_PARTS:
    parser.error("--vision-only requires --images")

NO_IMAGE_LINE = "\n\nImages were supplied, but this seat cannot see images. VISUAL VERDICT: ABSTAIN. Do not judge layout, branding, spacing, font size or contrast."

def message_content(vision=False, text=None):
    text = PROMPT if text is None else text
    if IMAGE_PARTS and vision:
        return [{"type": "text", "text": text}] + IMAGE_PARTS
    if IMAGE_PARTS:
        return text + NO_IMAGE_LINE
    return text

LABEL_LINE = re.compile(r"^\s*[A-Z][A-Z0-9 .+/_-]{1,80}:")

def task_tail():
    """The judging instruction and answer format: the last run of label lines (VERDICT 1: ...) plus the paragraph before it."""
    lines = PROMPT.split("\n")
    last = max((i for i, l in enumerate(lines) if LABEL_LINE.match(l)), default=-1)
    if last < 0:
        return PROMPT[-1500:]
    first = last
    while first > 0 and (LABEL_LINE.match(lines[first - 1]) or not lines[first - 1].strip()):
        first -= 1
    lead = first
    while lead > 0 and lines[lead - 1].strip() and len("\n".join(lines[lead - 1:first])) < 1500:
        lead -= 1
    return "\n".join(lines[lead:])

# Only the answer-format labels count as a verdict. A prompt may write the choice as
# "VERDICT: READY or VERDICT: FIX FIRST" on one line; split on " or " so each alternative
# is its own label, else no seat's single verdict line ever matches the combined string.
LABELS = []
for l in re.findall(r"(?m)^\s*([A-Z][A-Z0-9 .+/_-]{1,80}):", task_tail()):
    LABELS.extend(part.strip() for part in l.split(" or "))

def choice_text(choice):
    message = choice.get("message", {})
    text = message.get("content") or ""
    if isinstance(text, list):
        text = "\n".join(p.get("text", "") for p in text if isinstance(p, dict))
    if not text:
        text = message.get("reasoning") or "\n".join(d.get("text", "") for d in message.get("reasoning_details", []))
    return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.S).strip()

def est_tokens(text):
    return int(len(text) / 3.5) + 1  # measured 14 Oct: 28,546 chars of the YC brief = 7,655 Groq tokens

def curl_json(url, key=None, payload=None):
    """Keep Council's curl transport: urllib is Cloudflare-blocked on this PC."""
    command = ["curl", "-s", "-m", "240", "-w", "\n%{http_code}", url]
    if key:
        command += ["-H", f"Authorization: Bearer {key}"]
    raw = None
    if payload is not None:
        fd, path = tempfile.mkstemp(suffix=".json")  # a 30 KB brief on stdin was fine; images are not, so always a temp file
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        command += ["-H", "Content-Type: application/json", "--data-binary", f"@{path}"]
    try:
        r = subprocess.run(command, input=raw, capture_output=True, text=True, encoding="utf-8", errors="replace")
    finally:
        if payload is not None:
            try: os.unlink(path)
            except Exception: pass
    body, _, code = r.stdout.rpartition("\n")
    try:
        code = int(code.strip())
    except ValueError:
        code = 0
    try:
        parsed = json.loads(body or "{}")
    except json.JSONDecodeError:
        parsed = {"error": "invalid curl response: " + body[:200]}
    if isinstance(parsed, list) and parsed:  # Gemini wraps an error in a list
        parsed = parsed[0]
    if not isinstance(parsed, dict):
        parsed = {"error": str(parsed)[:200]}
    return code, parsed

def classify(code, body):
    """ok / too_large / rate / quota / gone / err, plus the seconds the provider asked us to wait."""
    if isinstance(body.get("choices"), list) and body["choices"]:
        return "ok", 0
    blob = json.dumps(body).lower()
    wait = 0
    m = re.search(r"(?:try again|retry) in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s|([\d.]+)ms)", blob)
    if m:
        h, mi, s, ms = m.groups()
        wait = int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0) + float(ms or 0) / 1000
    if re.search(r"request too large|too many tokens|context length|maximum context|input is too long|prompt is too long|exceeds the (?:maximum|limit)|token limit", blob):
        return "too_large", 0
    if code == 402 or "quota" in blob or "insufficient" in blob or "exhausted" in blob or (code == 429 and wait > 300):
        return "quota", wait
    if re.search(r"model.{0,40}(not found|does not exist|decommissioned|deprecated|no endpoints)|no endpoints found|not a valid model", blob) or code == 404:
        return "gone", 0
    if code in (429, 500, 502, 503, 504, 0) or re.search(r"rate.?limit|capacity|overloaded|try again|temporarily", blob) or not blob.strip("{} "):
        return "rate", wait
    return "err", 0

def short(body):
    return re.sub(r"\s+", " ", json.dumps(body))[:300]

def post_with_retry(url, key, payload, log, retries=3):
    """One request, up to 3 retries on a rate limit, spread over time. Returns (kind, code, body)."""
    backoff = (15, 30, 60)
    for attempt in range(retries + 1):
        code, body = curl_json(url, key, payload)
        kind, wait = classify(code, body)
        if kind != "rate" or attempt == retries:
            return kind, code, body
        pause = min(max(wait + 1, backoff[attempt]), 120)
        log.append(f"rate limit on {payload['model']} (http {code}), waited {pause:.0f}s, retry {attempt + 1} of 3")
        time.sleep(pause)
    return "rate", code, body

def chunk_brief(limit_tokens, max_tokens):
    """Split the brief into pieces that fit a seat's input limit, on paragraph or line boundaries."""
    overhead = max_tokens + est_tokens(task_tail()) + 300  # the answer, the task restated with every part, the wrapper lines
    budget_chars = int(max(limit_tokens - overhead, 1000) * 3.5)
    pieces, current = [], ""
    for para in re.split(r"(?<=\n)", PROMPT):
        if len(current) + len(para) > budget_chars and current:
            pieces.append(current); current = ""
        while len(para) > budget_chars:  # a single oversized line
            pieces.append(para[:budget_chars]); para = para[budget_chars:]
        current += para
    if current:
        pieces.append(current)
    return pieces

def checked(text, accept_unlabelled=False):
    flat = re.sub(r"[*_`#:]", "", text)
    if accept_unlabelled or not LABELS or any(l in flat for l in LABELS):
        return text[:6000]
    return "__NOVERDICT__ " + text[:6000]

def run_chunked(url, key, model, max_tokens, params, vision, limit_tokens, log):
    """Seat reads the brief in parts, notes each, then judges from its own notes."""
    pieces = chunk_brief(limit_tokens, max_tokens)
    if len(pieces) < 2 and est_tokens(PROMPT) > limit_tokens - max_tokens:
        pieces = chunk_brief(limit_tokens // 2, max_tokens)
    tail = task_tail()
    notes = []
    for i, piece in enumerate(pieces, 1):
        ask = (f"You are a Council seat reading a long brief in {len(pieces)} parts because of your input limit. "
               f"This is part {i} of {len(pieces)}. Do not give a verdict yet. Write compact notes (under 250 words) on the facts, "
               f"strengths and weaknesses in this part that matter for the judging task, which is:\n\n{tail}\n\n"
               f"=== PART {i} OF {len(pieces)} ===\n{piece}\n=== END OF PART {i} ===\n\nNOTES ON PART {i}:")
        payload = {"model": model, "messages": [{"role": "user", "content": message_content(False, ask)}],
                   "max_tokens": max_tokens, "temperature": 0.3}
        payload.update(params or {})
        kind, code, body = post_with_retry(url, key, payload, log)
        if kind != "ok":
            return kind, code, body
        notes.append(f"NOTES ON PART {i} OF {len(pieces)}:\n{choice_text(body['choices'][0])}")
    kind, code, body = finish_from_notes(url, key, model, max_tokens, params, vision, "\n\n".join(notes), tail, log)
    if kind == "ok":
        body["_seat_note"] = f"input over this seat's limit ({limit_tokens} tokens); read the brief in {len(pieces)} parts, then judged from its own notes"
    return kind, code, body

def finish_from_notes(url, key, model, max_tokens, params, vision, notes, tail, log):
    """One verdict call from the seat's own notes or its own unfinished draft."""
    ask = ("You are a Council seat. You already read the brief and wrote the notes below yourself. "
           "Judge from these notes only; do not invent anything not in them. Do not think out loud; output only the required lines.\n\n"
           + notes + "\n\nNow answer the judging task exactly as instructed:\n\n" + tail)
    payload = {"model": model, "messages": [{"role": "user", "content": message_content(vision, ask)}],
               "max_tokens": max_tokens, "temperature": 0.5}
    payload.update(params or {})
    return post_with_retry(url, key, payload, log)

def run_seat(label, url, keys, models, max_tokens=900, params=None, vision=False, delay=0.0, accept_unlabelled=False):
    """Try each model on each key. Full brief first; chunk if too large; sibling model on quota or gone."""
    if delay:
        time.sleep(delay)  # spread the first calls so shared keys do not burst
    log, used = [], []
    keys = [k for k in keys if k]
    if not keys:
        return label, "__SKIPPED__ no API key for " + label
    for n, model in enumerate(models):
        for k, key in enumerate(keys):
            payload = {"model": model, "messages": [{"role": "user", "content": message_content(vision)}],
                       "max_tokens": max_tokens, "temperature": 0.5}
            payload.update(params or {})
            # an upstream rate limit rarely clears for a second key, so the alternate key gets one try, no backoff
            kind, code, body = post_with_retry(url, key, payload, log, retries=3 if k == 0 else 0)
            if kind == "too_large":
                m = re.search(r"limit[^0-9]{0,10}(\d{3,7})", json.dumps(body), re.I)
                limit = int(m.group(1)) if m else min(8000, est_tokens(PROMPT) // 2 + max_tokens)
                log.append(f"{model}: brief too large for one call (limit {limit} tokens), reading in parts")
                kind, code, body = run_chunked(url, key, model, max_tokens, params, vision, limit, log)
            if kind == "ok":
                text = choice_text(body["choices"][0])
                if not text:
                    log.append(f"{model}: empty answer")
                    continue
                text = checked(text, accept_unlabelled)
                notes = [body.get("_seat_note")] if body.get("_seat_note") else []
                if text.startswith("__NOVERDICT__"):  # it thought out loud and ran out of room: one more call to finish from its own draft
                    log.append(f"{model}: no verdict labels in first answer, asked it to finish from its own draft")
                    kind2, code2, body2 = finish_from_notes(url, key, model, max_tokens, params, vision,
                                                           "YOUR OWN UNFINISHED DRAFT:\n" + text[14:], task_tail(), log)
                    text2 = choice_text(body2["choices"][0]) if kind2 == "ok" else ""
                    if text2 and not checked(text2, accept_unlabelled).startswith("__NOVERDICT__"):
                        text = text2[:6000]
                        notes.append("first answer had no verdict lines; the seat finished from its own draft in a second call")
                if n > 0:
                    notes.append(f"{models[0]} was unavailable ({'; '.join(used)}); answered by sibling model {model} on the same key")
                if notes:
                    text = "SEAT NOTE: " + ". ".join(notes) + "\n" + text
                return label, text
            used.append(f"{model}: {kind} http {code} {short(body)}")
            if kind in ("rate", "quota") and len(keys) > 1:
                continue  # same model, next key
            break  # other failures: next model
    return label, "__ERROR__ every attempt failed. " + " | ".join(used + log)

def call_codex(label):
    """T2 arbiter. Windows: bare "codex" is a shim (WinError 2), a ~6KB prompt passed as
    argv arrives EMPTY, and given a repo as cwd it reads files instead of the brief.
    So: full .cmd path + prompt on stdin + a neutral empty working directory."""
    exe = os.environ.get("CODEX_BIN") or shutil.which("codex") or r"C:/Users/diony/AppData/Roaming/npm/codex.cmd"
    sandbox = tempfile.mkdtemp(prefix="council_sandbox_")
    guard = ("Answer ONLY from the brief text below. Do not read, list or open any files. "
             "Do not use tools. Output ONLY the required lines." + chr(10) + chr(10))
    fd, ppath = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(guard + PROMPT)
    try:
        with open(ppath, "r", encoding="utf-8") as stdin_f:
            r = subprocess.run([exe, "exec", "--skip-git-repo-check"],
                               stdin=stdin_f, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=900, cwd=sandbox)
        out = r.stdout or ""
        m = re.split(r"(?m)^codex$", out)
        text = (m[-1] if len(m) > 1 else out).strip()
        text = re.sub(r"(?m)^tokens used[\s\S]*$", "", text).strip()
        return label, text[:6000] if text else "__ERROR__ empty codex output"
    except Exception as e:
        return label, f"__ERROR__ {e}"
    finally:
        try: os.unlink(ppath)
        except Exception: pass

def call_openai_arbiter(label):
    """T2 arbiter on the OpenAI API key. Faris's rule: it burns API credits, NEVER a ChatGPT plan or login."""
    import urllib.request, urllib.error
    key = KEYS.get("OPENAI_API_KEY_CODEX", "")
    if not key:
        return label, "__SKIPPED__ no OPENAI_API_KEY_CODEX (the arbiter runs on the API key only, never on a ChatGPT login)"
    model = os.environ.get("COUNCIL_ARBITER_MODEL", "gpt-6-luna")
    guard = "Answer ONLY from the brief text below. Do not use tools. Output ONLY the required lines.\n\n"
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": guard + PROMPT}], "max_completion_tokens": 1500}).encode("utf-8")
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if KEYS.get("OPENAI_ORG_ID"):
        headers["OpenAI-Organization"] = KEYS["OPENAI_ORG_ID"]
    for attempt in range(3):
        try:
            req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=body, headers=headers)
            r = json.loads(urllib.request.urlopen(req, timeout=300).read().decode("utf-8"))
            text = (r["choices"][0]["message"].get("content") or "").strip()
            return label, text[:6000] if text else "__ERROR__ empty arbiter output"
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:200]
            if e.code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(10 * (attempt + 1)); continue
            return label, f"__ERROR__ arbiter {model} http {e.code} {msg}"
        except Exception as e:
            return label, f"__ERROR__ arbiter {model}: {e}"

G  = "https://api.groq.com/openai/v1/chat/completions"
O  = "https://openrouter.ai/api/v1/chat/completions"
OM = "https://openrouter.ai/api/v1/models"
MI = "https://api.mistral.ai/v1/chat/completions"
GEMU = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
IL = KEYS.get("ILMU_BASE_URL", "https://api.ilmu.ai/v1").rstrip("/") + "/chat/completions"
GK, OK_ = KEYS["GROQ_API_KEY"], KEYS["OPENROUTER_API_KEY"]
OK_ALT = KEYS.get("OPENROUTER_API_KEY_AITKL", "")
MK, GMK, ILK = KEYS.get("MISTRAL_API_KEY",""), KEYS.get("GEMINI_API_KEY",""), KEYS.get("ILMU_API_KEY","")

def load_openrouter_free_models():
    code, body = curl_json(OM)
    if code != 200:
        return {}
    return {m.get("id"): set((m.get("architecture") or {}).get("input_modalities") or [])
            for m in body.get("data", []) if str(m.get("id", "")).endswith(":free")}

# One live model-list GET per Council run. New seats resolve their fallbacks against it.
# The live catalogue is authoritative, but an unavailable catalogue must not disable Council.
# These are only fallback candidates; each still has to succeed as a free completion call.
FALLBACK_OR_FREE_MODELS = {
    "nex-agi/nex-n2.5-pro:free": set(), "nex-agi/nex-n2.5-mini:free": set(),
    "google/gemma-4-31b-it:free": {"image"}, "google/gemma-4-26b-a4b-it:free": {"image"},
    "nvidia/nemotron-3-ultra-550b-a55b:free": set(), "nvidia/nemotron-3-super-120b-a12b:free": set(),
    "poolside/laguna-s-2.1:free": set(), "poolside/laguna-xs-2.1:free": set(),
    "dots-studio/dots-3-note-preview:free": {"image"},
    "thinkingmachines/inkling-small:free": {"image"}, "thinkingmachines/inkling:free": {"image"},
}
OR_FREE_MODELS = FALLBACK_OR_FREE_MODELS.copy()
OR_FREE_MODELS.update(load_openrouter_free_models())
OR_FREE_IDS = set(OR_FREE_MODELS)
OR_VISION_IDS = {model for model, inputs in OR_FREE_MODELS.items() if "image" in inputs}
OR_PAID_KEY = OK_ALT or OK_  # /credits omits promo grants (13 Sep: $5 grant showed as 0), so just call; 402 = really out of money

# Sibling models on the same key, tried in order when the seat's own model is out of quota or gone.
# Listed on the Gemini key 14 Oct 2026. Each Gemini seat gets different siblings so two seats never share one model.
SIBLINGS = {
    "gemini-3.5-flash": ["gemini-3.5-flash-lite", "gemini-2.5-flash", "gemini-flash-latest"],
    "gemini-3.1-flash-lite": ["gemini-2.5-flash-lite", "gemini-3.1-flash-lite-preview", "gemini-flash-lite-latest"],
    "openai/gpt-oss-120b": ["openai/gpt-oss-20b"],
    "openai/gpt-oss-20b": ["openai/gpt-oss-120b"],
    "qwen/qwen3.8-27b": [],  # Groq lists no other Qwen (14 Oct); the part-by-part read is its only route
    "open-mistral-nemo": ["mistral-small-latest"],
    "ministral-8b-latest": ["mistral-small-latest"],
    "ministral-3b-latest": ["ministral-8b-latest"],
    "ilmu-v3.1": [],
}

def call(label, url, key, model, max_tokens=900, params=None, vision=False, delay=0.0):
    return run_seat(label, url, [key], [model] + SIBLINGS.get(model, []), max_tokens, params, vision, delay)

# Same open models served straight by Google AI Studio on the Gemini key (listed 14 Oct). Used when OpenRouter's free pool is rate-limited upstream.
DIRECT_ROUTE = {"google/gemma-4-31b-it:free": (GEMU, GMK, "gemma-4-31b-it"), "google/gemma-4-26b-a4b-it:free": (GEMU, GMK, "gemma-4-26b-a4b-it")}

def call_or_free(label, preferences, max_tokens=900, delay=0.0):
    """OpenRouter free seat: every listed free slug, primary key then alternate key, with backoff; then the same model direct from its maker if we hold that key."""
    candidates = [slug for slug in preferences if slug in OR_FREE_IDS]
    if not candidates:
        return label, "__SKIPPED__ no listed OpenRouter free model among " + ", ".join(preferences)
    vision = bool(IMAGE_PARTS and candidates[0] in OR_VISION_IDS)
    params = {} if vision else {"include_reasoning": False, "reasoning": {"effort": "low", "exclude": True}}
    label, text = run_seat(label, O, [OK_, OK_ALT], candidates, max_tokens, params, vision, delay, accept_unlabelled=vision)
    direct = [DIRECT_ROUTE[m] for m in preferences if m in DIRECT_ROUTE and DIRECT_ROUTE[m][1]]
    if text.startswith("__ERROR__") and direct:
        url, key, _ = direct[0]
        label, text2 = run_seat(label, url, [key], [m for _, _, m in direct], max_tokens, {}, vision, 0, accept_unlabelled=vision)
        if not text2.startswith(("__ERROR__", "__SKIPPED__")):
            return label, "SEAT NOTE: OpenRouter free route failed (" + text[10:300] + "); same model answered direct from Google AI Studio on the Gemini key.\n" + text2
        text += " | direct route: " + text2[10:600]
    return label, text

def call_or_paid(label, model, max_tokens=900, delay=0.0):
    if not OR_PAID_KEY:
        return label, "__SKIPPED__ no OpenRouter key"
    label, text = run_seat(label, O, [OR_PAID_KEY], [model], max_tokens, {"include_reasoning": False}, False, delay)
    if text.startswith("__ERROR__") and "402" in text:
        return label, "__SKIPPED__ OpenRouter credits exhausted. " + text[10:]
    return label, text

# TIER 1 — free. Groq seats: 8,000 tokens per request per model (measured 14 Oct), so a long brief is read in parts.
JOBS = [
    ("GPT-OSS-120B (OpenAI-open/Groq)", G, GK, "openai/gpt-oss-120b", 900, {"include_reasoning":False,"reasoning_effort":"low"}),
    ("GPT-OSS-20B (OpenAI-open/Groq)", G, GK, "openai/gpt-oss-20b", 900, {"include_reasoning":False,"reasoning_effort":"low"}),
    ("Gemini-3.5-Flash (Google)",    GEMU, GMK, "gemini-3.5-flash", 2000, {"reasoning_effort": "none"}),
    ("Mistral-Nemo (Mistral)",       MI, MK, "open-mistral-nemo"),
    ("Qwen3.8-27B (Alibaba/Groq)",    G, GK, "qwen/qwen3.8-27b", 900, {"reasoning_effort":"none","reasoning_format":"hidden"}),
    ("Gemini-3.1-Flash-Lite (Google)", GEMU, GMK, "gemini-3.1-flash-lite", 2000, {"reasoning_effort": "none"}),
    ("Ministral-8B (Mistral)",        MI, MK, "ministral-8b-latest"),
    ("Ministral-3B (Mistral)",        MI, MK, "ministral-3b-latest"),
]
# Verified 9 Oct with an image completion; do not assume the other text seats can see images.
VISION_MODELS = {"gemini-3.5-flash"}
OR_FREE_JOBS = [
    ("Gemma-4 (Google-open/OR)", ["google/gemma-4-31b-it:free", "google/gemma-4-26b-a4b-it:free"]),
    ("Nemotron-3 (NVIDIA/OR)", ["nvidia/nemotron-3-ultra-550b-a55b:free", "nvidia/nemotron-3-super-120b-a12b:free"]),
    ("Laguna (Poolside/OR)", ["poolside/laguna-s-2.1:free", "poolside/laguna-xs-2.1:free"]),
]
OR_FREE_VISION_JOBS = [
    ("Dots3-Note Vision (Dots/OR)", ["dots-studio/dots-3-note-preview:free"], 900),
    ("Inkling Vision (Thinking Machines/OR)", ["thinkingmachines/inkling-small:free", "thinkingmachines/inkling:free"], 900),
]
OR_PAID_JOBS = [("MiniMax-M3 (MiniMax/OR)", "minimax/minimax-m3")]
# TIER 2 — paid. Never default; run only when Faris asks for both tiers.
TIER2 = [("IlmuClaw (YTL, T2)", IL, ILK, "ilmu-v3.1")]

# Windows console is cp1252; model output contains non-breaking hyphens and dashes that
# crash print() mid-run and lose the whole round. Never let a log line kill a council run.
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')
tier2 = args.tier2
out = {}
jobs = [job for job in JOBS if job[3] in VISION_MODELS] if args.vision_only else JOBS + (TIER2 if tier2 else [])
STAGGER = 2.0  # seconds between seat starts: Mistral allows 1 request/s per key, Groq and Gemini count bursts
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
    futs = []
    for i, j in enumerate(jobs):
        futs.append(ex.submit(call, *j, vision=bool(IMAGE_PARTS and j[3] in VISION_MODELS), delay=i * STAGGER))
    if not args.vision_only:
        for i, (label, preferences) in enumerate(OR_FREE_JOBS):
            futs.append(ex.submit(call_or_free, label, preferences, delay=(len(futs) + i) * STAGGER))
    if IMAGE_PARTS:
        for i, (label, preferences, max_tokens) in enumerate(OR_FREE_VISION_JOBS):
            futs.append(ex.submit(call_or_free, label, preferences, max_tokens, delay=(len(futs) + i) * STAGGER))
    if tier2 and not args.vision_only:
        for i, (label, model) in enumerate(OR_PAID_JOBS):
            futs.append(ex.submit(call_or_paid, label, model, delay=(len(futs) + i) * STAGGER))
        futs.append(ex.submit(call_codex, "Codex CLI (T2 ARBITER, ChatGPT login)") if os.environ.get("COUNCIL_ALLOW_CODEX_CLI") == "1" else ex.submit(call_openai_arbiter, "OpenAI arbiter (T2, API key)"))
    for f in concurrent.futures.as_completed(futs):
        label, text = f.result()
        text = text or "__ERROR__ empty"
        out[label] = text
        bad = text.startswith(("__ERROR__", "__NOVERDICT__", "__SKIPPED__"))
        print(f"[{label}] {'ERR ' + text[:110] if bad else str(len(text)) + ' chars' + (' (' + text.split(chr(10))[0][:90] + ')' if text.startswith('SEAT NOTE') else '')}", flush=True)

OUT_PATH = args.out or os.path.join(HERE, "council_out.json")
with open(OUT_PATH, "w", encoding="utf-8") as f:
    json.dump(out, f, indent=1)
answered = sum(1 for v in out.values() if not v.startswith(("__ERROR__","__NOVERDICT__","__SKIPPED__")))
print("DONE", answered, "of", len(out), "->", OUT_PATH)
for label, text in out.items():
    if text.startswith(("__ERROR__","__NOVERDICT__","__SKIPPED__")):
        print("MISSING", label + ":", text[:300])
