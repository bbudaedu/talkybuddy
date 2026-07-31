# -*- coding: utf-8 -*-
"""FailoverPolicy 單元測試。

時鐘是注入的，所以不需要真的等待；網路也不必真的拔掉。
"""

from __future__ import annotations

import pytest

from edge.runtime.pipecat_adapters.failover import FailoverPolicy, Route


class _Clock:
    """可手動推進的假時鐘。"""

    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _policy(clock=None, **kw) -> FailoverPolicy:
    return FailoverPolicy(clock=clock or _Clock(), **kw)


def test_starts_on_primary():
    """一開始要試雲端——本地是退路不是預設。"""
    p = _policy()
    assert p.route is Route.PRIMARY
    assert p.degraded is False


def test_single_failure_does_not_degrade():
    """單次逾時不算數，鏈路 RTT 116ms 本來就會偶爾抖。"""
    p = _policy(failure_threshold=2)
    assert p.record_failure() is Route.PRIMARY
    assert p.degraded is False


def test_consecutive_failures_degrade():
    """連續失敗達門檻就降級——繼續送過去只是每輪白等一次逾時。"""
    p = _policy(failure_threshold=2)
    p.record_failure()
    assert p.record_failure() is Route.FALLBACK
    assert p.degraded is True


def test_success_resets_failure_streak():
    """失敗計數必須是「連續」的，中間成功一次就歸零。"""
    p = _policy(failure_threshold=2)
    p.record_failure()
    p.record_success()
    assert p.record_failure() is Route.PRIMARY  # 只算 1 次連續失敗


def test_degrade_ignores_cooldown():
    """降級不看冷卻：雲端已經壞了，等冷卻只是讓孩子多等幾輪逾時。"""
    clock = _Clock()
    p = _policy(clock=clock, failure_threshold=1, cooldown_s=999.0)
    assert p.record_failure() is Route.FALLBACK


def test_recovery_needs_more_successes_than_failures_needed_to_degrade():
    """升回的門檻刻意比降級高——降級安全，升級有風險。"""
    clock = _Clock()
    p = _policy(clock=clock, failure_threshold=2, recovery_threshold=3, cooldown_s=0.0)
    p.record_failure()
    p.record_failure()
    assert p.degraded is True

    p.record_success()
    assert p.degraded is True, "一次成功就升回會在不穩鏈路上抖動"
    p.record_success()
    assert p.degraded is True
    assert p.record_success() is Route.PRIMARY


def test_recovery_blocked_until_cooldown_elapsed():
    """冷卻沒過就算成功次數夠也不升回——防止 up-and-down 鏈路來回切。"""
    clock = _Clock()
    p = _policy(clock=clock, failure_threshold=1, recovery_threshold=1, cooldown_s=30.0)

    p.record_failure()
    assert p.degraded is True

    clock.advance(29.0)
    assert p.record_success() is Route.FALLBACK, "冷卻未過不該升回"

    clock.advance(2.0)
    assert p.record_success() is Route.PRIMARY


def test_failure_during_recovery_resets_success_streak():
    """升回途中再失敗，成功計數要歸零，不能累積。"""
    clock = _Clock()
    p = _policy(clock=clock, failure_threshold=5, recovery_threshold=3, cooldown_s=0.0)
    p.force(Route.FALLBACK)

    p.record_success()
    p.record_success()
    p.record_failure()
    p.record_success()
    p.record_success()
    assert p.degraded is True, "成功計數應已被失敗打斷並歸零"
    assert p.record_success() is Route.PRIMARY


def test_flapping_link_does_not_thrash():
    """成敗交錯的鏈路不該一直切換——每次切換對孩子都是一次語音風格突變。"""
    clock = _Clock()
    p = _policy(clock=clock, failure_threshold=2, recovery_threshold=3, cooldown_s=30.0)

    switches = []
    prev = p.route
    for i in range(20):
        p.record_failure() if i % 2 == 0 else p.record_success()
        clock.advance(1.0)
        if p.route is not prev:
            switches.append(p.route)
            prev = p.route

    assert switches == [], f"成敗交錯不該觸發任何切換，實際切了 {switches}"


def test_force_pins_route_and_clears_history():
    """人工指定（demo 控制）要立即生效，且不被舊計數推翻。"""
    clock = _Clock()
    p = _policy(clock=clock, failure_threshold=2)
    p.record_failure()  # 累積 1 次

    assert p.force(Route.FALLBACK) is Route.FALLBACK
    assert p.record_success() is Route.FALLBACK  # 計數已清空，不會立刻升回


def test_switch_to_same_route_is_noop_and_does_not_refresh_cooldown():
    """切到同一個路由不該刷新冷卻，否則反覆 force 會凍住真正的切換。"""
    clock = _Clock()
    p = _policy(clock=clock, failure_threshold=1, recovery_threshold=1, cooldown_s=30.0)
    p.record_failure()  # → FALLBACK，冷卻起算

    clock.advance(31.0)
    p.force(Route.FALLBACK)  # 同一個路由，應為 no-op
    assert p.record_success() is Route.PRIMARY, "no-op 不該把冷卻重新起算"


@pytest.mark.parametrize(
    "kw",
    [
        {"failure_threshold": 0},
        {"recovery_threshold": 0},
        {"cooldown_s": -1.0},
    ],
)
def test_invalid_config_rejected(kw):
    """設定錯誤要當場炸，不要等到現場才發現策略是空的。"""
    with pytest.raises(ValueError):
        FailoverPolicy(**kw)


# --- 重試窗：降級之後要能升得回來 -----------------------------------------


def test_should_try_primary_is_true_while_healthy():
    """沒降級時當然要打雲端。"""
    p = FailoverPolicy()
    assert p.should_try_primary() is True


def test_degraded_skips_primary_until_cooldown_elapses():
    """剛降級的那段時間不要再浪費逾時去試雲端。"""
    now = [0.0]
    p = FailoverPolicy(failure_threshold=2, cooldown_s=30.0, clock=lambda: now[0])
    p.record_failure()
    p.record_failure()
    assert p.degraded is True
    assert p.should_try_primary() is False

    now[0] = 29.9
    assert p.should_try_primary() is False
    now[0] = 30.0
    assert p.should_try_primary() is True, "冷卻過了就該重試一次，否則永遠升不回去"


def test_failed_reprobe_pushes_the_next_window_out():
    """重試失敗要把下一次重試往後推，不可以每輪都白等一次逾時。"""
    now = [0.0]
    p = FailoverPolicy(failure_threshold=2, cooldown_s=30.0, clock=lambda: now[0])
    p.record_failure()
    p.record_failure()

    now[0] = 30.0
    assert p.should_try_primary() is True
    p.record_failure()                      # 重試也失敗
    assert p.should_try_primary() is False, "失敗後又立刻重試 = 每輪多等一次逾時"

    now[0] = 60.0
    assert p.should_try_primary() is True


def test_recovers_after_enough_successful_reprobes():
    """連續成功達門檻就升回雲端。"""
    now = [0.0]
    p = FailoverPolicy(
        failure_threshold=2, recovery_threshold=2, cooldown_s=30.0, clock=lambda: now[0]
    )
    p.record_failure()
    p.record_failure()
    assert p.degraded is True

    now[0] = 30.0
    p.record_success()
    now[0] = 60.0
    p.record_success()
    assert p.degraded is False, "連續成功達門檻卻沒升回 PRIMARY"
