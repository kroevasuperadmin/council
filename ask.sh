#!/usr/bin/env bash
# ask.sh <brief.txt> [--tier2]
#
# Runs the Council panel on one brief and writes a one-page summary.md next to it.
# - Per-seat timeouts: the runner already caps each HTTP call at 240s (curl -m 240) and
#   retries rate limits with backoff. On top of that this wrapper caps the WHOLE panel run
#   with `timeout` (COUNCIL_RUN_TIMEOUT, default 1200s) so a stuck seat can never hang the box.
# - Raw per-seat answers (the runner's JSON: {seat name: that seat's text}) go in a subfolder
#   named <brief>.seats/ next to the brief, as council_out.json.
# - summary.md (next to the brief) holds: the vote tally, the reason for each risk raised,
#   SPLIT yes/no, and "seats answered out of seats called".
#
# No paid tiers by default (Tier 1 free seats only). Pass --tier2 to also run paid seats.
# Seats are the existing Groq / Gemini / Mistral / OpenRouter (pasarapi) seats. No Grok.
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUNNER="$HERE/council/run_council.py"

if [[ $# -lt 1 || ! -f "${1:-}" ]]; then
    echo "Usage: $0 <brief.txt> [--tier2]" >&2
    exit 2
fi
BRIEF="$(cd -- "$(dirname -- "$1")" && pwd)/$(basename -- "$1")"
shift || true

TIER2_FLAG=""
for arg in "$@"; do
    case "$arg" in
        --tier2) TIER2_FLAG="--tier2" ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

# Keys: prefer the restored config file; the Python runner also accepts plain env vars.
export COUNCIL_KEYS_FILE="${COUNCIL_KEYS_FILE:-$HOME/.config/council/keys.env}"

SEATS_DIR="${BRIEF}.seats"
mkdir -p "$SEATS_DIR"
OUT_JSON="$SEATS_DIR/council_out.json"
RUN_LOG="$SEATS_DIR/run.log"
SUMMARY="$(dirname -- "$BRIEF")/summary.md"

RUN_TIMEOUT="${COUNCIL_RUN_TIMEOUT:-1200}"   # whole-panel cap in seconds

echo "Council: judging $(basename -- "$BRIEF") ${TIER2_FLAG:+(with Tier 2) }..." >&2
set +e
timeout "${RUN_TIMEOUT}s" python3 "$RUNNER" --prompt "$BRIEF" --out "$OUT_JSON" --no-brand $TIER2_FLAG >"$RUN_LOG" 2>&1
RC=$?
set -e
if [[ $RC -eq 124 ]]; then
    echo "WARNING: panel hit the ${RUN_TIMEOUT}s wrapper timeout; summarising whatever was saved." >&2
fi

if [[ ! -f "$OUT_JSON" ]]; then
    echo "ERROR: no council_out.json produced. See $RUN_LOG" >&2
    exit 1
fi

# Build the one-page summary from the raw seat JSON.
python3 "$HERE/council/summarize.py" "$OUT_JSON" "$BRIEF" "$RUN_LOG" "$RC" >"$SUMMARY"

echo "----" >&2
echo "summary.md : $SUMMARY" >&2
echo "raw seats  : $OUT_JSON" >&2
echo "run log    : $RUN_LOG" >&2
cat "$SUMMARY"
