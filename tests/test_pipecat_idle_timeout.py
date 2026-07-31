# -*- coding: utf-8 -*-
"""玩偶待機時不可以被 pipecat 自己砍掉。

## 2026-08-01 板子實測

pipecat 服務啟動後**整整 5 分鐘**、沒有任何人講話，log 出現：

```
WARNING | pipecat.pipeline.worker:_idle_timeout_detected - Idle timeout detected.
Idle pipeline detected, cancelling pipeline worker...
arecord: pcm_read:2272: read error: Interrupted system call
```

`PipelineWorker` 預設 `idle_timeout_secs=300`、`cancel_on_idle_timeout=True`，
而它認定的「活著」是 `idle_timeout_frames=(BotSpeakingFrame, UserSpeakingFrame)`
——**沒人講話就算閒置**。

最糟的部分不是死掉，是**死得看不出來**：Python 行程沒有退出，systemd 仍顯示
`active`，`Restart=always` 因此不會救它。玩偶啞了而監控說一切正常，症狀跟
按鍵故障、麥克風被佔用一模一樣（`switch_doll.sh` 的 docstring 記過同一類坑）。

## 這不是按鍵觸發帶來的

打開 PTT 只是讓它**必然**發生（沒人按就完全沒有 frame）。VAD 連續聽的版本在
安靜房間裡同樣會死——決賽現場架好玩偶等上台的那幾分鐘正好踩中。

`local_client.py:119` 早就寫著這條需求：「玩偶必須能長時間待機，放在桌上等人
來按，閒置多久都不能死。」pipecat 這條路一直沒對齊它。
"""

from __future__ import annotations

import asyncio
import warnings

import pytest
from pipecat.frames.frames import Frame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.pipeline.runner import WorkerRunner

from pipecat.frames.frames import EndFrame

from edge.probes.probe_live_conversation import (
    IDLE_TIMEOUT_SECS,
    build_worker,
    serve_pipeline,
    silence_runner_deprecation,
)


class _PassThrough(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


def _pipeline() -> Pipeline:
    return Pipeline([_PassThrough()])


async def _dies_while_idle(worker: PipelineWorker, within_s: float) -> bool:
    """閒置期間 worker 有沒有被砍掉。

    **刻意走 `serve_pipeline` 而不是直接 `runner.run()`**：這樣真正的 runner
    API 才會被測到。改用 `add_workers()` 之類的寫法若打錯，這裡就會炸。

    Args:
        worker: 待觀察的 worker。
        within_s: 觀察多久。

    Returns:
        True 代表 runner 自己結束了（＝被 idle timeout 砍掉）。
    """
    task = asyncio.create_task(
        serve_pipeline(WorkerRunner(), worker, seconds=0.0, forever=True)
    )
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=within_s)
        return True
    except asyncio.TimeoutError:
        return False
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_idle_timeout_really_kills_the_doll():
    """先重現板子上那個失效——否則下面那條測試證明不了任何事。"""
    worker = PipelineWorker(_pipeline(), idle_timeout_secs=0.3)

    assert await _dies_while_idle(worker, within_s=5.0), (
        "設了 idle_timeout_secs 就應該在閒置後被砍掉；"
        "若這條過不了，代表 pipecat 換了機制，下面的防護也要重新檢查"
    )


@pytest.mark.asyncio
async def test_worker_survives_idling_with_no_one_speaking():
    """關掉 idle timeout 之後，沒人講話也不能死。"""
    worker = build_worker(_pipeline(), idle_timeout_secs=None)

    assert not await _dies_while_idle(worker, within_s=1.5), (
        "玩偶放在桌上等人來按，閒置多久都不能死（local_client.py:119）"
    )


# --------------------------------------------------------------------------
# pipeline 死了就要讓行程退出，systemd 才救得到
#
# 關掉 idle timeout 只是拿掉**一種**死法。真正的結構問題是：原本的
# `asyncio.gather(runner.run(worker), stop_after())` 在服務模式下，
# `stop_after()` 是 `while True: await asyncio.sleep(3600)`——runner 死了，
# gather 還在等那個睡一小時的協程。行程不退出 → systemd 顯示 active →
# `Restart=always` 永遠不觸發 → 玩偶靜默變啞而沒有人救。
# --------------------------------------------------------------------------


