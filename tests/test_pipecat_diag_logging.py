# -*- coding: utf-8 -*-
"""診斷訊息要看得見，但不能讓 pipecat 的 DEBUG 刷版。

## 2026-08-01 板子實測

加了閘門診斷（`PlaybackGate 開啟/關閉上行`）之後上板，journal 裡**一行都沒有**。
原因在 `probe_live_conversation.py` 的收尾：

```python
logger.remove()
logger.add(sys.stderr, level="WARNING")  # 只留警告，免得刷版蓋掉對話
```

那行 `WARNING` 有正當理由——pipecat 每個 frame 都有 DEBUG，全開會把
「👂 聽成 / 🗣 玩偶說」整個蓋掉，而現場有人在讀那份 log。

所以不能把等級整個調低，只能**把我們自己的 adapter 開到 INFO**：
它們印的都是每輪一兩行、專門為了現場診斷而寫的訊息。
"""

from __future__ import annotations

import pytest
from loguru import logger

from edge.probes.probe_live_conversation import configure_logging, is_diagnostic_record


@pytest.fixture(autouse=True)
def restore_logger():
    """`configure_logging` 會 `logger.remove()`，測完要還回去。"""
    yield
    logger.remove()
    import sys

    logger.add(sys.stderr)


def _record(name: str) -> dict:
    return {"name": name}


def test_our_adapters_count_as_diagnostic():
    assert is_diagnostic_record(_record("edge.runtime.pipecat_adapters.playback_gate"))
    assert is_diagnostic_record(_record("edge.runtime.pipecat_adapters.press_to_talk"))


def test_pipecat_internals_do_not():
    """pipecat 的 DEBUG 一旦放行就會蓋掉對話——那正是當初設 WARNING 的原因。"""
    assert not is_diagnostic_record(_record("pipecat.pipeline.worker"))
    assert not is_diagnostic_record(_record("pipecat.processors.frame_processor"))


async def _run_gate_closing(captured_sink) -> None:
    """真的驅動一次 `PlaybackGateFilter` 關閘門。

    **必須走真的 processor**：loguru 的 `record["name"]` 取自呼叫端的 frame，
    從測試檔直接呼叫 `logger.info` 會被歸到測試模組，filter 就測不到真實情況。
    """
    from pipecat.frames.frames import InputAudioRawFrame
    from pipecat.tests.utils import run_test

    from edge.runtime.live_client import PlaybackGate
    from edge.runtime.pipecat_adapters.playback_gate import PlaybackGateFilter

    configure_logging(sink=captured_sink)
    gate = PlaybackGate(rate=22050, now=lambda: 1000.0)
    gate.note_audio(22050 * 2)          # 玩偶在講話 → 閘門關閉
    await run_test(
        PlaybackGateFilter(gate, now=lambda: 1000.0),
        frames_to_send=[
            InputAudioRawFrame(audio=b"\x11" * 640, sample_rate=16000, num_channels=1)
        ],
        expected_down_frames=None,
    )


@pytest.mark.asyncio
async def test_diagnostic_info_reaches_the_log():
    """我們自己的 INFO 要印得出來，否則診斷等於沒加（板子上實測就是沒有）。"""
    captured: list[str] = []
    await _run_gate_closing(lambda m: captured.append(m.record["message"]))

    assert any("關閉上行" in m for m in captured), f"診斷訊息沒印出來：{captured}"


@pytest.mark.asyncio
async def test_pipecat_debug_does_not_flood_the_log():
    """pipecat 每個 frame 都有 DEBUG，漏出來會蓋掉「👂 聽成 / 🗣 玩偶說」。"""
    captured: list[str] = []
    await _run_gate_closing(lambda m: captured.append(m.record["name"] + "|" + m.record["message"]))

    leaked = [m for m in captured if m.startswith("pipecat")]
    assert not leaked, f"pipecat 的訊息漏出來了：{leaked[:5]}"


@pytest.mark.asyncio
async def test_stuck_warning_is_not_logged_twice():
    """兩個 sink 若都收同一筆 WARNING，現場會看到重複訊息而以為出了兩次事。"""
    from pipecat.frames.frames import InputAudioRawFrame
    from pipecat.tests.utils import run_test

    from edge.runtime.live_client import PlaybackGate
    from edge.runtime.pipecat_adapters.playback_gate import PlaybackGateFilter

    captured: list[str] = []
    configure_logging(sink=lambda m: captured.append(m.record["message"]))

    clock = [1000.0]
    gate = PlaybackGate(rate=22050, now=lambda: clock[0])
    gate.note_audio(22050 * 2 * 30)     # 30 秒下行 → 閘門會關很久
    f = PlaybackGateFilter(gate, now=lambda: clock[0], stuck_warn_s=10.0)

    mic = InputAudioRawFrame(audio=b"\x11" * 640, sample_rate=16000, num_channels=1)
    await run_test(f, frames_to_send=[mic], expected_down_frames=None)
    clock[0] += 10.5
    await run_test(f, frames_to_send=[mic], expected_down_frames=None)

    hits = sum("已關閉上行" in m for m in captured)
    assert hits == 1, f"示警應該只有一次，實際 {hits} 次：{captured}"
