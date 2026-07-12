#!/usr/bin/env python3
"""openWakeword 檔案偵測 spike：對任意 WAV 跑喚醒詞模型，印出每個模型的最高分與超門檻幀數。

用法：
    python detect.py <wav> [<wav> ...] [--threshold 0.5] [--models hey_jarvis,alexa]

openWakeword 需要 16kHz 單聲道 int16；本腳本會自動重取樣與轉單聲道。
純檔案驗證，不需麥克風。
"""
import argparse
import os
import sys

import glob

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
import openwakeword
from openwakeword.model import Model

CHUNK = 1280  # 80ms @ 16kHz，openWakeword 建議的串流塊大小
TARGET_SR = 16000

# resources/models 目錄，內含各喚醒詞 onnx
_MODELS_DIR = os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models")


def resolve_model_paths(names):
    """把喚醒詞名稱（hey_jarvis）解析成 onnx 檔路徑（hey_jarvis_v0.1.onnx）。"""
    paths = []
    for name in names:
        hits = sorted(glob.glob(os.path.join(_MODELS_DIR, f"{name}*.onnx")))
        # 排除特徵/vad 類模型
        hits = [h for h in hits if not os.path.basename(h).startswith(
            ("melspectrogram", "embedding", "silero"))]
        if not hits:
            raise SystemExit(f"找不到模型 {name} 於 {_MODELS_DIR}")
        paths.append(hits[0])
    return paths


def load_16k_mono_int16(path):
    audio, sr = sf.read(path, dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)  # 轉單聲道
    if sr != TARGET_SR:
        # 用 polyphase 重取樣到 16kHz
        from math import gcd
        g = gcd(sr, TARGET_SR)
        audio = resample_poly(audio, TARGET_SR // g, sr // g)
    # float32 [-1,1] -> int16
    audio = np.clip(audio, -1.0, 1.0)
    return (audio * 32767).astype(np.int16)


def run(model, samples, threshold):
    """逐塊送入模型，回傳 {model_name: (max_score, frames_over_threshold)}。"""
    peaks = {}
    over = {}
    n = len(samples)
    for start in range(0, n - CHUNK + 1, CHUNK):
        chunk = samples[start:start + CHUNK]
        scores = model.predict(chunk)
        for name, s in scores.items():
            peaks[name] = max(peaks.get(name, 0.0), float(s))
            if s >= threshold:
                over[name] = over.get(name, 0) + 1
    return {name: (peaks.get(name, 0.0), over.get(name, 0)) for name in peaks}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wavs", nargs="+")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--models", default="hey_jarvis,alexa,hey_mycroft",
                    help="逗號分隔的喚醒詞模型名")
    args = ap.parse_args()

    model_names = [m.strip() for m in args.models.split(",") if m.strip()]
    paths = resolve_model_paths(model_names)
    print(f"載入模型: {model_names} (onnx)")
    model = Model(wakeword_model_paths=paths)

    # 用模型實際註冊的鍵名來顯示（可能帶版本後綴）
    model_names = list(model.models.keys())
    print(f"門檻: {args.threshold}\n")
    header = f"{'檔案':32s} " + " ".join(f"{m[:12]:>14s}" for m in model_names)
    print(header)
    print("-" * len(header))
    for wav in args.wavs:
        if not os.path.exists(wav):
            print(f"{os.path.basename(wav):32s}  <找不到檔案>")
            continue
        samples = load_16k_mono_int16(wav)
        model.reset()  # 清狀態，避免跨檔案汙染
        res = run(model, samples, args.threshold)
        cells = []
        for m in model_names:
            peak, cnt = res.get(m, (0.0, 0))
            flag = "🔴FIRE" if cnt > 0 else "      "
            cells.append(f"{peak:5.3f}/{cnt:2d}{flag}")
        print(f"{os.path.basename(wav):32s} " + " ".join(f"{c:>14s}" for c in cells))
    print("\n格式: 最高分/超門檻幀數 (🔴FIRE = 該檔觸發了喚醒)")


if __name__ == "__main__":
    main()
