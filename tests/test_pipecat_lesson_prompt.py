# -*- coding: utf-8 -*-
"""LessonPromptInjector 測試。"""

from __future__ import annotations

import pytest
from pipecat.frames.frames import TextFrame, TranscriptionFrame
from pipecat.tests.utils import run_test
from pipecat.utils.time import time_now_iso8601

from edge.runtime.pipecat_adapters.lesson_prompt import LessonPromptInjector
from server.llm import build_user_prompt


def _frame(text: str) -> TranscriptionFrame:
    return TranscriptionFrame(text, "child", time_now_iso8601())


async def _run(proc, frames):
    down, _ = await run_test(proc, frames_to_send=frames, expected_down_frames=None)
    return down


@pytest.mark.asyncio
async def test_injects_target_sentence_into_prompt():
    """核心缺口：LLM 必須收到目標英文句，否則帶讀句會掉成中文。"""
    proc = LessonPromptInjector(target="I want an apple.")
    down = await _run(proc, [_frame("我想要蘋果")])

    out = [f for f in down if isinstance(f, TranscriptionFrame)][0]
    assert "I want an apple." in out.text
    assert "學生剛剛說：「我想要蘋果」" in out.text


@pytest.mark.asyncio
async def test_uses_shared_template_not_a_second_copy():
    """必須與 EdgeLLM 共用同一份模板，否則兩條路徑會漂移。"""
    proc = LessonPromptInjector(target="I want an apple.", directive="本輪策略：放慢")
    down = await _run(proc, [_frame("我想要蘋果")])

    out = [f for f in down if isinstance(f, TranscriptionFrame)][0]
    assert out.text == build_user_prompt("我想要蘋果", "I want an apple.", "本輪策略：放慢")


@pytest.mark.asyncio
async def test_original_transcript_is_preserved():
    """孩子實際說的話不能被蓋掉——那是要存進對話紀錄的。"""
    proc = LessonPromptInjector(target="I want an apple.")
    down = await _run(proc, [_frame("我想要蘋果")])

    out = [f for f in down if isinstance(f, TranscriptionFrame)][0]
    assert out.result == "我想要蘋果"
    assert out.text != "我想要蘋果", "text 應已被換成完整 prompt"


@pytest.mark.asyncio
async def test_existing_result_is_not_clobbered():
    """若上游已經放了 result（服務原始輸出），不要覆蓋它。"""
    proc = LessonPromptInjector(target="I want an apple.")
    f = _frame("我想要蘋果")
    f.result = {"raw": "來自 ASR 的原始結果"}
    down = await _run(proc, [f])

    out = [x for x in down if isinstance(x, TranscriptionFrame)][0]
    assert out.result == {"raw": "來自 ASR 的原始結果"}


@pytest.mark.asyncio
async def test_lesson_provider_is_called_per_utterance():
    """教材每輪可能不同（SRS 會換題），provider 要每次被呼叫。"""
    calls = []

    def provider():
        calls.append(1)
        return (f"Sentence {len(calls)}.", None)

    proc = LessonPromptInjector(lesson_provider=provider)
    down = await _run(proc, [_frame("第一句"), _frame("第二句")])

    texts = [f.text for f in down if isinstance(f, TranscriptionFrame)]
    assert len(calls) == 2
    assert "Sentence 1." in texts[0]
    assert "Sentence 2." in texts[1]


@pytest.mark.asyncio
async def test_provider_failure_falls_back_without_breaking_conversation():
    """教材取不到就少了目標句，但對話不能中斷。"""

    def boom():
        raise RuntimeError("lesson 取得失敗")

    proc = LessonPromptInjector(lesson_provider=boom, target="Fallback sentence.")
    down = await _run(proc, [_frame("我想要蘋果")])

    out = [f for f in down if isinstance(f, TranscriptionFrame)][0]
    assert "Fallback sentence." in out.text


@pytest.mark.asyncio
async def test_blank_transcription_is_left_alone():
    """空白逐字稿不該被包成 prompt 送進 LLM。"""
    proc = LessonPromptInjector(target="I want an apple.")
    down = await _run(proc, [_frame("   ")])

    out = [f for f in down if isinstance(f, TranscriptionFrame)][0]
    assert out.text == "   "


@pytest.mark.asyncio
async def test_other_frames_pass_through():
    """只碰 TranscriptionFrame。"""
    proc = LessonPromptInjector(target="I want an apple.")
    down = await _run(proc, [TextFrame("這是別的 frame")])

    texts = [
        f.text
        for f in down
        if isinstance(f, TextFrame) and not isinstance(f, TranscriptionFrame)
    ]
    assert texts == ["這是別的 frame"]
