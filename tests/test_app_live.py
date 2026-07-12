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


import json


class _FakeSession:
    """假的 NovaSonicSession：記錄呼叫、events() 吐預設事件序。"""
    last = None

    def __init__(self, *a, **k):
        self.started_with = None
        self.audio_chunks = []
        self.ended = False
        self.closed = False
        _FakeSession.last = self

    async def start(self, system_prompt):
        self.started_with = system_prompt

    async def send_audio(self, pcm16):
        self.audio_chunks.append(pcm16)

    async def end_user_turn(self):
        self.ended = True

    async def events(self):
        from server.nova_sonic import NovaEvent
        yield NovaEvent("transcript", role="USER", text="我想吃蘋果")
        yield NovaEvent("transcript", role="ASSISTANT", text="好棒！apple。")
        yield NovaEvent("audio", audio=b"\x00\x00" * 4)
        yield NovaEvent("turn_end")

    async def close(self):
        self.closed = True


def _wire_fake(monkeypatch):
    monkeypatch.setattr(config, "LIVE_S2S_ENABLED", True)
    monkeypatch.setattr(nova_sonic, "available", lambda: True)
    monkeypatch.setattr(app_mod, "_make_live_session", lambda: _FakeSession())


def test_ws_live_turn_stores_interaction(monkeypatch):
    _wire_fake(monkeypatch)
    monkeypatch.setattr(app_mod.guardrails, "consent_granted", lambda: True)
    seen = {}
    monkeypatch.setattr(app_mod.store, "add_interaction",
                        lambda d: seen.update(d) or 1)
    client = TestClient(app_mod.app)
    with client.websocket_connect("/ws/live") as ws:
        ws.send_bytes(b"\x01\x02" * 8)
        ws.send_text(json.dumps({"type": "user_end"}))
        # 收到 transcript / audio / turn_end
        kinds = []
        for _ in range(4):
            m = ws.receive()
            if "text" in m and m["text"]:
                kinds.append(json.loads(m["text"]).get("type"))
            elif m.get("bytes"):
                kinds.append("audio")
        ws.send_text(json.dumps({"type": "bye"}))
    assert "live_transcript" in kinds
    assert "audio" in kinds
    # transcript 落地：USER→asr_text、ASSISTANT→reply_text
    assert seen.get("asr_text") == "我想吃蘋果"
    assert seen.get("reply_text") == "好棒！apple。"


def test_ws_live_denied_without_consent(monkeypatch):
    _wire_fake(monkeypatch)
    monkeypatch.setattr(app_mod.guardrails, "consent_granted", lambda: False)
    client = TestClient(app_mod.app)
    with client.websocket_connect("/ws/live") as ws:
        m = ws.receive()
        payload = json.loads(m["text"])
        assert payload["type"] == "live_error"
        assert payload["reason"] == "consent_required"


def test_ws_live_denied_when_unavailable(monkeypatch):
    _wire_fake(monkeypatch)
    monkeypatch.setattr(nova_sonic, "available", lambda: False)
    monkeypatch.setattr(app_mod.guardrails, "consent_granted", lambda: True)
    client = TestClient(app_mod.app)
    with client.websocket_connect("/ws/live") as ws:
        payload = json.loads(ws.receive()["text"])
        assert payload["type"] == "live_error"
        assert payload["reason"] == "unavailable"
