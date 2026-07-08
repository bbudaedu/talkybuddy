"""ReplySource 介面與 StubReplySource 單元測試（不需真模型）。"""
import pytest

from server.streaming.reply_source import ReplyContext, StubReplySource


async def _collect(source, asr_text, ctx):
    return [s async for s in source.stream(asr_text, ctx)]


@pytest.mark.asyncio
async def test_stub_yields_default_four_sentences():
    source = StubReplySource()
    out = await _collect(source, "隨便一句", ReplyContext())
    assert out == ["你好呀，我是企鵝。", "今天天氣真好。", "我們一起來玩遊戲吧。", "你想先做什麼呢？"]


@pytest.mark.asyncio
async def test_stub_yields_custom_sentences():
    source = StubReplySource(sentences=["甲。", "乙。"])
    out = await _collect(source, "x", ReplyContext(directive="測試策略"))
    assert out == ["甲。", "乙。"]
