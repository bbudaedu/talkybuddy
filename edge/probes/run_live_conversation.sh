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
#
# ## 雲端大腦
#
# 板子上放一份 $LAB/.env（**不是** /root/talkybuddy/.env，決賽路徑不碰）：
#
#   GEMINI_API_KEY=...            # 或 TALKYBUDDY_CLOUD_PROVIDER=bedrock + AWS 憑證
#
# 有這個檔就自動走雲端；沒有就是原本的純 edge 路徑，行為完全不變。
# 要臨時關掉雲端：`TALKYBUDDY_PIPECAT_CLOUD=0 bash -s < ...`
#
# 暖機是自動的：probe 會在印出「開始了」之前先打一次雲端，把冷啟動吃掉
# （板子實測第一次 1209-1905ms、穩態 691-950ms，冷的那次會超過 1.5s 上界，
# 剛好落在孩子講的第一句話上）。
set -u

SECONDS_TO_RUN="${1:-60}"
HARD_LIMIT=$(( SECONDS_TO_RUN + 60 ))   # 留給模型載入、雲端暖機與收尾
LAB=/root/pipecat-lab
LOG=/tmp/live_conversation.log
ENV_FILE="$LAB/.env"

if pgrep -x arecord >/dev/null; then
    echo "❌ 已有 arecord 在跑（很可能是 local-client 正在錄音），不啟動。"
    pgrep -a arecord
    exit 2
fi

# 有 .env 就走雲端。刻意不做「偵測到憑證就自動切」以外的猜測——
# 呼叫端可以用 TALKYBUDDY_PIPECAT_CLOUD=0 明確關掉。
CLOUD_MODE="${TALKYBUDDY_PIPECAT_CLOUD:-}"
if [ -z "$CLOUD_MODE" ]; then
    if [ -f "$ENV_FILE" ]; then CLOUD_MODE=1; else CLOUD_MODE=0; fi
fi

if [ "$CLOUD_MODE" = "1" ] && [ ! -f "$ENV_FILE" ]; then
    echo "❌ 要走雲端但找不到 $ENV_FILE。"
    echo "   寧可現在就停，也不要靜默跑成 edge 卻以為在跑雲端。"
    exit 2
fi

if [ "$CLOUD_MODE" = "1" ]; then
    echo "🌩  大腦：雲端（讀 $ENV_FILE，失敗當輪降級回 llama-server）"
else
    echo "🧠 大腦：本機 llama-server（未啟用雲端）"
fi

rm -f "$LOG"
setsid nohup bash -c "
    cd '$LAB' || exit 1
    if [ '$CLOUD_MODE' = '1' ]; then
        set -a; . '$ENV_FILE'; set +a
        export TALKYBUDDY_PIPECAT_CLOUD=1
    fi
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
