#!/usr/bin/env bash
# 啟動 TalkyBuddy 伺服器（從 repo 根目錄以 venv python 執行 uvicorn）
set -euo pipefail

# 用腳本自身位置推 repo 根目錄。原本寫死 /home/budaedu/hackathon/talkybuddy，
# 而 repo 實際在 /home/budaedu/talkybuddy——那個路徑今天還存在（是一個空的
# `chore: init gsd` repo），所以這支腳本不會報錯，只會**啟動另一份程式碼**。
# 決賽現場最不想遇到的失敗形狀：跑得起來，但跑的不是你剛改好的東西。
cd "$(dirname "$0")/.."
exec .venv/bin/python -m uvicorn server.app:app --host 0.0.0.0 --port 8787
