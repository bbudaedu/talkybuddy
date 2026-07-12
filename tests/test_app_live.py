# -*- coding: utf-8 -*-
"""/api/status live_s2s 欄 + /ws/live 端點行為測試（注入 fake NovaSonicSession）。"""
from __future__ import annotations

from starlette.testclient import TestClient

from server import app as app_mod
from server import config, nova_sonic


def test_status_has_live_s2s_true(monkeypatch):
    monkeypatch.setattr(config, "LIVE_S2S_ENABLED", True)
    monkeypatch.setattr(nova_sonic, "available", lambda: True)
    client = TestClient(app_mod.app)
    body = client.get("/api/status").json()
    assert body["live_s2s"] is True


def test_status_live_s2s_false_when_flag_off(monkeypatch):
    monkeypatch.setattr(config, "LIVE_S2S_ENABLED", False)
    monkeypatch.setattr(nova_sonic, "available", lambda: True)
    client = TestClient(app_mod.app)
    assert client.get("/api/status").json()["live_s2s"] is False


def test_status_live_s2s_false_when_unavailable(monkeypatch):
    monkeypatch.setattr(config, "LIVE_S2S_ENABLED", True)
    monkeypatch.setattr(nova_sonic, "available", lambda: False)
    client = TestClient(app_mod.app)
    assert client.get("/api/status").json()["live_s2s"] is False
