# -*- coding: utf-8 -*-
"""sensevoice_stt.py — 把板上既有的 SenseVoice ASR 包成 pipecat 的 STTService。

板子上 `models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/` 已經有模型，
`server/asr_sensevoice.py` 已經有載入邏輯、鎖、以及 OpenCC 簡轉繁。本 adapter
**不重新實作任何一項**，只補 pipecat 需要的兩件事：

1. `transcribe()` 吃的是 **wav 檔路徑**，`run_stt()` 拿到的是 **PCM bytes**
2. sherpa 的 decode 是阻塞的 native 呼叫，必須挪出 event loop

## 為什麼繼承 `SegmentedSTTService` 而不是 `STTService`

這一條錯了會壞得很難看，值得寫清楚。

`STTService` 是給**串流式** ASR 用的：它對收到的**每一個** `AudioRawFrame`
（約 20ms）都呼叫一次 `run_stt`。SenseVoice 是**離線非自回歸**模型，
一次要吃完整一段語句、單次推論約 147ms。兩者湊在一起會同時壞兩件事：

- 每 20ms 觸發一次 147ms 的推論 → 佇列直接爆掉
- 每次只拿到 20ms 音訊 → 辨識結果沒有意義

`SegmentedSTTService` 才是離線模型的基底：它靠 VAD 事件切出語句段落，
只在說完一句之後呼叫一次 `run_stt`。它另外維護一個**前置緩衝**來補償
「實際開口」與「VAD 判定開口」之間的延遲——這正好對應
`project-edge-s2s-tuning` 記的痛點（孩子話音剛落就跟讀，開頭會被吃掉）。

`wants_wav_segments` 覆寫成 `False`：那是基底類別給本地模型的明確契約
（預設包成 WAV 是為了雲端 API 的上傳格式，我們直接吃 raw PCM）。

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
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.utils.time import time_now_iso8601

from server.asr_sensevoice import SenseVoiceASREngine

# int16 → float32 [-1.0, 1.0]。與 asr_sensevoice._read_wav 用 soundfile
# 讀出的 float32 值域一致，模型才會拿到它預期的輸入。
_INT16_MAX = 32768.0

# 回報給 pipecat settings 的模型識別字串（僅供 log/metrics 辨識，不影響載入；
# 實際模型路徑由 server.config.SENSEVOICE_DIR 決定）。
_MODEL_ID = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"

# 「使用者說完」到「拿到逐字稿」的 p99 秒數，pipecat 用它排程。
# 不填的話它預設 1.0s——比實測慢了將近一個數量級，會讓 pipeline 多等。
# 依據：2026-07-31 板子實測，SenseVoice 辨識 2 秒音訊 147ms（單次，模型已暖）。
# 這裡取 0.4s 留餘裕（較長語句、與 llama-server 搶 CPU 時會變慢）。
_TTFS_P99_S = 0.4


class SenseVoiceSTTService(SegmentedSTTService):
    """以板上既有 SenseVoice 引擎實作的 pipecat STT 服務（VAD 分段）。"""

    Settings = STTSettings
    """沿用基底的 settings 形狀——SenseVoice 沒有額外可調參數需要暴露。"""

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
        # pipecat 會在 pipeline 啟動時檢查 settings 是否都已初始化，未填的欄位會
        # 在 log 印 ERROR（`STTSettings: the following fields are NOT_GIVEN`）。
        # `language=None` 是基底類別給「服務自己偵測語言」的正式表達方式——
        # SenseVoice 就是多語言自動偵測（zh/en/ja/ko/yue），不接受外部指定。
        kwargs.setdefault("ttfs_p99_latency", _TTFS_P99_S)
        super().__init__(
            settings=self.Settings(model=_MODEL_ID, language=None),
            sample_rate=sample_rate,
            **kwargs,
        )
        self._engine = engine or SenseVoiceASREngine()
        self._user_id = user_id

    def can_generate_metrics(self) -> bool:
        """Report that this service produces TTFB/processing metrics.

        Returns:
            True.
        """
        return True

    @property
    def wants_wav_segments(self) -> bool:
        """Whether segments should arrive wrapped in a WAV container.

        Returns:
            False — SenseVoice consumes raw 16-bit PCM directly, so the base
            class should hand over the unwrapped buffer. This is the
            subclass-level contract for local models, not a preference.
        """
        return False

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
