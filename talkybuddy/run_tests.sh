#!/usr/bin/env bash
# Run the full TalkyBuddy test suite in the main .venv.
#
# As of A2-4 the streaming / barge-in deps (pipecat-ai + pytest-asyncio) are
# consolidated into the main .venv, so a single venv now runs everything —
# the main suite (tests/) and the streaming/barge-in suite
# (server/streaming/tests/) together.
#
# Usage: ./run_tests.sh
set -euo pipefail

cd "$(dirname "$0")"

MAIN_PY=".venv/bin/python"

echo "=== full suite (.venv): main + streaming/barge-in ==="
# `python -m pytest` puts the repo root on sys.path, so `import server...`
# resolves for the streaming tests without an explicit PYTHONPATH.
"$MAIN_PY" -m pytest -q
