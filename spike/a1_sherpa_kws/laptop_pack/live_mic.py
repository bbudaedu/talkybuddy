#!/usr/bin/env python3
"""筆電真人聲即時喚醒詞測試：對麥克風講「哈囉學伴/說說學伴/嗨學伴」看即時偵測。
用法：  python live_mic.py [--threshold 0.25] [--score 2.0]
Ctrl+C 結束。目標：找出「真人聲會觸發、但正常講話不誤觸發」的門檻。
"""
import argparse, glob, os, sys
import numpy as np
import sounddevice as sd
import sherpa_onnx

HERE = os.path.dirname(os.path.abspath(__file__))


def find_model():
    d = glob.glob(os.path.join(HERE, "sherpa-onnx-kws-zipformer-*"))
    if not d:
        sys.exit("找不到模型資料夾，請先執行 python bootstrap.py")
    return d[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.25, help="越低越靈敏(易誤觸發)")
    ap.add_argument("--score", type=float, default=2.0, help="關鍵詞 boosting 分數")
    ap.add_argument("--keywords", default=os.path.join(HERE, "keywords.txt"))
    args = ap.parse_args()

    D = find_model()
    spotter = sherpa_onnx.KeywordSpotter(
        tokens=f"{D}/tokens.txt",
        encoder=f"{D}/encoder-epoch-12-avg-2-chunk-16-left-64.onnx",
        decoder=f"{D}/decoder-epoch-12-avg-2-chunk-16-left-64.onnx",
        joiner=f"{D}/joiner-epoch-12-avg-2-chunk-16-left-64.onnx",
        num_threads=2,
        keywords_file=args.keywords,
        keywords_score=args.score,
        keywords_threshold=args.threshold,
    )

    print("=" * 52)
    print("關鍵詞：", ", ".join(l.split("@")[-1].strip()
                              for l in open(args.keywords, encoding="utf-8") if l.strip()))
    print(f"門檻={args.threshold}  boosting={args.score}")
    try:
        print("預設輸入裝置：", sd.query_devices(kind="input")["name"])
    except Exception:
        pass
    print("對麥克風講喚醒詞試試… (Ctrl+C 結束)")
    print("=" * 52)

    sr = 16000
    block = int(0.1 * sr)  # 100ms
    stream = spotter.create_stream()
    n = 0
    with sd.InputStream(channels=1, dtype="float32", samplerate=sr, blocksize=block) as mic:
        while True:
            data, _ = mic.read(block)
            stream.accept_waveform(sr, data.reshape(-1))
            while spotter.is_ready(stream):
                spotter.decode_stream(stream)
            r = spotter.get_result(stream)
            if r:
                n += 1
                print(f"  🔴 偵測 #{n}: 【{r}】")
                spotter.reset_stream(stream)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n結束。")
