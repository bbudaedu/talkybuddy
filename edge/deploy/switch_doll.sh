#!/usr/bin/env bash
# 在兩條玩偶路徑之間切換，且**保證切完一定有一個在跑**。
#
# 為什麼要這支：systemd 的 `Conflicts=` **只停不啟**。直接 `systemctl stop`
# 某一個，兩個都會是 inactive、玩偶直接變啞，而症狀跟按鍵故障一模一樣
# （記憶 project-edge-deploy）。決賽現場出這種事很難查。
#
# 用法（在板子上）：
#   ./switch_doll.sh pipecat   # 切到 pipecat（雲端腦、VAD 連續聽）
#   ./switch_doll.sh local     # 切回 local-client（按 power 鍵、走 server）
#   ./switch_doll.sh status    # 現在誰在跑
set -u

PIPECAT=talkybuddy-pipecat.service
LOCAL=talkybuddy-local-client.service

_status() {
    for svc in "$PIPECAT" "$LOCAL"; do
        printf '%-36s %s\n' "$svc" "$(systemctl is-active "$svc" 2>/dev/null)"
    done
    printf '%-36s %s\n' "arecord（麥克風）" \
        "$(pgrep -x arecord >/dev/null && echo '被佔用' || echo '空著')"
}

case "${1:-status}" in
    pipecat)
        systemctl stop "$LOCAL" 2>/dev/null
        sleep 1
        pkill -9 -x arecord 2>/dev/null   # 確保麥克風真的放開了再啟動
        pkill -9 -x aplay 2>/dev/null
        sleep 1
        systemctl start "$PIPECAT" || { echo "❌ pipecat 起不來，切回 local"; systemctl start "$LOCAL"; exit 1; }
        echo "✅ 已切到 pipecat"
        ;;
    local)
        systemctl stop "$PIPECAT" 2>/dev/null
        sleep 1
        pkill -9 -x arecord 2>/dev/null
        pkill -9 -x aplay 2>/dev/null
        sleep 1
        systemctl start "$LOCAL" || { echo "❌ local-client 起不來"; exit 1; }
        echo "✅ 已切到 local-client"
        ;;
    status) ;;
    *) echo "用法：$0 {pipecat|local|status}"; exit 2 ;;
esac

echo "---"
_status
