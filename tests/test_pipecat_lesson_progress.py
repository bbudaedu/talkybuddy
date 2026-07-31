# -*- coding: utf-8 -*-
"""LessonProgress — 「孩子會了就換下一句」的狀態機。

2026-07-31 模擬對話實測：交給 prompt 判斷時，模型要到第 7 輪才換句子，而且
孩子明講兩次它才動；中間還出現「我們馬上就來練習貓咪」然後照樣帶讀 dog 的
自相矛盾。決賽鏡頭 1 只有 60 秒約 3～4 輪——第 7 輪才換等於台上永遠不換。

所以把計數搬進程式碼。這個檔案釘住的是：**什麼叫唸對了**（ASR 會插字）、
**什麼時候前進**、以及**斷了要重數**。
"""
from __future__ import annotations

import pytest

from edge.runtime.pipecat_adapters.lesson_progress import (
    LessonProgress,
    says_target,
)

SENTS = ["I see a dog.", "I see a cat.", "I see a bird."]


# --- says_target：容忍 ASR 的髒，不容忍真的沒唸對 -------------------------


def test_exact_match():
    assert says_target("I see a dog.", "I see a dog.") is True


def test_case_and_punctuation_are_ignored():
    assert says_target("i see a dog", "I see a dog.") is True


def test_tolerates_asr_inserting_a_word():
    """真機案例：`I want an apple.` 被聽成 `I want to an apple.`。

    字串相等會判成沒唸對，孩子明明說對了卻被留在同一句——比不換更糟。
    """
    assert says_target("I want to an apple.", "I want an apple.") is True


def test_tolerates_surrounding_chatter():
    """孩子常常唸完接著講別的。"""
    assert says_target("I see a dog 老師我累了", "I see a dog.") is True


def test_missing_word_is_not_a_match():
    assert says_target("I see dog", "I see a dog.") is False


def test_wrong_order_is_not_a_match():
    assert says_target("a dog I see", "I see a dog.") is False


def test_different_sentence_is_not_a_match():
    assert says_target("I see a cat.", "I see a dog.") is False


def test_empty_inputs_are_safe():
    assert says_target("", "I see a dog.") is False
    assert says_target("I see a dog.", "") is False


# --- 前進邏輯 -------------------------------------------------------------


def test_starts_on_the_first_sentence():
    p = LessonProgress(SENTS)
    assert p.current == "I see a dog."
    assert p.upcoming == ["I see a cat.", "I see a bird."]


def test_advances_after_one_correct_by_default():
    """預設 1 次就換——決賽鏡頭只有 3～4 輪，門檻 2 就換不到第二句。"""
    p = LessonProgress(SENTS)
    assert p.observe("I see a dog") is True
    assert p.current == "I see a cat."
    assert p.advances == 1


def test_wrong_answer_does_not_advance():
    p = LessonProgress(SENTS)
    assert p.observe("我不想念") is False
    assert p.current == "I see a dog."


def test_threshold_two_requires_two_in_a_row():
    p = LessonProgress(SENTS, advance_after=2)
    assert p.observe("I see a dog") is False
    assert p.current == "I see a dog."
    assert p.observe("I see a dog") is True
    assert p.current == "I see a cat."


def test_streak_resets_when_the_child_misses():
    """連續才算會。斷掉就重數——與 FailoverPolicy 的連續計數同理。"""
    p = LessonProgress(SENTS, advance_after=2)
    p.observe("I see a dog")
    p.observe("我想吃餅乾")       # 斷了
    assert p.observe("I see a dog") is False, "斷掉之後應該要重新數"
    assert p.observe("I see a dog") is True


def test_walks_through_every_sentence():
    p = LessonProgress(SENTS)
    p.observe("I see a dog")
    p.observe("I see a cat")
    assert p.current == "I see a bird."
    assert p.upcoming == []


def test_last_sentence_stays_current_and_marks_finished():
    """練完最後一句不可以變成 None——玩偶還是要有東西可以帶讀。"""
    p = LessonProgress(SENTS)
    for s in ("I see a dog", "I see a cat", "I see a bird"):
        p.observe(s)
    assert p.finished is True
    assert p.current == "I see a bird.", "練完了仍要留最後一句給玩偶用"


def test_empty_material_is_safe():
    p = LessonProgress([])
    assert p.current is None
    assert p.observe("I see a dog") is False
    assert p.finished is False


def test_blank_entries_are_dropped():
    p = LessonProgress(["I see a dog.", "  ", "", "I see a cat."])
    assert p.upcoming == ["I see a cat."]


def test_advance_after_must_be_at_least_one():
    with pytest.raises(ValueError):
        LessonProgress(SENTS, advance_after=0)


# --- pipeline 接點 ---------------------------------------------------------


@pytest.mark.asyncio
async def test_processor_advances_on_a_correct_transcript():
    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.tests.utils import run_test
    from pipecat.utils.time import time_now_iso8601

    from edge.runtime.pipecat_adapters.lesson_progress import LessonProgressProcessor

    progress = LessonProgress(SENTS)
    proc = LessonProgressProcessor(progress)

    await run_test(
        proc,
        frames_to_send=[TranscriptionFrame("I see a dog", "child", time_now_iso8601())],
        expected_down_frames=None,
    )

    assert progress.current == "I see a cat."


@pytest.mark.asyncio
async def test_processor_does_not_modify_the_frame():
    """只讀不改——孩子的逐字稿要原封不動往下走。"""
    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.tests.utils import run_test
    from pipecat.utils.time import time_now_iso8601

    from edge.runtime.pipecat_adapters.lesson_progress import LessonProgressProcessor

    proc = LessonProgressProcessor(LessonProgress(SENTS))
    down, _ = await run_test(
        proc,
        frames_to_send=[TranscriptionFrame("I see a dog", "child", time_now_iso8601())],
        expected_down_frames=None,
    )

    texts = [f.text for f in down if isinstance(f, TranscriptionFrame)]
    assert texts == ["I see a dog"]
