#!/usr/bin/env bash
# 說說學伴 — 邊緣端 launcher（proot-distro Debian 內執行）
#
# 用途：進入 proot Debian 後，於裝置 rootfs 上以既有 server/（不複製 code，見
# D-05 / edge/runtime/README.md）啟動 uvicorn，注入 TALKYBUDDY_PIPELINE_PROFILE=edge。
#
# 路徑約定：此腳本假設部署佈局為
#   <TARGET_ROOT>/server/...        （edge/deploy/push.sh adb push 既有 server/）
#   <TARGET_ROOT>/edge/runtime/run_edge.sh   （本檔案，push 到相同 <TARGET_ROOT>）
# 不硬編個人開發者 home 絕對路徑；一律以本腳本自身位置相對定位 <TARGET_ROOT>
# （= 本腳本所在目錄的上兩層），可在任何 push 目標路徑下正確運作（Android 14
# proot 這一條路徑，不做 dual-host／Yocto 抽象，YAGNI，D-02）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${TARGET_ROOT}"

export TALKYBUDDY_PIPELINE_PROFILE=edge

# 優先使用裝置上已建置的 venv（沿用 scripts/setup_env.sh 的 .venv 慣例）；
# proot-distro 首次 provisioning 若尚未建 venv，退回系統 python3（假設已
# apt 安裝好相依套件，供 board bring-up spike 快速起跑）。
if [ -x "${TARGET_ROOT}/.venv/bin/python" ]; then
  PYTHON_BIN="${TARGET_ROOT}/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

exec "${PYTHON_BIN}" -m uvicorn server.app:app --host 0.0.0.0 --port 8787
