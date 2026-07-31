# -*- coding: utf-8 -*-
"""lesson_prompt.py — 把教材（目標句／教學策略）注入送給 LLM 的 user message。

## 為什麼需要（2026-07-31 實測發現的缺口）

pipecat 的 context aggregator 會**直接把 `TranscriptionFrame` 的文字當成
user message**。全鏈路實測的結果是：

```
孩子說：我想要蘋果
玩偶回：跟我說一遍：我想要蘋果。      ← 目標句從英文掉成中文
```

因為模型根本沒收到目標英文句，也沒收到本輪的教學策略。對照
`server/llm.py::build_user_prompt`，真正該送進去的是：

```
學生剛剛說：「{student_text}」
目標英文句：{target}
{directive}
請照規則回覆：先一句繁體中文稱讚鼓勵，再用「跟我說一遍：<英文句>」帶讀目標英文句。
```

**這就是「可控」那一半價值要動手的地方。** 沒有它，pipecat 版的教學品質會比
現行架構更差——現行的 `EdgeLLM.generate` 一直都有帶目標句。

模板一律向 `server.llm.build_user_prompt` 借，不在這裡另寫一份會漂移的版本。

## 原始逐字稿不能被蓋掉

孩子實際說的話要存進對話紀錄（現行 `_store_live_turn` 就是這麼做的）。
本 processor 覆寫 `frame.text` 之前，會把原文放進 `frame.result`
（`TranscriptionFrame` 內建欄位，原本用來放服務原始輸出，這裡借來保留原文）。

**擺放順序很重要**：要放在「記錄逐字稿的處理器」之後、`context_aggregator.user()`
之前。放錯位置的話，存進紀錄的會是整段 prompt 而不是孩子講的話。
"""

from __future__ import annotations

from collections.abc import Callable

from loguru import logger
from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from server.llm import build_user_prompt

LessonProvider = Callable[[], tuple[str | None, str | None]]
"""回傳 `(target_sentence, directive)` 的可呼叫物件，每輪取一次最新教材。"""


class LessonPromptInjector(FrameProcessor):
    """把 `TranscriptionFrame` 的文字換成含教材的完整 user prompt。"""

    def __init__(
        self,
        *,
        lesson_provider: LessonProvider | None = None,
        target: str | None = None,
        directive: str | None = None,
        **kwargs,
    ):
        """Initialize the lesson prompt injector.

        Args:
            lesson_provider: Called once per utterance to fetch the current
                ``(target_sentence, directive)``. Use this in production so the
                lesson can change between turns (mirrors
                ``server.lesson.build_lesson``).
            target: Fixed target sentence, used when no provider is given.
            directive: Fixed teaching directive, used when no provider is given.
        """
        super().__init__(**kwargs)
        self._lesson_provider = lesson_provider
        self._target = target
        self._directive = directive

    def _current_lesson(self) -> tuple[str | None, str | None]:
        """取得本輪教材；provider 失敗時退回固定值，絕不讓對話中斷。"""
        if self._lesson_provider is None:
            return self._target, self._directive
        try:
            target, directive = self._lesson_provider()
            return target, directive
        except Exception:
            # 教材取不到就用上一次的固定值——回覆會少了目標句，但對話還在。
            logger.exception("lesson_provider 失敗，改用固定 target/directive")
            return self._target, self._directive

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Rewrite TranscriptionFrame text into a full teaching prompt.

        Args:
            frame: The frame flowing through the pipeline.
            direction: Frame direction.
        """
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and frame.text and frame.text.strip():
            student_text = frame.text
            target, directive = self._current_lesson()
            # 原文先留起來再覆寫，否則對話紀錄會存到整段 prompt。
            if frame.result is None:
                frame.result = student_text
            frame.text = build_user_prompt(student_text, target, directive)
        await self.push_frame(frame, direction)
