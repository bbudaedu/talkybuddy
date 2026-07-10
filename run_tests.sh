#!/usr/bin/env bash
# Run the full TalkyBuddy test suite across venvs.
#
# 主套件跑主 .venv；streaming/barge-in 測試（server/streaming/tests）需 pytest-asyncio
# ＋ pipecat 全家桶，跑獨立 .venv-streaming（A2 real-wiring，見 requirements-streaming.txt）。
#
# Usage: ./run_tests.sh
set -euo pipefail

cd "$(dirname "$0")"

MAIN_PY=".venv/bin/python"
STREAM_PY=".venv-streaming/bin/python"

echo "=== main suite (.venv) ==="
if [ -x "$MAIN_PY" ]; then
  # server/streaming/tests 的 conftest.py 在缺 pytest-asyncio 時自我 skip，
  # 主 venv 直接跑會 collect 其餘全部而不報錯。
  "$MAIN_PY" -m pytest -q --ignore=server/streaming/tests
else
  echo "SKIP: main venv not found at $MAIN_PY（本 worktree 未建主 .venv）" >&2
fi

echo
echo "=== streaming / barge-in suite (.venv-streaming) ==="
if [ -x "$STREAM_PY" ]; then
  PYTHONPATH="$(pwd)" "$STREAM_PY" -m pytest server/streaming/tests -q
else
  echo "SKIP: streaming venv not found at $STREAM_PY（見 requirements-streaming.txt）" >&2
  exit 1
fi
