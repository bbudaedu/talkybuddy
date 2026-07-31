# -*- coding: utf-8 -*-
"""safety_gate.py — LLM 輸出的兒童安全過濾，攔在念出來之前。

## 為什麼要有，以及為什麼不能放在 TTS 之後

現行 `EdgeLLM.generate` 拿到 LLM 回覆後會先跑 `guardrails.passes_guardrail`，
不通過就整段丟掉、降級回 scaffold 的確定性回覆。pipecat 版原本**完全沒有這一關**。

而且它**必須在 TTS 之前**：不安全的句子一旦進了 TTS，就已經合成、送進 aplay
緩衝、從喇叭出來了——那時候再攔沒有意義。

## 為什麼要自己聚合句子

LLM 輸出是**串流 token**（`LLMTextFrame` 一次可能只有兩三個字）。
逐 token 送去比對禁詞會兩頭落空：詞被切開就比不中，而單獨的字又容易誤判。

所以這裡用 pipecat 內建的 `SimpleTextAggregator` 累積到句子邊界才檢查，
**通過整句才放行**。副作用是下游拿到的是整句而非 token——但 TTS 本來就要
聚合成句子才合成，所以 first-audio 不受影響。

## 不通過的時候

丟棄該回合**後續所有** LLM 文字（不是只丟那一句——同一則回覆裡出現一句
不安全內容，後面的也不該信任），改送 `fallback_text`。

`fallback_text` 應該由呼叫端帶入 scaffold 的確定性回覆，行為才跟現行一致。

## 一個刻意保留的保守行為

`guardrails.passes_guardrail` 在**安全模組不可用時回 False**（寧可降級也不放行
未過濾內容）。本 processor 沿用那個判斷，代價是：安全模組壞掉時，每一輪都會
變成 fallback 罐頭回覆。**那是刻意的**——但會 log warning，因為那個症狀
（玩偶突然只會講同一句話）不看 log 很難猜到原因。
"""

from __future__ import annotations

from collections.abc import Callable

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.utils.text.simple_text_aggregator import SimpleTextAggregator


class SafetyGateProcessor(FrameProcessor):
    """句子級的兒童安全閘門，攔在 TTS 之前。"""

    def __init__(
        self,
        *,
        fallback_text: str = "我們先練今天的句子好不好？",
        safety_check: Callable[[str], bool] | None = None,
        **kwargs,
    ):
        """Initialize the safety gate.

        Args:
            fallback_text: Spoken instead when a sentence is rejected. Pass the
                scaffold's deterministic reply here so behaviour matches the
                current `EdgeLLM.generate` degradation path.
            safety_check: Callable returning True when text is safe. Defaults to
                ``guardrails.passes_guardrail``. Injected in tests.
        """
        super().__init__(**kwargs)
        self._fallback_text = fallback_text
        self._safety_check = safety_check
        self._aggregator = SimpleTextAggregator()
        self._blocked = False
        self._warned_unavailable = False

    def _reset_aggregator(self) -> None:
        """清空句子緩衝。

        刻意建新實例而不是呼叫 `reset()`——`SimpleTextAggregator` **沒有**那個方法
        （只有 `handle_interruption`）。先前誤呼叫 `reset()` 時它既不生效也不報錯，
        結果上一則被擋下的殘句留在緩衝裡，把**下一則**回覆也一起汙染成不安全。
        """
        self._aggregator = SimpleTextAggregator()

    def _is_safe(self, text: str) -> bool:
        """檢查一句話是否安全；安全模組不可用時保守回 False（沿用既有策略）。"""
        if self._safety_check is not None:
            return bool(self._safety_check(text))
        try:
            from server import guardrails

            safe = guardrails.passes_guardrail(text)
        except Exception:
            safe = False
        if not safe and not self._warned_unavailable:
            # 只警告一次，免得每句刷版；但一定要講，因為「玩偶只會講同一句話」
            # 這個症狀不看 log 猜不到是安全模組壞了。
            self._warned_unavailable = True
            logger.warning(
                "SafetyGate 擋下 LLM 輸出。若持續發生，先確認 guardrails/scaffold "
                "是否可用——安全模組不可用時本閘門一律不放行。"
            )
        return safe

    async def _emit(self, text: str, direction: FrameDirection) -> None:
        if text.strip():
            await self.push_frame(LLMTextFrame(text), direction)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Gate LLM text sentence by sentence.

        Args:
            frame: The frame flowing through the pipeline.
            direction: Frame direction.
        """
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._blocked = False
            self._reset_aggregator()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMTextFrame):
            if self._blocked:
                return  # 該回合已被擋下，後續文字一律丟棄
            async for aggregation in self._aggregator.aggregate(frame.text):
                sentence = aggregation.text
                if not sentence.strip():
                    continue
                if self._is_safe(sentence):
                    await self._emit(sentence, direction)
                else:
                    logger.warning("SafetyGate 攔下不安全句子，改送 fallback")
                    self._blocked = True
                    self._reset_aggregator()
                    await self._emit(self._fallback_text, direction)
                    return
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            # 回覆結束時把還沒湊成完整句子的殘餘吐出去（LLM 可能沒有以標點收尾）。
            if not self._blocked:
                tail = self._aggregator.text.text
                if tail.strip():
                    if self._is_safe(tail):
                        await self._emit(tail, direction)
                    else:
                        logger.warning("SafetyGate 攔下結尾殘句，改送 fallback")
                        await self._emit(self._fallback_text, direction)
            self._reset_aggregator()
            self._blocked = False
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)
