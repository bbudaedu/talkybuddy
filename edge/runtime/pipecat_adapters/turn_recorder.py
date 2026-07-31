# -*- coding: utf-8 -*-
"""turn_recorder.py — 把每一輪對話落地，讓「上次」有東西可以記得。

## 為什麼需要

`profile.build_profile` 會從 `interactions` 算出孩子的興趣、正在學的字、
常犯的錯與情緒；`child_brief` 再把它變成玩偶開場的記憶。但**那條鏈的起點是
互動紀錄**——而 pipecat 這條路從來沒有寫過任何一筆。

也就是說：不接這一層，玩偶永遠是第一次見到每個孩子。

`server/app.py::_store_live_turn` 已經為 `/ws/live` 做過同一件事，本模組沿用
它的紀錄形狀，但**欄位名以 `store.py:820` 的示範資料為準**，因為那才是所有
讀取端真正吃的形狀。source 標成 `pipecat` 以便分辨資料來自哪條路徑。

⚠️ **`_store_live_turn` 的欄位名是錯的**（2026-07-31 查出）：

| 它寫的 | 讀取端要的 | 誰在讀 |
|---|---|---|
| `asr_text` | `student_text` | `profile.build_profile`（profile.py:112） |
| `reply_text` | `ai_response_text` | 同上（profile.py:113） |
| `asr_conf` | `asr_confidence` | profile.py:114、`srs`、`diagnose` |

三個名字全不合。不會報錯，只會讓那條路徑產生的互動**完全不進畫像**——
玩偶記得「聊過幾次」，卻永遠說不出「上次我們練過哪個字」。本模組寫正確的
名字並以測試釘住；`_store_live_turn` 那邊是 `/ws/live` 路徑、決賽前不動，
但**這個問題要記著**。

## 落地失敗絕不影響對話

寫資料庫是「為了下一次」，而孩子正在等這一次的回答。任何例外都只記 log。
這與 `_store_live_turn` 的處置相同。

## 擺放位置

要在 **LLM 之後**（才看得到玩偶說了什麼）。孩子說的話由
`student_text_provider` 提供——`TranscriptionFrame` 在上游就被
`LessonPromptInjector` 覆寫成整段 prompt 了，這裡拿不到原文。
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


class TurnRecorderProcessor(FrameProcessor):
    """每完成一輪就寫一筆互動紀錄。"""

    def __init__(
        self,
        *,
        student_text_provider: Callable[[], str | None],
        add_interaction: Callable[[dict], object] | None = None,
        source: str = "pipecat",
        **kwargs,
    ):
        """Initialize the turn recorder.

        Args:
            student_text_provider: Returns what the child said this turn. Pass
                something that saw the raw transcript before the lesson prompt
                overwrote it.
            add_interaction: Defaults to ``server.store.add_interaction``.
                Injected in tests so they never touch a real database.
            source: Stamped on each record so the data's origin stays visible.
        """
        super().__init__(**kwargs)
        self._student_text_provider = student_text_provider
        self._add_interaction = add_interaction
        self._source = source
        self._buffer: list[str] = []
        self.written = 0
        """成功寫入幾筆（給探針與現場報數用）。"""

    def _store(self, record: dict) -> None:
        if self._add_interaction is not None:
            self._add_interaction(record)
            return
        from server import store

        store.add_interaction(record)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Accumulate the reply and write one record per completed turn.

        Args:
            frame: The frame flowing through the pipeline.
            direction: Frame direction.
        """
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._buffer.clear()
        elif isinstance(frame, LLMTextFrame):
            self._buffer.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            reply = "".join(self._buffer).strip()
            self._buffer.clear()
            try:
                asr_text = (self._student_text_provider() or "").strip()
            except Exception:
                logger.exception("取不到孩子的逐字稿，本輪不落地")
                asr_text = ""
            if asr_text or reply:
                try:
                    self._store({
                        # 欄位名以 store.py:820 的示範資料為準——那是所有
                        # 讀取端（profile / srs / diagnose）真正吃的形狀。
                        "student_text": asr_text,
                        # 欄位名是 asr_confidence，**不是** asr_conf。
                        # profile.build_profile / srs / diagnose 三處讀的都是
                        # 前者；寫錯名字不會報錯，只會讓信心值一律當成 0.0，
                        # 於是沒有任何字被算進 learning/mastered_vocab——
                        # 玩偶記得「聊過幾次」，卻永遠說不出「上次我們練過
                        # 哪個字」。2026-07-31 就是這樣被咬到的。
                        "asr_confidence": 1.0,
                        "ai_response_text": reply,
                        "scores": {},
                        "source": self._source,
                    })
                    self.written += 1
                except Exception:
                    # 寫入是「為了下一次」，孩子正在等這一次的回答。不可以擋。
                    logger.exception("互動紀錄落地失敗（不影響對話）")

        await self.push_frame(frame, direction)
