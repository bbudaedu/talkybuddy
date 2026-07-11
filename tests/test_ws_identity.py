# -*- coding: utf-8 -*-
from __future__ import annotations

from starlette.testclient import TestClient

from server import app as app_mod, auth, store


def test_ws_writes_interaction_under_token_student():
    tok = auth.issue_token("STU-WS-1", "student")
    client = TestClient(app_mod.app)
    with client.websocket_connect(f"/ws/talk?token={tok}") as ws:
        ws.send_json({"type": "text_input", "source": "manual", "text": "hello"})
        # 收到 reply 事件即可（不驗 TTS 內容）
        for _ in range(6):
            msg = ws.receive_json()
            if msg.get("type") == "reply":
                break
    rows = store.list_interactions(student_id="STU-WS-1")
    assert len(rows) >= 1


def test_ws_without_token_rejected():
    client = TestClient(app_mod.app)
    try:
        with client.websocket_connect("/ws/talk") as ws:
            ws.receive_json()
        rejected = False
    except Exception:
        rejected = True
    assert rejected
