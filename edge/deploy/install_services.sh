#!/usr/bin/env bash
# 安裝 systemd unit，讓裝置**開機即待機**：不必 SSH 進去手動起 stack。
#
# 為什麼需要這個（2026-07-30）：
#   1. `local_client` 先前以 nohup 手動啟動，閒置後崩潰就沒人接手——玩偶放在
#      桌上等評審過來，按下去毫無反應，而從外面看跟按鍵故障一模一樣。
#      程式內的斷線重連已修（見 local_client.run_loop），但**任何未預期的崩潰
#      仍需要有人兜底重啟**，這正是 Restart=always 的職責。
#   2. 「無螢幕實體伴讀裝置」這個宣稱要站得住，就不能每次都靠一台筆電 SSH
#      進去啟動。插電開機就該能用。
#
# 用法（在裝置上，需 root）：
#   ./edge/deploy/install_services.sh          # 安裝 + enable（不啟動）
#   ./edge/deploy/install_services.sh --now    # 安裝 + enable + 立刻啟動
#
# ⚠️ 若目前有手動 nohup 起的 uvicorn / llama-server / local_client 在跑，
#    請先停掉再 --now，否則會搶 8787 埠。本腳本會偵測並提醒。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_ROOT="${TALKYBUDDY_EDGE_DEVICE_ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
UNITS=(talkybuddy-server talkybuddy-local-client)
SYSTEMD_DIR=/etc/systemd/system

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: 需要 root（要寫 ${SYSTEMD_DIR}）" >&2
  exit 1
fi

if [ ! -x "${TARGET_ROOT}/.venv/bin/python" ]; then
  echo "ERROR: 找不到 ${TARGET_ROOT}/.venv/bin/python——請先跑 provision_device.sh" >&2
  exit 1
fi

echo "=== 安裝 unit（TARGET_ROOT=${TARGET_ROOT}）==="
for unit in "${UNITS[@]}"; do
  src="${SCRIPT_DIR}/${unit}.service"
  [ -f "$src" ] || { echo "ERROR: 缺少 ${src}" >&2; exit 1; }
  # 用 | 當分隔符：路徑含 / 會撞到 sed 的預設分隔符
  sed "s|@TARGET_ROOT@|${TARGET_ROOT}|g" "$src" > "${SYSTEMD_DIR}/${unit}.service"
  echo "  → ${SYSTEMD_DIR}/${unit}.service"
done

systemctl daemon-reload
systemctl enable "${UNITS[@]}"
echo "=== 已 enable（開機自動啟動）==="

# 手動起的行程會與 service 搶 8787 埠，先警告再說
stale=$(pgrep -af 'uvicorn|llama-server|local_client' 2>/dev/null | grep -v systemd || true)
if [ -n "$stale" ]; then
  echo
  echo "⚠️ 偵測到手動啟動的行程，會與 service 搶埠："
  echo "$stale" | sed 's/^/    /'
  echo "    停掉：pkill -f 'uvicorn|llama-server|local_client'"
fi

if [ "${1:-}" = "--now" ]; then
  echo
  echo "=== 立刻啟動 ==="
  systemctl restart "${UNITS[@]}"
  sleep 3
  systemctl --no-pager --lines=0 status "${UNITS[@]}" || true
else
  echo
  echo "尚未啟動。要現在啟動："
  echo "  systemctl start ${UNITS[*]}"
fi

echo
echo "常用："
echo "  systemctl status talkybuddy-local-client"
echo "  journalctl -u talkybuddy-local-client -f    # 看「按一下按鍵開始錄音...」"
echo "  systemctl restart talkybuddy-server"
