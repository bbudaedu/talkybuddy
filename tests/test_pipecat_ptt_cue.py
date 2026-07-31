# -*- coding: utf-8 -*-
"""按鍵之後要有提示音，否則人不知道玩偶已經在聽了。

## 2026-08-01 真人測試抓到的

按鍵觸發本身完全正常，但使用者回報「我按了 沒反應」。log 裡他自己講出了原因：

```
🔘 按鍵觸發，開始聽
👂 聽成：要按按鍵才開始說，我都不知道。
```

**這不是 bug，是設計缺陷**：按下去玩偶沒有任何回應，人會以為它壞了。決賽現場
小孩一定會犯一模一樣的錯，而「按了沒反應」的樣子跟玩偶真的壞掉分不出來。

## 為什麼是嗶聲不是「我有在聽」

玩偶只要**講話**，PlaybackGate 就得關上行（否則它把自己的話收回去，逐字稿會
出現自己剛講的句子——2026-07-31 踩過）。語音提示的成本是每輪多 3.4 秒
（0.8s 語音 + 2.0s aplay 緩衝 + 0.6s tail），而一輪目前才 15 秒。

純音不一樣：SenseVoice 不會把它辨識成字，Silero VAD 是**語音**偵測器、對單一
正弦波不敏感，所以**不必關閘門**，成本只有嗶聲本身的長度。

淡入淡出是必要的：方波邊緣是寬頻的「喀」聲，那反而可能觸發 VAD。
"""

from __future__ import annotations

import struct

import pytest
from pipecat.frames.frames import InputAudioRawFrame
from pipecat.tests.utils import run_test

from edge.runtime.pipecat_adapters.press_to_talk import (
    PressToTalkFilter,
    PressToTalkGate,
    beep_pcm,
)

RATE = 22050


def _samples(pcm: bytes) -> list[int]:
    return list(struct.unpack(f"<{len(pcm) // 2}h", pcm))


def _mic() -> InputAudioRawFrame:
    return InputAudioRawFrame(audio=b"\x11" * 640, sample_rate=16000, num_channels=1)


# --------------------------------------------------------------------------
# 嗶聲本身
# --------------------------------------------------------------------------


def test_beep_has_the_requested_duration():
    pcm = beep_pcm(RATE, ms=150)
    assert len(pcm) == int(RATE * 0.150) * 2, "16-bit mono，長度要對得上取樣率"


def test_beep_is_actually_audible():
    """靜音的提示音等於沒做——而且從 log 看不出來。"""
    peak = max(abs(s) for s in _samples(beep_pcm(RATE)))
    assert peak > 3000, f"音量太小（peak={peak}），玩偶喇叭那個距離聽不到"


def test_beep_fades_in_and_out():
    """突然開始／結束的方波邊緣是寬頻「喀」聲，反而可能觸發 VAD。"""
    s = _samples(beep_pcm(RATE))
    assert abs(s[0]) < 200, f"開頭沒淡入（{s[0]}）"
    assert abs(s[-1]) < 200, f"結尾沒淡出（{s[-1]}）"


def test_beep_does_not_clip():
    """削波會產生大量諧波，把純音變成寬頻噪音，就失去「不觸發 VAD」的前提。"""
    assert max(abs(s) for s in _samples(beep_pcm(RATE, volume=0.9))) < 32767


# --------------------------------------------------------------------------
# 閘門何時該發提示
# --------------------------------------------------------------------------


def test_gate_offers_a_cue_once_per_arm():
    """一次按鍵一聲。重複發會變成連續嗶嗶，比沒有更糟。"""
    gate = PressToTalkGate(now=lambda: 1000.0)
    gate.arm()

    assert gate.take_cue() is True
    assert gate.take_cue() is False, "同一次 arm 只能取走一次"


def test_no_cue_before_the_key_is_pressed():
    gate = PressToTalkGate(now=lambda: 1000.0)
    assert gate.take_cue() is False


def test_each_arm_offers_a_fresh_cue():
    clock = [1000.0]
    gate = PressToTalkGate(now=lambda: clock[0])
    gate.arm()
    gate.take_cue()
    gate.disarm()

    gate.arm()
    assert gate.take_cue() is True, "下一次按鍵要再嗶一次"


# --------------------------------------------------------------------------
# filter 把提示接出去
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filter_plays_the_cue_after_arming():
    """arm 之後第一個音訊 frame 就要把提示送出去，不能等到孩子開口。

    trigger 必須是**阻塞**的：`lambda: None` 會讓等待執行緒立刻再 arm 一次
    （那是正確行為——按兩次就該嗶兩次），測試會變成 race。
    """
    import threading

    played: list[bytes] = []

    async def cue():
        played.append(b"beep")

    gate = PressToTalkGate(now=lambda: 1000.0)
    never = threading.Event()
    filt = PressToTalkFilter(gate, trigger=never.wait, cue=cue)
    gate.arm()

    await run_test(filt, frames_to_send=[_mic(), _mic()], expected_down_frames=None)

    assert len(played) == 1, f"應該只嗶一次，實際 {len(played)} 次"


@pytest.mark.asyncio
async def test_filter_does_not_play_the_cue_while_disarmed():
    played: list[bytes] = []

    async def cue():
        played.append(b"beep")

    gate = PressToTalkGate(now=lambda: 1000.0)
    import threading

    never = threading.Event()
    filt = PressToTalkFilter(gate, trigger=never.wait, cue=cue)

    await run_test(filt, frames_to_send=[_mic()], expected_down_frames=None)

    assert played == [], "沒按鍵就不該出聲"


@pytest.mark.asyncio
async def test_cue_failure_does_not_break_the_conversation():
    """喇叭出問題時提示音發不出來，但玩偶還是要能聽孩子講話。"""

    import threading

    async def broken_cue():
        raise BrokenPipeError("aplay 掛了")

    gate = PressToTalkGate(now=lambda: 1000.0)
    never = threading.Event()
    filt = PressToTalkFilter(gate, trigger=never.wait, cue=broken_cue)
    gate.arm()

    down, _ = await run_test(
        filt, frames_to_send=[_mic()], expected_down_frames=None
    )

    audio = [f for f in down if isinstance(f, InputAudioRawFrame)]
    assert audio and audio[0].audio == b"\x11" * 640, "上行仍要原封不動通過"
