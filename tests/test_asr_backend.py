# -*- coding: utf-8 -*-
"""ASR 後端工廠與引擎的單元測試（不載入真模型）。"""

from __future__ import annotations


def test_factory_returns_whisper_class():
    from server.asr_base import get_asr_engine_class
    from server.asr_whisper import WhisperASREngine
    assert get_asr_engine_class("whisper") is WhisperASREngine


def test_asr_shim_exposes_engine_class():
    # shim 需可實例化，且具契約方法
    from server.asr import ASREngine
    eng = ASREngine()
    assert hasattr(eng, "available")
    assert hasattr(eng, "transcribe")
    assert hasattr(eng, "_ensure_model")
