# -*- coding: utf-8 -*-
"""Probe：edge sherpa-onnx 合成耗時 vs. 雲端 Polly 網路來回耗時，哪邊真的比較快？

用法（在 Genio 520 實機上跑，走裝置真實的乙太/熱點網路路徑）：
    cd /home/budaedu/talkybuddy
    set -a && . ./.env.aws && set +a   # workshop 憑證，會過期
    .venv/bin/python edge/probes/probe_tts_latency.py

只做量測，不改 server/tts.py 或任何 pipeline 程式碼。

判讀：
- edge 那欄是 RTF（合成秒數 / 音訊秒數）。RTF < 1 代表比即時快；
  例如 RTF=0.5 代表合成 1 秒的音訊只花 0.5 秒。這是本機 CPU 的真實負擔，
  不受網路影響。
- cloud 那欄是「發出請求到拿到完整音訊」的秒數，走裝置真實的上游網路
  （乙太 → 手機熱點 → 行動網路 → us-west-2）。這裡故意逐段循序呼叫
  （不平行），因為對照 server/tts.py 現有的逐段合成方式；真的要接線
  時再考慮平行化。
- 兩欄都印出來，不下結論——現場網路每天不一樣，數字比你我的判斷準。
"""
import sys
import time
import wave
import io

# repo root 由檔案位置推導：這支要複製到 Genio 520 上跑，而板子的 repo 在
# /root/talkybuddy，寫死開發機路徑會直接 ModuleNotFoundError。
import os
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
print(f"repo root = {_ROOT}", flush=True)

# 與 server/tts.py 的 segments 格式一致：[(lang, text), ...]
CASES = {
    "短句_中文":   [("zh", "你說得很棒！")],
    "短句_英文":   [("en", "I want an apple.")],
    "教學回合_中英夾雜": [
        ("zh", "你說得很棒！我們來試下一個。"),
        ("en", "Can you say cat and dog for me?"),
    ],
}


def bench_edge():
    from server.tts import TTSEngine

    tts = TTSEngine()
    print(f"[edge] TTSEngine.available() = {tts.available()}", flush=True)
    if not tts.available():
        print("[edge] 引擎不可用，略過 edge 量測", flush=True)
        return

    # 先暖機：第一次用到某個 voice 會付模型載入的錢，混在 RTF 裡會讓人誤判
    # 「CPU 跑卡」。開發機實測：暖機前 RTF 0.63／0.58，暖機後 0.06 —— 差十倍。
    print("\n[edge] 暖機中（載入中英兩個 voice，不計入下方數字）…", flush=True)
    t0 = time.monotonic()
    for _, segments in CASES.items():
        tts.synth(segments)
    print(f"[edge] 暖機耗時 {time.monotonic() - t0:.2f}s", flush=True)

    print("\n=== edge（sherpa-onnx，本機 CPU，穩態）===", flush=True)
    for name, segments in CASES.items():
        t0 = time.monotonic()
        wav = tts.synth(segments)
        synth_s = time.monotonic() - t0
        if not wav:
            print(f"[{name}] 合成失敗")
            continue
        with wave.open(io.BytesIO(wav)) as w:
            audio_s = w.getnframes() / w.getframerate()
        rtf = synth_s / audio_s if audio_s > 0 else float("inf")
        print(
            f"[{name}] 合成耗時={synth_s:.2f}s  音訊長度={audio_s:.2f}s  "
            f"RTF={rtf:.2f}{'（比即時快）' if rtf < 1 else '（比即時慢，會卡）'}"
        )


def bench_cloud():
    try:
        import boto3
    except Exception as exc:
        print(f"[cloud] 沒有 boto3，略過雲端量測：{exc!r}")
        return

    client = boto3.client("polly", region_name="us-west-2")
    try:
        client.describe_voices(LanguageCode="en-US")
    except Exception as exc:
        print(f"[cloud] AWS 憑證無效或連不上，略過雲端量測：{exc!r}")
        return

    voice_for_lang = {"en": "Ivy", "zh": "Zhiyu"}

    print("\n=== cloud（Polly，走裝置真實上游網路）===", flush=True)
    for name, segments in CASES.items():
        t0 = time.monotonic()
        total_bytes = 0
        ok = True
        for lang, text in segments:
            try:
                resp = client.synthesize_speech(
                    Text=text,
                    VoiceId=voice_for_lang.get(lang, "Ivy"),
                    Engine="neural",
                    OutputFormat="mp3",
                    SampleRate="22050",
                )
                total_bytes += len(resp["AudioStream"].read())
            except Exception as exc:
                print(f"[{name}] 第 {lang} 段失敗：{exc!r}")
                ok = False
                break
        elapsed_s = time.monotonic() - t0
        if ok:
            print(
                f"[{name}] 逐段循序耗時={elapsed_s:.2f}s "
                f"（{len(segments)} 段 API 呼叫，未平行化）  mp3 bytes={total_bytes}"
            )


if __name__ == "__main__":
    bench_edge()
    bench_cloud()
