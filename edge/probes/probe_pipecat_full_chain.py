# -*- coding: utf-8 -*-
"""全鏈路 probe：音訊 → STT → LLM → OpenCC → TTS → 音訊，並量 CPU 佔用。

與 `probe_pipecat_e2e.py` 的差別：那支從 `TranscriptionFrame` 起頭、跳過 STT；
這支**從真實音訊起頭**，走完 STT。

**輸入音訊用 TTS 自己合成再降採樣到 16kHz**，所以不需要麥克風——板子上
`live-client` 可能持有它，搶麥的症狀跟麥克風壞掉一模一樣（見 `38aa261`）。
VAD 邊界用手動送 frame 模擬（VAD 本身已單獨量過 1.90ms/窗）。
"""

import asyncio
import io
import resource
import sys
import time
import wave

import numpy as np
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    InputAudioRawFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSTextFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.openai.llm import OpenAILLMService

from edge.runtime.pipecat_adapters.edge_tts import EdgeVitsTTSService
from edge.runtime.pipecat_adapters.lesson_prompt import LessonPromptInjector
from edge.runtime.pipecat_adapters.opencc_processor import OpenCCProcessor
from edge.runtime.pipecat_adapters.readalong_guard import ReadalongGuardProcessor
from edge.runtime.pipecat_adapters.safety_gate import SafetyGateProcessor
from edge.runtime.pipecat_adapters.sensevoice_stt import SenseVoiceSTTService

LLAMA_BASE_URL = "http://127.0.0.1:8080/v1"
STT_RATE = 16000
CHUNK_BYTES = 640  # 20ms @16kHz 16-bit

try:
    from server.llm import EdgeLLM

    SYSTEM_PROMPT = EdgeLLM._SYSTEM_PROMPT
except Exception:
    SYSTEM_PROMPT = "你是陪伴孩子學英文的玩偶。用一句話回答。"

SPOKEN_TEXT = "我想要蘋果"
TARGET_SENTENCE = "I want an apple."


class Probe(FrameProcessor):
    """記錄關鍵 frame 的到達時刻與內容。"""

    def __init__(self, name_: str):
        super().__init__()
        self._n = name_
        self.t0: float | None = None
        self.marks: dict[str, float] = {}
        self.transcript: list[str] = []
        self.llm_text: list[str] = []
        self.tts_text: list[str] = []
        self.audio_bytes = 0

    def start_clock(self, t0: float):
        self.t0 = t0

    def _mark(self, label: str):
        if self.t0 is not None and label not in self.marks:
            self.marks[label] = (time.perf_counter() - self.t0) * 1000

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            self._mark("stt_done")
            self.transcript.append(frame.text)
        elif isinstance(frame, LLMTextFrame):
            self._mark("llm_first_text")
            self.llm_text.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            self._mark("llm_done")
        elif isinstance(frame, TTSTextFrame):
            self.tts_text.append(frame.text)
        elif isinstance(frame, TTSAudioRawFrame):
            self._mark("tts_first_audio")
            self.audio_bytes += len(frame.audio)
            self.marks["tts_last_audio"] = (time.perf_counter() - self.t0) * 1000
        await self.push_frame(frame, direction)


def _synth_16k(engine, text: str) -> bytes:
    """用 TTS 合成一段話並降採樣到 16kHz raw PCM（當作「孩子說的話」）。"""
    wav = engine.synth([("zh", text)])
    with wave.open(io.BytesIO(wav), "rb") as wf:
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        src_rate = wf.getframerate()
    idx = np.linspace(0, len(pcm) - 1, int(len(pcm) * STT_RATE / src_rate))
    return np.interp(idx, np.arange(len(pcm)), pcm).astype(np.int16).tobytes()


def _cpu_seconds() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


