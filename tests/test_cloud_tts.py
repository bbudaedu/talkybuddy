# -*- coding: utf-8 -*-
"""CloudTTS（ElevenLabs）單元測試：available 邏輯、WAV 封裝、降級回 None、請求內容。

以 monkeypatch 假造 urllib.request.urlopen，全程不連網。
"""

from __future__ import annotations

import io
import json
import wave

import pytest

from server import config
from server.cloud_tts import CloudTTS


class _FakeResp:
    """假 urlopen 回應：支援 context manager、read() 與 headers。"""

    def __init__(self, data: bytes, content_type: str = "audio/mpeg"):
        self._data = data
        self.headers = {"Content-Type": content_type}

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def keyed(monkeypatch):
    """設好金鑰與 voice_id 的環境。"""
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", "test-voice")
    monkeypatch.setattr(config, "ELEVENLABS_MODEL", "eleven_flash_v2_5")
    monkeypatch.setattr(config, "CLOUD_TTS_TIMEOUT_S", 6.0)


# --- available() ---------------------------------------------------------

def test_available_true_with_key_and_voice(keyed):
    assert CloudTTS().available() is True


def test_available_false_without_key(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "")
    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", "test-voice")
    assert CloudTTS().available() is False


def test_available_false_without_voice(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", "")
    assert CloudTTS().available() is False


# --- synth() 成功路徑 ----------------------------------------------------

def test_synth_wraps_raw_pcm_into_valid_wav(keyed, monkeypatch):
    raw_pcm = b"\x01\x00" * 100  # 100 個 int16 樣本
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _FakeResp(raw_pcm),
    )
    wav = CloudTTS().synth([("zh", "你好"), ("en", "hello")])
    assert wav is not None
    with wave.open(io.BytesIO(wav), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 22050
        assert wf.readframes(wf.getnframes()) == raw_pcm


def test_synth_builds_correct_request(keyed, monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeResp(b"\x00\x00")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    CloudTTS().synth([("zh", "你好"), ("en", "hi there")])

    assert "test-voice" in captured["url"]
    assert "output_format=pcm_22050" in captured["url"]
    assert captured["method"] == "POST"
    assert captured["headers"]["xi-api-key"] == "test-key"
    assert captured["headers"]["content-type"] == "application/json"
    assert captured["body"]["model_id"] == "eleven_flash_v2_5"
    # 中英兩段併成單一字串（原生中英混讀）
    assert captured["body"]["text"] == "你好 hi there"
    assert captured["timeout"] == 6.0


def test_synth_passthrough_when_body_already_riff(keyed, monkeypatch):
    riff = b"RIFF" + b"\x00" * 40  # 假裝已是 WAV 容器
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _FakeResp(riff),
    )
    assert CloudTTS().synth([("zh", "你好")]) == riff


# --- synth() 降級回 None -------------------------------------------------

def test_synth_none_on_empty_segments(keyed):
    assert CloudTTS().synth([]) is None


def test_synth_none_on_blank_text(keyed):
    assert CloudTTS().synth([("zh", "   "), ("en", "")]) is None


def test_synth_none_on_http_error(keyed, monkeypatch):
    def boom(req, timeout=None):
        raise RuntimeError("network down / non-2xx")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert CloudTTS().synth([("zh", "你好")]) is None


def test_synth_none_on_empty_body(keyed, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _FakeResp(b""),
    )
    assert CloudTTS().synth([("zh", "你好")]) is None


def test_synth_none_without_key(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "")
    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", "")
    assert CloudTTS().synth([("zh", "你好")]) is None


def test_synth_none_on_malformed_segment(keyed):
    # 1-element tuple → unpacking would raise; must degrade to None, never raise
    assert CloudTTS().synth([("zh",)]) is None


def test_synth_none_on_html_200(keyed, monkeypatch):
    # HTTP 200 但 body 是反向代理/錯誤頁注入的 HTML → 視為假成功，須降級回 None
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _FakeResp(
            b"<html>error</html>", content_type="text/html; charset=utf-8"
        ),
    )
    assert CloudTTS().synth([("zh", "你好")]) is None


def test_synth_none_on_odd_length_pcm(keyed, monkeypatch):
    # 非 RIFF 且長度為奇數 → 不可能是合法 16-bit LE mono PCM，須降級回 None
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _FakeResp(b"\x01\x02\x03", content_type="audio/pcm"),
    )
    assert CloudTTS().synth([("zh", "你好")]) is None
