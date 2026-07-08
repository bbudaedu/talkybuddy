"""StreamingTurnManager：串流全雙工一輪的編排 + barge-in 取消。

資料流：TranscriptionFrame → ReplySource.stream() 逐句 → 逐句 push TTSSpeakFrame
（下游 SherpaInterruptibleTTSService 逐句合成）。回覆進行中收到新的
VADUserStartedSpeakingFrame ＝ barge-in → 先 push InterruptionFrame（TTS 句界停）、
後 cancel reply task（停止上游餵句）。產出 TurnResult。

只 import server.pipeline.TurnResult（dataclass），不碰 _process_text/run_turn_audio 熱路徑。
"""
from __future__ import annotations

import asyncio

from pipecat.frames.frames import (
    TranscriptionFrame,
    TTSSpeakFrame,
    InterruptionFrame,
    VADUserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

from server.pipeline import TurnResult
from server.streaming.reply_source import ReplyContext, ReplySource


class StreamingTurnManager(FrameProcessor):
    def __init__(self, reply_source: ReplySource, ctx: ReplyContext | None = None, **kwargs):
        super().__init__(**kwargs)
        self._reply_source = reply_source
        self._ctx = ctx if ctx is not None else ReplyContext()
        self._reply_task: asyncio.Task | None = None
        self._bot_speaking = False
        self.result = TurnResult()

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            # 一輪開始：記 asr_text，啟動逐句回覆 task（不轉發轉錄，避免 TTS 合成轉錄文字）
            self.result.asr_text = getattr(frame, "text", "") or ""
            self._reply_task = asyncio.create_task(self._stream_reply())
            return

        if isinstance(frame, VADUserStartedSpeakingFrame) and self._bot_speaking:
            # barge-in：先中止合成（句界停），後取消上游餵句 task
            await self.push_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
            if self._reply_task is not None and not self._reply_task.done():
                self._reply_task.cancel()
            if "barge_in" not in self.result.state_events:
                self.result.state_events.append("barge_in")
            self._bot_speaking = False
            return

        # 其餘 frame（含首個 VADUserStartedSpeakingFrame、音訊、EndFrame 等）照常轉發
        await self.push_frame(frame, direction)

    async def _stream_reply(self):
        """逐句從 ReplySource 取回覆，push TTSSpeakFrame；被 cancel 時保留已合成部分句。"""
        self._bot_speaking = True
        try:
            async for sentence in self._reply_source.stream(self.result.asr_text, self._ctx):
                await self.push_frame(TTSSpeakFrame(text=sentence), FrameDirection.DOWNSTREAM)
                self.result.reply_text += sentence
        except asyncio.CancelledError:
            # barge-in 中斷：reply_text 已含中斷前的部分句，直接收斂
            raise
        finally:
            self._bot_speaking = False
