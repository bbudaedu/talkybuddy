# -*- coding: utf-8 -*-
"""failover.py — 雲端／本地切換的決策狀態機。

## 為什麼要有這個東西，而且為什麼是「換 service」不是「換 client」

現行做法是兩個獨立的 client 行程（`live_client` 走雲端 S2S、`local_client` 走
回合式本地），用 systemd `Conflicts=` 互斥。那個做法有兩個已經咬過人的問題
（見記憶 `project-edge-deploy`）：

1. **`Conflicts=` 只停不啟**——`systemctl stop talkybuddy-live-client` 之後
   兩個 client 都是 inactive、**玩偶直接變啞**，症狀跟按鍵故障一模一樣
2. **兩個 client 會搶同一支麥克風**（commit `38aa261`），而「麥克風被佔用」
   的症狀又跟「麥克風壞掉」一模一樣

pipecat 的形狀讓這兩個問題同時消失：**麥克風由單一個 transport 持有，
從頭到尾不轉移**，切換發生在 pipeline 內部的 service 層。切失敗最壞的結果是
「這一輪回答比較笨」，而不是「玩偶不會講話了」。

**所以本模組只決定「用哪個 service」，永遠不碰 transport。** 這不是實作細節，
是這個設計存在的理由。

## 為什麼需要遲滯與冷卻

edge↔server 這條鏈路實測 RTT 116ms、頻寬約 550kB/s，而且**會整條斷掉**
（2026-07-31 03:1x 就斷過一次，ping 100% 丟包）。在這種鏈路上，
「一失敗就切、一成功就切回」會不停抖動，而每次抖動對孩子來說都是一次
語音風格突變（雲端聲音 ↔ 本地聲音）。

所以：

- 連續 `failure_threshold` 次失敗才降級（單次逾時不算數）
- 連續 `recovery_threshold` 次成功**且**距上次切換超過 `cooldown_s` 才升回
- 升回的門檻刻意比降級高——**降級是安全的（本地一定在），升級是有風險的**

## 這個狀態機不做 I/O

它不知道什麼叫「雲端」，只吃 success/failure 事件。時鐘也是注入的。
這讓它可以被完整測試，不必真的把網路拔掉。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from enum import Enum


class Route(Enum):
    """目前該把請求送去哪裡。"""

    PRIMARY = "primary"
    """雲端（或遠端 GPU）——比較好，但可能不可達。"""

    FALLBACK = "fallback"
    """裝置本機——比較笨，但一定在。"""


class FailoverPolicy:
    """以連續成功／失敗次數決定路由，帶遲滯與冷卻。"""

    def __init__(
        self,
        *,
        failure_threshold: int = 2,
        recovery_threshold: int = 3,
        cooldown_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        """Initialize the failover policy.

        Args:
            failure_threshold: Consecutive failures before dropping to fallback.
                Keep small — a stalled turn is already a bad turn.
            recovery_threshold: Consecutive successes before returning to primary.
                Deliberately higher than `failure_threshold`: dropping is safe,
                climbing back is not.
            cooldown_s: Minimum seconds between route switches. Guards against
                flapping on a link that is up-and-down rather than cleanly down.
            clock: Monotonic time source, injected so tests need no real waiting.

        Raises:
            ValueError: If any threshold is below 1 or cooldown is negative.
        """
        if failure_threshold < 1 or recovery_threshold < 1:
            raise ValueError("failure_threshold 與 recovery_threshold 都必須 >= 1")
        if cooldown_s < 0:
            raise ValueError("cooldown_s 不可為負")

        self._failure_threshold = failure_threshold
        self._recovery_threshold = recovery_threshold
        self._cooldown_s = cooldown_s
        self._clock = clock

        self._route = Route.PRIMARY
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._last_switch_at: float | None = None
        # 最近一次「降級期間的重試」發生在什麼時候。見 should_try_primary()。
        self._last_probe_at: float | None = None

    @property
    def route(self) -> Route:
        """Current route.

        Returns:
            Where requests should go right now.
        """
        return self._route

    @property
    def degraded(self) -> bool:
        """Whether we are currently on the fallback.

        Returns:
            True when running on the local fallback.
        """
        return self._route is Route.FALLBACK

    def should_try_primary(self) -> bool:
        """這一輪該不該（再）試一次雲端。

        沒降級時永遠是 True。降級之後就有一個矛盾要解：**不呼叫雲端，就永遠
        觀察不到「連續成功」，也就永遠升不回去**——`record_success` 等的那個
        訊號根本不會出現。所以降級期間仍要週期性地重試一次。

        重試的節奏用 `cooldown_s` 這同一個旋鈕，從「上次切換」或「上次重試」
        兩者較晚的那個算起。少了後者的話，冷卻一過就會變成**每一輪都重試**，
        鏈路真的斷掉時等於每輪白等一次 `CLOUD_LLM_TIMEOUT_S`。

        Returns:
            True 表示這一輪應該先嘗試雲端（失敗仍可當輪降級）。
        """
        if self._route is Route.PRIMARY:
            return True
        since = max(
            self._last_switch_at if self._last_switch_at is not None else 0.0,
            self._last_probe_at if self._last_probe_at is not None else 0.0,
        )
        return (self._clock() - since) >= self._cooldown_s

    def record_success(self) -> Route:
        """Record one successful request.

        Returns:
            The route to use for the next request.
        """
        self._consecutive_failures = 0
        self._consecutive_successes += 1

        if (
            self._route is Route.FALLBACK
            and self._consecutive_successes >= self._recovery_threshold
            and self._cooldown_elapsed()
        ):
            self._switch(Route.PRIMARY)
        return self._route

    def record_failure(self) -> Route:
        """Record one failed request (timeout, connection refused, 5xx…).

        Returns:
            The route to use for the next request.
        """
        # 這次失敗是不是「降級期間的重試」——要在可能的 _switch 之前判定，
        # 否則剛降級的那一次會被誤記成重試，把下一個重試窗白白往後推。
        was_degraded = self._route is Route.FALLBACK

        self._consecutive_successes = 0
        self._consecutive_failures += 1

        # 降級不看冷卻：雲端已經壞了，繼續送過去只是讓每一輪都白等一次逾時。
        if (
            self._route is Route.PRIMARY
            and self._consecutive_failures >= self._failure_threshold
        ):
            self._switch(Route.FALLBACK)
        elif was_degraded:
            self._last_probe_at = self._clock()
        return self._route

    def force(self, route: Route) -> Route:
        """Pin the route manually (demo control, `switch_mode.sh` equivalent).

        Resets the counters so an operator decision is not immediately undone
        by stale history.

        Args:
            route: The route to pin to.

        Returns:
            The now-current route.
        """
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._switch(route)
        return self._route

    def _cooldown_elapsed(self) -> bool:
        """距離上次切換是否已超過冷卻時間（從未切換過視為已過）。"""
        if self._last_switch_at is None:
            return True
        return (self._clock() - self._last_switch_at) >= self._cooldown_s

    def _switch(self, route: Route) -> None:
        """切換路由並重置計數；切到相同路由是 no-op（不刷新冷卻）。"""
        if route is self._route:
            return
        self._route = route
        self._last_switch_at = self._clock()
        self._last_probe_at = None
        self._consecutive_failures = 0
        self._consecutive_successes = 0
