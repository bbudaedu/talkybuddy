# -*- coding: utf-8 -*-
"""/api/wake-config 端點契約測試（不需真模型/瀏覽器）。anyio_backend 由 conftest 提供。"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from server import config
from server.app import app

pytestmark = pytest.mark.anyio


async def _client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver")


async def test_wake_config_shape_and_enabled_true(monkeypatch):
    monkeypatch.setattr(config, "PICOVOICE_ACCESS_KEY", "test-key-123")
    async with await _client() as client:
        resp = await client.get("/api/wake-config")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "enabled", "access_key", "keyword_builtin", "keyword_label",
        "keyword_public_path", "model_public_path", "sensitivity", "sherpa",
    }
    assert body["enabled"] is True
    assert body["access_key"] == "test-key-123"
    assert isinstance(body["sensitivity"], float)


async def test_wake_config_disabled_when_no_key(monkeypatch):
    monkeypatch.setattr(config, "PICOVOICE_ACCESS_KEY", "")
    async with await _client() as client:
        resp = await client.get("/api/wake-config")
    body = resp.json()
    assert body["enabled"] is False
    assert body["access_key"] == ""


async def test_wake_config_has_sherpa_block(monkeypatch):
    monkeypatch.setattr(config, "WAKE_SHERPA_ENABLED", True)
    async with await _client() as client:
        resp = await client.get("/api/wake-config")
    assert resp.status_code == 200
    sherpa = resp.json()["sherpa"]
    assert set(sherpa.keys()) == {
        "enabled", "base_url", "keywords", "keywords_threshold", "keywords_score",
    }
    assert sherpa["enabled"] is True
    assert sherpa["base_url"] == "/static/vendor/sherpa-kws/"
    assert sherpa["keywords"] == "sh uō sh uō x ué b àn @說說學伴"
    assert sherpa["keywords_threshold"] == 0.25
    assert isinstance(sherpa["keywords_score"], float)
