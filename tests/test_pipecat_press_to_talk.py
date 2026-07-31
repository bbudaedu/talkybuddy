# -*- coding: utf-8 -*-
"""按鍵觸發（press-to-talk）測試。

## 為什麼需要（2026-08-01 決賽日）

pipecat 是 VAD 連續聽，local-client 是按 power 鍵才聽。決賽會場很吵，而
`PIPECAT_HANDOFF.md` 第二節記著噪音誤觸真的發生過——「寶貝多米」被辨識成
「コび」。近場門檻那條備案在這塊板子上走不通（記憶 `project-edge-s2s-tuning`：
`TALKYBUDDY_EDGE_NEAR_FIELD_PEAK` 必須是 0，否則玩偶完全不回話），所以只剩按鍵。

## 為什麼是兩個 processor

`vad` 是獨立的 `VADProcessor`，排在 `PlaybackGateFilter` 之後
（`probe_live_conversation.py:391-392`）。要在 VAD **之前**封嘴才擋得住噪音，
但 `UserStoppedSpeakingFrame` 是 VAD 往下游推的——同一個 processor 看不到兩邊。
與 `PlaybackGateFilter`/`PlaybackGateSink` 同一個理由、同一個形狀。

## 失效方向刻意選「開」

按鍵讀不到時要 **armed**（退回現行 VAD 行為），不是保持靜音。決賽現場玩偶
變吵勝過玩偶全聾——後者的症狀跟壞掉一模一樣，而且沒有人救得回來。
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from pipecat.frames.frames import InputAudioRawFrame, UserStoppedSpeakingFrame
from pipecat.tests.utils import run_test

from edge.runtime.pipecat_adapters.press_to_talk import (
    PressToTalkDisarmer,
    PressToTalkFilter,
    PressToTalkGate,
)


def _mic(n: int = 640) -> InputAudioRawFrame:
    return InputAudioRawFrame(audio=b"\x11" * n, sample_rate=16000, num_channels=1)


async def _run(proc, frames):
    down, _ = await run_test(proc, frames_to_send=frames, expected_down_frames=None)
    return down


def _audio(frames):
    return [f for f in frames if isinstance(f, InputAudioRawFrame)]


# --------------------------------------------------------------------------
# PressToTalkGate：純狀態機，時鐘可注入，不需要任何非同步
# --------------------------------------------------------------------------


def test_gate_starts_disarmed():
    """沒按鍵之前不該聽——這正是要擋的噪音誤觸。"""
    gate = PressToTalkGate(now=lambda: 1000.0)
    assert gate.is_armed() is False


def test_gate_is_armed_after_the_key_is_pressed():
    clock = [1000.0]
    gate = PressToTalkGate(idle_timeout_s=15.0, now=lambda: clock[0])
    gate.arm()
    assert gate.is_armed() is True


def test_gate_disarms_itself_after_idle_timeout():
    """按了鍵卻沒開口就走人時要自己關掉，否則玩偶會一直開著聽會場噪音。"""
    clock = [1000.0]
    gate = PressToTalkGate(idle_timeout_s=15.0, now=lambda: clock[0])
    gate.arm()

    clock[0] += 14.9
    assert gate.is_armed() is True, "還在逾時之內"

    clock[0] += 0.2
    assert gate.is_armed() is False, "超過 15 秒該自己關掉"


def test_gate_disarm_takes_effect_immediately():
    clock = [1000.0]
    gate = PressToTalkGate(idle_timeout_s=15.0, now=lambda: clock[0])
    gate.arm()
    gate.disarm()
    assert gate.is_armed() is False


# --------------------------------------------------------------------------
# PressToTalkFilter：VAD 之前封嘴
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uplink_is_silenced_before_the_key_is_pressed():
    """沒 armed 時上行換成靜音，VAD 因此不會被會場噪音觸發。"""
    gate = PressToTalkGate(now=lambda: 1000.0)
    never = threading.Event()
    filt = PressToTalkFilter(gate, trigger=never.wait)

    down = await _run(filt, [_mic()])

    assert _audio(down)[0].audio == b"\x00" * 640
    assert filt.muted_frames == 1


@pytest.mark.asyncio
async def test_silenced_frame_keeps_shape():
    """送靜音而非丟棄：VAD 狀態機需要連續時間軸，挖洞會留下斷口。"""
    gate = PressToTalkGate(now=lambda: 1000.0)
    never = threading.Event()
    filt = PressToTalkFilter(gate, trigger=never.wait)

    out = _audio(await _run(filt, [_mic(320)]))[0]

    assert len(out.audio) == 320
    assert out.sample_rate == 16000
    assert out.num_channels == 1


@pytest.mark.asyncio
async def test_uplink_passes_after_the_key_is_pressed():
    """按下按鍵之後上行原封不動送進 VAD。"""
    clock = [1000.0]
    gate = PressToTalkGate(idle_timeout_s=15.0, now=lambda: clock[0])
    pressed = threading.Event()
    filt = PressToTalkFilter(gate, trigger=pressed.wait)

    down = await _run(filt, [_mic()])
    assert _audio(down)[0].audio == b"\x00" * 640, "按之前仍是靜音"

    pressed.set()
    await asyncio.sleep(0.1)  # 讓等待按鍵的執行緒把 gate armed 起來

    down = await _run(filt, [_mic()])
    assert _audio(down)[0].audio == b"\x11" * 640, "按下之後應原封不動通過"


@pytest.mark.asyncio
async def test_waiting_for_the_key_does_not_freeze_the_event_loop():
    """`wait_for_trigger` 是同步阻塞，直接呼叫會凍住整個 pipeline。

    這正是 2026-07-30 local-client 閒置後崩潰的根因（`local_client.py:124-127`）。
    若沒走 `asyncio.to_thread`，下面那個 `asyncio.sleep` 永遠不會回來，本測試會逾時。
    """
    gate = PressToTalkGate(now=lambda: 1000.0)
    blocking = threading.Event()
    filt = PressToTalkFilter(gate, trigger=blocking.wait)

    await _run(filt, [_mic()])          # 啟動等待按鍵的背景工作
    await asyncio.wait_for(asyncio.sleep(0.05), timeout=1.0)   # loop 還轉得動

    assert gate.is_armed() is False
    blocking.set()


@pytest.mark.asyncio
async def test_arms_when_the_key_device_is_broken():
    """按鍵讀不到就 armed——玩偶變吵可以救，玩偶全聾在決賽現場救不回來。"""
    clock = [1000.0]
    gate = PressToTalkGate(idle_timeout_s=15.0, now=lambda: clock[0])

    def broken():
        raise OSError("/dev/input/event1: No such device")

    filt = PressToTalkFilter(gate, trigger=broken)

    await _run(filt, [_mic()])          # 啟動背景工作，它會撞到例外
    await asyncio.sleep(0.1)

    down = await _run(filt, [_mic()])
    assert _audio(down)[0].audio == b"\x11" * 640, "按鍵壞掉時應退回連續聽"


# --------------------------------------------------------------------------
# PressToTalkDisarmer：VAD 之後收孩子講完的訊號
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disarms_when_the_child_stops_speaking():
    """孩子講完就關閘門，不要讓它繼續開著收玩偶回覆期間的環境噪音。"""
    clock = [1000.0]
    gate = PressToTalkGate(idle_timeout_s=15.0, now=lambda: clock[0])
    gate.arm()

    await _run(PressToTalkDisarmer(gate), [UserStoppedSpeakingFrame()])

    assert gate.is_armed() is False


@pytest.mark.asyncio
async def test_disarmer_passes_frames_through():
    """它只是旁觀者——吃掉 frame 會讓下游的 STT 收不到分段結束訊號。"""
    gate = PressToTalkGate(now=lambda: 1000.0)
    down = await _run(PressToTalkDisarmer(gate), [UserStoppedSpeakingFrame()])

    assert any(isinstance(f, UserStoppedSpeakingFrame) for f in down)
