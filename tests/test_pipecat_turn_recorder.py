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
    assert rec["asr_text"] == "我看到一隻狗"
    assert "I see a dog." in rec["reply_text"]
    assert rec["source"] == "pipecat"


@pytest.mark.asyncio
async def test_two_turns_write_two_records():
    written = []
    proc = TurnRecorderProcessor(
        student_text_provider=lambda: "嗨", add_interaction=written.append
    )

    await run_test(proc, frames_to_send=_turn("第一句") + _turn("第二句"),
                   expected_down_frames=None)

    assert [r["reply_text"] for r in written] == ["第一句", "第二句"]


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
    assert len(written) == 1 and written[0]["asr_text"] == ""
