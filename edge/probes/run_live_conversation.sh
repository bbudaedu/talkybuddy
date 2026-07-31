#!/usr/bin/env bash
# 在板子上啟動真人對話 probe，且**保證麥克風會被還回去**。
#
# ## 為什麼需要這層包裝
#
# 2026-07-31 直接用前景 ssh 跑 probe，板子中途斷網、連線被切，probe 的 finally
# 清理區塊有沒有跑到無從得知——而殘留的 arecord 會佔住麥克風，讓 local-client
# 開不了錄音，症狀跟麥克風壞掉一模一樣（38aa261）。決賽當天出這種事很難查。
#
# 這支腳本用三層保險：
#   1. setsid + nohup：脫離 ssh session，連線斷了也不會被 SIGHUP 帶走
#   2. timeout：不管 probe 卡在哪，硬性上限一到就收掉
#   3. 收尾一律 pkill arecord/aplay：不依賴 Python 的 finally 有沒有跑到
#
# 用法（在開發機執行）：
#   ssh root@<板子> 'bash -s' < edge/probes/run_live_conversation.sh 90
# 之後讀結果：
#   ssh root@<板子> 'cat /tmp/live_conversation.log'
set -u

SECONDS_TO_RUN="${1:-60}"
HARD_LIMIT=$(( SECONDS_TO_RUN + 45 ))   # 留給模型載入與收尾
LAB=/root/pipecat-lab
LOG=/tmp/live_conversation.log

if pgrep -x arecord >/dev/null; then
    echo "❌ 已有 arecord 在跑（很可能是 local-client 正在錄音），不啟動。"
    pgrep -a arecord
    exit 2
fi

rm -f "$LOG"
setsid nohup bash -c "
    cd '$LAB' || exit 1
    PYTHONPATH='$LAB' timeout '$HARD_LIMIT' ./.venv/bin/python probe_live_conversation.py '$SECONDS_TO_RUN' > '$LOG' 2>&1
    rc=\$?
    # 不管上面怎麼結束（正常、timeout、被殺），麥克風一定要還回去。
    pkill -9 -x arecord 2>/dev/null
    pkill -9 -x aplay 2>/dev/null
    echo \"[wrapper] probe 結束 rc=\$rc，已確保 arecord/aplay 收乾淨\" >> '$LOG'
" >/dev/null 2>&1 &

echo "✅ 已在背景啟動，硬性上限 ${HARD_LIMIT}s"
echo "   對話 ${SECONDS_TO_RUN}s；結果在 $LOG"
echo "   即使 ssh 斷線也會自己收尾"
