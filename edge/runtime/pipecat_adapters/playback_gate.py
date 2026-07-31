# -*- coding: utf-8 -*-
"""playback_gate.py — 玩偶講話時把上行換成靜音，避免它聽到自己。

## 為什麼需要（2026-07-31 真人實測）

喇叭與 USB 麥克風同在玩偶內、板子裝不了 AEC。真人測試時玩偶把自己的話收了回去：

```
👂 聽成：到了嗎？跟我說一定方。      ← 玩偶剛講的「跟我說一遍」
疑似自我打斷次數：3
```

先試過 pipecat 的 `AlwaysUserMuteStrategy`，**不夠**——它掛在
`LLMUserAggregatorParams` 上，只決定「要不要把結果送進 LLM」，
VAD 與 STT 照樣聽、照樣辨識。要真正閉嘴必須攔在**音訊進 VAD 之前**。

## 直接重用 `live_client.PlaybackGate`

那個類別是 2026-07-30 一連串真機事故換來的，三個關鍵都不是憑空想得到的：

1. 追蹤**預估播完的時刻**，不是「最後收到資料的時刻」——下行音訊成批到達，
   aplay 收下後還要花對應時長才播完（曾丟棄 74.2s 卻播了 85.3s）
2. 要再扣掉 **aplay 的緩衝延遲**（`--buffer-time` 設多少，發聲就晚多少）
3. 打斷後緩衝被清空，那些音訊永遠不會發聲，**此時不該再等緩衝延遲**

所以這裡只寫 pipecat 的接線，判斷邏輯一律交給它。

## 為什麼送靜音而不是丟棄

記憶 `project-edge-s2s-tuning` 的通則：**不要在串流中挖洞**。
VAD 的狀態機依賴連續音訊，直接不送會留下斷口。送靜音則內容誠實
（使用者當下確實沒說話）、串流連續、且不含迴音。

## 接線方式：兩個 processor 共享一個 gate

```
transport.input() → PlaybackGateFilter(gate) → VAD → STT → …
                 → TTS → PlaybackGateSink(gate) → transport.output()
```

必須是兩個：上行與下行在 pipeline 的**不同位置**，單一 processor 看不到兩邊。
`gate` 實例共享，所以 sink 記下的播放時長，filter 立刻看得到。
"""

from __future__ import annotations

import time
from typing import Callable

from loguru import logger
from pipecat.frames.frames import Frame, InputAudioRawFrame, TTSAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from edge.runtime.live_client import PlaybackGate

# 上行關超過這麼久就示警。玩偶最長的一段回覆約 6 秒，加緩衝與 tail 約 8.6 秒，
# 所以 10 秒以上一定不正常——那正是 2026-08-01「換句子之後卡住」的樣子。
DEFAULT_STUCK_WARN_S = 10.0


class PlaybackGateSink(FrameProcessor):
    """記錄下行音訊長度，讓 gate 知道玩偶會講到什麼時候。放在 TTS 之後。"""

    def __init__(self, gate: PlaybackGate, **kwargs):
        """Initialize the playback gate sink.

        Args:
            gate: Shared PlaybackGate instance, also held by the filter.
        """
        super().__init__(**kwargs)
        self._gate = gate

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Note outgoing audio duration on the shared gate.

        Args:
            frame: The frame flowing through the pipeline.
            direction: Frame direction.
        """
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSAudioRawFrame) and frame.audio:
            self._gate.note_audio(len(frame.audio))
        await self.push_frame(frame, direction)


class PlaybackGateFilter(FrameProcessor):
    """玩偶講話期間把上行音訊換成靜音。放在 transport.input() 之後、VAD 之前。"""

    def __init__(
        self,
        gate: PlaybackGate,
        *,
        now: Callable[[], float] = time.monotonic,
        stuck_warn_s: float = DEFAULT_STUCK_WARN_S,
        on_reopen: Callable[[], None] | None = None,
        **kwargs,
    ):
        """Initialize the playback gate filter.

        Args:
            gate: Shared PlaybackGate instance, also held by the sink.
            now: Injectable clock, for tests.
            stuck_warn_s: Warn once when the uplink stays closed this long.
            on_reopen: 上行重新開啟時呼叫一次。按鍵觸發靠它「玩偶講完就自動
                開始聽」——跟讀的自然反應是立刻跟著唸，不是先按鈕。
        """
        super().__init__(**kwargs)
        self._gate = gate
        self._now = now
        self._on_reopen = on_reopen
        self._stuck_warn_s = stuck_warn_s
        self._muted_frames = 0
        self._was_open = True
        self._closed_since: float | None = None
        self._closed_at_frames = 0
        self._warned_stuck = False

    @property
    def muted_frames(self) -> int:
        """How many uplink frames were silenced (for diagnostics).

        Returns:
            Count of frames replaced with silence since start.
        """
        return self._muted_frames

    def _log_transition(self, is_open: bool) -> None:
        """關／開上行都留下可讀的紀錄，重開時附上「聾了多久、吃掉幾幀」。"""
        if is_open:
            deaf_s = 0.0 if self._closed_since is None else self._now() - self._closed_since
            eaten = self._muted_frames - self._closed_at_frames
            logger.info(f"PlaybackGate 開啟上行（關了 {deaf_s:.1f}s，靜音 {eaten} 幀）")
            self._closed_since = None
            self._warned_stuck = False
            if self._on_reopen is not None:
                try:
                    self._on_reopen()
                except Exception:
                    # 通知對象壞掉不該讓玩偶聾掉——聽孩子講話比自動開始聽重要。
                    logger.warning("上行重開的通知失敗，對話照常進行", exc_info=True)
        else:
            self._closed_since = self._now()
            self._closed_at_frames = self._muted_frames
            logger.info("PlaybackGate 關閉上行（玩偶在講話）")

    def _warn_if_stuck(self) -> None:
        """關太久就示警一次。每幀都警告會把 log 洗爛，現場反而更查不到東西。"""
        if self._warned_stuck or self._closed_since is None:
            return
        deaf_s = self._now() - self._closed_since
        if deaf_s < self._stuck_warn_s:
            return
        self._warned_stuck = True
        logger.warning(
            f"⚠️ PlaybackGate 已關閉上行 {deaf_s:.1f}s（超過 {self._stuck_warn_s:.0f}s）"
            "——玩偶現在聽不到孩子。若之後沒有『開啟上行』，卡住的就是這個閘門。"
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Replace uplink audio with silence while the bot is speaking.

        Args:
            frame: The frame flowing through the pipeline.
            direction: Frame direction.
        """
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            is_open = self._gate.is_open()
            if is_open != self._was_open:
                # INFO 而非 DEBUG：服務的 journalctl 看不到 DEBUG，而
                # 2026-08-01 查「換句子之後卡住」缺的正是這兩行。
                self._log_transition(is_open)
                self._was_open = is_open
            if not is_open:
                self._muted_frames += 1
                self._warn_if_stuck()
                # 送靜音而非丟棄：VAD 的狀態機需要連續音訊，挖洞會留下斷口。
                frame = InputAudioRawFrame(
                    audio=b"\x00" * len(frame.audio),
                    sample_rate=frame.sample_rate,
                    num_channels=frame.num_channels,
                )
        await self.push_frame(frame, direction)
