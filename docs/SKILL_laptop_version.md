---
name: council
description: Run a multi-model review council (9 free models across Meta/Alibaba/Google/Cohere/OpenAI-open/Poolside) over any artefact — emails, copy, plans, decisions — then apply the Jernih/Keruh filter and return ONE filtered recommendation. Use when Faris says "ask the council", "run the council", "get opinions", or wants a second/third opinion before committing. Costs zero Claude tokens for the model calls.
---

# Council

Nine models judge the artefact independently. Their votes are **layer 1 only**.
Claude then applies **Simpan yang Jernih, Buang yang Keruh** and delivers one
decision. Never report a raw tally as a recommendation.

## Setup (already done on this machine)
- Keys: `C:\Users\diony\.model-keys.env` (GROQ_API_KEY, OPENROUTER_API_KEY)
- Runner: `~/.claude/skills/council/run_council.py`
- **Python's urllib is Cloudflare-blocked here — the runner shells out to `curl`. Keep it that way.**

## The three-layer method

### Layer 1 — collect
Edit the PROMPT block in `run_council.py`, then `python run_council.py`.
Results land in `council2_out.json`.

**Prompt rules that make the difference between signal and noise:**
1. **State every deliberate stylistic choice as OFF-LIMITS, explicitly and exhaustively.** In the first run, "no signature block" was read as "delete his name" by 2 models and polluted the whole tally. Ambiguity in the brief is Claude's fault, not the model's.
2. **FORCE discrimination.** Never allow "all good". Demand:
   - a strict RANK of every item, no ties
   - a MUST FIX naming one exact phrase, mandatory even if they like everything
   - WHY TOP / WHY BOTTOM in one sentence each
   A model that would rubber-stamp everything still produces usable signal under forced choice.
3. **Point them at the audience, not at best practice.** "What does THIS recipient do when they read THIS?" beats "review this email."
4. Keep max_tokens ~900. Reasoning models burn the budget thinking and return nothing.

### Layer 2 — Jernih/Keruh filter (Claude's job, the actual work)
Classify every opinion before counting:

| Failure | Diagnosis | Action |
|---|---|---|
| Misread the brief | Claude's ambiguity | Fix the brief, RE-ASK. Don't discard silently. |
| Generic praise, no discrimination | Model too weak to rank voluntarily | Force-rank prompt. Re-ask. |
| Advice that would worsen the artefact | Genuine taste clash | KEEP AS SYMPTOM, not instruction — it marks where a reader stumbled. |
| Returned template/reasoning, no verdict | Token budget blown | Discard this run, lower max_tokens. |

Never "flip" a bad opinion — the opposite of bad advice is not good advice.
**Convergence across independent models on one exact phrase is the strongest
signal the council can produce.** Weight it above everything else.

### Layer 3 — decide
State the recommendation, name what was discarded and why (Faris audits the
filtering), and flag any genuine split as a taste call for him.

## Roster — Council 2.0 (finalised 15 Aug 2026, health-checked live)
10 voices across **6 independent providers** — no single provider cap can gut it.
Keys all in `.model-keys.env` (MISTRAL/GEMINI/ILMU added 15 Aug). All free.
- **Groq** (uncapped): `qwen/qwen3.6-27b` · `openai/gpt-oss-120b` · `groq/compound-mini`
- **Mistral**: `mistral-large-latest`  ·  **Google**: `gemini-3.5-flash`  ·  **Ilmu**: `ilmu-v3.1` (IlmuClaw, your 250M)
- **OpenRouter** (`:free`, bonus when up): `nvidia/nemotron-3.5-lightning` · `nvidia/nemotron-nano-12b-v2-vl` · `poolside/laguna-s-2.1`
- **Codex** (`gpt-5.6-luna`, CLI, separate call): the sharpest / high-stakes voice — the designated arbiter.

Sharp/independent (cap-proof): **Codex · Gemini · Mistral · IlmuClaw** + Laguna/Nemotron when OpenRouter is up.
### OpenRouter runtime seats (13 Sep 2026)

- **MiniMax-M3 (MiniMax/OR)** now uses paid `minimax/minimax-m3`; it is recorded as `__SKIPPED__ no OpenRouter credits` when neither OpenRouter key has credit.
- prompt.txt is read as utf-8-sig: a PowerShell-written prompt starts with a BOM, which used to hide every VERDICT label and mark all seats __NOVERDICT__.
- **NEX-N2.5 (NEX/OR)**, **Gemma-4 (Google-open/OR)** (replaced the 2.6B Liquid seat, too small to vote well), **Nemotron-3 (NVIDIA/OR)** and **Laguna (Poolside/OR)** are free fallback seats. Each resolves its preference list against the live `:free` model list once per run, so a removed slug does not kill the seat.
- Free OpenRouter calls use `OPENROUTER_API_KEY`, then retry once on 429 with `OPENROUTER_API_KEY_AITKL`. Paid OpenRouter calls use whichever of those keys has credits; no credits means skip, never a paid API call.

**Dropped:** `llama-3.1-8b-instant` (rubber-stamps), `llama-3.3-70b-versatile` (unavailable), `openai/gpt-oss-20b` (spirals), `cohere/north-mini-code` (spirals + capped).
Health check: `python C:/Users/diony/dev/rizqey-voice/council_health2.py` (pings every voice, shows capped/down).

## Limits
- ~25K chars of artefact max, or small models 413. Split large batches.
- Add Codex (`/codex challenge`) as a 10th voice for anything high-stakes; it is
  the most rigorous but over-polices sales copy — treat its style notes as keruh
  unless they name a checkable fact.

## Rizqey modes (short prompts — stack in order)

Templates live beside this file (`*_mode_prompt.txt`, `prospect_test_prompt.txt`). Canonical copies in git: `dev/rizqey-console/.cursor/skills/council/`. Sync: `powershell -File dev/rizqey-console/.cursor/skills/council/sync-to-claude-council.ps1`.

| Mode | File | Use when |
|---|---|---|
| **hook** | `hook_mode_prompt.txt` | After stage-3 draft; opening beat / H&M test |
| **objection** | `objection_mode_prompt.txt` | Before send; unanswered pushback |
| **prospect test** | `prospect_test_prompt.txt` | **Required** before irreversible send |
| **red-team** | `red_team_mode_prompt.txt` | Adversarial kill pass on batch or plan |
| **premortem** | `premortem_mode_prompt.txt` | Before arming a wave; assume failure in 30 days |

**Sonar stage 7 stack (typical):** hook → objection → prospect test. Add **premortem** on the wave plan before the first council run; add **red-team** when stakes are high.

**Run:** copy the mode file to `prompt.txt`, paste artefact under the `PASTE` delimiter, `python run_council.py` (`--t2` high-stakes). Jernih/Keruh on output — never report a raw tally.

Console Engine 360 runs the same prompts via LLM when you tick optional Council checkboxes; prospect test always runs last.
