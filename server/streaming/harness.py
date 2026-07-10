"""A2-2 驗收 harness：組 pipeline 跑二元淨判（模仿 spike run_spike.py plumbing）。

no_stt=True：直接注入 TranscriptionFrame，專驗編排/barge-in（免載 1GB SenseVoice）。
no_stt=False：走真 FunASRSTTService（VAD 前綴 frame 觸發段落轉錄）。
"""
from __future__ import annotations

import asyncio
import wave
from pathlib import Path

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.pipeline.runner import PipelineRunner
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.frames.frames import (
    InputAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    EndFrame,
)

from server.streaming.turn_manager import StreamingTurnManager
from server.streaming.interruptible_tts import SherpaInterruptibleTTSService
from server.streaming.barge_in_gate import BargeInDetectedFrame

_TB_ROOT = Path(__file__).resolve().parents[2]
_WAV = _TB_ROOT / "models" / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17" / "test_wavs" / "zh.wav"


class OutputSink(FrameProcessor):
    """吞 TTS 音訊 frame、計數，不需真喇叭。"""

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


class InjectAtSentenceDriver(FrameProcessor):
    """看到第 N 個 TTSSpeakFrame（bot 已開講到第 N 句）後回注 frame_factory() 到 UPSTREAM。

    plumbing（A2-2 執行期定案）：TTSSpeakFrame 由 manager 往 DOWNSTREAM push，本 driver 須排
    manager **之後**才看得到；回注的 frame 往 **UPSTREAM** 才回得到 manager（其 process_frame
    不分方向判斷 barge-in frame）。frame_factory 決定注入哪種 frame（BargeInDetectedFrame＝
    真 barge-in；VADUserStartedSpeakingFrame＝驗舊耦合已移除）。
    """

    def __init__(self, frame_factory, at_sentence: int = 2, **kwargs):
        super().__init__(**kwargs)
        self._make = frame_factory
        self._at = at_sentence
        self._seen = 0
        self._fired = False

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSSpeakFrame):
            self._seen += 1
            if not self._fired and self._seen >= self._at:
                self._fired = True
                await self.push_frame(self._make(), FrameDirection.UPSTREAM)
        await self.push_frame(frame, direction)


def _load_wav_frames(chunk_ms: int = 200):
    with wave.open(str(_WAV), "rb") as wf:
        rate, ch = wf.getframerate(), wf.getnchannels()
        pcm = wf.readframes(wf.getnframes())
    step = int(rate * chunk_ms / 1000) * 2 * ch
    for i in range(0, len(pcm), step):
        yield InputAudioRawFrame(audio=pcm[i:i + step], sample_rate=rate, num_channels=ch)


async def run_once(reply_source, *, barge_in: bool, no_stt: bool = True, inject_frame_factory=None):
    """跑一輪，回 (OutputSink, StreamingTurnManager)。

    barge_in=True 時於第 2 個 TTSSpeakFrame 回注 inject_frame_factory()（預設 BargeInDetectedFrame）。
    """
    sink = OutputSink()
    manager = StreamingTurnManager(reply_source)

    procs = []
    if not no_stt:
        from pipecat.services.funasr.stt import FunASRSTTService
        procs.append(FunASRSTTService())
    # barge-in driver 須置於 manager 之後才看得到下行 TTSSpeakFrame；回注 frame 走 UPSTREAM 回 manager
    procs.append(manager)
    if barge_in:
        procs.append(InjectAtSentenceDriver(inject_frame_factory or BargeInDetectedFrame, at_sentence=2))
    procs += [SherpaInterruptibleTTSService(), sink]
    task = PipelineTask(Pipeline(procs))

    async def feed():
        if no_stt:
            await task.queue_frame(TranscriptionFrame(text="測試一句", user_id="u", timestamp="0"))
        else:
            await task.queue_frame(VADUserStartedSpeakingFrame(start_secs=0.2))
            for f in _load_wav_frames():
                await task.queue_frame(f)
            await task.queue_frame(VADUserStoppedSpeakingFrame(stop_secs=0.5))
        await asyncio.sleep(12)
        await task.queue_frame(EndFrame())

    await asyncio.gather(PipelineRunner().run(task), feed())
    return sink, manager
