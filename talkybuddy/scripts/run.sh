#!/usr/bin/env bash
# 啟動 TalkyBuddy 伺服器（從 repo 根目錄以 venv python 執行 uvicorn）
set -euo pipefail

cd /home/budaedu/hackathon/talkybuddy
exec .venv/bin/python -m uvicorn server.app:app --host 0.0.0.0 --port 8787
