# -*- coding: utf-8 -*-
"""fetch_models.py — 只抓雲端主線需要的模型（容器 build 階段用）。

刻意不抓 qwen2.5-1.5b GGUF（1.1GB）：雲端腦走 Bedrock Converse，
edge LLM 在雲端容器內用不到。

抓的東西：
  - SenseVoice-Small（ASR 主力，~457MB）
  - piper zh/en 聲音（TTS 在地降級鏈，~122MB；ElevenLabs 掛掉時的保命線）

用法：
    python deploy/aws/fetch_models.py [--models-dir models]

已存在的檔案會跳過，可重複執行。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

SENSEVOICE_REPO = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
SENSEVOICE_DIR = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"

# piper 聲音（sherpa-onnx 可直接吃 piper 的 .onnx 格式，不需安裝 piper-tts 套件）
PIPER_VOICES = [
    ("rhasspy/piper-voices", "zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx",
     "zh_CN-huayan-medium.onnx"),
    ("rhasspy/piper-voices", "zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx.json",
     "zh_CN-huayan-medium.onnx.json"),
    ("rhasspy/piper-voices", "en/en_US/lessac/medium/en_US-lessac-medium.onnx",
     "en_US-lessac-medium.onnx"),
    ("rhasspy/piper-voices", "en/en_US/lessac/medium/en_US-lessac-medium.onnx.json",
     "en_US-lessac-medium.onnx.json"),
]


def _fetch_file(repo: str, remote: str, dest: Path) -> None:
    from huggingface_hub import hf_hub_download

    if dest.exists():
        print(f"  [skip] {dest.name} 已存在")
        return
    print(f"  [get ] {remote}")
    src = hf_hub_download(repo_id=repo, filename=remote)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dest)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-dir", default="models")
    args = ap.parse_args()
    models = Path(args.models_dir)
    models.mkdir(parents=True, exist_ok=True)

    print("=== [1/2] SenseVoice-Small（ASR）===")
    sv_dir = models / SENSEVOICE_DIR
    if (sv_dir / "model.int8.onnx").exists():
        print("  [skip] SenseVoice 已存在")
    else:
        from huggingface_hub import snapshot_download

        print(f"  [get ] {SENSEVOICE_REPO}")
        path = snapshot_download(repo_id=SENSEVOICE_REPO)
        shutil.copytree(path, sv_dir, dirs_exist_ok=True)

    print("=== [2/2] piper 聲音（TTS 在地降級）===")
    for repo, remote, local in PIPER_VOICES:
        _fetch_file(repo, remote, models / local)

    print("\n完成。雲端容器不含 qwen GGUF（改走 Bedrock Converse）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
