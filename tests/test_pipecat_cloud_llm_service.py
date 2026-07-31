# -*- coding: utf-8 -*-
"""CloudLLMService — 把既有的 CloudLLM 接進 pipecat pipeline。

這裡釘住的是三件在真機上會痛、但單元測試很容易漏掉的事：

1. **雲端這一輪失敗，孩子仍然要聽到回覆。** 不是下一輪切過去，是**這一輪**
   就降級。掉一輪對孩子來說就是玩偶沒反應。
2. **降級之後要升得回來。** 靠 FailoverPolicy 的重試窗。
3. **prompt 不可以被重組。** 上游 LessonPromptInjector 已經組好了。

全程不觸網：CloudLLM 與 fallback 都以假物件注入。
"""
from __future__ import annotations

import pytest
from pipecat.frames.frames import (
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TextFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.tests.utils import run_test

from edge.runtime.pipecat_adapters.cloud_llm_service import CloudLLMService
from edge.runtime.pipecat_adapters.failover import FailoverPolicy, Route

TARGET = "I want an apple."
PROMPT = "學生剛剛說：「我想要蘋果」\n目標英文句：I want an apple.\n請照規則回覆："


class _FakeCloud:
    """假 CloudLLM：照 replies 依序回answers，用完就一直回最後一個。"""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls: list[tuple[str, str | None]] = []

    def generate_from_prompt(self, user_prompt: str, *, target: str | None):
        self.calls.append((user_prompt, target))
        if not self._replies:
            return None
        return self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]


def _ctx() -> LLMContextFrame:
    ctx = LLMContext(messages=[{"role": "system", "content": "你是玩偶"}])
    ctx.add_message({"role": "user", "content": PROMPT})
    return LLMContextFrame(ctx)


async def _run(svc, n: int = 1):
    down, _ = await run_test(
        svc, frames_to_send=[_ctx() for _ in range(n)], expected_down_frames=None
    )
    return down


def _texts(down) -> list[str]:
    return [f.text for f in down if isinstance(f, LLMTextFrame)]


@pytest.mark.asyncio
async def test_cloud_reply_is_pushed_downstream():
    cloud = _FakeCloud(["很好！跟我說一遍：I want an apple."])
    svc = CloudLLMService(cloud=cloud, target_provider=lambda: TARGET)

    down = await _run(svc)

    assert _texts(down) == ["很好！跟我說一遍：I want an apple."]
    assert any(isinstance(f, LLMFullResponseStartFrame) for f in down)
    assert any(isinstance(f, LLMFullResponseEndFrame) for f in down)


@pytest.mark.asyncio
async def test_prompt_is_passed_through_untouched():
    """上游已經組好 prompt，這一層不可以再組一次。"""
    cloud = _FakeCloud(["好棒！跟我說一遍：I want an apple."])
    svc = CloudLLMService(cloud=cloud, target_provider=lambda: TARGET)

    await _run(svc)

    assert cloud.calls == [(PROMPT, TARGET)]


@pytest.mark.asyncio
async def test_cloud_failure_falls_back_within_the_same_turn():
    """這一輪雲端掛了，孩子仍然要聽到（比較笨的）回覆，不能沉默。"""
    cloud = _FakeCloud([None])
    seen: list[str] = []

    def _fallback(user_prompt: str, *, target: str | None):
        seen.append(user_prompt)
        return "沒關係！跟我說一遍：I want an apple."

    svc = CloudLLMService(cloud=cloud, fallback=_fallback, target_provider=lambda: TARGET)
    down = await _run(svc)

    assert _texts(down) == ["沒關係！跟我說一遍：I want an apple."]
    assert seen == [PROMPT], "降級也必須吃同一個 prompt，不可以另組一份"


@pytest.mark.asyncio
async def test_degrades_after_threshold_and_stops_calling_cloud():
    """連續失敗達門檻後就別再浪費逾時。"""
    cloud = _FakeCloud([None])
    policy = FailoverPolicy(failure_threshold=2, cooldown_s=30.0, clock=lambda: 0.0)
    svc = CloudLLMService(
        cloud=cloud,
        fallback=lambda p, *, target: "罐頭回覆",
        policy=policy,
        target_provider=lambda: TARGET,
    )

    await _run(svc, n=4)

    assert policy.route is Route.FALLBACK
    assert len(cloud.calls) == 2, f"降級後仍在打雲端（打了 {len(cloud.calls)} 次）"


@pytest.mark.asyncio
async def test_recovers_when_cloud_comes_back():
    """冷卻過後重試成功，要升回雲端。"""
    now = [0.0]
    cloud = _FakeCloud([None, None, "回來了！跟我說一遍：I want an apple."])
    policy = FailoverPolicy(
        failure_threshold=2, recovery_threshold=1, cooldown_s=30.0, clock=lambda: now[0]
    )
    svc = CloudLLMService(
        cloud=cloud,
        fallback=lambda p, *, target: "罐頭回覆",
        policy=policy,
        target_provider=lambda: TARGET,
    )

    await _run(svc, n=2)
    assert policy.route is Route.FALLBACK

    now[0] = 30.0
    down = await _run(svc, n=1)

    assert policy.route is Route.PRIMARY
    assert _texts(down) == ["回來了！跟我說一遍：I want an apple."]


@pytest.mark.asyncio
async def test_no_fallback_configured_pushes_nothing_but_does_not_crash():
    """沒設降級來源時，失敗就是這一輪沒回覆——但 pipeline 不可以炸掉。"""
    cloud = _FakeCloud([None])
    svc = CloudLLMService(cloud=cloud, target_provider=lambda: TARGET)

    down = await _run(svc)

    assert _texts(down) == []
    assert any(isinstance(f, LLMFullResponseEndFrame) for f in down)


@pytest.mark.asyncio
async def test_other_frames_pass_through():
    cloud = _FakeCloud(["好"])
    svc = CloudLLMService(cloud=cloud, target_provider=lambda: TARGET)

    down, _ = await run_test(
        svc, frames_to_send=[TextFrame("別的 frame")], expected_down_frames=None
    )

    assert any(
        isinstance(f, TextFrame) and not isinstance(f, LLMTextFrame) and f.text == "別的 frame"
        for f in down
    )
    assert cloud.calls == []
