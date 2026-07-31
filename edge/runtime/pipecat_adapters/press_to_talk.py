# -*- coding: utf-8 -*-
"""press_to_talk.py — 按鍵觸發：沒按之前把上行換成靜音。

## 為什麼需要（2026-08-01 決賽日）

local-client 是 press-to-talk（power 鍵），對環境噪音**天生免疫**；pipecat 走
VAD 連續聽。決賽會場很吵，而噪音誤觸不是假設——`PIPECAT_HANDOFF.md` 第二節記著
「寶貝多米」被辨識成「コび」。

近場門檻那條備案在這塊板子上**走不通**，不要再試：記憶 `project-edge-s2s-tuning`
記著 `TALKYBUDDY_EDGE_NEAR_FIELD_PEAK` 必須是 0，否則玩偶完全不回話（預設 0.06
會讓它全聾）。所以只剩按鍵這一條。

## 為什麼是兩個 processor

要擋噪音就必須攔在 **VAD 之前**（讓 VAD 根本不觸發），但「孩子講完了」這個訊號
（`UserStoppedSpeakingFrame`）是 `VADProcessor` 往**下游**推的——擺在 VAD 前面的
processor 永遠看不到它。

```
transport.input() → PressToTalkFilter(g) → PlaybackGateFilter → VAD → PressToTalkDisarmer(g) → STT → …
```

跟 `PlaybackGateFilter`/`PlaybackGateSink` 同一個理由、同一個已驗證過的形狀：
兩個 processor 共享一個 state 物件。

## 為什麼送靜音而不是丟棄

沿用 `playback_gate.py` 的通則（記憶 `project-edge-s2s-tuning`）：**不要在串流中
挖洞**。VAD 的狀態機依賴連續音訊，直接不送會留下斷口。

## 失效方向刻意選「開」

按鍵讀不到（裝置節點不見、權限不對）時 `PressToTalkGate` 會**永久 armed**，退回
現行的 VAD 連續聽。理由是不對稱的：玩偶變吵，現場的人看得出來也還能講話；玩偶
全聾的症狀跟壞掉一模一樣，沒有人救得回來，而那是決賽最貴的失敗。

## 為什麼用自己的執行緒而不是 `asyncio.to_thread`

`wait_for_trigger()` 是同步阻塞，可能擋好幾分鐘——直接在 async 裡呼叫會**凍結整個
event loop**（2026-07-30 local-client 閒置後崩潰的根因，見 `local_client.py:124`）。
`asyncio.to_thread` 也解得掉，但它會把預設 executor 的一個名額**永久佔住**，而這裡
的等待正是永不返回的那種。專屬 daemon 執行緒沒有這個問題，也不必跟著 pipecat 的
setup/cleanup 生命週期走。
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from loguru import logger
from pipecat.frames.frames import Frame, InputAudioRawFrame, UserStoppedSpeakingFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from edge.runtime import audio_io

# 按了鍵卻沒開口就走人時要自己關掉，否則閘門一直開著收會場噪音。
DEFAULT_IDLE_TIMEOUT_S = 15.0

# 等待「孩子講完 → 閘門關上」的輪詢間隔。20Hz 對背景執行緒是免費的，
# 換來不必在兩個執行緒之間拉一條 Event。
_REARM_POLL_S = 0.05


class PressToTalkGate:
    """armed 與否的狀態機。時鐘可注入，所以測試不必真的等 15 秒。"""

    def __init__(
        self,
        *,
        idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S,
        now: Callable[[], float] = time.monotonic,
    ):
        """Initialize the press-to-talk gate.

        Args:
            idle_timeout_s: Auto-disarm this long after arming with no speech.
            now: Injectable clock, for tests.
        """
        self._idle_timeout_s = idle_timeout_s
        self._now = now
        self._armed_at: float | None = None
        self._always_armed = False

    def arm(self) -> None:
        """Open the gate; it stays open until disarmed or idle-timed-out."""
        self._armed_at = self._now()

    def disarm(self) -> None:
        """Close the gate immediately."""
        self._armed_at = None

    def arm_permanently(self) -> None:
        """Fail open: the key can't be read, so fall back to continuous VAD.

        A noisy doll is recoverable on stage; a deaf one is not.
        """
        self._always_armed = True

    def is_armed(self) -> bool:
        """Whether uplink audio should reach the VAD right now.

        Returns:
            True when the key was pressed recently enough, or when the key
            device turned out to be unreadable.
        """
        if self._always_armed:
            return True
        if self._armed_at is None:
            return False
        return (self._now() - self._armed_at) < self._idle_timeout_s


class PressToTalkFilter(FrameProcessor):
    """沒按鍵之前把上行換成靜音。擺在 VAD **之前**。"""

    def __init__(
        self,
        gate: PressToTalkGate,
        *,
        trigger: Callable[[], object] | None = None,
        **kwargs,
    ):
        """Initialize the press-to-talk filter.

        Args:
            gate: Shared gate instance, also held by the disarmer.
            trigger: Blocking call that returns when the key is pressed.
                Defaults to the device's physical-key wait.
        """
        super().__init__(**kwargs)
        self._gate = gate
        self._trigger = trigger or audio_io.wait_for_trigger
        self._thread: threading.Thread | None = None
        self._muted_frames = 0
        self._was_armed: bool | None = None

    @property
    def muted_frames(self) -> int:
        """How many uplink frames were silenced (for diagnostics).

        Returns:
            Count of frames replaced with silence since start.
        """
        return self._muted_frames

    def _ensure_waiter(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._wait_loop, name="press-to-talk", daemon=True
        )
        self._thread.start()

    def _wait_loop(self) -> None:
        """等按鍵 → armed → 等閘門關上 → 再等按鍵。跑在自己的執行緒。"""
        while True:
            try:
                self._trigger()
            except Exception:
                # 讀不到按鍵就退回連續聽，並且大聲講——靜默地變成連續聽，
                # 現場會以為按鍵有效而百思不解為何噪音還是進得來。
                logger.warning(
                    "⚠️ 按鍵讀不到，press-to-talk 退回 VAD 連續聽（會收環境噪音）",
                    exc_info=True,
                )
                self._gate.arm_permanently()
                return
            logger.info("🔘 按鍵觸發，開始聽")
            self._gate.arm()
            while self._gate.is_armed():
                time.sleep(_REARM_POLL_S)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Replace uplink audio with silence until the key is pressed.

        Args:
            frame: The frame flowing through the pipeline.
            direction: Frame direction.
        """
        await super().process_frame(frame, direction)
        self._ensure_waiter()
        if isinstance(frame, InputAudioRawFrame):
            armed = self._gate.is_armed()
            if armed != self._was_armed:
                logger.debug(f"PressToTalk {'開啟' if armed else '關閉'}上行")
                self._was_armed = armed
            if not armed:
                self._muted_frames += 1
                frame = InputAudioRawFrame(
                    audio=b"\x00" * len(frame.audio),
                    sample_rate=frame.sample_rate,
                    num_channels=frame.num_channels,
                )
        await self.push_frame(frame, direction)


class PressToTalkDisarmer(FrameProcessor):
    """孩子講完就關閘門。擺在 VAD **之後**（那是 `UserStoppedSpeakingFrame` 的來源）。"""

    def __init__(self, gate: PressToTalkGate, **kwargs):
        """Initialize the disarmer.

        Args:
            gate: Shared gate instance, also held by the filter.
        """
        super().__init__(**kwargs)
        self._gate = gate

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Close the gate once the child finishes an utterance.

        Args:
            frame: The frame flowing through the pipeline.
            direction: Frame direction.
        """
        await super().process_frame(frame, direction)
        if isinstance(frame, UserStoppedSpeakingFrame):
            logger.debug("PressToTalk 孩子講完，關閉上行，等下一次按鍵")
            self._gate.disarm()
        # 只是旁觀者：吃掉 frame 會讓下游 STT 收不到分段結束訊號。
        await self.push_frame(frame, direction)
