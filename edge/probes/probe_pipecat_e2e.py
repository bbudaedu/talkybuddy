# -*- coding: utf-8 -*-
"""端到端 pipeline：TranscriptionFrame → LLM → TTS → 音訊，並分解各段耗時。

刻意不接麥克風（板子上 live-client 可能持有它）。STT 已單獨量過 147ms，
這裡量的是 LLM 與 TTS 這兩段——round_total 的主體。
"""

import asyncio
import sys
import time

from pipecat.frames.frames import (
    EndFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.utils.time import time_now_iso8601

from edge.runtime.pipecat_adapters.edge_tts import EdgeVitsTTSService

LLAMA_BASE_URL = "http://127.0.0.1:8080/v1"

# 用 edge LLM 的**真實** prompt，否則與現行 round_total 4685ms 不可比。
# 短 prompt 量出來的漂亮數字沒有意義——那不是 pipecat 的功勞，是 prompt 短。
try:
    from server.llm import EdgeLLM

    SYSTEM_PROMPT = EdgeLLM._SYSTEM_PROMPT
    _REAL_PROMPT = True
except Exception:
    SYSTEM_PROMPT = "你是陪伴孩子學英文的玩偶。用一句話回答，先中文再英文。"
    _REAL_PROMPT = False

# 沿用 server/llm.py:130 的 user_prompt 形狀（含目標句與規則覆述）。
USER_TEXT = (
    "學生剛剛說：「我想要蘋果」\n"
    "目標英文句：I want an apple.\n"
    "請照規則回覆：先一句繁體中文稱讚鼓勵，"
    "再用「跟我說一遍：<英文句>」帶讀目標英文句。"
)


class Timeline(FrameProcessor):
    """記錄每個關鍵 frame 的到達時刻。"""

    def __init__(self):
        super().__init__()
        self.t0: float | None = None
        self.marks: list[tuple[str, float]] = []
        self.audio_bytes = 0
        self.text_parts: list[str] = []

    def start_clock(self):
        self.t0 = time.perf_counter()

    def _mark(self, label: str):
        if self.t0 is not None:
            self.marks.append((label, (time.perf_counter() - self.t0) * 1000))

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            if not any(m[0] == "llm_first_text" for m in self.marks):
                self._mark("llm_first_text")
            self.text_parts.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            self._mark("llm_done")
        elif isinstance(frame, TTSAudioRawFrame):
            if not any(m[0] == "tts_first_audio" for m in self.marks):
                self._mark("tts_first_audio")
            self.audio_bytes += len(frame.audio)
            self._mark("tts_last_audio")
        await self.push_frame(frame, direction)


async def main(use_real_tts: bool):
    if use_real_tts:
        from server.tts import TTSEngine

        tts_engine = TTSEngine()
        # 先暖機一次，把 voice 模型載入的 2s 冷啟動排除在量測外。
        tts_engine.synth([("zh", "暖機")])
    else:
        import io
        import wave

        class _Fake:
            def synth(self, segments):
                buf = io.BytesIO()
                with wave.open(buf, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(22050)
                    wf.writeframes(b"\x01\x02" * 11025)
                return buf.getvalue()

        tts_engine = _Fake()

    llm = OpenAILLMService(model="qwen", api_key="none", base_url=LLAMA_BASE_URL)
    tts = EdgeVitsTTSService(engine=tts_engine)
    timeline = Timeline()

    context = LLMContext(messages=[{"role": "system", "content": SYSTEM_PROMPT}])
    agg = LLMContextAggregatorPair(context)

    # 兩個探針：llm_probe 在 TTS 之前（LLMTextFrame 會被 TTS 聚合消費掉，
    # 放後面就看不到了）；timeline 在 TTS 之後量音訊。
    llm_probe = Timeline()
    llm_probe.t0 = None
    worker = PipelineWorker(
        Pipeline([agg.user(), llm, llm_probe, tts, timeline, agg.assistant()])
    )
    runner = PipelineRunner()

    async def feed():
        await asyncio.sleep(1.0)
        timeline.start_clock()
        llm_probe.start_clock()
        await worker.queue_frames([TranscriptionFrame(USER_TEXT, "child", time_now_iso8601())])
        await asyncio.sleep(30.0)
        await worker.queue_frames([EndFrame()])

    await asyncio.gather(runner.run(worker), feed())

    def first(marks, label):
        for m_label, ms in marks:
            if m_label == label:
                return ms
        return None

    print("=" * 64)
    print(f"system prompt：{'真實 EdgeLLM' if _REAL_PROMPT else '簡短替代'}"
          f"（{len(SYSTEM_PROMPT)} 字）  user prompt {len(USER_TEXT)} 字")
    print(f"使用者輸入：我想要蘋果")
    print(f"玩偶回應　：{''.join(llm_probe.text_parts).strip()}")
    print("-" * 64)
    for label in ("llm_first_text", "llm_done"):
        ms = first(llm_probe.marks, label)
        if ms is not None:
            print(f"  {label:16s} {ms:8.0f} ms")
    ms = first(timeline.marks, "tts_first_audio")
    if ms is not None:
        print(f"  {'tts_first_audio':16s} {ms:8.0f} ms")
    last = [m for m in timeline.marks if m[0] == "tts_last_audio"]
    if last:
        print(f"  {'tts_last_audio':16s} {last[-1][1]:8.0f} ms  ← round_total")
    print(f"  音訊總量 {timeline.audio_bytes} bytes = {timeline.audio_bytes / 44100:.2f}s")
    print("=" * 64)


if __name__ == "__main__":
    asyncio.run(main(use_real_tts="--real" in sys.argv))
