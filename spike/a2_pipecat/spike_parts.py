"""spike 專用的三個最小 pipeline 元件。

frame/基類名以 probe_pipecat.py 實測（Pipecat 1.5.0）為準：
- FrameProcessor / FrameDirection ＠ pipecat.processors.frame_processor
- FrameProcessor.process_frame **不自動轉發** frame（只處理 StartFrame/InterruptionFrame
  等生命週期），故每個殼都要自行 push_frame 轉發。
- interruption frame ＝ InterruptionFrame（非 StartInterruptionFrame）。
- TranscriptionFrame 是 TextFrame 子類 → StubLLM 消費它、不轉發，改 push TTSSpeakFrame，
  避免 TTS 把轉錄文字也拿去合成。
- 回覆用 TTSSpeakFrame（整段一次進 run_tts，由我們的 InterruptibleSynth 切句／斷點）。
"""
from __future__ import annotations

import asyncio

from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.frames.frames import (
    TranscriptionFrame,
    TTSSpeakFrame,
    TTSAudioRawFrame,
    InterruptionFrame,
)

_REPLY = "你好呀，我是企鵝。今天天氣真好。我們一起來玩遊戲吧。你想先做什麼呢？"


class StubLLMService(FrameProcessor):
    """把任何 STT 轉錄映成固定多句中文回覆（確保有句界可打斷）。"""

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            print(f"[StubLLM] got transcript: {getattr(frame, 'text', '')!r} -> fixed reply")
            # 消費 TranscriptionFrame（不轉發，否則 TTS 會把轉錄當文字合成），改推固定回覆
            await self.push_frame(TTSSpeakFrame(text=_REPLY), FrameDirection.DOWNSTREAM)
        else:
            await self.push_frame(frame, direction)


class InterruptDriver(FrameProcessor):
    """看到回覆 TTSSpeakFrame 通過後計時 delay_s，push InterruptionFrame 模擬 barge-in。

    錨定 TTS 起始（而非 pipeline StartFrame），確保打斷落在『播放中』。
    """

    def __init__(self, delay_s: float = 1.5, **kwargs):
        super().__init__(**kwargs)
        self._delay_s = delay_s
        self._task = None

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if self._task is None and isinstance(frame, TTSSpeakFrame):
            self._task = asyncio.create_task(self._fire())
        await self.push_frame(frame, direction)

    async def _fire(self):
        await asyncio.sleep(self._delay_s)
        print(f"[InterruptDriver] firing interruption after {self._delay_s}s")
        await self.push_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)


class OutputSink(FrameProcessor):
    """吞掉 TTS 音訊 frame、累計 byte 數與 frame 數，不需真喇叭。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.total_bytes = 0
        self.frame_count = 0

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSAudioRawFrame):
            self.total_bytes += len(frame.audio)
            self.frame_count += 1
        await self.push_frame(frame, direction)
