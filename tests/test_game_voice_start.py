# -*- coding: utf-8 -*-
"""用講的開局：裝置沒有螢幕，這是現場唯一叫得出遊戲的方式。

測試一律走 ``/ws/talk``——`edge/runtime/local_client.py`（無螢幕原生迴路）與
瀏覽器吃的是同一條，所以這條路徑綠了就代表兩邊都通。

2026-07-29 的教訓：89 條遊戲測試全過但遊戲根本不會觸發，因為沒有一條測
「開局 → 在 /ws/talk 講話」。不要再犯。
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from server import app as app_mod, auth

_SID = "STUDENT-VOICE-GAME"
_AUTH = {"Authorization": f"Bearer {auth.issue_token(_SID, 'tutor')}"}


@pytest.fixture
def client():
    c = TestClient(app_mod.app)
    yield c
    # 遊戲狀態掛在行程上，不清會漏給下一條測試
    c.post("/api/game", json={"game": "none"}, headers=_AUTH)


def _say(ws, text: str) -> dict:
    ws.send_json({"type": "text_input", "text": text})
    for _ in range(8):
        msg = ws.receive_json()
        if msg.get("type") == "reply":
            return msg
    raise AssertionError(f"沒有收到 reply：{text!r}")


def _state(client) -> dict:
    return client.get("/api/game", headers=_AUTH).json()


def _ws(client):
    return client.websocket_connect(f"/ws/talk?token={auth.issue_token(_SID, 'student')}")


def test_saying_the_game_name_starts_it(client):
    """「我要玩火眼金睛」→ 這局要真的開起來，而且回覆是遊戲開場白。"""
    with _ws(client) as ws:
        msg = _say(ws, "我要玩火眼金睛")

    assert _state(client)["game"] == "i_spy", "用講的沒開起來"
    assert msg["text"], "開局要有開場白，孩子看不到螢幕、只聽得到聲音"


def test_starting_by_voice_costs_no_llm_time(client):
    """開局是純規則——該輪不該花任何 LLM 時間（斷網與連網一模一樣的證據）。"""
    with _ws(client) as ws:
        msg = _say(ws, "我要玩火眼金睛")

    assert msg["latency_ms"]["llm"] == 0


def test_the_next_utterance_is_judged_by_the_game(client):
    """用講的開局之後，下一句要走遊戲判定而不是自由對話。"""
    with _ws(client) as ws:
        _say(ws, "我要玩火眼金睛")
        msg = _say(ws, "I see a dog.")

    st = _state(client)
    assert st["turns"] == 1, "開局了但下一句沒走遊戲判定"
    assert "狗" in st["found"]
    assert "狗" in msg["text"]


def test_saying_stop_ends_the_game(client):
    """「不玩了」→ 這局結束，回到自由對話。"""
    with _ws(client) as ws:
        _say(ws, "我要玩火眼金睛")
        msg = _say(ws, "不玩了")

    assert _state(client)["game"] is None, "喊停了但這局還黏著"
    assert msg["text"], "結束也要有回覆，不能靜默"


def test_ordinary_speech_does_not_start_a_game(client):
    """誤觸防護：一般聊天不得把孩子丟進遊戲。"""
    with _ws(client) as ws:
        _say(ws, "我想吃蘋果")

    assert _state(client)["game"] is None


def test_mentioning_a_game_name_without_intent_does_not_start_it(client):
    """「點餐時間到了」是在聊餐廳，不是要玩點餐遊戲。"""
    with _ws(client) as ws:
        _say(ws, "點餐時間到了")

    assert _state(client)["game"] is None
