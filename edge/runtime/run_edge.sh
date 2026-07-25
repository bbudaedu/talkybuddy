#!/usr/bin/env bash
# 說說學伴 — 邊緣端 launcher（Hti G520，Yocto 原生 glibc Linux 內執行）
#
# 用途：於裝置 SSH shell 內，以既有 server/（不複製 code，見 D-05 /
# edge/runtime/README.md）啟動 uvicorn，注入 TALKYBUDDY_PIPELINE_PROFILE=edge。
# 07-03 board bring-up 實測確認 Yocto 為原生環境，本腳本不含 proot/Android 特定
# 邏輯，直接沿用即可（見 edge/BOARD_BRINGUP_DECISION.md）。
#
# 路徑約定：此腳本假設部署佈局為
#   <TARGET_ROOT>/server/...        （edge/deploy/push.sh rsync 推送既有 server/）
#   <TARGET_ROOT>/edge/runtime/run_edge.sh   （本檔案，push 到相同 <TARGET_ROOT>）
# 不硬編個人開發者 home 絕對路徑；一律以本腳本自身位置相對定位 <TARGET_ROOT>
# （= 本腳本所在目錄的上兩層），可在任何 push 目標路徑下正確運作。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${TARGET_ROOT}"

export TALKYBUDDY_PIPELINE_PROFILE=edge

# 優先使用裝置上已建置的 venv（沿用 scripts/setup_env.sh 的 .venv 慣例）；
# 首次 provisioning（見 edge/runtime/provision_device.sh）若尚未建 venv，退回
# 系統 python3（假設已裝好相依套件，供 board bring-up spike 快速起跑）。
if [ -x "${TARGET_ROOT}/.venv/bin/python" ]; then
  PYTHON_BIN="${TARGET_ROOT}/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

# Phase 8（ELOOP-02，Blocker 4）：先背景拉起 llama-server（edge/runtime/
# run_llama_server.py main()，內部 os.execv 換成交叉編譯出的 native binary，
# host 一律經該模組預設 127.0.0.1／loopback，本腳本不硬編任何對外位址），
# 等其 /health 就緒後才 exec uvicorn。llama-server 綁定範圍與 uvicorn 的
# 0.0.0.0（既有、已接受風險）完全無關，不得修改下方 uvicorn 啟動行。
"${PYTHON_BIN}" -m edge.runtime.run_llama_server &
LLAMA_SERVER_PID=$!

LLAMA_SERVER_HEALTH_PORT="${TALKYBUDDY_LLM_SERVER_PORT:-8080}"
LLAMA_SERVER_HEALTH_URL="http://127.0.0.1:${LLAMA_SERVER_HEALTH_PORT}/health"

for i in $(seq 1 30); do
  curl -sf "${LLAMA_SERVER_HEALTH_URL}" >/dev/null 2>&1 && break
  sleep 1
done

if ! curl -sf "${LLAMA_SERVER_HEALTH_URL}" >/dev/null 2>&1; then
  # 逾時不中止 uvicorn 啟動：EdgeLLM.available() 的短逾時設計本來就容忍
  # llama-server 稍晚/未就緒，pipeline 會走 scaffold-only 降級，不 crash
  # （T-08-07；RESEARCH.md Pattern 3 trade-off）。
  echo "WARN: llama-server（PID ${LLAMA_SERVER_PID}）未在 30 秒內於 ${LLAMA_SERVER_HEALTH_URL} 回應 /health，" >&2
  echo "      仍繼續啟動 uvicorn；本輪對話將走 scaffold-only 降級，直到 llama-server 就緒。" >&2
else
  # 08-05 checkpoint 真機實測發現：llama-server 剛起、prompt cache 全空時第一次
  # /v1/chat/completions 要重算整段 system prompt（≈293 token，Genio 520 上 ≈7.5 秒），
  # 把冷啟動第一輪端到端延遲推到 10 秒（超過 D-05 的 3–4 秒門檻）。這裡在 exec uvicorn
  # 之前先送一次暖身呼叫吃下這筆成本，讓開機後現場觀眾聽到的第一句就落在門檻內。
  # 失敗不可擋開機（warmup_llama_server.warmup 內部已吞例外回 False）。
  echo "warming up llama-server prompt cache..."
  "${PYTHON_BIN}" -m edge.runtime.warmup_llama_server "http://127.0.0.1:${LLAMA_SERVER_HEALTH_PORT}" || true
fi

exec "${PYTHON_BIN}" -m uvicorn server.app:app --host 0.0.0.0 --port 8787
