#!/usr/bin/env bash
# Run the full TalkyBuddy test suite across both venvs.
#
# The main suite runs in the main .venv. The streaming/barge-in tests
# (server/streaming/tests) need pytest-asyncio + the pipecat stack, which live
# in the A2 spike venv only. Until those deps are consolidated, this script is
# the single entry point that exercises everything.
#
# Usage: ./run_tests.sh
set -euo pipefail

cd "$(dirname "$0")"

MAIN_PY=".venv/bin/python"
SPIKE_PY="spike/a2_pipecat/.venv/bin/python"

echo "=== main suite (.venv) ==="
# conftest.py in server/streaming/tests skips itself when pytest-asyncio is
# absent, so a plain run here collects everything else without erroring.
"$MAIN_PY" -m pytest -q --ignore=server/streaming/tests

echo
echo "=== streaming / barge-in suite (spike venv) ==="
if [ -x "$SPIKE_PY" ]; then
  "$SPIKE_PY" -m pytest server/streaming/tests -q
else
  echo "SKIP: spike venv not found at $SPIKE_PY" >&2
  exit 1
fi
