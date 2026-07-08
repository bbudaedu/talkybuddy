# -*- coding: utf-8 -*-
"""VoicePipeline 雲端 TTS 路由：cloud 優先、失敗 fall back edge、edge 模式不碰雲端。"""

from __future__ import annotations

import pytest

from server.pipeline import VoicePipeline

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class StubASR:
    def available(self) -> bool:
        return True

    def transcribe(self, wav_path: str):
        return ("我要一個蘋果", 0.9)


class StubLLM:
    def available(self) -> bool:
        return True

    def generate(self, student_text, scaffold_result, directive=None):
        return "好呀，我們來聊聊。"


class StubTTS:
    """邊緣 TTS：回固定 b"EDGE"，並記錄呼叫次數。"""

    def __init__(self, wav=b"EDGE", available=True):
        self._wav = wav
        self._available = available
        self.calls = 0

    def available(self) -> bool:
        return self._available

    def synth(self, segments):
        self.calls += 1
        return self._wav


class StubCloud:
    """雲端 TTS：可設定回傳與可用性，記錄呼叫次數。"""

    def __init__(self, wav, available=True):
        self._wav = wav
        self._available = available
        self.calls = 0

    def available(self) -> bool:
        return self._available

    def synth(self, segments):
        self.calls += 1
        return self._wav


async def _emit(_payload):  # 忽略事件
    return None


async def test_cloud_mode_uses_cloud_when_available():
    edge = StubTTS(wav=b"EDGE")
    cloud = StubCloud(wav=b"CLOUD")
    vp = VoicePipeline(StubASR(), StubLLM(), edge, cloud_tts=cloud)
    vp.network_mode = "cloud"

    result = await vp.run_turn_text("我要一個蘋果", _emit)

    assert result.tts_wav == b"CLOUD"
    assert cloud.calls == 1
    assert edge.calls == 0          # 雲端成功 → 不碰邊緣


async def test_cloud_mode_falls_back_to_edge_when_cloud_returns_none():
    edge = StubTTS(wav=b"EDGE")
    cloud = StubCloud(wav=None)     # 模擬逾時/斷網
    vp = VoicePipeline(StubASR(), StubLLM(), edge, cloud_tts=cloud)
    vp.network_mode = "cloud"

    result = await vp.run_turn_text("我要一個蘋果", _emit)

    assert result.tts_wav == b"EDGE"
    assert cloud.calls == 1
    assert edge.calls == 1          # 雲端回 None → 靜默降級邊緣


async def test_edge_mode_never_touches_cloud():
    edge = StubTTS(wav=b"EDGE")
    cloud = StubCloud(wav=b"CLOUD")
    vp = VoicePipeline(StubASR(), StubLLM(), edge, cloud_tts=cloud)
    vp.network_mode = "edge"

    result = await vp.run_turn_text("我要一個蘋果", _emit)

    assert result.tts_wav == b"EDGE"
    assert cloud.calls == 0          # edge 模式完全不呼叫雲端


async def test_cloud_unavailable_uses_edge_without_calling_synth():
    edge = StubTTS(wav=b"EDGE")
    cloud = StubCloud(wav=b"CLOUD", available=False)
    vp = VoicePipeline(StubASR(), StubLLM(), edge, cloud_tts=cloud)
    vp.network_mode = "cloud"

    result = await vp.run_turn_text("我要一個蘋果", _emit)

    assert result.tts_wav == b"EDGE"
    assert cloud.calls == 0          # available()=False → 不呼叫 synth


async def test_no_cloud_engine_defaults_to_edge():
    edge = StubTTS(wav=b"EDGE")
    vp = VoicePipeline(StubASR(), StubLLM(), edge)  # 不傳 cloud_tts → 向後相容
    vp.network_mode = "cloud"

    result = await vp.run_turn_text("我要一個蘋果", _emit)

    assert result.tts_wav == b"EDGE"
    assert edge.calls == 1
