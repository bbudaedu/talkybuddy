# -*- coding: utf-8 -*-
"""互動紀錄的完整 metadata —— 長期記憶的原料。

現況只記 `student_text` / `ai_response_text` / `scores` / `latency_ms`，
從紀錄裡**看不出這輪發生了什麼**：回覆是雲端還是 edge 生的？孩子有沒有命中
詞庫？是不是在玩遊戲？當日課程是什麼？

而 AgentCore Memory 的 `USER_PREFERENCE`（喜好／個性）與 `EPISODIC`
（跨情節反思 →「什麼方式對這孩子有效」）要學的正是這些。少了它們，記憶層
拿到的只是一串沒有上下文的句子。

隱私：`sync_client.project_for_upload()` 是**白名單**投影（「任何未來新增的
欄位預設就不會出現在輸出裡」），所以本地記得再細也不會外洩。最後一條測試
把這個性質釘住。
"""

from __future__ import annotations

import pytest

from server import store, sync_client
from server.pipeline import VoicePipeline
from tests.test_pipeline import StubASR, StubLLM, StubTTS, _collecting_emit

pytestmark = pytest.mark.anyio


def _latest() -> dict:
    rows = store.list_interactions(limit=1)
    assert rows, "沒有寫進任何互動紀錄"
    return rows[0]


async def test_scaffold_reply_is_recorded_as_scaffold():
    """LLM 不可用 → 回覆來自 scaffold，紀錄要說得出來。"""
    events: list[dict] = []
    emit = await _collecting_emit(events)
    vp = VoicePipeline(StubASR(), StubLLM(available=False), StubTTS())

    await vp.run_turn_text("我喜歡蘋果", emit)

    assert _latest()["reply_source"] == "scaffold"


async def test_edge_llm_reply_is_recorded_as_edge():
    """回覆由 edge LLM 生成 → `reply_source` 要是 edge，不能只看 network_mode。

    `network_mode` 說的是「這輪打算試雲端嗎」，不是「誰真的生出這句話」。
    雲端逾時降級 edge 時兩者會不一致——那正是最該記下來的一輪。
    """
    events: list[dict] = []
    emit = await _collecting_emit(events)
    vp = VoicePipeline(StubASR(), StubLLM(reply="很棒！跟我說一遍：I like apples."), StubTTS())

    await vp.run_turn_text("我喜歡蘋果", emit)

    assert _latest()["reply_source"] == "edge"


async def test_whether_the_child_hit_the_vocabulary_is_recorded():
    """`matched`：孩子這輪有沒有跟上。這是「什麼方式對這孩子有效」的核心訊號。"""
    events: list[dict] = []
    emit = await _collecting_emit(events)
    vp = VoicePipeline(StubASR(), StubLLM(available=False), StubTTS())

    await vp.run_turn_text("我喜歡蘋果", emit)

    assert isinstance(_latest()["matched"], bool)


async def test_stuck_streak_is_recorded():
    """連續卡關數：孩子是偶爾答不出來，還是已經卡了好幾輪。"""
    events: list[dict] = []
    emit = await _collecting_emit(events)
    vp = VoicePipeline(StubASR(), StubLLM(available=False), StubTTS())

    await vp.run_turn_text("我喜歡蘋果", emit)

    assert isinstance(_latest()["stuck_streak"], int)


async def test_game_turns_record_which_game():
    """遊戲回合要記得是哪個遊戲——否則事後分不出這句話是在玩還是在聊。"""
    events: list[dict] = []
    emit = await _collecting_emit(events)
    vp = VoicePipeline(StubASR(), StubLLM(available=False), StubTTS())
    vp.start_game("i_spy", topic="animal")
    try:
        await vp.run_turn_text("I see a dog.", emit)
    finally:
        vp.end_game()

    row = _latest()
    assert row["reply_source"] == "game"
    assert row["game"]["kind"] == "i_spy"


async def test_free_chat_turns_have_no_game():
    """沒在玩遊戲時 `game` 為 None，不要塞空 dict 讓下游得判斷兩種空。"""
    events: list[dict] = []
    emit = await _collecting_emit(events)
    vp = VoicePipeline(StubASR(), StubLLM(available=False), StubTTS())

    await vp.run_turn_text("我喜歡蘋果", emit)

    assert _latest()["game"] is None


async def test_turns_of_one_conversation_share_a_session_id():
    """同一條連線的多輪要能被歸成一場對話（EPISODIC 的「情節」邊界）。"""
    events: list[dict] = []
    emit = await _collecting_emit(events)
    vp = VoicePipeline(StubASR(), StubLLM(available=False), StubTTS())

    await vp.run_turn_text("我喜歡蘋果", emit)
    first = _latest()["session_id"]
    await vp.run_turn_text("我喜歡香蕉", emit)
    second = _latest()["session_id"]

    assert first and first == second


async def test_a_different_conversation_gets_a_different_session_id():
    events: list[dict] = []
    emit = await _collecting_emit(events)

    vp1 = VoicePipeline(StubASR(), StubLLM(available=False), StubTTS())
    await vp1.run_turn_text("我喜歡蘋果", emit)
    first = _latest()["session_id"]

    vp2 = VoicePipeline(StubASR(), StubLLM(available=False), StubTTS())
    await vp2.run_turn_text("我喜歡蘋果", emit)
    second = _latest()["session_id"]

    assert first != second


def test_the_new_metadata_never_leaves_the_device():
    """隱私回歸保護：上傳投影是白名單，新欄位一律不得出現在上傳 payload。"""
    row = {
        "device_id": "D", "student_id": "S", "ts": "2026-07-29T10:00:00+08:00",
        "student_text": "我喜歡蘋果", "ai_response_text": "很棒！",
        "scores": {"fluency": 60}, "latency_ms": {"llm": 1},
        # 本次新增的欄位
        "reply_source": "edge", "matched": True, "stuck_streak": 2,
        "game": {"kind": "i_spy"}, "lesson": {"topic": "animal"},
        "session_id": "abc123",
    }

    out = sync_client.project_for_upload(row)

    for leaked in ("reply_source", "matched", "stuck_streak", "game", "lesson", "session_id"):
        assert leaked not in out, f"{leaked} 不該上雲"
