# -*- coding: utf-8 -*-
"""PlaybackGateFilter / PlaybackGateSink 測試。

`PlaybackGate` 的時鐘可注入，所以不需要真的等待。
"""

from __future__ import annotations

import pytest
from loguru import logger
from pipecat.frames.frames import InputAudioRawFrame, TTSAudioRawFrame
from pipecat.tests.utils import run_test

from edge.runtime.live_client import PlaybackGate
from edge.runtime.pipecat_adapters.playback_gate import PlaybackGateFilter, PlaybackGateSink

RATE = 22050


@pytest.fixture
def logged():
    """收集 loguru 記錄，用來釘住診斷輸出。

    這些訊息**就是**交付物：2026-08-01 真人測試「換句子之後卡住」查不下去，
    正是因為 log 看不出閘門何時重開（`PIPECAT_HANDOFF.md` 三之三）。
    """
    records: list = []
    sink_id = logger.add(lambda m: records.append(m.record), level="DEBUG")
    yield records
    logger.remove(sink_id)


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


# --------------------------------------------------------------------------
# 診斷輸出（2026-08-01）：真人測試「換句子之後卡住」時，log 裡輪 2 之後一個
# VAD 事件都沒有，但**證不出是哪個閘門**。以下把證據直接印出來。
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_transitions_are_visible_at_info_level(logged):
    """關／開上行要看得見。DEBUG 在服務的 journalctl 裡根本不會出現。"""
    clock = [1000.0]
    gate = _gate(clock)
    gate.note_audio(RATE * 2)
    f = PlaybackGateFilter(gate, now=lambda: clock[0])

    await _run(f, [_mic()])

    closed = [r for r in logged if "關閉上行" in r["message"]]
    assert closed, "閘門關閉沒有留下紀錄"
    assert closed[0]["level"].name == "INFO", "DEBUG 在服務 log 看不到"


@pytest.mark.asyncio
async def test_reopening_reports_how_long_the_uplink_was_deaf(logged):
    """重開時要講出聾了多久、吃掉幾幀——這正是上次查不下去缺的那個數字。"""
    clock = [1000.0]
    gate = _gate(clock)
    gate.note_audio(RATE * 2)          # 1 秒音訊
    f = PlaybackGateFilter(gate, now=lambda: clock[0])

    await _run(f, [_mic()])            # 關閉
    clock[0] += 1.0 + 2.0 + 0.6 + 0.1  # 播完 + 緩衝 + tail
    await _run(f, [_mic()])            # 重開

    opened = [r for r in logged if "開啟上行" in r["message"]]
    assert opened, "閘門重開沒有留下紀錄"
    msg = opened[-1]["message"]
    assert "3.7" in msg, f"要講出關了多久，實際訊息：{msg}"
    assert "1 幀" in msg, f"要講出吃掉幾幀，實際訊息：{msg}"


@pytest.mark.asyncio
async def test_warns_when_the_gate_stays_closed_far_too_long(logged):
    """關超過門檻就示警——「卡住」的煙硝證據，不必再靠事後推理。

    用 30 秒的下行音訊模擬懷疑中的失效樣態：閘門累積出遠在未來的播放結束
    時刻。回覆上限 25 字 ≈ 4 秒，所以真跑起來不可能合法地關這麼久。
    """
    clock = [1000.0]
    gate = _gate(clock)
    gate.note_audio(RATE * 2 * 30)
    f = PlaybackGateFilter(gate, now=lambda: clock[0], stuck_warn_s=10.0)

    await _run(f, [_mic()])
    clock[0] += 10.5
    await _run(f, [_mic()])

    warns = [r for r in logged if r["level"].name == "WARNING"]
    assert warns, "關太久卻沒有示警"
    assert "10.5" in warns[0]["message"]


@pytest.mark.asyncio
async def test_stuck_warning_is_not_repeated_every_frame(logged):
    """一秒 50 幀，每幀都警告會把 log 洗爛，現場反而更查不到東西。"""
    clock = [1000.0]
    gate = _gate(clock)
    gate.note_audio(RATE * 2 * 30)
    f = PlaybackGateFilter(gate, now=lambda: clock[0], stuck_warn_s=10.0)

    await _run(f, [_mic()])
    clock[0] += 10.5
    await _run(f, [_mic(), _mic(), _mic()])

    warns = [r for r in logged if r["level"].name == "WARNING"]
    assert len(warns) == 1, f"應該只警告一次，實際 {len(warns)} 次"


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
