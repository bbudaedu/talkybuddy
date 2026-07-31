# -*- coding: utf-8 -*-
"""SafetyGateProcessor 與 ReadalongGuardProcessor 測試。

兩者的檢查函式都可注入，所以不需要真的 guardrails 模組就能測邏輯。
"""

from __future__ import annotations

import pytest
from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.tests.utils import run_test

from edge.runtime.pipecat_adapters.readalong_guard import ReadalongGuardProcessor
from edge.runtime.pipecat_adapters.safety_gate import SafetyGateProcessor

TARGET = "I want an apple."


async def _run(proc, frames: list[Frame]) -> list[Frame]:
    down, _ = await run_test(proc, frames_to_send=frames, expected_down_frames=None)
    return down


def _texts(frames: list[Frame]) -> list[str]:
    return [f.text for f in frames if isinstance(f, LLMTextFrame)]


def _tokens(*chunks: str) -> list[Frame]:
    """把文字切成 token 片段送入，模擬 LLM 串流。"""
    return [LLMFullResponseStartFrame(), *[LLMTextFrame(c) for c in chunks], LLMFullResponseEndFrame()]


# --------------------------------------------------------------------------
# SafetyGate
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_safe_text_passes_through_as_sentences():
    """安全內容要放行；下游拿到的是整句（TTS 本來就聚合成句，不影響 first-audio）。"""
    proc = SafetyGateProcessor(safety_check=lambda t: True)
    down = await _run(proc, _tokens("你真", "棒！", "跟我說一遍：", "I want an apple."))

    joined = "".join(_texts(down))
    assert "你真棒！" in joined
    assert "I want an apple." in joined


@pytest.mark.asyncio
async def test_unsafe_sentence_is_replaced_by_fallback():
    """不安全的句子不能送到 TTS，改送 fallback。"""
    proc = SafetyGateProcessor(
        safety_check=lambda t: "壞話" not in t, fallback_text="我們先練今天的句子好不好？"
    )
    down = await _run(proc, _tokens("這裡有壞話。", "後面還有別的。"))

    joined = "".join(_texts(down))
    assert "壞話" not in joined
    assert "我們先練今天的句子好不好？" in joined


@pytest.mark.asyncio
async def test_rest_of_response_is_dropped_after_a_rejection():
    """同一則回覆出現一句不安全內容，後面的也不該信任。"""
    proc = SafetyGateProcessor(
        safety_check=lambda t: "壞話" not in t, fallback_text="安全回覆。"
    )
    down = await _run(proc, _tokens("這裡有壞話。", "這句是安全的。"))

    joined = "".join(_texts(down))
    assert "這句是安全的。" not in joined


@pytest.mark.asyncio
async def test_tail_without_punctuation_is_still_checked():
    """LLM 沒以標點收尾時，殘句也要過檢查才放行。"""
    seen = []

    def check(t):
        seen.append(t)
        return True

    proc = SafetyGateProcessor(safety_check=check)
    down = await _run(proc, _tokens("沒有標點的結尾"))

    assert "沒有標點的結尾" in "".join(_texts(down))
    assert any("沒有標點的結尾" in s for s in seen), "殘句必須經過安全檢查"


@pytest.mark.asyncio
async def test_gate_resets_between_responses():
    """上一則被擋不能影響下一則（同一場對話的連續兩輪）。"""
    proc = SafetyGateProcessor(safety_check=lambda t: "壞話" not in t, fallback_text="F")
    down = await _run(proc, _tokens("有壞話。") + _tokens("這次是好話。"))

    assert "這次是好話。" in "".join(_texts(down)), "第二則不該被第一則的攔截波及"


@pytest.mark.asyncio
async def test_unavailable_safety_module_blocks_conservatively():
    """安全模組不可用時保守不放行——沿用 EdgeLLM 的既有策略。"""
    proc = SafetyGateProcessor(safety_check=lambda t: False, fallback_text="安全回覆。")
    down = await _run(proc, _tokens("任何內容。"))

    joined = "".join(_texts(down))
    assert joined.strip() == "安全回覆。"


# --------------------------------------------------------------------------
# ReadalongGuard
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_compliant_response_is_untouched():
    """已含合規帶讀 → 一個字都不加。"""
    proc = ReadalongGuardProcessor(target=TARGET, ensure_fn=lambda t, tg: t)
    down = await _run(proc, _tokens("你真棒！跟我說一遍：I want an apple."))

    assert "".join(_texts(down)) == "你真棒！跟我說一遍：I want an apple."


@pytest.mark.asyncio
async def test_missing_readalong_is_appended():
    """漏了帶讀句 → 在結尾補一句。"""
    proc = ReadalongGuardProcessor(
        target=TARGET,
        ensure_fn=lambda t, tg: f"{t} 跟我說一遍：{tg}",
    )
    down = await _run(proc, _tokens("你真棒！"))

    joined = "".join(_texts(down))
    assert joined.startswith("你真棒！")
    assert f"跟我說一遍：{TARGET}" in joined


@pytest.mark.asyncio
async def test_noncompliant_readalong_gets_a_correct_one_appended():
    """帶讀了錯的句子時，串流下改不掉已唸出的內容，只能追加正確的。

    這是串流換取 first-audio 提前的代價，孩子會聽到兩句帶讀。
    """
    # 模擬 ensure_readalong 的「刪掉錯的、補上對的」行為（結果不是原文的前綴）
    proc = ReadalongGuardProcessor(
        target=TARGET, ensure_fn=lambda t, tg: f"你真棒！跟我說一遍：{tg}"
    )
    down = await _run(proc, _tokens("你真棒！跟我說一遍：我想要蘋果。"))

    joined = "".join(_texts(down))
    assert f"跟我說一遍：{TARGET}" in joined, "必須追加一句正確的帶讀"


@pytest.mark.asyncio
async def test_no_target_means_no_intervention():
    """沒有目標句（例如自由聊天輪）就不該插手。"""
    called = []
    proc = ReadalongGuardProcessor(
        target=None, ensure_fn=lambda t, tg: called.append(1) or t
    )
    down = await _run(proc, _tokens("今天天氣很好。"))

    assert called == []
    assert "".join(_texts(down)) == "今天天氣很好。"


@pytest.mark.asyncio
async def test_target_provider_is_called_per_response():
    """教材每輪會換，target 要每則重新取。"""
    calls = []

    def provider():
        calls.append(1)
        return f"Sentence {len(calls)}."

    proc = ReadalongGuardProcessor(
        target_provider=provider, ensure_fn=lambda t, tg: f"{t} 跟我說一遍：{tg}"
    )
    await _run(proc, _tokens("第一則。"))
    down = await _run(proc, _tokens("第二則。"))

    assert len(calls) == 2
    assert "Sentence 2." in "".join(_texts(down))


@pytest.mark.asyncio
async def test_guard_failure_does_not_break_conversation():
    """護欄本身爆炸時維持原文，不得中斷對話。"""

    def boom(t, tg):
        raise RuntimeError("guardrails 壞了")

    proc = ReadalongGuardProcessor(target=TARGET, ensure_fn=boom)
    down = await _run(proc, _tokens("你真棒！"))

    assert "你真棒！" in "".join(_texts(down))
