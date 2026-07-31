# -*- coding: utf-8 -*-
"""Bedrock 全域節流：競賽規範是「每秒 1 個請求以下」。

規範原文（黑客松競賽環境規範與限制_20260722.pdf，Bedrock 規範第 1 條）：

    參賽隊伍需控制 Amazon Bedrock 的請求限制在每秒 1 個請求以下（RPS/TPS）。

專案有五個互相不知道對方存在的 Bedrock 呼叫端（cloud_llm、diagnose、
homework、report、orchestrator）。孩子講一句話，即時回覆 + 背景 agent 可能
同時發出去，瞬間就是 2–3 RPS。

**撞到 throttling 的症狀不會長得像違規，會長得像「雲端很慢」**——因為所有
呼叫端都 `except Exception: return None` 靜默降級。這個專案已經被
「靜默降級 + 假綠燈」咬過一次，所以節流本身必須有測試釘住。

測試不真的睡：`acquire()` 的 `now` / `sleep` 都可注入。
"""

from __future__ import annotations

import pytest

from server import bedrock_throttle


class _Clock:
    """可控時鐘：sleep 直接推進時間，不真的等。"""

    def __init__(self, start: float = 1000.0):
        self.t = start
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


@pytest.fixture(autouse=True)
def _fresh():
    bedrock_throttle.reset()
    yield
    bedrock_throttle.reset()


def test_the_first_request_goes_straight_through():
    c = _Clock()
    assert bedrock_throttle.acquire(now=c.now, sleep=c.sleep) == 0.0
    assert c.slept == []


def test_a_second_request_in_the_same_second_is_held_back():
    """兩個呼叫端同時要送——這正是規範要防的情況。"""
    c = _Clock()
    bedrock_throttle.acquire(now=c.now, sleep=c.sleep)
    waited = bedrock_throttle.acquire(now=c.now, sleep=c.sleep)
    assert waited > 0, "第二個請求沒有被節流"
    assert waited >= 1.0, f"間隔 {waited}s 不足 1 秒，仍會違規"


def test_requests_spaced_out_naturally_are_not_delayed():
    """已經隔夠久的請求不該被多等——節流不是無條件加延遲。"""
    c = _Clock()
    bedrock_throttle.acquire(now=c.now, sleep=c.sleep)
    c.t += 5.0
    assert bedrock_throttle.acquire(now=c.now, sleep=c.sleep) == 0.0


def test_three_back_to_back_requests_end_up_at_least_two_seconds_apart():
    """三個連發最後要橫跨 ≥2 秒，才真的是 1 RPS 以下。"""
    c = _Clock()
    start = c.t
    for _ in range(3):
        bedrock_throttle.acquire(now=c.now, sleep=c.sleep)
    assert c.t - start >= 2.0, f"三個請求只花了 {c.t - start}s"


def test_waiting_too_long_gives_up_instead_of_blocking_the_turn(monkeypatch):
    """排隊超過上限就放棄。

    即時路徑的雲端預算只有 1.5s，排隊 10 秒再送出去也沒意義——
    不如及早讓呼叫端降級回 edge，那條路已經驗證過。
    """
    monkeypatch.setattr(bedrock_throttle, "_MAX_WAIT_S", 0.5)
    c = _Clock()
    bedrock_throttle.acquire(now=c.now, sleep=c.sleep)
    with pytest.raises(bedrock_throttle.ThrottleTimeout):
        bedrock_throttle.acquire(now=c.now, sleep=c.sleep)


def test_the_throttle_is_actually_wired_into_the_bedrock_call():
    """節流沒被呼叫就等於不存在。

    釘住它真的在 converse() 之前——這是全專案唯一的收斂點，
    五個呼叫端都經過它。
    """
    import inspect

    from server import bedrock_converse

    src = inspect.getsource(bedrock_converse.converse_chat)
    assert "bedrock_throttle.acquire" in src, "Bedrock 呼叫路徑沒有經過節流器"
    assert src.index("bedrock_throttle.acquire") < src.index("client.converse"), \
        "節流器必須在送出請求之前"