async def _runner_warnings(silence: bool) -> list:
    """在乾淨的 filter 狀態下跑一次 serve_pipeline，收集 runner 相關的警告。

    Args:
        silence: 是否套用模組的消音設定。

    Returns:
        訊息提到 WorkerRunner 的 DeprecationWarning 清單。
    """
    worker = PipelineWorker(_pipeline(), idle_timeout_secs=0.3)
    with warnings.catch_warnings(record=True) as caught:
        warnings.resetwarnings()          # 不受 pytest 的 -W 設定影響
        warnings.simplefilter("always")
        if silence:
            silence_runner_deprecation()  # 這行之後才是我們要驗的狀態
        await asyncio.wait_for(
            serve_pipeline(WorkerRunner(), worker, seconds=0.0, forever=True),
            timeout=5.0,
        )
    return [
        w for w in caught
        if issubclass(w.category, DeprecationWarning) and "WorkerRunner" in str(w.message)
    ]


@pytest.mark.asyncio
async def test_the_runner_deprecation_warning_is_real():
    """先證明這個警告真的會出現——否則下面那條測試證明不了任何事。"""
    assert await _runner_warnings(silence=False), (
        "pipecat 不再發這個警告的話，消音就是多餘的，該拿掉"
    )


@pytest.mark.asyncio
async def test_runner_is_started_without_deprecation_noise():
    """啟動 pipeline 不可以在 log 印 deprecation 警告。

    決賽現場有人在讀這份 log 判斷玩偶到底活了沒（本檔選 `WorkerRunner` 而非
    `PipelineRunner` 的理由就是這個）。板子上實測，那兩行雜訊會蓋在
    「🟢 開始了」正下方。
    """
    assert not await _runner_warnings(silence=True), "啟動時仍印了 deprecation 警告"


@pytest.mark.asyncio
async def test_add_workers_would_break_self_healing():
    """釘住地雷：官方建議的 `add_workers()+run()` **不能**拿來換掉 `run(worker)`。

    pipecat 1.6.0 把 `run(worker)` 標成 deprecated，建議改用
    `add_workers(worker)` + `run()`。下一個 session 很容易照做——但兩者語意
    相反（1.5.0 與 1.6.0 實測一致）：

        run(worker)             worker 死 → runner 跟著結束 → 行程退出 → systemd 重啟
        add_workers() + run()   worker 死 → runner **繼續跑** → 行程不退出

    換過去等於把 `serve_pipeline` 的自癒能力拿掉，退回「service active 但玩偶
    啞了」。這條測試若哪天變紅，代表 pipecat 修好了語意差異，那時才可以改。
    """
    worker = PipelineWorker(_pipeline(), idle_timeout_secs=0.3)
    runner = WorkerRunner()
    runner.add_workers(worker)

    task = asyncio.create_task(runner.run())
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class _FakeWorker:
    """只記下被塞了什麼 frame，並在收到 EndFrame 時通知 runner 收工。"""

    def __init__(self):
        self.queued: list = []
        self.ended = asyncio.Event()

    async def queue_frames(self, frames):
        self.queued.extend(frames)
        if any(isinstance(f, EndFrame) for f in frames):
            self.ended.set()


class _FakeRunner:
    """`dies_after` 秒後自己結束（模擬 pipeline 掛掉），否則等 EndFrame。"""

    def __init__(self, dies_after: float | None = None):
        self._dies_after = dies_after

    async def run(self, worker):
        if self._dies_after is not None:
            await asyncio.sleep(self._dies_after)
            return
        await worker.ended.wait()


@pytest.mark.asyncio
async def test_service_mode_returns_when_the_pipeline_dies():
    """服務模式下 pipeline 一死就要回來，讓 main() 退出、systemd 重啟。

    改壞的樣子是這條測試逾時——那正是板子上「service active 但玩偶啞了」。
    """
    worker = _FakeWorker()
    runner = _FakeRunner(dies_after=0.1)

    await asyncio.wait_for(
        serve_pipeline(runner, worker, seconds=0.0, forever=True), timeout=3.0
    )


@pytest.mark.asyncio
async def test_probe_mode_still_ends_after_the_requested_seconds():
    """限時模式（探針、真人測試用）行為不變：時間到就送 EndFrame 收尾。"""
    worker = _FakeWorker()
    runner = _FakeRunner()          # 不會自己結束，等 EndFrame

    await asyncio.wait_for(
        serve_pipeline(runner, worker, seconds=0.2, forever=False), timeout=3.0
    )

    assert any(isinstance(f, EndFrame) for f in worker.queued), (
        "限時模式必須送 EndFrame，否則 pipeline 不會乾淨收尾"
    )


def test_shipped_default_disables_the_idle_timeout():
    """出貨用的預設值必須是「不會自己死」。

    上面兩條證明了機制，這條釘住我們實際送出去的是安全的那一邊——
    預設值若被改回 300，玩偶會在安靜的會場待機五分鐘後靜默變啞。
    """
    assert IDLE_TIMEOUT_SECS is None
