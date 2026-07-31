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

from loguru import logger
from pipecat.frames.frames import Frame, InputAudioRawFrame, TTSAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from edge.runtime.live_client import PlaybackGate


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

    def __init__(self, gate: PlaybackGate, **kwargs):
        """Initialize the playback gate filter.

        Args:
            gate: Shared PlaybackGate instance, also held by the sink.
        """
        super().__init__(**kwargs)
        self._gate = gate
        self._muted_frames = 0
        self._was_open = True

    @property
    def muted_frames(self) -> int:
        """How many uplink frames were silenced (for diagnostics).

        Returns:
            Count of frames replaced with silence since start.
        """
        return self._muted_frames

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
                logger.debug(f"PlaybackGate {'開啟' if is_open else '關閉'}上行")
                self._was_open = is_open
            if not is_open:
                self._muted_frames += 1
                # 送靜音而非丟棄：VAD 的狀態機需要連續音訊，挖洞會留下斷口。
                frame = InputAudioRawFrame(
                    audio=b"\x00" * len(frame.audio),
                    sample_rate=frame.sample_rate,
                    num_channels=frame.num_channels,
                )
        await self.push_frame(frame, direction)
