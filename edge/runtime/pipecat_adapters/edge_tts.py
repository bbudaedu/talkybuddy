# -*- coding: utf-8 -*-
"""edge_tts.py — 把板上既有的 sherpa-onnx VITS 合成包成 pipecat 的 TTSService。

`server/tts.py` 的 `TTSEngine.synth()` 已經處理好聲音載入、espeak 資料、
中英雙語與 22050Hz 重採樣，本 adapter 不重做，只補 pipecat 需要的三件事：

1. `synth()` 回傳**完整 WAV bytes**，pipecat 要的是一串 `TTSAudioRawFrame`（raw PCM）
2. `synth()` 是同步阻塞的，必須挪出 event loop
3. `run_tts()` 只給一段 text，得自己決定要用中文還是英文聲音

## WAV header 必須剝掉

`synth()` 回的是含 44-byte RIFF header 的完整 WAV。直接當成 raw PCM 送出去，
對端會把 header bytes 讀成音訊取樣——開頭會有一聲爆音。這與
`live_client` docstring 記的是同一個坑（「WAV header 只在檔案開頭出現一次，
串流送出去會讓對端把 header bytes 當成音訊取樣」）。這裡用 `wave` 模組解析
而不是硬切 44 bytes，因為 WAV 允許在 `data` 之前插入其他 chunk。

## 這條路徑不是串流的——而且改不動

`TTSEngine.synth()` 一次合成完整段落才回傳，所以 **first-audio 延遲 = 整句
合成時間**，把輸出切成 chunk 並不會讓第一個 byte 早一點出來。切 chunk 仍然值得，
因為那讓輸出可以被 barge-in 中途丟棄、也讓 aplay 的緩衝比較平順。

真正的串流要換後端（雲端 TTS 或支援串流的本地引擎），不在本 adapter 的範圍。
**別把它當成延遲的解藥**——round_total 目前仍由 LLM 的 3.9s 主宰。

## 語言判斷是啟發式的

`run_tts()` 的契約只給一段文字。這裡用「含 CJK 字元 → zh，否則 en」判斷。
中英混排的句子會整段用中文聲音念，英文詞會有腔調。既有 `synth()` 支援
逐段指定語言（`[("zh", ...), ("en", ...)]`），要更好的效果得由上游先分段，
本 adapter 保留 `segments_provider` 讓上游注入那個能力。
"""

from __future__ import annotations

import asyncio
import io
import wave
from collections.abc import AsyncGenerator, Callable

from loguru import logger
from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService

from server.tts import TARGET_RATE, TTSEngine

# 每個 TTSAudioRawFrame 的位元組數。22050Hz×2 bytes = 44100 B/s，
# 4410 bytes ≈ 100ms，與既有 live_client 的下行分塊同量級。
_CHUNK_BYTES = 4410

# 回報給 pipecat settings 的模型識別字串（僅供 log/metrics；實際模型路徑由
# server.tts 的 PIPER_ZH / PIPER_EN 決定，聲音依文字語言動態選）。
_MODEL_ID = "sherpa-onnx-vits-piper"


def _has_cjk(text: str) -> bool:
    """文字是否含中日韓字元（決定要用中文還是英文聲音）。"""
    return any("一" <= ch <= "鿿" for ch in text)


def _wav_to_pcm(wav_bytes: bytes) -> bytes:
    """自 WAV bytes 取出 raw PCM（剝掉 header，見模組 docstring）。"""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        return wf.readframes(wf.getnframes())


class EdgeVitsTTSService(TTSService):
    """以板上既有 sherpa-onnx VITS 實作的 pipecat TTS 服務。"""

    Settings = TTSSettings
    """沿用基底的 settings 形狀——聲音是依語言動態選的，沒有額外參數要暴露。"""

    def __init__(
        self,
        *,
        engine: TTSEngine | None = None,
        sample_rate: int | None = None,
        chunk_bytes: int = _CHUNK_BYTES,
        segments_provider: Callable[[str], list[tuple[str, str]]] | None = None,
        **kwargs,
    ):
        """Initialize the edge VITS TTS service.

        Args:
            engine: Existing engine to share; defaults to a fresh one.
            sample_rate: Output rate. Defaults to the engine's 22050Hz —
                override only if you know the pipeline resamples.
            chunk_bytes: Bytes per emitted audio frame.
            segments_provider: Optional splitter turning one string into
                `[(lang, text), ...]`, for proper mixed zh/en narration.
        """
        # push_start_frame=True 是**必要的，不是選項**：基底類別預設 False，
        # 那表示它不會建立 audio context，而我們 yield 的 TTSAudioRawFrame 帶著
        # 一個不存在的 context_id → **音訊會被靜靜丟棄，下游收到 0 個 frame**。
        # 這個症狀在直接呼叫 run_tts() 的單元測試裡完全看不出來（frame 明明有產出），
        # 只有跑真實 pipeline 才會現形。官方本地 TTS（Piper）同樣是這兩個都設 True。
        super().__init__(
            push_start_frame=True,
            push_stop_frames=True,
            settings=self.Settings(model=_MODEL_ID, voice=None, language=None),
            sample_rate=sample_rate or TARGET_RATE,
            **kwargs,
        )
        self._engine = engine or TTSEngine()
        self._chunk_bytes = chunk_bytes
        self._segments_provider = segments_provider

    def can_generate_metrics(self) -> bool:
        """Report that this service produces TTFB/processing metrics.

        Returns:
            True.
        """
        return True

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        """Synthesize one piece of text into playable audio frames.

        Args:
            text: Text to speak.
            context_id: Pipecat TTS context this audio belongs to.

        Yields:
            TTSAudioRawFrame chunks, or an ErrorFrame when synthesis failed.
        """
        text = (text or "").strip()
        if not text:
            return

        segments = (
            self._segments_provider(text)
            if self._segments_provider
            else [("zh" if _has_cjk(text) else "en", text)]
        )

        await self.start_processing_metrics()
        await self.start_ttfb_metrics()

        wav = await asyncio.to_thread(self._engine.synth, segments)

        await self.stop_ttfb_metrics()

        if not wav:
            # synth 回 None 代表所有語言的 voice 都缺／都失敗。這是設定問題，
            # 不該靜靜地讓玩偶變啞——那個症狀跟麥克風壞掉長得一樣難查。
            await self.stop_processing_metrics()
            yield ErrorFrame(f"邊緣 TTS 合成失敗（segments={segments!r}）")
            return

        try:
            pcm = _wav_to_pcm(wav)
        except Exception:
            await self.stop_processing_metrics()
            logger.exception("TTS 輸出不是合法 WAV，無法取出 PCM")
            yield ErrorFrame("邊緣 TTS 輸出無法解析為 WAV")
            return

        for i in range(0, len(pcm), self._chunk_bytes):
            yield TTSAudioRawFrame(
                audio=pcm[i : i + self._chunk_bytes],
                sample_rate=self.sample_rate,
                num_channels=1,
                context_id=context_id,
            )

        await self.stop_processing_metrics()
