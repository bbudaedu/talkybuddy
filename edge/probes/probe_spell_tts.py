# -*- coding: utf-8 -*-
"""Spike：本地 TTS 能不能把 apple 念成逐字母 a-p-p-l-e？

方法：對幾種候選寫法合成 WAV，再用本地 SenseVoice ASR 轉回文字。
ASR 若讀回一串分開的字母（而非 "apple"），就代表 TTS 真的在念字母。
"""
import sys, wave, io, os
sys.path.insert(0, "/home/budaedu/talkybuddy")

from server.tts import TTSEngine

CANDIDATES = [
    ("plain",      "A P P L E"),
    ("dotted",     "A. P. P. L. E."),
    ("dash",       "A-P-P-L-E"),
    ("comma",      "A, P, P, L, E,"),
    ("lower_dot",  "a. p. p. l. e."),
    ("word_only",  "apple"),
]

OUT = "/tmp/claude-1000/-home-budaedu-talkybuddy/969b8140-f101-4764-b32a-60afa9a3533e/scratchpad/spell_out"
os.makedirs(OUT, exist_ok=True)

tts = TTSEngine()
print("tts.available() =", tts.available(), flush=True)

paths = []
for name, text in CANDIDATES:
    wav = tts.synth([("en", text)])
    if not wav:
        print(f"[{name}] 合成失敗")
        continue
    p = f"{OUT}/{name}.wav"
    with open(p, "wb") as f:
        f.write(wav)
    with wave.open(io.BytesIO(wav)) as w:
        dur = w.getnframes() / w.getframerate()
    print(f"[{name}] text={text!r} → {p} ({dur:.2f}s)", flush=True)
    paths.append((name, text, p))

print("\n=== ASR 讀回 ===", flush=True)
from server.asr import ASREngine
asr = ASREngine()
print("asr.available() =", asr.available(), flush=True)
for name, text, p in paths:
    try:
        out = asr.transcribe(p)
    except Exception as e:
        out = f"ERROR {e!r}"
    print(f"[{name}] {text!r} → {out!r}", flush=True)
