"""把 InterruptibleSynth 包成 Pipecat TTS service（從 spike 畢業搬入）。

Pipecat 1.5.0 實測：
- 基類＝pipecat.services.tts_service.TTSService（純本地同步 TTS）。
- run_tts(self, text, context_id) 為 async generator，yield TTSAudioRawFrame。
- interruption frame＝InterruptionFrame；base.process_frame 收到走 _handle_interruption
  並取消 run_tts task。額外覆寫 process_frame，在 super 之前令核心 interrupt()。
- sherpa generate 阻塞 → 逐句 asyncio.to_thread，event loop 才能句間處理 InterruptionFrame。

來源：從 spike/a2_pipecat/interruptible_tts.py 畢業搬入（A2-1 spike 驗過），
僅更新 import 路徑至 server.streaming.*，並讓 voice 可注入（A2-2 spec 2026-07-08）。
"""
from __future__ import annotations

import asyncio

import numpy as np

from pipecat.frames.frames import TTSAudioRawFrame, InterruptionFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.tts_service import TTSService

from server.streaming.interruptible_synth import InterruptibleSynth, split_sentences
from server.streaming.tests.sherpa_voice import load_zh_voice


class SherpaInterruptibleTTSService(TTSService):
    """句級可中止 sherpa TTS。中止邏輯委派 InterruptibleSynth。"""

    def __init__(self, voice=None, **kwargs):
        voice = voice if voice is not None else load_zh_voice()
        super().__init__(sample_rate=int(voice.sample_rate), **kwargs)
        self._synth = InterruptibleSynth(voice)

    async def process_frame(self, frame, direction: FrameDirection):
        if isinstance(frame, InterruptionFrame):
            self._synth.interrupt()  # 先令核心中止，再交給 base（base 取消 run_tts task）
        await super().process_frame(frame, direction)

    async def run_tts(self, text: str, context_id: str):
        self._synth.reset()
        for sentence in split_sentences(text):
            if self._synth.is_interrupted():
                break
            audio = await asyncio.to_thread(self._synth.synth_one, sentence)
            if audio is None:
                break
            samples = np.asarray(audio.samples, dtype=np.float32)
            pcm = np.clip(np.rint(samples * 32767.0), -32768, 32767).astype(np.int16)
            yield TTSAudioRawFrame(
                audio=pcm.tobytes(),
                sample_rate=int(audio.sample_rate),
                num_channels=1,
                context_id=context_id,
            )
            # 擬真播放節奏：sherpa 合成遠快於即時，若不 sleep，barge-in 觸發前就全合成完。
            await asyncio.sleep(len(samples) / int(audio.sample_rate))
