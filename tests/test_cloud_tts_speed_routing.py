# -*- coding: utf-8 -*-
"""放慢語速要交給 API 還是自己做，取決於模型——不能兩個都做。

放慢語速本身是需求：國小雙語帶讀，比原聲慢一點小朋友才跟得上（CLOUD_TTS_SPEED
預設 0.90）。問題是達成方式因模型而異，**2026-07-30 用真 API 實測**：

    eleven_turbo_v2_5   speed=1.0 → 4.74s 語音 ／ speed=0.7 → 5.94s   ← 有效
    eleven_v3           speed=1.0 → 4.32s 語音 ／ speed=0.7 → 4.16s   ← 被忽略

原本預設是 `eleven_v3`，所以程式碼**無條件**在合成後跑 WSOLA 時間伸縮
（server/timestretch.py）來補。2026-07-30 改用 `eleven_turbo_v2_5`（延遲從
約 3s 降到 0.37s）之後，若那段 WSOLA 還留著，就會變成 API 放慢一次、WSOLA
再放慢一次——**慢到不能聽**，而且沒有任何錯誤訊息。

所以分流條件寫在 `_model_honours_speed()`，兩邊都要有測試釘住。
"""

from __future__ import annotations

import json

import pytest

from server import config
from server.cloud_tts import CloudTTS, _model_honours_speed


class _FakeResp:
    def __init__(self, data: bytes):
        self._data, self.headers = data, {"Content-Type": "audio/pcm"}

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def captured(monkeypatch):
    """攔下送出的 request，讓測試可以檢查 body。"""
    box = {}

    def _fake_urlopen(req, **kwargs):
        box["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp(b"\x00\x01" * 8000)

    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", "test-voice")
    monkeypatch.setattr(config, "CLOUD_TTS_TIMEOUT_S", 5.0)
    monkeypatch.setattr(config, "CLOUD_TTS_SPEED", 0.90)
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    return box


SEGS = [("zh", "好，我們一起唸一次喔。")]


# ---------------------------------------------------------------------------
# 哪些模型吃 speed
# ---------------------------------------------------------------------------

def test_v3_is_known_to_ignore_speed():
    assert _model_honours_speed("eleven_v3") is False


@pytest.mark.parametrize("model", ["eleven_turbo_v2_5", "eleven_flash_v2_5",
                                   "eleven_multilingual_v2"])
def test_v2_5_family_honours_speed(model):
    assert _model_honours_speed(model) is True


def test_unknown_models_are_assumed_to_honour_speed():
    """未知模型走 API 放慢這條路。

    猜錯的後果不對稱：假設「不吃」而其實吃 → API 與 WSOLA 兩次放慢、慢到不能聽；
    假設「吃」而其實不吃 → 語速維持原速，只是沒放慢，仍然可用。選傷害小的那邊。
    """
    assert _model_honours_speed("eleven_some_future_model") is True


# ---------------------------------------------------------------------------
# 吃 speed 的模型：交給 API，不得再做 WSOLA
# ---------------------------------------------------------------------------

def test_speed_is_sent_to_the_api_for_turbo(captured, monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_MODEL", "eleven_turbo_v2_5")
    CloudTTS().synth(SEGS)
    assert captured["body"]["voice_settings"]["speed"] == 0.90


def test_wsola_is_not_applied_on_top_of_api_speed(captured, monkeypatch):
    """雙重放慢是這次改動最容易犯、又最不會報錯的錯。"""
    monkeypatch.setattr(config, "ELEVENLABS_MODEL", "eleven_turbo_v2_5")

    def _boom(*a, **k):
        raise AssertionError("API 已經放慢過了，不該再跑 WSOLA")

    monkeypatch.setattr("server.cloud_tts.stretch_pcm16", _boom)
    assert CloudTTS().synth(SEGS) is not None


# ---------------------------------------------------------------------------
# 不吃 speed 的模型：維持原本的 WSOLA 補償
# ---------------------------------------------------------------------------

def test_speed_is_not_sent_for_v3(captured, monkeypatch):
    """送了也沒用，但送出去會讓人誤以為已經處理了。"""
    monkeypatch.setattr(config, "ELEVENLABS_MODEL", "eleven_v3")
    CloudTTS().synth(SEGS)
    assert "speed" not in captured["body"]["voice_settings"]


def test_wsola_still_runs_for_v3(captured, monkeypatch):
    """v3 的放慢完全靠 WSOLA——這條路不能因為改預設模型就壞掉。"""
    calls = []
    monkeypatch.setattr(config, "ELEVENLABS_MODEL", "eleven_v3")
    monkeypatch.setattr("server.cloud_tts.stretch_pcm16",
                        lambda raw, speed, rate: calls.append((speed, rate)) or raw)
    CloudTTS().synth(SEGS)
    assert calls == [(0.90, 22050)]


def test_speed_of_one_means_no_slowdown_anywhere(captured, monkeypatch):
    """1.0＝不處理。這時候連 speed 參數都不必送，少一個變因。"""
    monkeypatch.setattr(config, "ELEVENLABS_MODEL", "eleven_turbo_v2_5")
    monkeypatch.setattr(config, "CLOUD_TTS_SPEED", 1.0)
    CloudTTS().synth(SEGS)
    assert "speed" not in captured["body"]["voice_settings"]
