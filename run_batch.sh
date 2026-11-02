#!/usr/bin/env bash
# Two free-tier decisions concurrently; successful JSON files are resume markers.
set -euo pipefail
if [[ $# != 1 || ! -d "$1" ]]; then
    echo "Usage: $0 DIRECTORY_OF_TXT_FILES" >&2
    exit 2
fi
export COUNCIL_KEYS_FILE="$HOME/.config/council/keys.env"
exec python3 - "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" "$1" <<'PY'
import concurrent.futures
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

root, directory = map(Path, sys.argv[1:])
files = sorted(directory.resolve().glob('*.txt'))
if not files:
    sys.exit('No .txt decision files found')

def run(prompt):
    output = prompt.with_suffix('.json')
    if output.exists():
        print(f'SKIP {prompt.name}', flush=True)
        return True
    # Only publish an output after a successful run, so crashes remain resumable.
    for attempt in range(1, 4):
        fd, name = tempfile.mkstemp(prefix='.' + prompt.stem + '-', suffix='.json', dir=prompt.parent)
        os.close(fd)
        temporary = Path(name)
        try:
            print(f'RUN {prompt.name} attempt {attempt}/3', flush=True)
            with prompt.with_suffix('.log').open('a', encoding='utf-8') as log:
                result = subprocess.run(
                    ['python3', str(root / 'council/run_council.py'), '--prompt', str(prompt),
                     '--out', str(temporary), '--no-brand'], cwd=root,
                    stdout=log, stderr=subprocess.STDOUT)
            if result.returncode == 0:
                os.replace(temporary, output)
                print(f'DONE {prompt.name}', flush=True)
                return True
            if result.returncode == 2:  # configuration/argument failure; retries cannot fix it
                print(f'FAIL {prompt.name}: check {prompt.with_suffix(".log")}', file=sys.stderr)
                return False
        finally:
            temporary.unlink(missing_ok=True)
        if attempt < 3:
            time.sleep(30 * attempt)
    print(f'FAIL {prompt.name}: retries exhausted; see .log', file=sys.stderr)
    return False

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(run, files))
sys.exit(0 if all(results) else 1)
PY
