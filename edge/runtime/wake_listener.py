#!/usr/bin/env python3
"""裝置端原生喚醒監聽：ALSA 串流 → sherpa-onnx KWS →（命中後）交給既有 pipeline。

**為什麼需要這支**：喚醒層原本跑在瀏覽器（`web/porcupine-engine.js` 與
sherpa-onnx KWS Web），意味著必須有一台電腦開著頁面對它講話，Genio 520 才會動。
提案書寫的是「無螢幕實體伴讀裝置」，現況與之不符；且普惠論述不該要求家裡有電腦。
本模組把喚醒與收音搬到裝置原生。緣由與完整規劃見 `edge/NATIVE_KWS_PLAN.md`。

**設計約束（皆為實測結果，勿隨意更動）**：

- 麥克風必須用 **`plughw:`** 而非 `hw:` —— USB 麥克風（Jieli，card 1）原生只支援
  48kHz，而 pipeline 要 16kHz mono 且 edge 端刻意不裝 ffmpeg。`hw:` 只會印一行
  warning 然後靜默給你 48kHz。`plughw` 在 ALSA 層重採樣，零額外 process。
- **引擎必須常駐。** 實測冷載入 TTS 2.66s／ASR 2.30s／KWS 1.32s，
  暖機後分別為 0.81s／0.31s。每次喚醒才載入會讓第一句慢到不可用。
- 音訊不落地：錄音檔用後即刪，比照 `server/pipeline.py` 既有紀律。

**已知硬體限制**：USB 麥克風有**實體靜音鍵**，重開機後回到靜音狀態，
且軟體無法偵測或控制。開機自檢應提示使用者確認（見 `--selftest`）。

用法：

    # 封閉自檢（自我合成 → 播放 → 錄音 → 偵測，不需人聲）
    ./.venv/bin/python edge/runtime/wake_listener.py --selftest

    # 實際監聽（Ctrl-C 結束）
    ./.venv/bin/python edge/runtime/wake_listener.py
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time

# 喚醒詞 pinyin 標音，與 `server/config.py::WAKE_SHERPA_KEYWORDS` 同一組
DEFAULT_KEYWORDS = "sh uō sh uō x ué b àn @說說學伴"

MIC_DEVICE = os.environ.get("TALKYBUDDY_MIC_DEVICE", "plughw:1,0")
SPK_DEVICE = os.environ.get("TALKYBUDDY_SPK_DEVICE", "plughw:0,0")
KWS_DIR = os.environ.get(
    "TALKYBUDDY_KWS_DIR",
    "models/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01",
)
SAMPLE_RATE = 16000
CHUNK = 3200  # 0.2 秒 @16kHz


def _build_spotter(kws_dir: str, keywords: str, threshold: float, score: float):
    import sherpa_onnx

    tag = "epoch-12-avg-2-chunk-16-left-64"
    kw_file = os.path.join(tempfile.gettempdir(), "talkybuddy_kws_keywords.txt")
    with open(kw_file, "w", encoding="utf-8") as fh:
        fh.write(keywords.rstrip("\n") + "\n")

    return sherpa_onnx.KeywordSpotter(
        tokens=os.path.join(kws_dir, "tokens.txt"),
        encoder=os.path.join(kws_dir, f"encoder-{tag}.int8.onnx"),
        decoder=os.path.join(kws_dir, f"decoder-{tag}.int8.onnx"),
        joiner=os.path.join(kws_dir, f"joiner-{tag}.int8.onnx"),
        keywords_file=kw_file,
        num_threads=2,          # 常駐服務，刻意不與 llama-server 的 6 執行緒相爭
        provider="cpu",
        keywords_score=score,
        keywords_threshold=threshold,
    )


def _scan(spotter, samples, sample_rate: int) -> list[tuple[str, float]]:
    """對一段既有音訊做喚醒掃描，回傳 (關鍵詞, 秒數) 清單。"""
    stream = spotter.create_stream()
    hits: list[tuple[str, float]] = []
    for i in range(0, len(samples), CHUNK):
        stream.accept_waveform(sample_rate, samples[i : i + CHUNK])
        while spotter.is_ready(stream):
            spotter.decode_stream(stream)
            result = spotter.get_result(stream)
            if result:
                hits.append((result, round(i / sample_rate, 2)))
                spotter.reset_stream(stream)
    return hits


def selftest(spotter) -> int:
    """自我合成 → 喇叭 → 麥克風 → 偵測。含負向對照，不需人聲。"""
    import soundfile as sf
    import wave

    sys.path.insert(0, os.getcwd())
    from server.tts import TTSEngine

    tts = TTSEngine()
    cases = [("正向", "說說學伴", True), ("負向", "今天天氣真好", False)]
    failures = 0

    for label, text, expect_hit in cases:
        wav_bytes = tts.synth([("zh", text)])
        if not wav_bytes:
            print(f"✗ {label}：TTS 回傳空值，無法自檢")
            failures += 1
            continue

        say_path = os.path.join(tempfile.gettempdir(), "tb_kws_say.wav")
        rec_path = os.path.join(tempfile.gettempdir(), "tb_kws_rec.wav")
        try:
            with open(say_path, "wb") as fh:
                fh.write(wav_bytes)
            with wave.open(say_path) as w:
                duration = w.getnframes() / w.getframerate()

            rec = subprocess.Popen(
                ["arecord", "-D", MIC_DEVICE, "-f", "S16_LE", "-r", str(SAMPLE_RATE),
                 "-c", "1", "-d", str(int(duration) + 2), rec_path],
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.8)  # 讓錄音先起來，否則會漏掉開頭
            subprocess.run(["aplay", "-D", SPK_DEVICE, say_path], stderr=subprocess.DEVNULL)
            rec.wait()

            samples, sr = sf.read(rec_path, dtype="float32")
            if samples.ndim > 1:
                samples = samples[:, 0]
            peak = float(abs(samples).max())
            hits = _scan(spotter, samples, sr)
        finally:
            for p in (say_path, rec_path):
                if os.path.exists(p):
                    os.unlink(p)

        ok = bool(hits) == expect_hit
        failures += 0 if ok else 1
        mark = "✓" if ok else "✗"
        print(f"{mark} {label}「{text}」peak={peak:.4f} → {hits if hits else '無偵測'}")
        if peak < 0.001:
            print("  ⚠ 錄到靜音——USB 麥克風有實體靜音鍵，請確認已按下開啟")

    return failures


def listen(spotter) -> None:
    """常駐監聽。以 arecord 子行程串流，避免額外 Python 音訊相依。"""
    import numpy as np

    print(f"監聽中（{MIC_DEVICE}）……說「說說學伴」試試，Ctrl-C 結束", flush=True)
    proc = subprocess.Popen(
        ["arecord", "-D", MIC_DEVICE, "-f", "S16_LE", "-r", str(SAMPLE_RATE),
         "-c", "1", "-t", "raw", "-q"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    stream = spotter.create_stream()
    silence_warned = False
    frames_seen = 0
    try:
        while True:
            raw = proc.stdout.read(CHUNK * 2)  # int16 = 2 bytes
            if not raw:
                break
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            frames_seen += 1
            if frames_seen == 25 and not silence_warned:  # 約 5 秒後檢查一次
                silence_warned = True
                print("（提示：若始終無反應，請確認 USB 麥克風的實體靜音鍵已開啟）", flush=True)

            stream.accept_waveform(SAMPLE_RATE, samples)
            while spotter.is_ready(stream):
                spotter.decode_stream(stream)
                result = spotter.get_result(stream)
                if result:
                    print(f"[{time.strftime('%H:%M:%S')}] 偵測到喚醒詞：{result}", flush=True)
                    spotter.reset_stream(stream)
    except KeyboardInterrupt:
        print("\n結束監聽")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true",
                        help="自我合成閉環自檢，不需人聲")
    parser.add_argument("--kws-dir", default=KWS_DIR)
    parser.add_argument("--keywords", default=DEFAULT_KEYWORDS)
    parser.add_argument("--threshold", type=float, default=0.25,
                        help="越低越易觸發、也越易誤觸（對齊 config.WAKE_SHERPA_THRESHOLD）")
    parser.add_argument("--score", type=float, default=1.0)
    args = parser.parse_args()

    if not os.path.isdir(args.kws_dir):
        print(f"找不到 KWS 模型目錄：{args.kws_dir}", file=sys.stderr)
        return 2

    t0 = time.time()
    spotter = _build_spotter(args.kws_dir, args.keywords, args.threshold, args.score)
    print(f"KeywordSpotter 就緒（{time.time() - t0:.2f}s）", flush=True)

    if args.selftest:
        return 1 if selftest(spotter) else 0
    listen(spotter)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
