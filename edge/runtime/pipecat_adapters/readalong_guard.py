# -*- coding: utf-8 -*-
"""readalong_guard.py — 確保每則回覆都帶讀了正確的目標英文句。

## 為什麼要有

現行 `EdgeLLM.generate` 的最後一步是 `guardrails.ensure_readalong(text, target)`，
它保證回覆恰好含一句合規的「跟我說一遍：<目標英文句>」。pipecat 版原本沒有這一關。

那個護欄不是理論上的謹慎：`edge/PR7_MERGE_VALIDATION_2026-07-29.md` §三 記著
兩個真機案例——`<>` 包裹時舊的子字串比對判為「有帶讀」而不補正（格式跑掉沒人管），
中文句號時判為「沒帶讀」而重複補一次（同一句被唸兩遍）。

## 串流下只能補、不能改（誠實限制）

`ensure_readalong` 的完整行為包含**刪除**：它會清掉「要孩子跟讀中文」的錯誤帶讀句
（2026-07-29 真機出現過「跟我說一遍：我看到一隻兔子。」），也會清掉格式跑掉的那句。

但在串流管線裡，**前面的句子早就送進 TTS 念出去了**，刪不掉。所以本 processor：

| 情況 | 串流下能做什麼 |
|---|---|
| 已合規 | 什麼都不做（多數情況） |
| 漏了帶讀句 | ✅ 在結尾補一句合規的 |
| 帶讀了錯的句子／格式跑掉 | ⚠️ 只能**再補一句正確的**，錯的那句已經念出去了 |

第三種情況孩子會聽到兩句帶讀（一錯一對）。**這是換取 first-audio 提前的代價。**
若哪天判定「寧可慢也不能念錯」，做法是在本 processor 緩衝整則回覆、
等 `LLMFullResponseEndFrame` 再一次送 TTS——那會失去串流重疊，是明確的取捨，
不是可以兩全的事。

## 放在哪

`LLM → SafetyGate → ReadalongGuard → TTS`。要在 TTS 之前，補的句子才唸得出來。
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

TargetProvider = Callable[[], str | None]
"""回傳本輪目標英文句的可呼叫物件。"""


class ReadalongGuardProcessor(FrameProcessor):
    """累積整則 LLM 回覆，結尾檢查帶讀句，缺了就補。"""

    def __init__(
        self,
        *,
        target: str | None = None,
        target_provider: TargetProvider | None = None,
        ensure_fn: Callable[[str, str | None], str] | None = None,
        **kwargs,
    ):
        """Initialize the read-along guard.

        Args:
            target: Fixed target sentence, used when no provider is given.
            target_provider: Called once per response to fetch the current
                target sentence (mirrors ``server.lesson.build_lesson``).
            ensure_fn: Defaults to ``guardrails.ensure_readalong``. Injected in tests.
        """
        super().__init__(**kwargs)
        self._target = target
        self._target_provider = target_provider
        self._ensure_fn = ensure_fn
        self._buffer: list[str] = []

    def _current_target(self) -> str | None:
        if self._target_provider is None:
            return self._target
        try:
            return self._target_provider()
        except Exception:
            logger.exception("target_provider 失敗，改用固定 target")
            return self._target

    def _ensure(self, text: str, target: str | None) -> str:
        if self._ensure_fn is not None:
            return self._ensure_fn(text, target)
        try:
            from server import guardrails

            return guardrails.ensure_readalong(text, target)
        except Exception:
            # 護欄不可用時維持原文：少一句帶讀，總比讓對話中斷好。
            logger.exception("guardrails.ensure_readalong 不可用，本輪不補帶讀句")
            return text

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Accumulate LLM text and append a compliant read-along if missing.

        Args:
            frame: The frame flowing through the pipeline.
            direction: Frame direction.
        """
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._buffer.clear()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMTextFrame):
            self._buffer.append(frame.text)
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            spoken = "".join(self._buffer).strip()
            self._buffer.clear()
            target = self._current_target()
            if spoken and target:
                fixed = self._ensure(spoken, target)
                if fixed != spoken:
                    if fixed.startswith(spoken):
                        # 純追加：把差額補送出去，TTS 會接著唸。
                        addition = fixed[len(spoken) :].strip()
                        if addition:
                            logger.debug(f"ReadalongGuard 補上帶讀句：{addition}")
                            await self.push_frame(LLMTextFrame(addition), direction)
                    else:
                        # 需要刪改——串流下改不動已唸出的內容，只能補一句正確的。
                        logger.warning(
                            "ReadalongGuard：回覆的帶讀句不合規，但已經唸出去了；"
                            "只能追加一句正確的（孩子會聽到兩句帶讀）"
                        )
                        await self.push_frame(
                            LLMTextFrame(f"跟我說一遍：{target}"), direction
                        )
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)
