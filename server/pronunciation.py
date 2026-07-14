"""pronunciation.py — 本地真聲學發音評測（read-aloud、reference 已知）。

定位：B 軸背景診斷層（不進即時路徑）。把 diagnose 的假 pronunciation（asr_confidence
映射）換成真發音命中率。選型見 spike/pron_assess/SPIKE-RESULT.md（路 A）。

作法（路 A，純 Python、零系統二進位）：
  1. wav2vec2 英語音素模型（vocab＝IPA）CTC argmax → 逐 id 折疊解碼成實際發音 IPA 序列。
  2. g2p_en 把 reference 英文句 → ARPAbet → 一張 39 條靜態表映射到同一套 IPA。
  3. edit-distance 對齊 → 命中率 0-100。

契約：
  available() -> bool                                  # 模型依賴是否就緒（lazy 探測、不拋）
  score(wav_path, reference_text) -> float | None      # 0-100；不可用/壞檔/空 ref → None

降級安全：import 期不載重依賴；模型單例懶載入；任何例外一律吞掉回 None，
呼叫端（diagnose/pipeline）維持既有行為。隱私：本地評分、只回分數。
"""

from __future__ import annotations

import threading

# 英語音素模型：vocab 為 CMU 39 音素對映的 IPA（非 ARPAbet）
MODEL_ID = "vitouphy/wav2vec2-xls-r-300m-timit-phoneme"

# g2p_en 的 ARPAbet（2 字母、去 stress 數字）→ 本模型 vocab 的 IPA 音素。
# TIMIT 縮減集無 ɔ/ʌ/ʒ 獨立音，合併到最近者。
ARPA_TO_IPA = {
    "aa": "ɑ", "ae": "æ", "ah": "ə", "ao": "ɑ", "aw": "aʊ", "ay": "aɪ",
    "b": "b", "ch": "ʧ", "d": "d", "dh": "ð", "eh": "ɛ", "er": "ɝ",
    "ey": "eɪ", "f": "f", "g": "g", "hh": "h", "ih": "ɪ", "iy": "i",
    "jh": "ʤ", "k": "k", "l": "l", "m": "m", "n": "n", "ng": "ŋ",
    "ow": "oʊ", "oy": "ɔɪ", "p": "p", "r": "ɹ", "s": "s", "sh": "ʃ",
    "t": "t", "th": "θ", "uh": "ʊ", "uw": "u", "v": "v", "w": "w",
    "y": "j", "z": "z", "zh": "ʃ",
}

# CTC 解碼要濾掉的非音素 token
_NON_PHONE = {"|", " ", "", "[UNK]", "[PAD]", "<s>", "</s>"}

# 模型單例（懶載入）；_state: None=未試、"failed"=載入失敗過、其餘=(proc, model, id2tok, pad_id)
_model_lock = threading.Lock()
_model_state: object = None


# ---------------------------------------------------------------------------
# 純函式（可獨立單元測試、免模型）
# ---------------------------------------------------------------------------

def _g2p_to_ipa(text: str) -> list[str]:
    """g2p_en → ARPAbet（去 stress 數字）→ 映射到模型 IPA 音素集。"""
    from g2p_en import G2p

    g2p = G2p()
    out = []
    for tok in g2p(text):
        a = "".join(c for c in tok.lower() if c.isalpha())
        if a in ARPA_TO_IPA:
            out.append(ARPA_TO_IPA[a])
    return out


def _ctc_collapse(ids: list[int], id2tok: dict, pad_id: int | None) -> list[str]:
    """CTC 折疊：連續相同 id 併一個、去 PAD，再 id→token、濾非音素。"""
    phones: list[str] = []
    prev = None
    for i in ids:
        if i != prev and i != pad_id:
            tok = id2tok.get(i, "")
            if tok not in _NON_PHONE:
                phones.append(tok)
        prev = i
    return phones


def _align_score(ref: list[str], hyp: list[str]) -> float:
    """edit-distance 對齊，回命中率 0-100（命中＝ref 音素被正確發出）。"""
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


# ---------------------------------------------------------------------------
# 模型載入 / 推論（懶、單例、降級安全）
# ---------------------------------------------------------------------------

def _load_model():
    """載入並快取模型單例；回 (proc, model, id2tok, pad_id) 或 None（失敗）。"""
    global _model_state
    if _model_state == "failed":
        return None
    if _model_state is not None:
        return _model_state
    with _model_lock:
        if _model_state == "failed":
            return None
        if _model_state is not None:
            return _model_state
        try:
            from transformers import Wav2Vec2Processor, Wav2Vec2ForCTC

            proc = Wav2Vec2Processor.from_pretrained(MODEL_ID)
            model = Wav2Vec2ForCTC.from_pretrained(MODEL_ID)
            model.eval()
            id2tok = {v: k for k, v in proc.tokenizer.get_vocab().items()}
            pad_id = proc.tokenizer.pad_token_id
            _model_state = (proc, model, id2tok, pad_id)
            return _model_state
        except Exception:
            _model_state = "failed"
            return None


def available() -> bool:
    """發音評測依賴是否就緒（torch/transformers/g2p_en/soundfile 可 import）。"""
    try:
        import torch  # noqa: F401
        import soundfile  # noqa: F401
        from transformers import Wav2Vec2ForCTC  # noqa: F401
        from g2p_en import G2p  # noqa: F401
    except Exception:
        return False
    return True


def _wav_to_phones(wav_path: str) -> list[str] | None:
    """讀 wav → 16kHz mono → CTC 解碼成 IPA 音素序列；失敗回 None。"""
    loaded = _load_model()
    if loaded is None:
        return None
    proc, model, id2tok, pad_id = loaded
    try:
        import torch
        import soundfile as sf

        audio, sr = sf.read(wav_path)
        if getattr(audio, "ndim", 1) > 1:
            audio = audio.mean(axis=1)
        if sr != 16000:
            import librosa
            audio = librosa.resample(audio.astype("float32"), orig_sr=sr, target_sr=16000)
        inputs = proc(audio, sampling_rate=16000, return_tensors="pt")
        with torch.no_grad():
            logits = model(inputs.input_values).logits
        ids = torch.argmax(logits, dim=-1)[0].tolist()
        return _ctc_collapse(ids, id2tok, pad_id)
    except Exception:
        return None


def score(wav_path: str, reference_text: str) -> float | None:
    """評 wav 對 reference_text 的發音命中率 0-100；不可用/壞檔/空 ref → None。"""
    if not wav_path or not (reference_text or "").strip():
        return None
    if not available():
        return None
    try:
        ref = _g2p_to_ipa(reference_text)
        if not ref:
            return None
        hyp = _wav_to_phones(wav_path)
        if hyp is None:
            return None
        return _align_score(ref, hyp)
    except Exception:
        return None
