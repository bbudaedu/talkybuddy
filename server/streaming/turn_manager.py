"""StreamingTurnManager：串流全雙工一輪的編排 + barge-in 取消。

資料流：TranscriptionFrame → ReplySource.stream() 逐句 → 逐句 push TTSSpeakFrame
（下游 SherpaInterruptibleTTSService 逐句合成）。回覆進行中收到
BargeInDetectedFrame ＝ barge-in → 先 push InterruptionFrame（TTS 句界停）、
後 cancel reply task（停止上游餵句）。產出 TurnResult。

只 import server.pipeline.TurnResult（dataclass），不碰 _process_text/run_turn_audio 熱路徑。
"""
from __future__ import annotations

import asyncio

from pipecat.frames.frames import (
    TranscriptionFrame,
    TTSSpeakFrame,
    InterruptionFrame,
)
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

from server.pipeline import TurnResult
from server.streaming.reply_source import ReplyContext, ReplySource
from server.streaming.barge_in_gate import BargeInDetectedFrame


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
            # 一輪開始：記 asr_text，啟動逐句回覆 task（不轉發轉錄，避免 TTS 合成轉錄文字）。
            # _bot_speaking 於此設 True 並維持，直到 barge-in 才設 False：manager 會瞬間 push
            # 完所有 TTSSpeakFrame（實際播放節奏在下游 TTS），若在 _stream_reply 結束即重置，
            # 稍後回注的 barge-in VAD frame 到達時 guard 已為 False、barge-in 失效。
            self.result.asr_text = getattr(frame, "text", "") or ""
            self._bot_speaking = True
            self._reply_task = asyncio.create_task(self._stream_reply())
            return

        if isinstance(frame, BargeInDetectedFrame) and self._bot_speaking:
            # barge-in：由 BargeInGate 的 BargeInDetectedFrame 觸發（獨立門檻）
            # 先中止合成（句界停），後取消上游餵句 task
            await self.push_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
            if self._reply_task is not None and not self._reply_task.done():
                self._reply_task.cancel()
            if "barge_in" not in self.result.state_events:
                self.result.state_events.append("barge_in")
            self._bot_speaking = False
            return

        # 其餘 frame（含 transport VAD/音訊/EndFrame 等）照常轉發
        await self.push_frame(frame, direction)

    async def _stream_reply(self):
        """逐句從 ReplySource 取回覆，push TTSSpeakFrame；被 cancel 時保留已合成部分句。

        注意：不在此重置 _bot_speaking（見 TranscriptionFrame 分支說明）——本 task 只負責
        把句子排入下游，播放節奏與「說話中」語意由 barge-in 分支收斂。
        """
        try:
            async for sentence in self._reply_source.stream(self.result.asr_text, self._ctx):
                await self.push_frame(TTSSpeakFrame(text=sentence), FrameDirection.DOWNSTREAM)
                self.result.reply_text += sentence
        except asyncio.CancelledError:
            # barge-in 中斷：reply_text 已含中斷前的部分句，直接收斂
            raise
