"""BatchReplySource：包現有批次大腦（scaffold + 一次性 LLM）成 ReplySource。

過渡實作，讓非串流大腦也能掛上串流迴路：一次算完整段回覆 → split_sentences
切句 → 逐句 yield。llm.generate 無模型/逾時/安全命中回 None → 降級用 scaffold 文字。
真串流 EdgeLLM / Bedrock FM 各自另立一個 ReplySource（不走本類）。

注入點（實測 2026-07-08）：server.llm 無模組級 generate、類別為 EdgeLLM，
其實例有 generate(student_text, scaffold, directive) -> str | None。故預設注入
EdgeLLM()（lazy 載模型、無模型時 generate 回 None，天然滿足降級）。測試可注入
任何具同名 generate 的 stub。
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

from server.llm import EdgeLLM
from server.scaffold import respond
from server.streaming.interruptible_synth import split_sentences
from server.streaming.reply_source import ReplyContext


class BatchReplySource:
    """包 scaffold.respond + 批次 llm.generate，切句後逐句 yield。"""

    def __init__(self, llm=None) -> None:
        # llm：任何具 generate(student_text, scaffold, directive) 的物件；預設 EdgeLLM 實例。
        self._llm = llm if llm is not None else EdgeLLM()

    async def stream(self, asr_text: str, ctx: ReplyContext) -> AsyncIterator[str]:
        # scaffold + llm 皆為阻塞呼叫 → 丟 thread，避免卡 event loop。
        scaffold = await asyncio.to_thread(respond, asr_text)
        enriched = await asyncio.to_thread(
            self._llm.generate, asr_text, scaffold, ctx.directive
        )
        full = enriched if (enriched and enriched.strip()) else scaffold.reply_text
        for sentence in split_sentences(full):
            yield sentence