async def main():
    from server.tts import TTSEngine

    tts_engine = TTSEngine()
    tts_engine.synth([("zh", "暖機")])  # 排除 voice 載入的冷啟動

    spoken_pcm = _synth_16k(tts_engine, SPOKEN_TEXT)

    stt = SenseVoiceSTTService(sample_rate=STT_RATE)
    llm = OpenAILLMService(model="qwen", api_key="none", base_url=LLAMA_BASE_URL)
    tts = EdgeVitsTTSService(engine=tts_engine)

    # 三個探針，因為每一段的 frame 都會被下一段消費掉：
    # TranscriptionFrame 被 agg.user() 吃掉、LLMTextFrame 被 TTS 吃掉，
    # 所以單一個放在最後的探針只看得到 TTS 的輸出。
    probe = Probe("out")
    probe_stt = Probe("stt")
    probe_llm = Probe("llm")

    context = LLMContext(messages=[{"role": "system", "content": SYSTEM_PROMPT}])
    agg = LLMContextAggregatorPair(context)

    worker = PipelineWorker(
        Pipeline(
            [
                stt,
                probe_stt,
                # 教材注入必須在 probe_stt 之後（逐字稿要先被記錄成孩子講的話）
                # 且在 agg.user() 之前（那裡才會把文字變成 LLM 的 user message）。
                LessonPromptInjector(target=TARGET_SENTENCE),
                agg.user(),
                llm,
                # 兩道護欄都必須在 TTS 之前：不安全的句子一旦合成就已經唸出去了，
                # 補的帶讀句也要來得及被唸。
                SafetyGateProcessor(),
                ReadalongGuardProcessor(target=TARGET_SENTENCE),
                probe_llm,
                tts,
                OpenCCProcessor(),
                probe,
                agg.assistant(),
            ]
        )
    )
    runner = PipelineRunner()

    async def feed():
        await asyncio.sleep(1.5)  # 等模型載入完
        t0 = time.perf_counter()
        for p in (probe, probe_stt, probe_llm):
            p.start_clock(t0)
        frames: list[Frame] = [VADUserStartedSpeakingFrame()]
        for i in range(0, len(spoken_pcm), CHUNK_BYTES):
            frames.append(
                InputAudioRawFrame(
                    audio=spoken_pcm[i : i + CHUNK_BYTES],
                    sample_rate=STT_RATE,
                    num_channels=1,
                )
            )
        frames.append(VADUserStoppedSpeakingFrame())
        await worker.queue_frames(frames)
        await asyncio.sleep(30.0)
        await worker.queue_frames([EndFrame()])

    cpu_before = _cpu_seconds()
    wall_before = time.perf_counter()
    await asyncio.gather(runner.run(worker), feed())
    cpu_used = _cpu_seconds() - cpu_before
    wall_used = time.perf_counter() - wall_before

    print("=" * 66)
    print(f"輸入音訊　　：{SPOKEN_TEXT}（TTS 合成後降採樣 16k，{len(spoken_pcm)/32000:.2f}s）")
    print(f"STT 聽成　　：{' '.join(probe_stt.transcript)}")
    print(f"LLM 原始輸出：{''.join(probe_llm.llm_text).strip()}")
    print(f"逐字稿(繁)　：{''.join(probe.tts_text).strip()}")
    print("-" * 66)
    merged = {**probe_stt.marks, **probe_llm.marks, **probe.marks}
    for label in ("stt_done", "llm_first_text", "llm_done", "tts_first_audio", "tts_last_audio"):
        if label in merged:
            tag = "  ← round_total" if label == "tts_last_audio" else ""
            print(f"  {label:16s} {merged[label]:8.0f} ms{tag}")
    print(f"  回應音訊　　 {probe.audio_bytes} bytes = {probe.audio_bytes / 44100:.2f}s")
    print("-" * 66)
    print(f"  CPU 時間 {cpu_used:.2f}s / wall {wall_used:.2f}s "
          f"= {cpu_used / wall_used * 100:.0f}% 單核當量（板子 8 核）")
    print(f"  峰值 RSS {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f} MB")
    print("=" * 66)


if __name__ == "__main__":
    asyncio.run(main())
