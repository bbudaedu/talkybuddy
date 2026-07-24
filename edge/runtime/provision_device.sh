#!/usr/bin/env bash
# 說說學伴 — 裝置端 Python 環境 provisioning
#
# 用途：在 Hti G520（Yocto，原生 glibc Linux，SSH 登入後）上執行一次，建立
# <TARGET_ROOT>/.venv 並安裝 server/ 啟動所需的 Python 套件。07-03 board
# bring-up 實測確認：裝置無 gcc/cmake，但 pip 能直接抓到 manylinux aarch64
# cp312 預編譯 wheel（fastapi/uvicorn[standard]/websockets/pydantic 皆已驗證
# 免編譯安裝成功），見 edge/BOARD_BRINGUP_DECISION.md。沿用既有
# scripts/setup_env.sh（M1 已審釘版清單）的版本選擇，但只裝邊緣端 MVP
# 實際需要的子集——不裝：
#   - torch / pipecat-ai[funasr]：Path 1 自架全雙工串流專用（server/streaming/），
#     邊緣 MVP 是回合式聽→想→說迴圈（ELOOP-01），不需要這條路徑。
#   - piper-tts：GPL-3.0 espeak-ng-data 殘留風險（見 CONCERNS.md）；邊緣 TTS
#     走 sherpa-onnx（Apache-2.0），不需要 piper。
#   - faster-whisper：ASR fallback，邊緣主力是 sherpa-onnx SenseVoice；board
#     bring-up spike（07-03）不需要，Phase 8 若要驗證 fallback 再另行安裝。
#   - llama-cpp-python：ELOOP-02 明訂邊緣 LLM 改用交叉編譯的 llama.cpp native
#     binary（-march=armv8.2-a+dotprod+i8mm）over localhost，非 Python wheel；
#     在弱腦 CPU 上編譯此 wheel 耗時且與 Phase 8 方向不符，此腳本刻意不裝。
#
# 不裝 ffmpeg：邊緣端 ALSA 直接擷取 16k mono WAV，server/pipeline.py 的
# RIFF-sniff fast path 走 soundfile 直讀，不需要 ffmpeg（見 D-07、
# edge/runtime/README.md「不裝 ffmpeg」一節）。
#
# 前置：此腳本假設在裝置上的 SSH shell 內、且 <TARGET_ROOT>（即
# edge/deploy/push.sh 的 push 目標目錄，已含 push 上來的 server/）為目前
# 工作目錄或以下方變數指定執行。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_ROOT="${TALKYBUDDY_EDGE_DEVICE_ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
cd "${TARGET_ROOT}"

echo "=== [1/2] 建立 venv 並安裝邊緣端所需套件 ==="
echo "  TARGET_ROOT=${TARGET_ROOT}"
python3 -m venv .venv
.venv/bin/pip install -U pip wheel setuptools 2>&1 | tail -2

# server/app.py 啟動最小相依（FastAPI + uvicorn + websocket 端點 + pydantic model）
.venv/bin/pip install fastapi 'uvicorn[standard]' websockets pydantic numpy soundfile huggingface_hub 2>&1 | tail -2

echo "=== [2/2] 安裝邊緣 ASR / TTS（sherpa-onnx，Apache-2.0）==="
.venv/bin/pip install sherpa-onnx onnx opencc 2>&1 | tail -2

touch .venv_ready
echo "=== DONE: 裝置端 venv 就緒（.venv_ready）；模型下載為 Phase 8 範疇，不在本腳本 ==="
