# -*- coding: utf-8 -*-
"""bedrock_throttle.py — 全域 Bedrock 請求節流器（每秒 1 個請求）。

為什麼需要
----------
2026 雲湧智生黑客松「黑客松競賽環境規範與限制_20260722.pdf」的 Bedrock 規範第 1 條：

    參賽隊伍需控制 Amazon Bedrock 的請求限制在每秒 1 個請求以下（RPS/TPS）。

這個專案有**四個**互相不知道對方存在的 Bedrock 呼叫端：

    server/cloud_llm.py        每一輪對話（即時路徑，1.5s 預算）
    server/diagnose.py         回合後背景診斷
    server/agents/homework.py  作業產生
    server/agents/report.py    週報
    server/agents/orchestrator.py 中央編排

`orchestrator` 那邊有節流，但那是「每 N 輪觸發一次」的業務節流，
**不是 RPS 限制**。孩子講一句話，即時回覆 + 背景 agent 可能同時發出去，
瞬間就是 2–3 RPS。

撞到 Bedrock throttling 的症狀是 `ThrottlingException`，而呼叫端全都
`except Exception: return None` 靜默降級——**現場看起來會是「雲端很慢／
雲端不穩」，不會有人想到是自己超速**。這個專案已經被「靜默降級 + 假綠燈」
咬過一次（`.planning/INVESTIGATION-cloud-brain-2026-07-30.md`），不要再來一次。

設計
----
最簡單能用的東西：一個模組級的鎖 + 上次放行時間戳。**不做 token bucket**——
規範說的是「每秒 1 個以下」，不是「平均每秒 1 個」，突發放行反而違規。

節流點放在 ``bedrock_converse`` 真正呼叫 ``client.converse()`` 之前，
那是全專案唯一的收斂點（四個呼叫端都經過它）。

阻塞式等待是刻意的：即時路徑本來就有 ``asyncio.wait_for`` 的逾時上界包著，
等太久會自然逾時降級回 edge——那是既有且驗證過的行為，比自己發明一套
「排隊中」狀態好。
"""

from __future__ import annotations

import logging
import os
import threading
import time

_log = logging.getLogger(__name__)

# 規範是「每秒 1 個請求以下」。取 1.05s 留 5% 餘裕：時鐘精度與網路重送都可能
# 讓伺服器端看到的間隔略短於本地量到的。寧可慢一點，不要擦邊違規。
_MIN_INTERVAL_S = float(os.environ.get("TALKYBUDDY_BEDROCK_MIN_INTERVAL_S", "1.05"))

# 等太久就放棄。即時路徑的雲端預算只有 1.5s，排隊超過這個數字再送出去也沒意義，
# 不如及早讓呼叫端降級回 edge。背景 agent 不受影響（它們的 timeout 大得多）。
_MAX_WAIT_S = float(os.environ.get("TALKYBUDDY_BEDROCK_MAX_WAIT_S", "8.0"))

_lock = threading.Lock()
_last_sent_at: float = 0.0


class ThrottleTimeout(RuntimeError):
    """排隊超過 ``_MAX_WAIT_S``。呼叫端應視同雲端不可用並降級。"""


def acquire(*, now=time.monotonic, sleep=time.sleep) -> float:
    """取得送出一個 Bedrock 請求的許可；必要時阻塞等待。回傳實際等待秒數。

    ``now`` / ``sleep`` 可注入，測試才不必真的睡。
    """
    global _last_sent_at
    waited = 0.0
    with _lock:
        gap = now() - _last_sent_at
        if gap < _MIN_INTERVAL_S:
            waited = _MIN_INTERVAL_S - gap
            if waited > _MAX_WAIT_S:
                raise ThrottleTimeout(
                    f"Bedrock 節流排隊 {waited:.1f}s 超過上限 {_MAX_WAIT_S}s，放棄本次呼叫"
                )
            sleep(waited)
        _last_sent_at = now()
    if waited > 0.5:
        _log.info("Bedrock 節流等待 %.2fs（規範上限 1 RPS）", waited)
    return waited


def reset() -> None:
    """測試用：清掉上次送出時間。"""
    global _last_sent_at
    with _lock:
        _last_sent_at = 0.0
