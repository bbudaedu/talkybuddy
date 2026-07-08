"""可抽換回覆句流介面（⚠️ 大腦免疫接縫）。

StreamingTurnManager 只依賴 ReplySource.stream；日後本地串流 EdgeLLM、
雲端 Bedrock FM 各實作一個 ReplySource，turn_manager 不動。對 research/16
（大腦換 Bedrock FM）決策免疫。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol, runtime_checkable

_STUB_REPLY = ["你好呀，我是企鵝。", "今天天氣真好。", "我們一起來玩遊戲吧。", "你想先做什麼呢？"]


@dataclass
class ReplyContext:
    """一輪回覆的上下文；不含任何 Pipecat 框架型別（保持 ReplySource 框架無關）。"""

    directive: str | None = None
    scaffold_result: object | None = None


@runtime_checkable
class ReplySource(Protocol):
    """逐句 yield 回覆文字（一次一句，供 InterruptibleSynth 立即合成）。"""

    def stream(self, asr_text: str, ctx: ReplyContext) -> AsyncIterator[str]:
        ...


class StubReplySource:
    """yield 固定多句中文（harness 用，確保有句界可打斷）。"""

    def __init__(self, sentences: list[str] | None = None) -> None:
        self._sentences = list(sentences) if sentences is not None else list(_STUB_REPLY)

    async def stream(self, asr_text: str, ctx: ReplyContext) -> AsyncIterator[str]:
        for sentence in self._sentences:
            yield sentence
