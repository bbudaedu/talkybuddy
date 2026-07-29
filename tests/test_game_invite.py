# -*- coding: utf-8 -*-
"""卡關時玩偶主動邀請玩遊戲，孩子用講的答應就開局。

裝置沒有螢幕，孩子不會知道有哪些遊戲可以玩——所以除了「用講的叫出來」
（`test_game_voice_start.py`），玩偶也得會自己開口邀請。

觸發訊號用既有的 `_stuck_streak`（連續兩輪沒命中詞庫，`STUCK_STREAK_THRESHOLD`）。
邀請與回答一定在同一條連線的相鄰兩輪，所以測試都在同一個 ws 區塊裡。

**「沒回應就不糾纏」是刻意的**：孩子已經卡關了，追問是二次挫折。
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from server import app as app_mod, auth, pipeline as pipeline_mod

_SID = "STUDENT-INVITE"
_AUTH = {"Authorization": f"Bearer {auth.issue_token(_SID, 'tutor')}"}

# 中文、且不會命中詞庫的句子——講兩句就會讓 _stuck_streak 達門檻
_STUCK = "我不知道"


@pytest.fixture
def client():
    c = TestClient(app_mod.app)
    yield c
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


def _invite_until(ws) -> dict:
    """一直講卡關句直到玩偶開口邀請；沒邀請就讓測試失敗。"""
    for _ in range(pipeline_mod.STUCK_STREAK_THRESHOLD + 2):
        msg = _say(ws, _STUCK)
        if "要不要" in msg["text"]:
            return msg
    raise AssertionError("連續卡關這麼多輪，玩偶都沒有開口邀請")


def test_the_toy_offers_a_game_after_repeated_stumbles(client):
    """連續卡關 → 玩偶主動問「要不要玩…」，而且講得出遊戲名。"""
    with _ws(client) as ws:
        msg = _invite_until(ws)

    assert "火眼金睛" in msg["text"], f"邀請沒說是哪個遊戲：{msg['text']!r}"


def test_saying_yes_to_the_invite_starts_the_game(client):
    """答應邀請 → 這局要真的開起來。"""
    with _ws(client) as ws:
        _invite_until(ws)
        msg = _say(ws, "好")

    assert _state(client)["game"] == "i_spy", "答應了卻沒開局"
    assert msg["text"]


def test_declining_the_invite_starts_nothing(client):
    """拒絕 → 不開局，但要有回應（不能靜默）。"""
    with _ws(client) as ws:
        _invite_until(ws)
        msg = _say(ws, "不要")

    assert _state(client)["game"] is None
    assert msg["text"]


def test_an_unrelated_answer_drops_the_invite_without_nagging(client):
    """聽不出是不是在回答 → 當作沒回應，這句照常走對話，而且不再追問。"""
    with _ws(client) as ws:
        _invite_until(ws)
        msg = _say(ws, "我看到一隻狗")

        assert _state(client)["game"] is None
        assert "要不要" not in msg["text"], "邀請被拒後還在追問，這是二次挫折"


def test_no_invite_while_a_game_is_running(client):
    """遊戲進行中不邀請——正在玩了還問要不要玩很荒謬。"""
    client.post("/api/game", json={"game": "i_spy", "student": _SID}, headers=_AUTH)

    with _ws(client) as ws:
        for _ in range(pipeline_mod.STUCK_STREAK_THRESHOLD + 2):
            msg = _say(ws, _STUCK)
            assert "要不要玩" not in msg["text"]


def test_the_invite_is_spoken_not_silent(client):
    """邀請必須併進這一輪的回覆文字——孩子只聽得到聲音，沒進 TTS 等於沒發生。"""
    with _ws(client) as ws:
        msg = _invite_until(ws)

    # 邀請是接在原本回覆後面，不是取代它
    assert len(msg["text"]) > len("要不要玩火眼金睛？")
