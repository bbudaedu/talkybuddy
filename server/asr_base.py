"""ASR 後端工廠：依 config.ASR_BACKEND 選擇引擎類別。

共同契約（CONTRACTS.md）：所有引擎皆提供
- available() -> bool
- transcribe(wav_path: str) -> tuple[str, float]
- _ensure_model()  # app.py 預熱會呼叫
"""

from __future__ import annotations


def get_asr_engine_class(backend: str | None = None) -> type:
    """回傳 ASR 引擎類別。

    - backend 未給 → 讀 config.ASR_BACKEND（讀取失敗保底 "sensevoice"）。
    - "whisper" → WhisperASREngine；其餘（含 "sensevoice" 與未知值）→ SenseVoiceASREngine。
    - 依「不自動 fallback」原則：未知值走主力 sensevoice，不猜測退回 whisper。
    """
    if backend is None:
        try:
            from server.config import ASR_BACKEND
            backend = ASR_BACKEND
        except Exception:
            backend = "sensevoice"
    if backend == "whisper":
        from server.asr_whisper import WhisperASREngine
        return WhisperASREngine
    from server.asr_sensevoice import SenseVoiceASREngine
    return SenseVoiceASREngine
