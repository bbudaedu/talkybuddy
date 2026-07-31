# -*- coding: utf-8 -*-
"""PlaybackGateFilter / PlaybackGateSink 測試。

`PlaybackGate` 的時鐘可注入，所以不需要真的等待。
"""

from __future__ import annotations

import pytest
from pipecat.frames.frames import InputAudioRawFrame, TTSAudioRawFrame
from pipecat.tests.utils import run_test

from edge.runtime.live_client import PlaybackGate
from edge.runtime.pipecat_adapters.playback_gate import PlaybackGateFilter, PlaybackGateSink

RATE = 22050


def _gate(clock):
    return PlaybackGate(tail_s=0.6, buffer_delay_s=2.0, now=lambda: clock[0], rate=RATE)


def _mic(n: int = 640) -> InputAudioRawFrame:
    return InputAudioRawFrame(audio=b"\x11" * n, sample_rate=16000, num_channels=1)


async def _run(proc, frames):
    down, _ = await run_test(proc, frames_to_send=frames, expected_down_frames=None)
    return down


@pytest.mark.asyncio
async def test_uplink_passes_when_bot_is_silent():
    """玩偶沒在講話時，上行原封不動。"""
    clock = [1000.0]
    f = PlaybackGateFilter(_gate(clock))
    down = await _run(f, [_mic()])

    audio = [x for x in down if isinstance(x, InputAudioRawFrame)]
    assert audio[0].audio == b"\x11" * 640
    assert f.muted_frames == 0


@pytest.mark.asyncio
async def test_uplink_becomes_silence_while_bot_speaks():
    """玩偶講話期間上行要變成靜音——否則它會聽到自己（真人實測「跟我說一定方」）。"""
    clock = [1000.0]
    gate = _gate(clock)
    gate.note_audio(RATE * 2)  # 1 秒的下行音訊

    f = PlaybackGateFilter(gate)
    down = await _run(f, [_mic()])

    audio = [x for x in down if isinstance(x, InputAudioRawFrame)]
    assert audio[0].audio == b"\x00" * 640, "應被換成靜音"
    assert f.muted_frames == 1


@pytest.mark.asyncio
async def test_silence_keeps_frame_shape():
    """送靜音而非丟棄：長度、取樣率、聲道數都要保持，VAD 才不會看到斷口。"""
    clock = [1000.0]
    gate = _gate(clock)
    gate.note_audio(RATE * 2)

    down = await _run(PlaybackGateFilter(gate), [_mic(320)])
    out = [x for x in down if isinstance(x, InputAudioRawFrame)][0]

    assert len(out.audio) == 320
    assert out.sample_rate == 16000
    assert out.num_channels == 1


@pytest.mark.asyncio
async def test_uplink_reopens_after_playback_plus_buffer_and_tail():
    """閘門要等「播完 + 緩衝延遲 + tail」才開——早開就會收到自己的尾音。"""
    clock = [1000.0]
    gate = _gate(clock)
    gate.note_audio(RATE * 2)  # 1 秒音訊
    f = PlaybackGateFilter(gate)

    clock[0] += 1.0 + 2.0 + 0.6 - 0.05  # 差一點點
    down = await _run(f, [_mic()])
    assert [x for x in down if isinstance(x, InputAudioRawFrame)][0].audio == b"\x00" * 640

    clock[0] += 0.1  # 過了
    down = await _run(f, [_mic()])
    assert [x for x in down if isinstance(x, InputAudioRawFrame)][0].audio == b"\x11" * 640


@pytest.mark.asyncio
async def test_sink_notes_downlink_audio_duration():
    """sink 要把下行音訊長度記進共享的 gate，filter 才知道要關多久。"""
    clock = [1000.0]
    gate = _gate(clock)
    sink = PlaybackGateSink(gate)

    assert gate.is_open() is True
    await _run(sink, [TTSAudioRawFrame(audio=b"\x00" * (RATE * 2), sample_rate=RATE, num_channels=1)])
    assert gate.is_open() is False, "sink 記錄後閘門應關閉"


@pytest.mark.asyncio
async def test_sink_and_filter_share_state():
    """兩個 processor 在 pipeline 的不同位置，必須共用同一個 gate 實例。"""
    clock = [1000.0]
    gate = _gate(clock)
    sink = PlaybackGateSink(gate)
    filt = PlaybackGateFilter(gate)

    await _run(sink, [TTSAudioRawFrame(audio=b"\x00" * (RATE * 2), sample_rate=RATE, num_channels=1)])
    down = await _run(filt, [_mic()])

    assert [x for x in down if isinstance(x, InputAudioRawFrame)][0].audio == b"\x00" * 640
    assert filt.muted_frames == 1
