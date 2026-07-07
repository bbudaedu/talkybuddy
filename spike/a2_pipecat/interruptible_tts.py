"""把 InterruptibleSynth 包成 Pipecat TTS service。

以 probe_pipecat.py 實測（Pipecat 1.5.0）為準：
- 基類＝pipecat.services.tts_service.TTSService（純本地同步 TTS，非 websocket 系）。
- 須覆寫 async generator：run_tts(self, text, context_id) -> AsyncGenerator[Frame|None]，
  yield TTSAudioRawFrame（base 的 tts_process_generator 會把非 None frame 併入 audio context）。
- interruption frame 類名＝InterruptionFrame；base.process_frame 收到後走 _handle_interruption
  並取消 run_tts task。我們額外覆寫 process_frame，在 super 之前令核心 interrupt()。

因 sherpa OfflineTts.generate 為阻塞呼叫，逐句以 asyncio.to_thread 執行，
event loop 才能在句間即時處理 InterruptionFrame（criterion #4 的關鍵）。
"""
from __future__ import annotations

import asyncio

import numpy as np

from pipecat.frames.frames import TTSAudioRawFrame, InterruptionFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.tts_service import TTSService

from spike.a2_pipecat.interruptible_synth import InterruptibleSynth, split_sentences
from spike.a2_pipecat.sherpa_voice import load_zh_voice


class SherpaInterruptibleTTSService(TTSService):
    """句級可中止 sherpa TTS。engine-agnostic 中止邏輯委派 InterruptibleSynth。"""

    def __init__(self, **kwargs):
        voice = load_zh_voice()
        super().__init__(sample_rate=int(voice.sample_rate), **kwargs)
        self._synth = InterruptibleSynth(voice)

    async def process_frame(self, frame, direction: FrameDirection):
        # interruption frame 進來 → 先令核心中止，再交給 base（base 會取消 run_tts task）
        if isinstance(frame, InterruptionFrame):
            self._synth.interrupt()
        await super().process_frame(frame, direction)

    async def run_tts(self, text: str, context_id: str):
        """收到一段文字 → 逐句合成 → yield 音訊 frame；被打斷則句界停。"""
        self._synth.reset()
        for sentence in split_sentences(text):
            if self._synth.is_interrupted():
                break
            # 阻塞式 sherpa generate 丟到 thread，讓 event loop 能在句間處理 InterruptionFrame
            audio = await asyncio.to_thread(self._synth.synth_one, sentence)
            if audio is None:
                break  # 已中止（句界或句內）→ 不再產出後續句
            samples = np.asarray(audio.samples, dtype=np.float32)
            pcm = np.clip(np.rint(samples * 32767.0), -32768, 32767).astype(np.int16)
            yield TTSAudioRawFrame(
                audio=pcm.tobytes(),
                sample_rate=int(audio.sample_rate),
                num_channels=1,
                context_id=context_id,
            )
            # 擬真：sherpa 合成遠快於即時播放，若不模擬播放節奏，4 句會在 barge-in
            # 觸發前就全合成完、無從觀察句界中止。以音訊時長 sleep 模擬「播放中」，
            # 讓下一句開始前有機會看到 interrupt flag → 句界乾淨停（criterion #4）。
            await asyncio.sleep(len(samples) / int(audio.sample_rate))
