"""BargeInGate 單元判準：真 SileroVAD，canned 語音→發 BargeInDetectedFrame；靜音→不發。"""
import asyncio
import wave
from pathlib import Path

import pytest

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.pipeline.runner import PipelineRunner
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.frames.frames import InputAudioRawFrame, EndFrame

from server.streaming.barge_in_gate import BargeInGate, BargeInDetectedFrame

_TB_ROOT = Path(__file__).resolve().parents[3]
_WAV = _TB_ROOT / "models" / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17" / "test_wavs" / "zh.wav"


class DetectSink(FrameProcessor):
    """計數下行的 BargeInDetectedFrame。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.detected = 0

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, BargeInDetectedFrame):
            self.detected += 1
        await self.push_frame(frame, direction)


def _speech_audio_frames(chunk_ms: int = 200):
    with wave.open(str(_WAV), "rb") as wf:
        rate, ch = wf.getframerate(), wf.getnchannels()
        pcm = wf.readframes(wf.getnframes())
    step = int(rate * chunk_ms / 1000) * 2 * ch
    for i in range(0, len(pcm), step):
        yield InputAudioRawFrame(audio=pcm[i:i + step], sample_rate=rate, num_channels=ch)


async def _run(frames):
    sink = DetectSink()
    task = PipelineTask(Pipeline([BargeInGate(), sink]))

    async def feed():
        for f in frames:
            await task.queue_frame(f)
        await asyncio.sleep(1)
        await task.queue_frame(EndFrame())

    await asyncio.gather(PipelineRunner().run(task), feed())
    return sink


@pytest.mark.asyncio
async def test_speech_emits_barge_in_detected():
    # 判準 A：canned 語音經 gate → ≥1 個 BargeInDetectedFrame
    sink = await _run(list(_speech_audio_frames()))
    assert sink.detected >= 1


@pytest.mark.asyncio
async def test_silence_emits_nothing():
    # 判準 B：靜音 → 0 個 BargeInDetectedFrame
    silence = [InputAudioRawFrame(audio=b"\x00" * 640, sample_rate=16000, num_channels=1) for _ in range(30)]
    sink = await _run(silence)
    assert sink.detected == 0
