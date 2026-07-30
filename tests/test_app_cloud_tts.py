# -*- coding: utf-8 -*-
"""app 接線：pipeline 帶入 cloud_tts 引擎、/api/status 曝露 cloud_tts 布林。"""

from __future__ import annotations

from starlette.testclient import TestClient

from server import app as app_mod
from server import config


def test_pipeline_has_cloud_tts_engine():
    assert app_mod.pipeline.cloud_tts is app_mod.cloud_tts_engine


def test_status_does_not_claim_cloud_tts_works_just_because_it_is_configured(
    monkeypatch,
):
    """設定齊全但還沒實際合成過 → cloud_tts 必須是 false。

    這條斷言在 2026-07-30 被翻轉過（原本是 `is True`）。理由：金鑰設好之後
    `CLOUD_TTS_TIMEOUT_S`（預設 1.5s）擋掉了每一次 `eleven_v3` 合成（暖機後
    約 3s），實際聽到的一直是邊緣語音，而自檢一路顯示綠燈。

    「設定齊全」與「跑得動」是兩件事，status 要回報後者——證據在
    `CloudTTS.verified()`，見 tests/test_cloud_tts_honesty.py。
    """
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", "v")
    client = TestClient(app_mod.app)

    body = client.get("/api/status").json()

    assert body["cloud_tts"] is False
    assert "尚未驗證" in body["cloud_tts_detail"]


def test_status_exposes_cloud_tts_false_without_key(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "")
    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", "")
    client = TestClient(app_mod.app)

    body = client.get("/api/status").json()

    assert body["cloud_tts"] is False
