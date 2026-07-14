"""spike.py — 驗證 wav2vec2-espeak 發音評分核心（read-aloud，已知 reference）。

驗收假說（不需知道原台詞、不需「發錯音」樣本）：
  同一段音檔，餵「正確 reference」應得高分、餵「錯誤 reference」應得低分。
  → 若成立＝評分器有鑑別力，wav2vec2-espeak + g2p_en + 對齊 這條路可行。

流程：
  1. faster-whisper 轉出音檔的實際英文台詞 T（＝正確 reference）。
  2. wav2vec2-lv-60-espeak CTC 解碼音檔 → 實際發音 IPA 音素序列。
  3. g2p_en 把 reference 句 → ARPAbet → 映射到 IPA。
  4. 對齊（edit-distance）→ 命中率分數 0-100。
  5. 正確 ref vs 錯誤 ref 各算一次，比較。

純 spike：獨立、可丟棄，不進 server/。
"""
from __future__ import annotations

import sys
import numpy as np
import soundfile as sf
import librosa

WAV = sys.argv[1] if len(sys.argv) > 1 else "docs/voice-reference/alice_v3_baseline.wav"
WRONG_REF = "The quick brown fox jumps over the lazy dog by the river."

MODEL_ID = "vitouphy/wav2vec2-xls-r-300m-timit-phoneme"  # 英語，vocab＝IPA（CMU 39 音素對映）

# g2p_en 的 ARPAbet（2 字母、去 stress 數字）→ 本模型 vocab 的 IPA 音素。
# 模型 vocab（46）：ɑ æ ə aʊ aɪ b ʧ d ð ɾ ɛ ɝ eɪ f g h ɪ i ʤ k l m n ŋ oʊ ɔɪ p ɹ s ʃ t θ ʊ u v w j z
# TIMIT 縮減集：無 ɔ/ʌ/ʒ/ɚ 獨立音，合併到最近者。
ARPA_TO_IPA = {
    "aa": "ɑ", "ae": "æ", "ah": "ə", "ao": "ɑ", "aw": "aʊ", "ay": "aɪ",
    "b": "b", "ch": "ʧ", "d": "d", "dh": "ð", "eh": "ɛ", "er": "ɝ",
    "ey": "eɪ", "f": "f", "g": "g", "hh": "h", "ih": "ɪ", "iy": "i",
    "jh": "ʤ", "k": "k", "l": "l", "m": "m", "n": "n", "ng": "ŋ",
    "ow": "oʊ", "oy": "ɔɪ", "p": "p", "r": "ɹ", "s": "s", "sh": "ʃ",
    "t": "t", "th": "θ", "uh": "ʊ", "uw": "u", "v": "v", "w": "w",
    "y": "j", "z": "z", "zh": "ʃ",  # 無 ʒ → 併 ʃ
}

# CTC 解碼要濾掉的非音素 token
_NON_PHONE = {"|", " ", "[UNK]", "[PAD]", "<s>", "</s>", ""}


def whisper_transcribe(wav_path: str) -> str:
    from faster_whisper import WhisperModel
    m = WhisperModel("base.en", device="cpu", compute_type="int8")
    segs, _ = m.transcribe(wav_path, language="en")
    return " ".join(s.text for s in segs).strip()


def g2p_ref(text: str) -> list[str]:
    """g2p_en → ARPAbet → 映射到模型 IPA 音素集（同源比對）。"""
    from g2p_en import G2p
    g2p = G2p()
    out = []
    for tok in g2p(text):
        a = "".join(c for c in tok.lower() if c.isalpha())  # 去 stress 數字/空白
        if a in ARPA_TO_IPA:
            out.append(ARPA_TO_IPA[a])
    return out


def wav2vec2_phones(wav_path: str) -> list[str]:
    """英語 IPA 音素模型：CTC argmax → 逐 id 解碼成 IPA 音素序列（去重、濾非音素）。"""
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
    ids = torch.argmax(logits, dim=-1)[0].tolist()

    id2tok = {v: k for k, v in proc.tokenizer.get_vocab().items()}
    pad_id = proc.tokenizer.pad_token_id
    # CTC 折疊：連續相同 id 併一個、去 PAD，再 id→token
    phones, prev = [], None
    for i in ids:
        if i != prev and i != pad_id:
            tok = id2tok.get(i, "")
            if tok not in _NON_PHONE:
                phones.append(tok)
        prev = i
    return phones


def align_score(ref: list[str], hyp: list[str]) -> float:
    """以 edit distance 對齊，回傳命中率 0-100（命中 = ref 音素被正確發出）。"""
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
    edits = dp[n][m]
    hits = max(0, n - edits)  # 粗估：ref 長度 - 編輯距離
    return round(100.0 * hits / n, 1)


def main():
    print(f"[1/4] 音檔：{WAV}")
    correct_ref = whisper_transcribe(WAV)
    print(f"[2/4] whisper 轉出台詞（＝正確 reference）：\n      {correct_ref!r}")

    hyp = wav2vec2_phones(WAV)
    print(f"[3/4] wav2vec2-espeak 實際發音音素（前 60）：\n      {' '.join(hyp[:60])}")
    print(f"      共 {len(hyp)} 個音素")

    ref_ok = g2p_ref(correct_ref)
    ref_bad = g2p_ref(WRONG_REF)
    score_ok = align_score(ref_ok, hyp)
    score_bad = align_score(ref_bad, hyp)

    print("[4/4] 鑑別力測試：")
    print(f"      正確 reference  分數 = {score_ok}  （ref {len(ref_ok)} 音素）")
    print(f"      錯誤 reference  分數 = {score_bad}  （ref {len(ref_bad)} 音素，句：{WRONG_REF!r}）")
    gap = round(score_ok - score_bad, 1)
    print(f"\n      差距 = {gap} 分  →  ", end="")
    if gap >= 20:
        print("✅ GO：評分器有鑑別力，此路可行")
    elif gap >= 8:
        print("🟡 弱鑑別：可行但需調校（音標映射/GOP 後驗）")
    else:
        print("🔴 NO-GO：鑑別力不足，需換 espeak 同源音標或真 CTC-GOP")


if __name__ == "__main__":
    main()
