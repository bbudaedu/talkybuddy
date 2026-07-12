#!/usr/bin/env python3
"""sherpa-onnx KWS 檔案偵測 spike：對 WAV 跑中文喚醒詞偵測。
用法: python kws_detect.py <keywords.txt> <wav> [<wav> ...]
"""
import sys, glob, os
import numpy as np, soundfile as sf
import sherpa_onnx

D = os.path.join(os.path.dirname(__file__),
                 "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01")


def load_16k(path):
    a, sr = sf.read(path, dtype="float32", always_2d=True)
    a = a.mean(axis=1)
    if sr != 16000:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(sr, 16000)
        a = resample_poly(a, 16000 // g, sr // g)
    # 前補 0.5s 靜音給 context
    return np.concatenate([np.zeros(8000, "float32"), a]).astype(np.float32)


def main():
    kw_file = sys.argv[1]
    wavs = sys.argv[2:]
    spotter = sherpa_onnx.KeywordSpotter(
        tokens=f"{D}/tokens.txt",
        encoder=f"{D}/encoder-epoch-12-avg-2-chunk-16-left-64.onnx",
        decoder=f"{D}/decoder-epoch-12-avg-2-chunk-16-left-64.onnx",
        joiner=f"{D}/joiner-epoch-12-avg-2-chunk-16-left-64.onnx",
        num_threads=2,
        keywords_file=kw_file,
        keywords_score=3.0,
        keywords_threshold=0.05,
    )
    print(f"keywords: {open(kw_file).read().strip()}\n")
    print(f"{'檔案':26s} 偵測結果")
    print("-" * 50)
    for w in wavs:
        samples = load_16k(w)
        s = spotter.create_stream()
        s.accept_waveform(16000, samples)
        tail = np.zeros(8000, "float32")  # 尾端靜音沖出結果
        s.accept_waveform(16000, tail)
        s.input_finished()
        hits = []
        while spotter.is_ready(s):
            spotter.decode_stream(s)
            r = spotter.get_result(s)
            if r:
                hits.append(r)
                spotter.reset_stream(s)
        flag = "🔴FIRE " + ",".join(hits) if hits else "      (無)"
        print(f"{os.path.basename(w):26s} {flag}")


if __name__ == "__main__":
    main()
