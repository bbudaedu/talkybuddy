# -*- coding: utf-8 -*-
"""test_games_wiring.py — 遊戲接進語音迴圈與 API。

驗的是「接線」：遊戲進行中，孩子講的話要由遊戲判定、回覆要是遊戲的回覆，
而且**斷網時行為完全一樣**。

一個刻意的設計決定：**遊戲進行中不呼叫雲端 LLM。**
判定必須是確定性的（同一句話同一個結果），而且斷網橋段要跟連網時一模一樣。
雲端 LLM 會讓兩者不同——那正是現場最不能發生的事。雲端的價值放在
遊戲之外的自由對話，不是遊戲判定。
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from server import auth, games, store
from server.app import app
from server.pipeline import VoicePipeline

pytestmark = pytest.mark.anyio

_SID = "STUDENT-AMING-004"
_AUTH = {"Authorization": f"Bearer {auth.issue_token(_SID, 'tutor')}"}


class _StubTTS:
    def available(self) -> bool:
        return False

    def synth(self, *a, **k):
        return None


def _pipeline(mode: str = "edge") -> VoicePipeline:
    vp = VoicePipeline(asr=None, llm=None, tts=_StubTTS())
    vp.network_mode = mode
    return vp


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# pipeline：遊戲進行中由遊戲判定
# ---------------------------------------------------------------------------

async def test_pipeline_has_no_game_by_default():
    assert _pipeline().game is None


async def test_starting_a_game_sets_state_and_returns_the_opening_line():
    vp = _pipeline()
    line = vp.start_game("i_spy", topic="animal")
    assert vp.game is not None
    assert vp.game.game == "i_spy"
    assert "I see" in line.en


async def test_unknown_game_kind_is_rejected():
    vp = _pipeline()
    with pytest.raises(ValueError):
        vp.start_game("不存在的遊戲")
    assert vp.game is None


async def test_end_game_clears_the_state():
    vp = _pipeline()
    vp.start_game("i_spy", topic="animal")
    vp.end_game()
    assert vp.game is None


async def test_game_turn_judges_and_advances(tmp_db):
    vp = _pipeline()
    vp.start_game("i_spy", topic="animal")
    turn = vp.play_turn("I see a dog.")
    assert turn.correct
    assert vp.game.found == ("狗",)
    assert "狗" in turn.reply_zh


async def test_play_turn_without_a_game_returns_none():
    assert _pipeline().play_turn("I see a dog.") is None


async def test_finished_game_is_cleared_automatically(tmp_db):
    """一局結束就把狀態清掉，否則下一句話會撞到「這關已完成」。"""
    vp = _pipeline()
    vp.start_game("i_spy", topic="animal", target_count=1)
    turn = vp.play_turn("I see a dog.")
    assert turn.done
    assert vp.game is None, "結束的局沒有被清掉"


@pytest.mark.parametrize("mode", ["edge", "cloud"])
async def test_game_judgement_is_identical_online_and_offline(mode, tmp_db):
    """**斷網與連網的判定必須一模一樣。**

    遊戲判定若走雲端，斷網橋段的行為就會變——那正是現場最不能發生的事。
    """
    vp = _pipeline(mode)
    vp.start_game("i_spy", topic="animal", target_count=3)
    a = vp.play_turn("I see a dog.")
    b = vp.play_turn("I see an apple.")
    assert (a.correct, a.word) == (True, "狗")
    assert (b.correct, b.word) == (False, "蘋果")


async def test_game_turn_never_calls_the_cloud(tmp_db, monkeypatch):
    """遊戲進行中一次都不碰雲端——這條是上面那條的機制保證。"""
    from server import cloud_llm

    def _boom(*a, **kw):
        raise AssertionError("遊戲判定不該呼叫雲端 LLM")

    monkeypatch.setattr(cloud_llm.CloudLLM, "generate", _boom)
    vp = _pipeline("cloud")
    vp.start_game("restaurant")
    turn = vp.play_turn("I want a hamburger.")
    assert turn.correct


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

async def test_start_game_over_http(tmp_db):
    async with await _client() as c:
        r = await c.post("/api/game", json={"game": "i_spy", "topic": "animal"},
                         headers=_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["game"] == "i_spy"
    assert body["prompt_zh"].strip()
    assert "I see" in body["prompt_en"]
    assert body["hints"]


async def test_get_current_game(tmp_db):
    async with await _client() as c:
        await c.post("/api/game", json={"game": "restaurant"}, headers=_AUTH)
        r = await c.get("/api/game", headers=_AUTH)
    body = r.json()
    assert body["game"] == "restaurant"
    assert body["done"] is False


async def test_stop_game_over_http(tmp_db):
    async with await _client() as c:
        await c.post("/api/game", json={"game": "i_spy"}, headers=_AUTH)
        r = await c.post("/api/game", json={"game": "none"}, headers=_AUTH)
        assert r.json()["game"] is None
        assert (await c.get("/api/game", headers=_AUTH)).json()["game"] is None


async def test_unknown_game_returns_422(tmp_db):
    async with await _client() as c:
        r = await c.post("/api/game", json={"game": "不存在"}, headers=_AUTH)
    assert r.status_code == 422


async def test_game_endpoints_require_auth(tmp_db):
    """遊戲狀態會帶出這個孩子到期的複習詞——那是學習弱項，與診斷同級。"""
    async with await _client() as c:
        assert (await c.post("/api/game", json={"game": "i_spy"})).status_code == 401
        assert (await c.get("/api/game")).status_code == 401


async def test_playable_games_are_discoverable(tmp_db):
    """前端要拿這份清單畫按鈕，不能在前端硬編一份會漂掉的。"""
    async with await _client() as c:
        r = await c.get("/api/games", headers=_AUTH)
    body = r.json()
    kinds = {g["kind"] for g in body["games"]}
    assert kinds == {"i_spy", "guess_who", "restaurant"}
    for g in body["games"]:
        assert g["zh"] and g["en_pattern"] and g["function"]
