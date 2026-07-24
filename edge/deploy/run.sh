#!/usr/bin/env bash
# 說說學伴 — 邊緣部署 [3/3] run：SSH 進裝置背景啟動 run_edge.sh 並 health-check
# （D-03 範圍：只驗 server 起來 + 回 health，不含完整聲音迴路）。
#
# 07-03 board bring-up 實測確認 Yocto 板卡無 adb 介面，改用 SSH 背景啟動 +
# 從開發端直接對裝置區網 IP 發出 curl（見 edge/BOARD_BRINGUP_DECISION.md）。
set -euo pipefail

SSH_HOST="${TALKYBUDDY_EDGE_SSH_HOST:?請設定 TALKYBUDDY_EDGE_SSH_HOST（裝置區網 IP；DHCP 配發，無固定預設值）}"
SSH_USER="${TALKYBUDDY_EDGE_SSH_USER:-root}"
TARGET_ROOT="${TALKYBUDDY_EDGE_DEVICE_ROOT:-/root/talkybuddy}"
SSH_TARGET="${SSH_USER}@${SSH_HOST}"
HEALTH_PORT="${TALKYBUDDY_EDGE_HEALTH_PORT:-8787}"
HEALTH_URL="${TALKYBUDDY_EDGE_HEALTH_URL:-http://${SSH_HOST}:${HEALTH_PORT}/}"
HEALTH_TIMEOUT_S="${TALKYBUDDY_EDGE_HEALTH_TIMEOUT_S:-30}"

echo "=== [3/3] run：SSH 於裝置啟動 run_edge.sh ==="
echo "  目標：${SSH_TARGET}:${TARGET_ROOT}"

echo "  - 確認裝置連線"
ssh -o ConnectTimeout=5 "${SSH_TARGET}" "echo ok" >/dev/null

echo "  - 於裝置背景啟動 edge/runtime/run_edge.sh"
ssh "${SSH_TARGET}" "cd '${TARGET_ROOT}' && nohup ./edge/runtime/run_edge.sh > /tmp/talkybuddy_edge.log 2>&1 & disown"

echo "  - health-check：從開發端直接對裝置區網 IP 發出（逾時 ${HEALTH_TIMEOUT_S}s）"
DEADLINE=$((SECONDS + HEALTH_TIMEOUT_S))
until curl -sf "${HEALTH_URL}" >/dev/null; do
  if [ "${SECONDS}" -ge "${DEADLINE}" ]; then
    echo "ERROR: health-check 逾時，server 未在 ${HEALTH_TIMEOUT_S}s 內回應 ${HEALTH_URL}" >&2
    echo "       裝置上 log：ssh ${SSH_TARGET} cat /tmp/talkybuddy_edge.log" >&2
    exit 1
  fi
  sleep 2
done

echo "=== health-check 通過：server 已在裝置上起來並回應 ${HEALTH_URL} ==="
