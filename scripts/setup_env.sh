#!/usr/bin/env bash
# 說說學伴 PC 原型 — 環境安裝與模型下載（Phase 0：x86 軟體先行）
set -uo pipefail
cd /home/budaedu/hackathon/talkybuddy
mkdir -p models logs data

echo "=== [1/4] 建立 venv 並安裝基礎套件 ==="
python3 -m venv .venv
.venv/bin/pip install -U pip wheel setuptools cmake ninja 2>&1 | tail -2
.venv/bin/pip install fastapi 'uvicorn[standard]' websockets pydantic pytest httpx numpy soundfile huggingface_hub 2>&1 | tail -2

echo "=== [2/4] 安裝 ASR / TTS ==="
.venv/bin/pip install faster-whisper 2>&1 | tail -2
# TTS 合成後端：sherpa-onnx（Apache-2.0），取代 piper-tts（GPL-3.0），沿用既有 Piper 格式聲音檔
.venv/bin/pip install sherpa-onnx onnx 2>&1 | tail -2
# 註：piper-tts 目前僅用來取得其內附的 espeak-ng-data 音素化資料（tts.py 的 fallback 路徑），
# 並非用於語音合成本身；espeak-ng-data 上游授權為 GPL-3.0，屬已知殘留風險（見 README 已知限制）。
.venv/bin/pip install piper-tts 2>&1 | tail -2
# ASR 主力：sherpa-onnx + SenseVoice-Small（int8）；OpenCC 做簡轉繁（s2twp）
.venv/bin/pip install opencc 2>&1 | tail -2
# A2 全雙工 barge-in（server/streaming）：pipecat-ai 串流編排 + pytest-asyncio。
# 版本釘住 A2-1 spike 已驗證組合；pipecat 的 numba 相依會把 numpy 降到 <2.5、
# onnxruntime 降到 1.24.x（皆與 sherpa/faster-whisper 相容，spike venv 已實證）。
# real-wiring 驗收（test_realwire_synth.py USE_STT=True + run_realwire）走真 SenseVoice STT
# ＝pipecat 的 FunASRSTTService → 需 funasr。先裝 CPU 版 torch（funasr 相依，避免預設拉
# CUDA 大輪子），再帶 [funasr] extra。實證不動上述 numpy/onnxruntime pins（2026-07-11）；
# 首跑 realwire 測試會經 modelscope 下載 SenseVoiceSmall（~900MB，之後快取）。
.venv/bin/pip install --index-url https://download.pytorch.org/whl/cpu torch torchaudio 2>&1 | tail -2
.venv/bin/pip install 'pipecat-ai[funasr]==1.5.0' 'pytest-asyncio==1.4.0' 2>&1 | tail -2

echo "=== [3/4] 下載模型（Qwen2.5-1.5B GGUF / whisper small / piper 聲音）==="
.venv/bin/python - <<'PY'
from huggingface_hub import hf_hub_download, snapshot_download
import shutil, os
os.makedirs('models', exist_ok=True)

# LLM: Qwen2.5-1.5B-Instruct Q4_K_M (~1.1GB)
p = hf_hub_download('Qwen/Qwen2.5-1.5B-Instruct-GGUF', 'qwen2.5-1.5b-instruct-q4_k_m.gguf')
shutil.copy(p, 'models/qwen2.5-1.5b-instruct-q4_k_m.gguf')
print('LLM ok')

# ASR: faster-whisper small（CTranslate2 INT8 可用）
snapshot_download('Systran/faster-whisper-small')
print('ASR ok')

# TTS: piper 中英聲音
for repo_path, out in [
    ('zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx', 'models/zh_CN-huayan-medium.onnx'),
    ('zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx.json', 'models/zh_CN-huayan-medium.onnx.json'),
    ('en/en_US/lessac/medium/en_US-lessac-medium.onnx', 'models/en_US-lessac-medium.onnx'),
    ('en/en_US/lessac/medium/en_US-lessac-medium.onnx.json', 'models/en_US-lessac-medium.onnx.json'),
]:
    p = hf_hub_download('rhasspy/piper-voices', repo_path)
    shutil.copy(p, out)
print('TTS ok')
PY

echo "=== [3.5/4] 下載 SenseVoice int8 模型（sherpa-onnx 官方 release, ~226MB）==="
SENSEVOICE_TARBALL="sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2"
SENSEVOICE_DIR="models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
if [ ! -f "$SENSEVOICE_DIR/model.int8.onnx" ]; then
  curl -L -o "models/$SENSEVOICE_TARBALL" \
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$SENSEVOICE_TARBALL"
  tar -xjf "models/$SENSEVOICE_TARBALL" -C models/
  rm -f "models/$SENSEVOICE_TARBALL"
  echo "SenseVoice ok"
else
  echo "SenseVoice 已存在，略過下載"
fi

echo "=== [4/4] 編譯安裝 llama-cpp-python（最耗時，失敗不擋整體）==="
.venv/bin/pip install llama-cpp-python 2>&1 | tail -3 || echo "WARN: llama-cpp-python 安裝失敗，系統將以規則式鷹架引擎運行（介面已預留降級）"

touch .venv_ready
echo "=== DONE: 環境就緒 (.venv_ready) ==="
