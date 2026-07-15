"""spike_b.py — 路 B：espeak 同源音標發音評分（對照路 A）。

模型：facebook/wav2vec2-lv-60-espeak-cv-ft（官方 Wav2Vec2Processor，phonemizer 後端）。
reference：phonemizer(espeak, en-us) → 與模型輸出同一套 espeak IPA。
依賴：espeak-ng 系統二進位（sudo apt）+ phonemizer。

驗收同路 A：正確 ref 高分、全錯 ref 低分、分數隨語音距離單調遞減。
"""
from __future__ import annotations

import sys
import soundfile as sf
import librosa

WAV = sys.argv[1] if len(sys.argv) > 1 else "spike/pron_assess/clean_en_apple.wav"
MODEL_ID = "facebook/wav2vec2-lv-60-espeak-cv-ft"


def phonemize_ref(text: str) -> list[str]:
    """phonemizer(espeak en-us) → 逐音素 IPA 序列（Separator 逐音素分隔，去 stress）。"""
    from phonemizer import phonemize
    from phonemizer.separator import Separator
    sep = Separator(phone=" ", word=" | ")
    out = phonemize(text, language="en-us", backend="espeak", strip=True,
                    with_stress=False, separator=sep, preserve_punctuation=False)
    return [p for p in out.split() if p and p != "|"]


def wav2vec2_phones(wav_path: str) -> list[str]:
    """官方 Wav2Vec2Processor 解碼 → espeak IPA 音素（空白分隔）。"""
    import torch
    from transformers import Wav2Vec2Processor, Wav2Vec2ForCTC

    proc = Wav2Vec2Processor.from_pretrained(MODEL_ID)
    model = Wav2Vec2ForCTC.from_pretrained(MODEL_ID)
    model.eval()
    audio, sr = sf.read(wav_path)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:
        audio = librosa.resample(audio.astype("float32"), orig_sr=sr, target_sr=16000)
    inputs = proc(audio, sampling_rate=16000, return_tensors="pt")
    with torch.no_grad():
        logits = model(inputs.input_values).logits
    ids = torch.argmax(logits, dim=-1)
    text = proc.batch_decode(ids)[0]  # espeak IPA，音素以空白分隔
    return [p for p in text.split() if p]


def align_score(ref: list[str], hyp: list[str]) -> float:
    n, m = len(ref), len(hyp)
    if n == 0:
        return 0.0
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
    hits = max(0, n - dp[n][m])
    return round(100.0 * hits / n, 1)


def main():
    print(f"音檔：{WAV}")
    hyp = wav2vec2_phones(WAV)
    print(f"路B hyp（espeak IPA）：{' '.join(hyp)}  （{len(hyp)} 音素）")
    refs = [
        ("正確：I want to eat an apple.", "I want to eat an apple."),
        ("近似：I want to see an apple.", "I want to see an apple."),
        ("近似：I want to eat an orange.", "I want to eat an orange."),
        ("半對：I want a big red apple today.", "I want a big red apple today."),
        ("全錯：The quick brown fox jumps.", "The quick brown fox jumps."),
        ("全錯：Hello how are you doing today.", "Hello how are you doing today."),
    ]
    scores = []
    for label, txt in refs:
        r = phonemize_ref(txt)
        s = align_score(r, hyp)
        scores.append(s)
        print(f"  {s:5.1f}  ({len(r):2d}音素)  {label}")
    gap = round(scores[0] - max(scores[-2:]), 1)
    print(f"\n路B 差距（正確 − 全錯最高）= {gap}  →  ", end="")
    print("✅ GO" if gap >= 20 else ("🟡 弱" if gap >= 8 else "🔴 NO-GO"))


if __name__ == "__main__":
    main()
