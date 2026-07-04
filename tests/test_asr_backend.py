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


# --- SenseVoice 引擎測試（fake recognizer，不載入真模型）---

class _FakeResult:
    def __init__(self, text): self.text = text


class _FakeStream:
    def __init__(self, text): self.result = _FakeResult(text)
    def accept_waveform(self, sample_rate, samples): pass


class _FakeRecognizer:
    def __init__(self, text): self._text = text
    def create_stream(self): return _FakeStream(self._text)
    def decode_stream(self, stream): pass


def test_factory_returns_sensevoice_class():
    from server.asr_base import get_asr_engine_class
    from server.asr_sensevoice import SenseVoiceASREngine
    assert get_asr_engine_class("sensevoice") is SenseVoiceASREngine


def test_sensevoice_available_false_when_model_missing(monkeypatch, tmp_path):
    from server import config
    from server.asr_sensevoice import SenseVoiceASREngine
    monkeypatch.setattr(config, "SENSEVOICE_DIR", tmp_path / "nope")
    eng = SenseVoiceASREngine()
    assert eng.available() is False


def test_sensevoice_opencc_s2twp():
    from server.asr_sensevoice import SenseVoiceASREngine
    eng = SenseVoiceASREngine()
    cc = eng._ensure_opencc()
    assert cc is not None
    assert cc.convert("我爱学习") == "我愛學習"


def test_sensevoice_transcribe_converts_to_traditional(monkeypatch):
    import numpy as np
    from server import asr_sensevoice as m
    eng = m.SenseVoiceASREngine()
    eng._recognizer = _FakeRecognizer("我爱学习")  # 簡體 stub
    monkeypatch.setattr(
        m, "_read_wav", lambda p: (np.zeros(16000, dtype="float32"), 16000)
    )
    text, conf = eng.transcribe("dummy.wav")
    assert text == "我愛學習"
    assert conf == 1.0


def test_sensevoice_transcribe_empty_returns_zero(monkeypatch):
    import numpy as np
    from server import asr_sensevoice as m
    eng = m.SenseVoiceASREngine()
    eng._recognizer = _FakeRecognizer("")  # 空辨識
    monkeypatch.setattr(
        m, "_read_wav", lambda p: (np.zeros(16000, dtype="float32"), 16000)
    )
    assert eng.transcribe("dummy.wav") == ("", 0.0)


def test_sensevoice_transcribe_returns_zero_when_model_unavailable():
    from server.asr_sensevoice import SenseVoiceASREngine
    eng = SenseVoiceASREngine()
    eng._load_failed = True  # 模擬載入失敗
    assert eng.transcribe("dummy.wav") == ("", 0.0)
