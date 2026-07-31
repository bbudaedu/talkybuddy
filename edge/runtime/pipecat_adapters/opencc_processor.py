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

## 降級策略

沿用 `asr_sensevoice._ensure_opencc` 的既定做法：OpenCC 缺失或轉換失敗一律
**回原文、不拋例外**。簡體逐字稿雖然不理想，但總比整場對話掛掉好。
"""

from __future__ import annotations

from loguru import logger
from pipecat.frames.frames import Frame, TTSTextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class OpenCCProcessor(FrameProcessor):
    """把通過的 `TTSTextFrame` 轉成繁體（台灣用詞）。"""

    def __init__(self, *, config: str | None = None, **kwargs):
        """Initialize the OpenCC processor.

        Args:
            config: OpenCC config name. Defaults to the project-wide
                ``server.config.OPENCC_CONFIG`` (``s2twp``), so there is a single
                source of truth for how the whole product converts text.
        """
        super().__init__(**kwargs)
        if config is None:
            try:
                from server.config import OPENCC_CONFIG

                config = OPENCC_CONFIG
            except Exception:
                config = "s2twp"
        self._config = config
        self._converter = None
        self._load_failed = False

    def _ensure_converter(self):
        """懶載入 OpenCC 轉換器；缺失／失敗回 None（不拋，比照 asr_sensevoice）。"""
        if self._converter is not None:
            return self._converter
        if self._load_failed:
            return None
        try:
            import opencc

            self._converter = opencc.OpenCC(self._config)
        except Exception:
            self._load_failed = True
            logger.warning(f"OpenCC({self._config}) 載入失敗，逐字稿將維持原文（不影響發音）")
            return None
        return self._converter

    def convert(self, text: str) -> str:
        """Convert one string to Traditional Chinese.

        Args:
            text: Source text, possibly Simplified.

        Returns:
            The converted text, or the original when OpenCC is unavailable or
            conversion raised.
        """
        if not text:
            return text
        cc = self._ensure_converter()
        if cc is None:
            return text
        try:
            return cc.convert(text)
        except Exception:
            logger.exception("OpenCC 轉換失敗，本句維持原文")
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
