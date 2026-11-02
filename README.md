# Council

A panel of AI models that each judge the same text and return a labelled verdict. Built by Faris's laptop team; this copy is for the Grok Bot box.
Python 3, standard library only (no pip install). Written on Windows; the three Windows-only paths were made portable on 2 Nov 2026 and this has NOT been run on Linux yet. Expect to fix small things on the first run.

## Run it headless

    export COUNCIL_KEYS_FILE=/path/to/keys.env        # KEY=VALUE lines, or just export the variables below
    python3 council/run_council.py --prompt batch.txt --out out.json --no-brand
    python3 council/run_council.py --prompt batch.txt --out out.json --no-brand --tier2    # adds the paid seats

- `--prompt FILE`  the brief to judge (default: council/prompt.txt, which is not included; always pass --prompt).
- `--out FILE`     where the JSON answers go: {seat name: that seat's text}.
- `--no-brand`     skip the brand and standards files. Use it for decision batches; use no flag for deck or page review.
- `--tier2`        also run the paid seats. Never the default.
- `--images ...`   rendered slides or pages for visual review (only vision seats can see them).
- It prints one line per seat, then `DONE n of m` and a `MISSING` line for any seat that failed, with the reason. A seat that cannot answer says why; nothing is dropped silently.
- A seat's answer must contain the uppercase labels the brief asks for (VERDICT 1:, REASON 1: ...). A seat without a label is asked once to finish from its own draft.

## What goes in a decision batch
One text file. It states the decision, the facts needed, the options, and the exact answer format (see batch_example.txt). Keep it to one decision per file, or at most about 10 numbered decisions per file with VERDICT 1..N labels. Groq seats take about 8,000 tokens per request; longer briefs are read in parts automatically, which is slower and weaker. For 231 decisions run a loop: one file per decision, one run per file, then read all the JSON files.

## Which models each tier calls
Tier 1 (free, default):
- Groq: openai/gpt-oss-120b, openai/gpt-oss-20b, qwen/qwen3.8-27b
- Google AI Studio (Gemini API): gemini-3.5-flash (also reads images), gemini-3.1-flash-lite
- Mistral: open-mistral-nemo, ministral-8b-latest, ministral-3b-latest
- OpenRouter free pool: Gemma-4, Nemotron-3, Laguna. With --images also Dots3-Note and Inkling vision seats.
Each seat falls back to sibling models on the same key if its model is out of quota or gone, and says so in a SEAT NOTE.
Tier 2 (paid, only with --tier2):
- IlmuClaw (ilmu-v3.1) through ILMU_BASE_URL
- OpenRouter paid: minimax/minimax-m3
- Codex CLI "arbiter": runs `codex exec --skip-git-repo-check` and reads the brief on stdin. It uses the ChatGPT login already on the box, no key. Set CODEX_BIN if codex is not on PATH.
No Grok and no Claude seat is called.

## Environment variable names (values are NOT in this repo; Faris supplies them)
- GROQ_API_KEY            required
- OPENROUTER_API_KEY      required (the script reads it at start even for Tier 1)
- OPENROUTER_API_KEY_AITKL  optional second OpenRouter key for fallback
- MISTRAL_API_KEY         Mistral seats (skipped without it)
- GEMINI_API_KEY          Gemini seats (skipped without it)
- ILMU_API_KEY and ILMU_BASE_URL   Tier 2 IlmuClaw only
- COUNCIL_KEYS_FILE       optional path to a KEY=VALUE file holding the above
- CODEX_BIN               optional path to the codex executable

## Reading the result
Each answer starts with its verdict label. Count the labels, read the one-line reasons, and give the most weight to any seat that names a concrete risk. Rules for how the laptop team weighs them (the Jernih/Keruh filter) are in docs/SKILL_laptop_version.md. A tie or a split goes to Faris, never to a coin toss.
Anything written for Faris or for outside people must also pass NO_AI_SLOP.md.

## Files
- council/run_council.py   the runner
- COUNCIL_STANDARDS.md, RIZQEY_BRAND.md   review and brand standards used when --no-brand is not set
- NO_AI_SLOP.md            writing rules
- prompts/                 the four special review modes (hook, objection, premortem, red team)
- docs/SKILL_laptop_version.md   how the laptop team uses Council (contains laptop paths; ignore those)
- batch_example.txt        a blank decision batch

## Linux decision batches

Place keys in `$HOME/.config/council/keys.env` (UTF-8; a BOM is accepted).
Run `./run_batch.sh /absolute/path/to/decisions` to run two `.txt` decisions
at a time using Tier 1 only. Each result is saved beside its prompt as `.json`;
existing JSON files are skipped. Logs append to the matching `.log` file.
Failed runs get up to three attempts with 30/60-second waits; configuration
errors stop immediately. An all-seat failure exits nonzero and is retried.
A partial panel with at least one answer is saved; inspect `MISSING` lines in
the log before relying on it. Delete its JSON to rerun that decision.
The wrapper fixes `COUNCIL_KEYS_FILE` to the path above; environment keys are
also accepted by the Python runner. Do not run overlapping wrappers on the
same input directory.
