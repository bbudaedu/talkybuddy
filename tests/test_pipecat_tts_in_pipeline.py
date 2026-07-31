# -*- coding: utf-8 -*-
"""EdgeVitsTTSService 在**真實 PipelineWorker** 驅動下的行為測試。

## 為什麼需要這一組（踩過的坑）

TTS 的音訊不是直接 push 下去的，而是先進 **audio context**，由背景的
`_audio_context_task` 在 context **關閉之後**才 drain 出去。而關閉 context 的
是 `on_turn_context_completed()`，它要等 **turn 邊界**（`LLMFullResponseEndFrame`）。

所以只送 `TextFrame` 而不送 turn 邊界時，症狀是：

- `synth()` **確實被呼叫**（合成真的發生了）
- 但下游收到 **0 個 frame**，連 TextFrame 都不會 pass through

這個症狀看起來完全像「adapter 壞了」，實際上是**驅動方式不完整**。
2026-07-31 曾為此誤判成 adapter 缺陷。補上 `LLMFullResponseStartFrame` /
`LLMFullResponseEndFrame` 之後音訊立刻正常流出。

板子上用真實 sherpa VITS 引擎跑同一條路徑：23 個 `TTSAudioRawFrame`、
99328 bytes（2.25s @22050Hz），與單獨量測 `synth()` 的結果一致。
"""

from __future__ import annotations

import asyncio
import io
import wave

import pytest
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    TTSAudioRawFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from edge.runtime.pipecat_adapters.edge_tts import EdgeVitsTTSService

_RATE = 22050
_PCM_BYTES = 22050  # 0.5s @22050Hz 16-bit mono


class _Collector(FrameProcessor):
    """收下游所有 frame 以供斷言。"""

    def __init__(self):
        super().__init__()
        self.frames: list[Frame] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        self.frames.append(frame)
        await self.push_frame(frame, direction)


def _make_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_RATE)
        wf.writeframes(pcm)
    return buf.getvalue()


class _FakeEngine:
    def __init__(self):
        self.calls: list = []

    def synth(self, segments):
        self.calls.append(segments)
        return _make_wav(b"\x01\x02" * (_PCM_BYTES // 2))


async def _run(frames_to_send: list[Frame]) -> tuple[_Collector, _FakeEngine]:
    """跑一次真實 pipeline，回傳收集器與引擎。"""
    engine = _FakeEngine()
    collector = _Collector()
    worker = PipelineWorker(Pipeline([EdgeVitsTTSService(engine=engine), collector]))
    runner = PipelineRunner()

    async def feed():
        await asyncio.sleep(0.3)
        await worker.queue_frames(frames_to_send)
        await asyncio.sleep(2.0)
        await worker.queue_frames([EndFrame()])

    await asyncio.gather(runner.run(worker), feed())
    return collector, engine


@pytest.mark.asyncio
async def test_tts_emits_audio_when_turn_boundary_present():
    """有 turn 邊界時，音訊要真的流到下游。"""
    collector, engine = await _run(
        [
            LLMFullResponseStartFrame(),
            TextFrame("你好，我們來練習說蘋果。"),
            LLMFullResponseEndFrame(),
        ]
    )

    audio = [f for f in collector.frames if isinstance(f, TTSAudioRawFrame)]
    assert audio, "有 turn 邊界卻沒有音訊流出"
    assert sum(len(f.audio) for f in audio) == _PCM_BYTES, "音訊總量應等於合成長度"
    assert all(f.sample_rate == _RATE for f in audio)
    assert engine.calls == [[("zh", "你好，我們來練習說蘋果。")]]


@pytest.mark.asyncio
async def test_audio_is_stuck_without_turn_boundary():
    """釘住那個會誤導人的症狀：少了 turn 邊界，合成有發生但音訊出不來。

    這不是 adapter 缺陷，是驅動方式不完整——但症狀跟「TTS 壞掉」一模一樣。
    寫成測試是為了讓下一個人（或下一個我）三秒內認出它，而不是再查一次。
    若哪天 pipecat 改成不需要 turn 邊界也會 flush，這個測試會紅，那是好消息。
    """
    collector, engine = await _run([TextFrame("你好，我們來練習說蘋果。")])

    assert engine.calls, "合成本身應該有被觸發"
    audio = [f for f in collector.frames if isinstance(f, TTSAudioRawFrame)]
    assert not audio, "少了 turn 邊界時音訊應該卡在 audio context 裡"
