"""BatchReplySource 單元測試：包批次後切句正確；LLM 回 None 時降級用 scaffold。"""
from dataclasses import dataclass

import pytest

from server.streaming.batch_reply_source import BatchReplySource
from server.streaming.reply_source import ReplyContext


@dataclass
class _FakeScaffold:
    reply_text: str
    target_sentence: str | None = None


class _FakeLLM:
    def __init__(self, out):
        self._out = out

    def generate(self, student_text, scaffold, directive=None):
        return self._out


async def _collect(source, asr_text, ctx):
    return [s async for s in source.stream(asr_text, ctx)]


@pytest.mark.asyncio
async def test_uses_llm_output_split_into_sentences(monkeypatch):
    import server.streaming.batch_reply_source as mod
    monkeypatch.setattr(mod, "respond", lambda t: _FakeScaffold(reply_text="鷹架句。"))
    source = BatchReplySource(llm=_FakeLLM("你好。今天天氣真好。"))
    out = await _collect(source, "學生說的話", ReplyContext())
    assert out == ["你好。", "今天天氣真好。"]


@pytest.mark.asyncio
async def test_falls_back_to_scaffold_when_llm_none(monkeypatch):
    import server.streaming.batch_reply_source as mod
    monkeypatch.setattr(mod, "respond", lambda t: _FakeScaffold(reply_text="很棒喔。跟我說一遍：apple。"))
    source = BatchReplySource(llm=_FakeLLM(None))
    out = await _collect(source, "蘋果", ReplyContext())
    assert out == ["很棒喔。", "跟我說一遍：apple。"]
