#!/usr/bin/env python3
# summarize.py OUT_JSON BRIEF RUN_LOG RC
# Reads the runner's {seat: text} JSON and prints a one-page summary.md:
#   - seats answered out of seats called
#   - vote tally (the VERDICT choice each answering seat gave)
#   - the reason for each risk raised (REASON / WEAKEST POINT lines)
#   - SPLIT yes/no (yes when answering seats do not agree on a single lead verdict)
# Nothing here calls a model or spends money; it only reads local JSON.
import json, os, re, sys

out_json, brief, run_log, rc = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]

with open(out_json, encoding="utf-8") as f:
    seats = json.load(f)

FAIL_PREFIXES = ("__ERROR__", "__NOVERDICT__", "__SKIPPED__")

def answered(text):
    return not text.startswith(FAIL_PREFIXES)

called = len(seats)
answering = {k: v for k, v in seats.items() if answered(v)}
failing = {k: v for k, v in seats.items() if not answered(v)}

# --- Verdict extraction -------------------------------------------------
# A brief asks for lines like "VERDICT 1: A or B or NEEDS-FARIS"; a seat answers
# "VERDICT 1: A". Pull the choice token after any VERDICT/VISUAL VERDICT label.
VERDICT_RE = re.compile(r"(?mi)^\s*[*_`#>\s-]*(?:VISUAL\s+)?VERDICT\s*\d*\s*:\s*(.+)$")

def normalize_choice(raw):
    # first token-ish word, stripped of markdown and trailing punctuation
    raw = re.sub(r"[*_`#]", "", raw).strip()
    # take up to the first separator so "A - because..." -> "A"
    token = re.split(r"[\s,.;:()]| - ", raw, maxsplit=1)[0].strip()
    return token.upper() if token else raw.strip().upper()[:40]

tally = {}            # choice -> [seat, ...]
seat_choice = {}      # seat -> choice
for seat, text in answering.items():
    m = VERDICT_RE.search(text)
    if m:
        choice = normalize_choice(m.group(1))
        seat_choice[seat] = choice
        tally.setdefault(choice, []).append(seat)
    else:
        seat_choice[seat] = "(no VERDICT label)"
        tally.setdefault("(no VERDICT label)", []).append(seat)

# --- Risk / reason extraction ------------------------------------------
RISK_RE = re.compile(r"(?mi)^\s*[*_`#>\s-]*(REASON\s*\d*|WEAKEST\s+POINT\s*\d*|RISK\s*\d*|FIX\s*\d*|VISUAL\s+DEFECTS)\s*:\s*(.+)$")
risks = []  # (seat, label, text)
for seat, text in answering.items():
    for m in RISK_RE.finditer(text):
        label = re.sub(r"\s+", " ", m.group(1)).strip().upper()
        reason = m.group(2).strip()
        if reason and reason.upper() not in ("NONE", "N/A"):
            risks.append((seat, label, reason))

# --- SPLIT decision -----------------------------------------------------
real_choices = {c: s for c, s in tally.items() if c != "(no VERDICT label)"}
if len(real_choices) <= 1:
    split = "NO"
    split_why = "all answering seats with a verdict agree" if real_choices else "no verdict labels found"
else:
    counts = sorted(((len(v), c) for c, v in real_choices.items()), reverse=True)
    top = counts[0][0]
    leaders = [c for n, c in counts if n == top]
    split = "YES"
    if len(leaders) > 1:
        split_why = "tie between " + " / ".join(leaders)
    else:
        split_why = "lead %s but seats disagree across %s" % (leaders[0], ", ".join(c for _, c in counts))

# --- Emit ---------------------------------------------------------------
w = sys.stdout.write
title = os.path.basename(brief)
w("# Council summary: %s\n\n" % title)
w("**Seats answered: %d of %d called.**\n\n" % (len(answering), called))
w("**SPLIT: %s** (%s)\n\n" % (split, split_why))

w("## Vote tally\n\n")
if tally:
    for choice, seatlist in sorted(tally.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        w("- **%s** - %d seat(s): %s\n" % (choice, len(seatlist), ", ".join(seatlist)))
else:
    w("- No verdicts parsed.\n")
w("\n")

w("## Risks raised (reason per risk)\n\n")
if risks:
    for seat, label, reason in risks:
        reason = reason if len(reason) <= 300 else reason[:297] + "..."
        w("- **%s** [%s]: %s\n" % (seat, label, reason))
else:
    w("- No risk/reason lines parsed from answering seats.\n")
w("\n")

if failing:
    w("## Seats that did not answer (with reason)\n\n")
    for seat, text in failing.items():
        reason = re.sub(r"\s+", " ", text)[:220]
        w("- **%s**: %s\n" % (seat, reason))
    w("\n")

w("## Provenance\n\n")
w("- Raw seat answers: `%s`\n" % out_json)
w("- Run log: `%s`\n" % run_log)
w("- Runner exit code: %s%s\n" % (rc, " (124 = wrapper timeout)" if rc == "124" else ""))
