# -*- coding: utf-8 -*-
"""pipeline 陪聊雲端分支：cloud 模式先試 cloud_llm、None 降級回本地 llm。"""
import asyncio
import pytest
from server.pipeline import VoicePipeline


class _StubLLM:
    """本地 EdgeLLM 替身。"""
    def __init__(self, text):
        self._text = text
        self.called = False

    def available(self):
        return True

    def generate(self, student_text, scaffold, directive=None):
        self.called = True
        return self._text


class _StubCloud:
    def __init__(self, text):
        self._text = text
        self.called = False

    def available(self):
        return True

    def generate(self, student_text, scaffold, directive=None):
        self.called = True
        return self._text


class _StubTTS:
    def available(self):
        return False


async def _emit(_payload):  # 忽略事件（emit 契約為 async callback）
    return None


async def _run(pipe):
    return await pipe.run_turn_text("我看到一隻貓", _emit)


def _make(llm, cloud, mode):
    pipe = VoicePipeline(asr=None, llm=llm, tts=_StubTTS(), cloud_llm=cloud)
    pipe.network_mode = mode
    return pipe


def test_cloud_mode_uses_cloud_llm():
    local = _StubLLM("本地回覆")
    cloud = _StubCloud("雲端回覆 跟我說一遍：I see a cat")
    pipe = _make(local, cloud, "cloud")
    result = asyncio.run(_run(pipe))
    assert cloud.called is True
    assert local.called is False
    assert result.reply_text == "雲端回覆 跟我說一遍：I see a cat"


def test_cloud_none_falls_back_to_local():
    local = _StubLLM("本地回覆 跟我說一遍：I see a cat")
    cloud = _StubCloud(None)  # 雲端逾時/失敗
    pipe = _make(local, cloud, "cloud")
    result = asyncio.run(_run(pipe))
    assert cloud.called is True
    assert local.called is True
    assert result.reply_text == "本地回覆 跟我說一遍：I see a cat"


def test_edge_mode_never_calls_cloud():
    local = _StubLLM("本地回覆 跟我說一遍：I see a cat")
    cloud = _StubCloud("雲端回覆")
    pipe = _make(local, cloud, "edge")
    result = asyncio.run(_run(pipe))
    assert cloud.called is False
    assert local.called is True


def test_no_cloud_injected_is_backward_compatible():
    local = _StubLLM("本地回覆 跟我說一遍：I see a cat")
    pipe = VoicePipeline(asr=None, llm=local, tts=_StubTTS())  # 不傳 cloud_llm
    pipe.network_mode = "cloud"
    result = asyncio.run(_run(pipe))
    assert local.called is True
    assert result.reply_text == "本地回覆 跟我說一遍：I see a cat"
