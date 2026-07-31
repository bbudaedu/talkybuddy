# -*- coding: utf-8 -*-
"""lesson_progress.py — 「孩子會了就換下一句」的決策狀態機。

## 為什麼不交給 prompt 判斷

`scaffold.build_live_system_prompt` 已經寫著「孩子跟上就換下一句」，加上
`more_sentences` 之後也給了材料。但 2026-07-31 模擬對話實測，模型要到**第 7 輪**
才換，而且孩子明講兩次「下次教我說 I see a cat 喔」它才動；中間還出現過
「我們馬上就來練習貓咪」然後照樣帶讀 dog 的自相矛盾。

原因不難理解：「孩子連續兩次唸對了嗎」是**跨輪的計數**，那是模型最不可靠的
一種推理，而且指令埋在一段很長的 prompt 裡。

決賽鏡頭 1 只有 60 秒、大概 3～4 輪。「第 7 輪才換」在台上等於**永遠不換**，
評審看不到玩偶會適應孩子——而那正是要展示的東西。

所以照這個專案既有的做法（`failover.FailoverPolicy` 也是純狀態機）：
**計數放程式碼，prompt 每次只拿到「當前這一句」。** 模型不必記得任何事。

## 判斷「唸對了」為什麼不用字串相等

ASR 會有插字與漏標點。2026-07-31 真機把 `I want an apple.` 聽成
`I want to an apple.`——多一個 to。字串相等會判成沒唸對，孩子明明說對了卻
一直被留在同一句，比不換更糟。

所以用**子序列比對**：目標句的每個字依序出現在孩子說的話裡就算數。它容忍
插字（to）、大小寫、標點與前後多餘的話（「I see a dog 老師我累了」），但不
容忍漏字或順序錯亂——那才是真的沒唸對。

## 這個狀態機不做 I/O

它只吃「孩子說了什麼」，不知道什麼叫 pipecat、不碰教材資料庫。所以可以被
完整測試，也可以被別條路徑重用。
"""

from __future__ import annotations

import re

from loguru import logger
from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# 預設連續唸對幾次就換下一句。
#
# 選 1 不是偷懶：決賽鏡頭 1 只有 60 秒，孩子大概講 3～4 句。門檻設 2 的話
# 台上就換不到第二句，「玩偶會依孩子表現前進」這件事就演不出來。
#
# 教學上想更保守（同一句多練幾次再前進）就把它調大——這是一個常數，
# 不是寫死的行為。
DEFAULT_ADVANCE_AFTER = 1

_WORD_RE = re.compile(r"[a-z0-9]+")


def _words(text: str) -> list[str]:
    """取小寫英文字詞；標點與中文一律忽略。"""
    return _WORD_RE.findall(str(text or "").lower())


def says_target(student_text: str, target: str) -> bool:
    """孩子這句話算不算把目標句唸對了。

    子序列比對而非字串相等，理由見模組 docstring（ASR 會插字）。

    Args:
        student_text: 孩子說的話（ASR 逐字稿）。
        target: 目前的目標英文句。

    Returns:
        目標句的每個字都依序出現在 student_text 裡就是 True。
    """
    want = _words(target)
    if not want:
        return False
    got = _words(student_text)
    i = 0
    for w in got:
        if w == want[i]:
            i += 1
            if i == len(want):
                return True
    return False


class LessonProgress:
    """依孩子的表現決定「現在該練哪一句」。"""

    def __init__(
        self,
        sentences: list[str],
        *,
        advance_after: int = DEFAULT_ADVANCE_AFTER,
    ):
        """Initialize the progression state.

        Args:
            sentences: Today's sentences in teaching order. The first one is
                where the session starts. An empty list is tolerated — the
                machine then simply has nothing to advance to.
            advance_after: Consecutive correct repetitions before moving on.

        Raises:
            ValueError: If ``advance_after`` is below 1.
        """
        if advance_after < 1:
            raise ValueError("advance_after 必須 >= 1")
        self._sentences = [str(s).strip() for s in (sentences or []) if str(s).strip()]
        self._advance_after = advance_after
        self._index = 0
        self._streak = 0
        self.last_utterance: str = ""
        """孩子最後說的一句原文。

        `LessonProgress` 本來就收得到每一句，順手留著——下游的
        `TurnRecorderProcessor` 需要它，而那時候 `TranscriptionFrame.text`
        已經被 `LessonPromptInjector` 覆寫成整段 prompt 了。
        """
        self.advances = 0
        """這一場換過幾次句子（給探針與現場報數用）。"""

    @property
    def current(self) -> str | None:
        """The sentence the child should be practising right now.

        Returns:
            The current target sentence, or None when there is no material.
        """
        if not self._sentences:
            return None
        return self._sentences[min(self._index, len(self._sentences) - 1)]

    @property
    def upcoming(self) -> list[str]:
        """The sentences still ahead of the current one.

        Returns:
            Remaining sentences, in teaching order.
        """
        return self._sentences[self._index + 1:]

    @property
    def finished(self) -> bool:
        """Whether every sentence has been practised.

        Returns:
            True once the last sentence has been mastered.
        """
        return bool(self._sentences) and self._index >= len(self._sentences)

    def observe(self, student_text: str) -> bool:
        """記下孩子這一輪說了什麼，必要時前進到下一句。

        Args:
            student_text: 孩子說的話（ASR 逐字稿）。

        Returns:
            這一次呼叫有沒有換句子。
        """
        self.last_utterance = str(student_text or "")
        target = self.current
        if not target:
            return False
        if not says_target(student_text, target):
            # 沒唸對就從頭數。連續才算會，斷掉就重來——這與
            # FailoverPolicy.record_failure 重置連續計數是同一個道理。
            self._streak = 0
            return False
        self._streak += 1
        if self._streak < self._advance_after:
            return False
        self._streak = 0
        if self._index < len(self._sentences):
            self._index += 1
            self.advances += 1
            return True
        return False


class LessonProgressProcessor(FrameProcessor):
    """看著孩子說的話，餵給 :class:`LessonProgress`。

    **擺放位置很重要**：要在 `LessonPromptInjector` **之前**。那個 processor 會
    把 `TranscriptionFrame.text` 覆寫成整段 prompt，之後再看就看不到孩子原本
    說了什麼了（原文雖然被存進 `frame.result`，但依賴那個順序太脆弱）。

    本 processor 只讀不改，不碰任何 frame 的內容。
    """

    def __init__(self, progress: LessonProgress, **kwargs):
        """Initialize the progress observer.

        Args:
            progress: The shared state machine. The pipeline's target/system
                providers should read ``progress.current`` so everything moves
                together.
        """
        super().__init__(**kwargs)
        self._progress = progress

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Feed each transcript to the progression state machine.

        Args:
            frame: The frame flowing through the pipeline.
            direction: Frame direction.
        """
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and frame.text and frame.text.strip():
            before = self._progress.current
            if self._progress.observe(frame.text):
                logger.info(
                    "孩子唸對了「{}」，換下一句「{}」", before, self._progress.current
                )
        await self.push_frame(frame, direction)
