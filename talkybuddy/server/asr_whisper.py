"""ASR 引擎（faster-whisper）—— 保留為 feature flag 可切換的 fallback。

契約：available() -> bool；transcribe(wav_path) -> tuple[str, float]；_ensure_model()。
規格與行為與原 asr.py 完全一致（small / int8 / language=None / beam_size=1 /
confidence=exp(mean avg_logprob) 夾 0-1 / 單例懶載入 / 降級安全）。
"""

from __future__ import annotations

import math
import threading


class WhisperASREngine:
    """faster-whisper 語音辨識引擎（單例懶載入、降級安全）。"""

    def __init__(self) -> None:
        self._model = None
        self._load_failed = False
        self._lock = threading.Lock()

    def available(self) -> bool:
        if self._model is not None:
            return True
        if self._load_failed:
            return False
        try:
            import faster_whisper  # noqa: F401
            return True
        except Exception:
            return False

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        if self._load_failed:
            return None
        with self._lock:
            if self._model is not None:
                return self._model
            if self._load_failed:
                return None
            try:
                from faster_whisper import WhisperModel
                try:
                    from server.config import ASR_MODEL
                except Exception:
                    ASR_MODEL = "small"
                self._model = WhisperModel(
                    ASR_MODEL, device="cpu", compute_type="int8"
                )
            except Exception:
                self._model = None
                self._load_failed = True
        return self._model

    def transcribe(self, wav_path: str) -> tuple[str, float]:
        model = self._ensure_model()
        if model is None:
            return ("", 0.0)
        try:
            segments, _info = model.transcribe(
                wav_path,
                language=None,
                beam_size=1,
            )
            texts: list[str] = []
            logprobs: list[float] = []
            for seg in segments:
                text = (seg.text or "").strip()
                if text:
                    texts.append(text)
                lp = getattr(seg, "avg_logprob", None)
                if lp is not None:
                    logprobs.append(float(lp))

            full_text = "".join(texts).strip() if texts else ""
            if not full_text:
                return ("", 0.0)

            if logprobs:
                mean_lp = sum(logprobs) / len(logprobs)
                confidence = math.exp(mean_lp)
                confidence = max(0.0, min(1.0, confidence))
            else:
                confidence = 0.0
            return (full_text, confidence)
        except Exception:
            return ("", 0.0)
