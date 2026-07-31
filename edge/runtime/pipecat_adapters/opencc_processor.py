# -*- coding: utf-8 -*-
"""opencc_processor.py — 把 LLM 吐出的簡體字轉成繁體（台灣用詞）。

## 為什麼需要

板上的 llama-server 跑 qwen2.5-1.5b，**回覆是簡體的**——實測問它「蘋果的英文」，
它回「苹果的英文是 apple」。ASR 那條路徑早就有 OpenCC（`asr_sensevoice.py`），
但 LLM→TTS 這條沒有。

## 為什麼放在 TTS **之後**，而不是之前

2026-07-31 閉環實測（TTS 合成 → 用 SenseVoice 回頭辨識）：

| 輸入 | 聽成 | | 輸入 | 聽成 |
|---|---|---|---|---|
| 蘋果 | 苹果。 | | 苹果 | 苹果。 |
| 跟我說一遍 | 跟我说一遍。 | | 跟我说一遍 | 跟我说一遍。 |
| 你說得很棒 | 你说的很棒。 | | 你说得很棒 | 你说的很棒。 |

**繁簡念出來的內容完全一致，沒有漏字**（音長差約 0.1s 是韻律差異）。
`zh_CN-huayan-medium` 兩種字體都念得對，**所以發音層面不需要轉換**。

真正需要繁體的是**給人看的文字**：逐字稿、存進 store 的對話紀錄、家長看的週報。
那些來自 `TTSTextFrame`——而 pipecat 的 `TTSTextFrame` 帶的是**原始未轉換文字**
（`tts_service.py:1182` 刻意如此，避免 TTS 專用標記污染對話 context），
所以官方的 `text_transforms` 鉤子改不到它。

## 為什麼不放在 LLM 之後、TTS 之前

那裡的 `LLMTextFrame` 是**串流 token 片段**，不是完整句子。OpenCC 的 `s2twp`
含詞彙轉換（如「軟件」→「軟體」），**逐 token 轉會破壞詞彙邊界**。

`TTSTextFrame` 則是 TTSService 聚合過的完整句子，轉換結果正確。

## 轉換本身一律委派給 `guardrails.to_traditional`

專案早就有這個函式（`server/guardrails.py:113`），而且它的 docstring 記著
2026-07-29 的實測背景：edge LLM 回過「看到一只兔子」，簡體用字直接進字幕。

本模組**只負責決定「在 pipeline 的哪個位置、對哪種 frame」轉換**，
轉換規則（`s2twp`、失敗回原文、英文不受影響）一律用那一份，
不在這裡另寫第二份會漂移的實作。
"""

from __future__ import annotations

from loguru import logger
from pipecat.frames.frames import Frame, TTSTextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class OpenCCProcessor(FrameProcessor):
    """把通過的 `TTSTextFrame` 轉成繁體（台灣用詞）。"""

    def __init__(self, *, converter=None, **kwargs):
        """Initialize the OpenCC processor.

        Args:
            converter: Optional callable taking and returning ``str``, used
                instead of ``guardrails.to_traditional``. For tests only —
                production should keep the shared implementation.
        """
        super().__init__(**kwargs)
        self._converter = converter

    def convert(self, text: str) -> str:
        """Convert one string to Traditional Chinese.

        Args:
            text: Source text, possibly Simplified.

        Returns:
            The converted text, or the original when conversion is unavailable
            or raised (`guardrails.to_traditional` already degrades that way).
        """
        if not text:
            return text
        if self._converter is not None:
            try:
                return self._converter(text)
            except Exception:
                logger.exception("注入的轉換器失敗，本句維持原文")
                return text
        try:
            from server import guardrails

            return guardrails.to_traditional(text)
        except Exception:
            # guardrails 自己已經對 opencc 缺失做過降級；能走到這裡代表連
            # import 都失敗（例如精簡部署）。維持原文，不讓對話中斷。
            logger.exception("guardrails.to_traditional 不可用，本句維持原文")
            return text

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Convert TTSTextFrame text in place, pass everything else through.

        Args:
            frame: The frame flowing through the pipeline.
            direction: Frame direction.
        """
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSTextFrame) and frame.text:
            converted = self.convert(frame.text)
            if converted != frame.text:
                frame.text = converted
        await self.push_frame(frame, direction)
