"""ASR 引擎（faster-whisper）。

契約（CONTRACTS.md）：
- class ASREngine
  - available() -> bool
  - transcribe(wav_path: str) -> tuple[str, float]  # (text, confidence 0-1)

規格：
- faster-whisper "small"、compute_type="int8"、language=None（自動偵測中英夾雜）、beam_size=1
- confidence = exp(平均 avg_logprob)，夾在 0-1
- 模型單例懶載入：第一次 transcribe 才載；載入失敗後 available() 回 False、
  transcribe 回 ("", 0.0)，絕不 throw
- import 期不可炸：faster_whisper 走 lazy import + try/except
"""

from __future__ import annotations

import math
import threading


class ASREngine:
    """faster-whisper 語音辨識引擎（單例懶載入、降級安全）。"""

    def __init__(self) -> None:
        self._model = None            # 懶載入的 WhisperModel 實例
        self._load_failed = False     # 曾嘗試載入但失敗
        self._lock = threading.Lock() # 保護單例載入

    # ------------------------------------------------------------------
    def available(self) -> bool:
        """回傳引擎是否可用。

        - 已載入成功 → True
        - 曾載入失敗 → False
        - 尚未載入 → 只檢查 faster_whisper 是否可 import（不觸發模型下載/載入）
        """
        if self._model is not None:
            return True
        if self._load_failed:
            return False
        try:
            import faster_whisper  # noqa: F401  # lazy import，僅探測可用性
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    def _ensure_model(self):
        """懶載入 WhisperModel 單例；失敗回 None 並記錄失敗狀態。"""
        if self._model is not None:
            return self._model
        if self._load_failed:
            return None
        with self._lock:
            # 雙重檢查：等鎖期間可能已被其他執行緒載好
            if self._model is not None:
                return self._model
            if self._load_failed:
                return None
            try:
                from faster_whisper import WhisperModel  # lazy import

                try:
                    from server.config import ASR_MODEL  # 契約定義的模型名
                except Exception:
                    ASR_MODEL = "small"  # config 尚未就緒時的保底值

                # 邊緣裝置情境：明確走 CPU（device="auto" 在缺 CUDA 函式庫
                # 的機器上會於 transcribe 期炸 libcublas，故不採用）
                self._model = WhisperModel(
                    ASR_MODEL, device="cpu", compute_type="int8"
                )
            except Exception:
                self._model = None
                self._load_failed = True
        return self._model

    # ------------------------------------------------------------------
    def transcribe(self, wav_path: str) -> tuple[str, float]:
        """辨識 wav 檔，回傳 (text, confidence)。

        - language=None 自動偵測（支援中英夾雜）、beam_size=1
        - confidence = exp(各段 avg_logprob 的平均)，夾在 0-1
        - 任何失敗（模型未載入、檔案問題等）回 ("", 0.0)，不 throw
        """
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
            for seg in segments:  # generator：此處才真正執行辨識
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
