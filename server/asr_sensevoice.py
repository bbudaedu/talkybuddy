"""ASR 引擎（sherpa-onnx + SenseVoice-Small，OpenCC 簡轉繁）。

契約（CONTRACTS.md）：available() / transcribe(wav_path) -> (text, confidence) / _ensure_model()。

規格：
- sherpa_onnx.OfflineRecognizer.from_sense_voice(model, tokens, use_itn=True) 單例懶載入。
- 輸入 16kHz mono wav（pipeline 已用 ffmpeg 轉好）；以 soundfile 讀 float32。
- 信心分數：辨識文字為空 → ("", 0.0)；非空 → (繁體text, 1.0)。SenseVoice 非自回歸，
  無 avg_logprob，改以「空結果」判斷雜音兜底。
- 簡轉繁：OpenCC s2twp，缺失/失敗 → 降級為原文（不 throw）。
- import 期不可炸；任何 transcribe 失敗回 ("", 0.0)。
"""

from __future__ import annotations

import threading


def _read_wav(path: str):
    """讀 wav 為 (samples float32 ndarray, sample_rate)。抽成 module 函式以利測試 monkeypatch。"""
    import soundfile as sf
    samples, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    return samples, sample_rate


class SenseVoiceASREngine:
    """sherpa-onnx SenseVoice 語音辨識引擎（單例懶載入、降級安全）。"""

    def __init__(self) -> None:
        self._recognizer = None
        self._load_failed = False
        self._opencc = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def available(self) -> bool:
        """sherpa_onnx 可 import 且 SenseVoice 模型檔存在 → True；載入失敗過 → False。"""
        if self._recognizer is not None:
            return True
        if self._load_failed:
            return False
        try:
            import sherpa_onnx  # noqa: F401
        except Exception:
            return False
        try:
            from server.config import SENSEVOICE_DIR
            return (SENSEVOICE_DIR / "model.int8.onnx").exists()
        except Exception:
            return False

    # ------------------------------------------------------------------
    def _ensure_model(self):
        """懶載入 OfflineRecognizer 單例；失敗回 None 並記錄失敗狀態。"""
        if self._recognizer is not None:
            return self._recognizer
        if self._load_failed:
            return None
        with self._lock:
            if self._recognizer is not None:
                return self._recognizer
            if self._load_failed:
                return None
            try:
                import sherpa_onnx
                from server.config import SENSEVOICE_DIR
                model = str(SENSEVOICE_DIR / "model.int8.onnx")
                tokens = str(SENSEVOICE_DIR / "tokens.txt")
                self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                    model=model,
                    tokens=tokens,
                    use_itn=True,
                    num_threads=2,
                )
            except Exception:
                self._recognizer = None
                self._load_failed = True
        return self._recognizer

    # ------------------------------------------------------------------
    def _ensure_opencc(self):
        """懶載入 OpenCC 轉換器；缺失/失敗回 None（不 throw）。"""
        if self._opencc is not None:
            return self._opencc
        try:
            import opencc
            from server.config import OPENCC_CONFIG
            self._opencc = opencc.OpenCC(OPENCC_CONFIG)
        except Exception:
            self._opencc = None
        return self._opencc

    # ------------------------------------------------------------------
    def transcribe(self, wav_path: str) -> tuple[str, float]:
        """辨識 wav，回傳 (繁體text, confidence)。失敗回 ("", 0.0)，不 throw。"""
        recognizer = self._ensure_model()
        if recognizer is None:
            return ("", 0.0)
        try:
            samples, sample_rate = _read_wav(wav_path)
            if getattr(samples, "ndim", 1) > 1:
                samples = samples[:, 0]  # 多聲道取第一聲道
            # decode_stream 呼叫進 sherpa_onnx native 層；多連線併發呼叫同一個
            # recognizer 單例時用鎖序列化，避免併發 native 呼叫（見 server/llm.py 同款註解）。
            with self._lock:
                stream = recognizer.create_stream()
                stream.accept_waveform(sample_rate, samples)
                recognizer.decode_stream(stream)
                text = (stream.result.text or "").strip()
            if not text:
                return ("", 0.0)
            cc = self._ensure_opencc()
            if cc is not None:
                try:
                    text = cc.convert(text).strip()
                except Exception:
                    pass
            return (text, 1.0) if text else ("", 0.0)
        except Exception:
            return ("", 0.0)
