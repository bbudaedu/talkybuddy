#!/usr/bin/env bash
# 切換玩偶的對話模式，並保證**永遠恰好有一個 client 在跑**。
#
# 為什麼需要這支（2026-07-30 實測踩到）：
#
#   systemd 的 `Conflicts=` 只負責「停掉另一個」，**不負責把它啟動回來**。
#   所以 `systemctl stop talkybuddy-live-client` 之後，兩個 client 都是
#   inactive，玩偶完全沒反應——而從外面看跟按鍵故障、麥克風壞掉一模一樣。
#   demo 當天測完 S2S 忘了切回去就中獎，而且現場沒有時間查。
#
#   直接下 `systemctl start` 也有陷阱：起錯那個會靜默把另一個停掉，
#   人不會發現自己剛剛換了模式。
#
# 用法（在裝置上，需 root）：
#
#   ./edge/deploy/switch_mode.sh turn     # 回合式（/ws/talk，預設、可 demo）
#   ./edge/deploy/switch_mode.sh live     # S2S（/ws/live，Nova Sonic，需 AWS 憑證）
#   ./edge/deploy/switch_mode.sh status   # 現在是哪個模式
set -euo pipefail

TURN_UNIT=talkybuddy-local-client.service
LIVE_UNIT=talkybuddy-live-client.service
SERVER_UNIT=talkybuddy-server.service

usage() {
  echo "用法：$0 {turn|live|status}" >&2
  echo "  turn   回合式（預設、可 demo）" >&2
  echo "  live   S2S（Nova Sonic）" >&2
  echo "  status 現在是哪個模式" >&2
}

show_status() {
  local turn live server
  turn=$(systemctl is-active "$TURN_UNIT" 2>/dev/null || true)
  live=$(systemctl is-active "$LIVE_UNIT" 2>/dev/null || true)
  server=$(systemctl is-active "$SERVER_UNIT" 2>/dev/null || true)

  printf "server       : %s\n" "$server"
  printf "回合式 client : %s\n" "$turn"
  printf "S2S    client : %s\n" "$live"
  echo

  if [ "$turn" = "active" ] && [ "$live" = "active" ]; then
    # 理論上 Conflicts= 不該讓這件事發生，但若真的發生了，症狀會是
    # 「上行 0 bytes、玩偶沒反應」——兩個搶同一支 USB 麥克風（ALSA 獨佔）。
    echo "⚠️ 兩個 client 同時在跑——它們會搶麥克風，玩偶不會有反應。"
    echo "   跑 '$0 turn' 或 '$0 live' 修正。"
    return 1
  fi
  if [ "$turn" != "active" ] && [ "$live" != "active" ]; then
    echo "⚠️ 沒有任何 client 在跑——按下按鍵不會有反應。"
    echo "   跑 '$0 turn' 回到可 demo 的預設狀態。"
    return 1
  fi
  if [ "$turn" = "active" ]; then
    echo "目前模式：回合式（按鍵 → 錄 4 秒 → ASR → LLM → TTS）"
  else
    echo "目前模式：S2S（Nova Sonic 持續串流，可插話）"
  fi
  if [ "$server" != "active" ]; then
    echo "⚠️ 但 server 沒在跑，client 會一直等不到 /api/status。"
    echo "   systemctl start $SERVER_UNIT"
    return 1
  fi
  return 0
}

switch_to() {
  local want="$1" other="$2" label="$3"

  if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: 需要 root" >&2
    exit 1
  fi

  # server 是兩種模式的共同前提，順手確保它在
  if [ "$(systemctl is-active "$SERVER_UNIT" 2>/dev/null || true)" != "active" ]; then
    echo "  server 沒在跑，先啟動"
    systemctl start "$SERVER_UNIT"
  fi

  # 明確停掉另一個而不是依賴 Conflicts=：意圖寫在腳本裡看得見，
  # 而且萬一 unit 檔被改壞、Conflicts 失效時這裡仍然正確。
  systemctl stop "$other" 2>/dev/null || true
  systemctl start "$want"
  sleep 3

  if [ "$(systemctl is-active "$want")" != "active" ]; then
    echo "ERROR: $want 起不來" >&2
    echo "  查原因：journalctl -u $want -n 30 --no-pager -o cat" >&2
    exit 1
  fi
  echo "已切換到：${label}"
  echo
  show_status
}

case "${1:-}" in
  turn)
    switch_to "$TURN_UNIT" "$LIVE_UNIT" "回合式"
    ;;
  live)
    # S2S 需要 AWS 憑證，缺的話 client 會起來但每場對話都失敗——
    # 先講清楚，不要讓人以為是按鍵或麥克風的問題
    if ! curl -s --max-time 8 http://127.0.0.1:8787/api/status 2>/dev/null \
         | grep -q '"live_s2s": *true'; then
      echo "⚠️ /api/status 的 live_s2s 不是 true——AWS 憑證可能沒設或無效。"
      echo "   S2S 會連得上但拿不到任何回覆。仍要繼續請按 Enter，取消按 Ctrl-C。"
      read -r _
    fi
    switch_to "$LIVE_UNIT" "$TURN_UNIT" "S2S（Nova Sonic）"
    ;;
  status)
    show_status
    ;;
  *)
    usage
    exit 1
    ;;
esac
