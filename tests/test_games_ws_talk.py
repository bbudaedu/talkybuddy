# -*- coding: utf-8 -*-
"""開局之後，孩子在 ``/ws/talk`` 講的話必須真的走遊戲判定。

這是現場**唯一**的真實路徑：老師用 ``/api/game`` 開局，孩子對著裝置講話。

既有的遊戲測試分別守住了 pipeline 層（``vp.play_turn``）與 HTTP 層
（``/api/game``），**中間這一段沒有測試**——而 2026-07-29 的裝置實測證明
它正是斷的：開了 i_spy，孩子講 "I see a dog."，回覆走自由對話
（「跟我說一遍：What animal do you like?」），這局的 ``turns`` 停在 0。

根因是 ``/api/game`` 動的是全域 ``pipeline`` 單例，而 ``/ws/talk`` 每條連線
新建自己的 ``VoicePipeline``，只承接 ``network_mode``、沒有承接遊戲狀態。
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from server import app as app_mod, auth

_SID = "STUDENT-WS-GAME"
_AUTH = {"Authorization": f"Bearer {auth.issue_token(_SID, 'tutor')}"}


@pytest.fixture
def client():
    c = TestClient(app_mod.app)
    yield c
    # 遊戲狀態掛在行程上，不清會漏給下一條測試
    c.post("/api/game", json={"game": "none"}, headers=_AUTH)


def _say(ws, text: str) -> dict:
    """送一句話，回傳該輪的 reply 事件。"""
    ws.send_json({"type": "text_input", "text": text})
    for _ in range(8):
        msg = ws.receive_json()
        if msg.get("type") == "reply":
            return msg
    raise AssertionError(f"沒有收到 reply：{text!r}")


def _start(client, **body) -> dict:
    return client.post("/api/game", json=body, headers=_AUTH).json()


def test_game_turn_advances_over_ws_talk(client):
    """開局 → 孩子講遊戲句 → 這局要真的前進。"""
    _start(client, game="i_spy", topic="animal", student=_SID)

    tok = auth.issue_token(_SID, "student")
    with client.websocket_connect(f"/ws/talk?token={tok}") as ws:
        msg = _say(ws, "I see a dog.")

    state = client.get("/api/game", headers=_AUTH).json()
    assert state["turns"] == 1, "這局沒有前進：/ws/talk 看不到 /api/game 開的局"
    assert "狗" in state["found"]
    assert "狗" in msg["text"], f"回覆不是遊戲判定：{msg['text']!r}"


def test_game_turn_over_ws_talk_skips_the_llm(client):
    """遊戲判定是純函式，該輪不該花任何 LLM 時間——也是「不碰雲端」的可觀測證據。"""
    _start(client, game="i_spy", topic="animal", student=_SID)

    tok = auth.issue_token(_SID, "student")
    with client.websocket_connect(f"/ws/talk?token={tok}") as ws:
        msg = _say(ws, "I see a dog.")

    assert msg["latency_ms"]["llm"] == 0, "遊戲回合走了 LLM，判定就不再是確定性的"


def test_ws_talk_returns_to_free_chat_after_the_game_ends(client):
    """結束這局之後要回到自由對話——狀態掛在行程上，黏住的話下一個孩子會中獎。"""
    _start(client, game="i_spy", topic="animal", student=_SID)
    client.post("/api/game", json={"game": "none"}, headers=_AUTH)

    tok = auth.issue_token(_SID, "student")
    with client.websocket_connect(f"/ws/talk?token={tok}") as ws:
        msg = _say(ws, "I see a dog.")

    assert client.get("/api/game", headers=_AUTH).json()["game"] is None
    assert msg["text"], "自由對話仍要有回覆"


def test_a_second_connection_sees_the_same_game(client):
    """孩子的瀏覽器重新整理（換一條連線）不該把進行中的遊戲弄丟。"""
    _start(client, game="i_spy", topic="animal", student=_SID)
    tok = auth.issue_token(_SID, "student")

    with client.websocket_connect(f"/ws/talk?token={tok}") as ws:
        _say(ws, "I see a dog.")
    with client.websocket_connect(f"/ws/talk?token={tok}") as ws:
        _say(ws, "I see a cat.")

    state = client.get("/api/game", headers=_AUTH).json()
    assert state["turns"] == 2, "換一條連線後這局就斷了"
    assert set(state["found"]) == {"狗", "貓"}
