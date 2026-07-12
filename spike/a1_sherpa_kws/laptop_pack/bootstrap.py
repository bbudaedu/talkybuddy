#!/usr/bin/env python3
"""一鍵環境準備（跨平台 Win/Mac/Linux）：建 venv、裝套件、下載中文 KWS 模型。
用法：  python bootstrap.py
完成後依畫面提示執行 live_mic.py。
"""
import os, sys, subprocess, venv, urllib.request, tarfile

HERE = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(HERE, ".venv")
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
             "kws-models/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01.tar.bz2")
MODEL_DIR = os.path.join(HERE, "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01")


def venv_python():
    return os.path.join(VENV, "Scripts" if os.name == "nt" else "bin",
                        "python.exe" if os.name == "nt" else "python")


def main():
    if not os.path.isdir(VENV):
        print("[1/3] 建立 venv ...")
        venv.EnvBuilder(with_pip=True).create(VENV)
    py = venv_python()
    print("[2/3] 安裝套件 (sherpa-onnx / sounddevice / numpy) ...")
    subprocess.check_call([py, "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    subprocess.check_call([py, "-m", "pip", "install", "--quiet",
                           "sherpa-onnx", "sounddevice", "numpy"])
    if not os.path.isdir(MODEL_DIR):
        print("[3/3] 下載中文 KWS 模型 (~40MB) ...")
        tarpath = os.path.join(HERE, "kws-zh.tar.bz2")
        urllib.request.urlretrieve(MODEL_URL, tarpath)
        with tarfile.open(tarpath, "r:bz2") as t:
            t.extractall(HERE)
        os.remove(tarpath)
    else:
        print("[3/3] 模型已存在，略過下載")

    run = f'"{py}" live_mic.py'
    print("\n✅ 準備完成。開始即時測試：\n")
    print(f"   cd {HERE}")
    print(f"   {run}\n")
    print("   （可加 --threshold 0.25 調靈敏度；預設 0.25，真人聲建議 0.2~0.35 之間試）")


if __name__ == "__main__":
    main()
