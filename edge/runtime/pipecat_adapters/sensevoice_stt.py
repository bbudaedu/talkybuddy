# -*- coding: utf-8 -*-
"""sensevoice_stt.py — 把板上既有的 SenseVoice ASR 包成 pipecat 的 STTService。

板子上 `models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/` 已經有模型，
`server/asr_sensevoice.py` 已經有載入邏輯、鎖、以及 OpenCC 簡轉繁。本 adapter
**不重新實作任何一項**，只補 pipecat 需要的兩件事：

1. `transcribe()` 吃的是 **wav 檔路徑**，`run_stt()` 拿到的是 **PCM bytes**
2. sherpa 的 decode 是阻塞的 native 呼叫，必須挪出 event loop

`_ensure_model()` 是 `CONTRACTS.md` 明列的公開契約（見 `asr_sensevoice.py`
docstring 第 3 行），所以這裡直接重用它拿到同一個 recognizer 單例，
不會多載入一份模型吃掉板子本就吃緊的 RAM。

## 為什麼一定要 to_thread

`recognizer.decode_stream()` 進 sherpa 的 native 層，是同步阻塞的。pipecat 的
pipeline 全部跑在單一 event loop 上——**在那裡阻塞等於同時凍住 VAD、音訊讀取
與播放**。對回合式 HTTP 服務這頂多是慢，對即時語音是災難：麥克風的 arecord
stdout 會塞住、下行音訊會斷。所以一律 `asyncio.to_thread`。

引擎自己的 `self._lock` 會把並行的 decode 序列化，執行緒安全由它保證。

## 沿用「空結果即雜音」的判斷

SenseVoice 是非自回歸模型，沒有 avg_logprob 可當信心分數，既有實作因此以
「辨識結果為空」當作雜音兜底。這裡照舊：空字串就不吐 TranscriptionFrame，
避免把環境噪音送進 LLM——決賽會場很吵，這條兜底很重要。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import numpy as np
from loguru import logger
from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.stt_service import STTService
from pipecat.utils.time import time_now_iso8601

from server.asr_sensevoice import SenseVoiceASREngine

# int16 → float32 [-1.0, 1.0]。與 asr_sensevoice._read_wav 用 soundfile
# 讀出的 float32 值域一致，模型才會拿到它預期的輸入。
_INT16_MAX = 32768.0


class SenseVoiceSTTService(STTService):
    """以板上既有 SenseVoice 引擎實作的 pipecat STT 服務。"""

    def __init__(
        self,
        *,
        engine: SenseVoiceASREngine | None = None,
        sample_rate: int | None = None,
        user_id: str = "",
        **kwargs,
    ):
        """Initialize the SenseVoice STT service.

        Args:
            engine: Existing engine to share. Defaults to a fresh one; pass the
                server's instance to avoid loading the model twice.
            sample_rate: Pipeline sample rate (SenseVoice expects 16kHz).
            user_id: User id stamped onto emitted TranscriptionFrames.
        """
        super().__init__(sample_rate=sample_rate, **kwargs)
        self._engine = engine or SenseVoiceASREngine()
        self._user_id = user_id

    def can_generate_metrics(self) -> bool:
        """Report that this service produces TTFB/processing metrics.

        Returns:
            True.
        """
        return True

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        """Transcribe one utterance of raw PCM.

        Args:
            audio: Raw int16 mono PCM at the pipeline's sample rate.

        Yields:
            A TranscriptionFrame when speech was recognised; nothing when the
            audio was empty or judged to be noise; an ErrorFrame if the engine
            is unavailable.
        """
        if not audio:
            return

        recognizer = self._engine._ensure_model()
        if recognizer is None:
            # 模型載不起來是設定問題，不是這一輪的問題——講清楚，別靜靜吞掉。
            yield ErrorFrame("SenseVoice 模型無法載入（模型檔缺失或 sherpa-onnx 不可用）")
            return

        await self.start_processing_metrics()
        await self.start_ttfb_metrics()

        text = await asyncio.to_thread(self._transcribe_pcm, recognizer, audio)

        await self.stop_ttfb_metrics()
        await self.stop_processing_metrics()

        if not text:
            # 空結果 = 雜音兜底（見模組 docstring）。不吐 frame，讓 LLM 不必理會噪音。
            logger.debug("SenseVoice 回空字串，視為雜音，不推 TranscriptionFrame")
            return

        yield TranscriptionFrame(text, self._user_id, time_now_iso8601())

    def _transcribe_pcm(self, recognizer, audio: bytes) -> str:
        """在工作執行緒裡做同步辨識，回傳繁體文字（失敗回空字串，不拋）。"""
        try:
            samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / _INT16_MAX
            # 與 asr_sensevoice.transcribe 相同：native 呼叫用引擎自己的鎖序列化。
            with self._engine._lock:
                stream = recognizer.create_stream()
                stream.accept_waveform(self.sample_rate, samples)
                recognizer.decode_stream(stream)
                text = (stream.result.text or "").strip()
            if not text:
                return ""
            cc = self._engine._ensure_opencc()
            if cc is not None:
                try:
                    text = cc.convert(text).strip()
                except Exception:
                    # 簡轉繁失敗就出簡體，總比整輪對話掉了好（沿用既有降級策略）。
                    pass
            return text
        except Exception:
            logger.exception("SenseVoice 辨識失敗，本輪視為無語音")
            return ""
