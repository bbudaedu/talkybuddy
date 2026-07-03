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
.venv/bin/pip install piper-tts 2>&1 | tail -2

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

echo "=== [4/4] 編譯安裝 llama-cpp-python（最耗時，失敗不擋整體）==="
.venv/bin/pip install llama-cpp-python 2>&1 | tail -3 || echo "WARN: llama-cpp-python 安裝失敗，系統將以規則式鷹架引擎運行（介面已預留降級）"

touch .venv_ready
echo "=== DONE: 環境就緒 (.venv_ready) ==="
