#!/usr/bin/env bash
# 說說學伴 — 邊緣部署 [3/3] run：adb shell 進裝置 proot Debian，啟動 run_edge.sh
# 並做 health-check（D-03 範圍：只驗 server 起來 + 回 health，不含完整聲音迴路）。
set -euo pipefail

DEVICE_PROOT_ROOTFS="${TALKYBUDDY_EDGE_PROOT_ROOTFS:-/data/data/com.termux/files/usr/var/lib/proot-distro/installed-rootfs/debian}"
TARGET_ROOT="${TALKYBUDDY_EDGE_DEVICE_ROOT:-${DEVICE_PROOT_ROOTFS}/root/talkybuddy}"
HEALTH_URL="${TALKYBUDDY_EDGE_HEALTH_URL:-http://127.0.0.1:8787/}"
HEALTH_TIMEOUT_S="${TALKYBUDDY_EDGE_HEALTH_TIMEOUT_S:-30}"

echo "=== [3/3] run：於裝置 proot Debian 啟動 run_edge.sh ==="
echo "  目標路徑：${TARGET_ROOT}"

echo "  - 確認裝置連線"
adb get-state >/dev/null

# TODO：實際「adb shell 進 proot-distro Debian」的具體指令依裝置上
# proot-distro/Termux 版本而定（例如 `proot-distro login debian`），
# 請參照 ~/hackathon/ 的 Hti G520 SDK 文件確認正確喚起方式。以下以
# nohup 背景啟動 server，供本機 adb shell 之後續 health-check 使用。
echo "  - 於裝置背景啟動 edge/runtime/run_edge.sh"
adb shell "cd '${TARGET_ROOT}' && nohup ./edge/runtime/run_edge.sh > /tmp/talkybuddy_edge.log 2>&1 &"

echo "  - health-check：等待 server 回應（逾時 ${HEALTH_TIMEOUT_S}s）"
DEADLINE=$((SECONDS + HEALTH_TIMEOUT_S))
until adb shell "curl -sf '${HEALTH_URL}' >/dev/null"; do
  if [ "${SECONDS}" -ge "${DEADLINE}" ]; then
    echo "ERROR: health-check 逾時，server 未在 ${HEALTH_TIMEOUT_S}s 內回應 ${HEALTH_URL}" >&2
    echo "       裝置上 log：${TARGET_ROOT}/../../tmp/talkybuddy_edge.log（見 adb shell cat /tmp/talkybuddy_edge.log）" >&2
    exit 1
  fi
  sleep 2
done

echo "=== health-check 通過：server 已在裝置上起來並回應 ${HEALTH_URL} ==="
