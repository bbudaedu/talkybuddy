# -*- coding: utf-8 -*-
"""TurnRecorderProcessor — 沒有它，玩偶永遠是第一次見到每個孩子。

`profile.build_profile` → `child_brief` 這條記憶鏈的起點是 `interactions`，
而 pipecat 這條路從來沒寫過任何一筆。這裡釘住三件事：一輪寫一筆、內容對、
以及**寫入失敗絕不擋對話**（孩子正在等回答，資料庫是為了下一次）。
"""
from __future__ import annotations

import pytest
from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.tests.utils import run_test

from edge.runtime.pipecat_adapters.turn_recorder import TurnRecorderProcessor


def _turn(text: str):
    return [
        LLMFullResponseStartFrame(),
        LLMTextFrame(text),
        LLMFullResponseEndFrame(),
    ]


@pytest.mark.asyncio
async def test_writes_one_record_per_turn():
    written = []
    proc = TurnRecorderProcessor(
        student_text_provider=lambda: "我看到一隻狗",
        add_interaction=written.append,
    )

    await run_test(proc, frames_to_send=_turn("你好棒！跟我說一遍：I see a dog."),
                   expected_down_frames=None)

    assert len(written) == 1
    rec = written[0]
    assert rec["student_text"] == "我看到一隻狗"
    assert "I see a dog." in rec["ai_response_text"]
    assert rec["source"] == "pipecat"


@pytest.mark.asyncio
async def test_two_turns_write_two_records():
    written = []
    proc = TurnRecorderProcessor(
        student_text_provider=lambda: "嗨", add_interaction=written.append
    )

    await run_test(proc, frames_to_send=_turn("第一句") + _turn("第二句"),
                   expected_down_frames=None)

    assert [r["ai_response_text"] for r in written] == ["第一句", "第二句"]


@pytest.mark.asyncio
async def test_empty_turn_is_not_recorded():
    """雲端整輪失敗又沒降級時不該留下空紀錄，那會污染 profile。"""
    written = []
    proc = TurnRecorderProcessor(
        student_text_provider=lambda: "", add_interaction=written.append
    )

    await run_test(proc, frames_to_send=[LLMFullResponseStartFrame(),
                                         LLMFullResponseEndFrame()],
                   expected_down_frames=None)

    assert written == []


@pytest.mark.asyncio
async def test_store_failure_does_not_break_the_pipeline():
    """寫入是為了下一次，孩子正在等這一次的回答——絕不可以擋。"""
    def _boom(_rec):
        raise RuntimeError("資料庫壞了")

    proc = TurnRecorderProcessor(
        student_text_provider=lambda: "嗨", add_interaction=_boom
    )

    down, _ = await run_test(proc, frames_to_send=_turn("你好棒！"),
                             expected_down_frames=None)

    assert any(isinstance(f, LLMTextFrame) for f in down), "回覆仍要往下走"
    assert proc.written == 0


@pytest.mark.asyncio
async def test_provider_failure_does_not_break_the_pipeline():
    written = []

    def _boom():
        raise RuntimeError("provider 壞了")

    proc = TurnRecorderProcessor(
        student_text_provider=_boom, add_interaction=written.append
    )

    down, _ = await run_test(proc, frames_to_send=_turn("你好棒！"),
                             expected_down_frames=None)

    assert any(isinstance(f, LLMTextFrame) for f in down)
    # 逐字稿取不到，但玩偶說的話仍值得留著
    assert len(written) == 1 and written[0]["student_text"] == ""


@pytest.mark.asyncio
async def test_confidence_field_name_is_asr_confidence():
    """欄位名寫錯不會報錯，只會讓記憶永遠是空的。

    `profile.build_profile`（server/profile.py:115）、`srs`、`diagnose` 讀的
    都是 `asr_confidence`，示範資料（store.py:830）也是。寫成 `asr_conf` 的話
    信心值一律當成 0.0，沒有任何字會被算進 learning/mastered_vocab——玩偶
    記得「聊過幾次」，卻永遠說不出「上次我們練過哪個字」。

    2026-07-31 實測 12 筆互動後 learning_vocab 仍是空的，就是這個原因。
    """
    written = []
    proc = TurnRecorderProcessor(
        student_text_provider=lambda: "I see a dog", add_interaction=written.append
    )

    await run_test(proc, frames_to_send=_turn("你好棒！"), expected_down_frames=None)

    rec = written[0]
    assert "asr_confidence" in rec, "欄位名必須是 asr_confidence"
    assert rec["asr_confidence"] == 1.0
    # 另外兩個名字同樣會靜默失效，一起釘住
    assert "student_text" in rec, "profile.build_profile 讀的是 student_text"
    assert "ai_response_text" in rec, "profile.build_profile 讀的是 ai_response_text"
