# -*- coding: utf-8 -*-
"""app 接線：pipeline 帶入 cloud_tts 引擎、/api/status 曝露 cloud_tts 布林。"""

from __future__ import annotations

from starlette.testclient import TestClient

from server import app as app_mod
from server import config


def test_pipeline_has_cloud_tts_engine():
    assert app_mod.pipeline.cloud_tts is app_mod.cloud_tts_engine


def test_status_exposes_cloud_tts_true_when_configured(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", "v")
    client = TestClient(app_mod.app)

    body = client.get("/api/status").json()

    assert body["cloud_tts"] is True


def test_status_exposes_cloud_tts_false_without_key(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "")
    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", "")
    client = TestClient(app_mod.app)

    body = client.get("/api/status").json()

    assert body["cloud_tts"] is False
