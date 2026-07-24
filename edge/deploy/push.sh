#!/usr/bin/env bash
# 說說學伴 — 邊緣部署 [2/3] push：rsync 推送 server/ + edge/runtime + web/ 到裝置（SSH）
#
# 07-03 board bring-up 實測確認：Hti G520 燒 Yocto 成功後是原生 glibc Linux + SSH，
# 沒有 adb 介面；改用 SSH/rsync 取代 adb push（見 edge/BOARD_BRINGUP_DECISION.md）。
# 假設於 repo 根目錄執行，且開發機已可直連裝置 SSH（同區網或已核准的 Tailscale
# subnet route）。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

SSH_HOST="${TALKYBUDDY_EDGE_SSH_HOST:?請設定 TALKYBUDDY_EDGE_SSH_HOST（裝置區網 IP；DHCP 配發，無固定預設值）}"
SSH_USER="${TALKYBUDDY_EDGE_SSH_USER:-root}"
TARGET_ROOT="${TALKYBUDDY_EDGE_DEVICE_ROOT:-/root/talkybuddy}"
SSH_TARGET="${SSH_USER}@${SSH_HOST}"

echo "=== [2/3] push：rsync 推送 server/、edge/runtime、web 到裝置 ==="
echo "  目標：${SSH_TARGET}:${TARGET_ROOT}"

echo "  - 確認裝置 SSH 連線"
ssh -o ConnectTimeout=5 "${SSH_TARGET}" "echo ok" >/dev/null

echo "  - 建立裝置端目標目錄"
ssh "${SSH_TARGET}" "mkdir -p '${TARGET_ROOT}/edge/runtime'"

echo "  - rsync server/ -> ${TARGET_ROOT}/server"
rsync -az --exclude='__pycache__' --exclude='*.pyc' server/ "${SSH_TARGET}:${TARGET_ROOT}/server/"

echo "  - rsync edge/runtime/ -> ${TARGET_ROOT}/edge/runtime"
rsync -az --exclude='__pycache__' --exclude='*.pyc' edge/runtime/ "${SSH_TARGET}:${TARGET_ROOT}/edge/runtime/"

echo "  - rsync web/ -> ${TARGET_ROOT}/web"
rsync -az --exclude='__pycache__' --exclude='*.pyc' web/ "${SSH_TARGET}:${TARGET_ROOT}/web/"

# 裝置端 Python 相依（venv + pip 套件）provisioning：edge/runtime/provision_device.sh
# 已隨 edge/runtime 推送到裝置；SSH 進裝置後於 ${TARGET_ROOT} 手動執行一次：
#   cd "${TARGET_ROOT}" && ./edge/runtime/provision_device.sh
# （沿用 scripts/setup_env.sh 之 M1 已審釘版清單子集，見該腳本註解；本 phase
# 不新增未釘版套件，見 edge/deploy/README.md）。

echo "=== push 完成：server/、edge/runtime、web 已送達裝置 ${SSH_TARGET}:${TARGET_ROOT} ==="
