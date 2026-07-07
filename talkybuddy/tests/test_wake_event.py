# -*- coding: utf-8 -*-
"""WS /ws/talk 的 wake 事件：記 log 且不干擾後續對話。"""
from __future__ import annotations

from starlette.testclient import TestClient

from server import app as app_module
from server.app import app


def test_ws_wake_event_logged_and_non_disruptive(monkeypatch, caplog):
    monkeypatch.setattr(app_module.llm_engine, "available", lambda: False)
    monkeypatch.setattr(app_module.tts_engine, "available", lambda: False)
    monkeypatch.setattr(app_module.asr_engine, "available", lambda: False)

    with caplog.at_level("INFO", logger="talkybuddy.wake"):
        with TestClient(app) as client:
            with client.websocket_connect("/ws/talk") as ws:
                ws.send_json({"type": "wake", "source": "wakeword"})
                # wake 不回訊息；接著送文字仍能正常對話
                ws.send_json({"type": "text_input", "text": "嗨"})
                got_reply = False
                for _ in range(6):
                    data = ws.receive_json()
                    if data["type"] == "reply":
                        got_reply = True
                    if data["type"] in ("tts_audio", "tts_unavailable"):
                        break

    assert got_reply
    assert any("source=wakeword" in r.getMessage() for r in caplog.records)
