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
    # warmup=False：這條量的是「一輪送出去的 prompt」，暖機會多一次呼叫。
    svc = CloudLLMService(cloud=cloud, target_provider=lambda: TARGET, warmup=False)

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
        warmup=False,
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
        warmup=False,
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
    svc = CloudLLMService(cloud=cloud, target_provider=lambda: TARGET, warmup=False)

    down, _ = await run_test(
        svc, frames_to_send=[TextFrame("別的 frame")], expected_down_frames=None
    )

    assert any(
        isinstance(f, TextFrame) and not isinstance(f, LLMTextFrame) and f.text == "別的 frame"
        for f in down
    )
    assert cloud.calls == []


# --- 暖機：孩子的第一句話不該是冷的 -----------------------------------------


@pytest.mark.asyncio
async def test_warmup_runs_before_the_first_real_turn():
    """pipeline 啟動時先打一次雲端，把 TLS handshake 的成本吃掉。

    2026-07-31 板子實測（Gemini 直連，10 輪）：第 1 輪 1121ms，第 2-10 輪
    799-962ms，中位 827ms。同一支探針更早一次量到第一輪 1599ms —— **超過
    CLOUD_LLM_TIMEOUT_S 的 1.5s 上界**。

    穩態明明很充裕，卻會在孩子講的**第一句話**上降級成笨回覆，而第一印象正是
    決賽現場最貴的那一輪。這與 probe_live_conversation 對 TTS 做
    `synth([("zh", "暖機")])` 是同一個道理，照做。
    """
    cloud = _FakeCloud(["暖機回覆", "很好！跟我說一遍：I want an apple."])
    svc = CloudLLMService(cloud=cloud, target_provider=lambda: TARGET)

    down = await _run(svc)

    assert len(cloud.calls) == 2, "沒有暖機（或暖機打了不只一次）"
    assert cloud.calls[0][0] != PROMPT, "暖機不該送真正的教學 prompt"
    # 暖機的回覆絕不可以流進 pipeline —— 孩子會聽到它
    assert _texts(down) == ["很好！跟我說一遍：I want an apple."]


@pytest.mark.asyncio
async def test_warmup_can_be_turned_off():
    cloud = _FakeCloud(["很好！跟我說一遍：I want an apple."])
    svc = CloudLLMService(cloud=cloud, target_provider=lambda: TARGET, warmup=False)

    await _run(svc)

    assert len(cloud.calls) == 1


@pytest.mark.asyncio
async def test_warmup_failure_does_not_break_startup():
    """雲端不可達時，暖機失敗不可以讓 pipeline 起不來。"""
    class _Dead:
        calls: list = []

        def generate_from_prompt(self, user_prompt, *, target):
            raise RuntimeError("網路不通")

    svc = CloudLLMService(
        cloud=_Dead(),
        fallback=lambda p, *, target: "罐頭回覆",
        target_provider=lambda: TARGET,
    )

    down = await _run(svc)

    assert _texts(down) == ["罐頭回覆"], "暖機炸掉之後這一輪也該正常降級"


# --- 即時陪聊契約：多輪 + 教練 prompt + 不強制帶讀 --------------------------


class _FakeChatCloud:
    """假 CloudLLM，錄下 generate_chat 收到的東西。"""

    def __init__(self, reply="當然好呀！我們一起練。"):
        self.reply = reply
        self.chat_calls: list[dict] = []
        self.prompt_calls: list = []

    def generate_chat(self, messages, *, system, target, enforce_readalong=True):
        self.chat_calls.append({
            "messages": list(messages), "system": system,
            "target": target, "enforce_readalong": enforce_readalong,
        })
        return self.reply

    def generate_from_prompt(self, user_prompt, *, target):
        self.prompt_calls.append((user_prompt, target))
        return self.reply


@pytest.mark.asyncio
async def test_live_mode_sends_history_and_coach_prompt():
    """給了 system_provider 就走即時陪聊：多輪、教練 prompt、不強制帶讀。"""
    cloud = _FakeChatCloud()
    svc = CloudLLMService(
        cloud=cloud,
        target_provider=lambda: TARGET,
        system_provider=lambda: "你是教練企鵝",
        warmup=False,
    )

    await _run(svc)

    assert cloud.prompt_calls == [], "即時陪聊不該再走單輪進入點"
    assert len(cloud.chat_calls) == 1
    call = cloud.chat_calls[0]
    assert call["system"] == "你是教練企鵝"
    assert call["enforce_readalong"] is False, "即時陪聊不強制帶讀"
    assert call["target"] == TARGET, "目標句仍要傳（護欄之外還有別的用途）"
    # context 的 system 與 user 都要在，玩偶才記得上下文
    roles = [m.get("role") for m in call["messages"]]
    assert "user" in roles


@pytest.mark.asyncio
async def test_without_system_provider_behaviour_is_unchanged():
    """沒給就是原本的單輪回合式契約——既有 probe 零迴歸。"""
    cloud = _FakeChatCloud()
    svc = CloudLLMService(cloud=cloud, target_provider=lambda: TARGET, warmup=False)

    await _run(svc)

    assert cloud.chat_calls == []
    assert cloud.prompt_calls == [(PROMPT, TARGET)]


@pytest.mark.asyncio
async def test_live_mode_still_falls_back_within_the_turn():
    """換契約不可以把當輪降級弄丟。"""
    class _DeadChat:
        def generate_chat(self, messages, *, system, target, enforce_readalong=True):
            return None

    svc = CloudLLMService(
        cloud=_DeadChat(),
        fallback=lambda p, *, target: "沒關係！跟我說一遍：I want an apple.",
        target_provider=lambda: TARGET,
        system_provider=lambda: "你是教練企鵝",
        warmup=False,
    )

    down = await _run(svc)

    assert _texts(down) == ["沒關係！跟我說一遍：I want an apple."]
