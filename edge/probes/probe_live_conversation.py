# -*- coding: utf-8 -*-
"""真人一輪：對著玩偶說話，走完整條 pipecat pipeline。

```
麥克風(arecord) → VAD → SenseVoice STT → 教材注入 → llama-server
      → 安全閘門 → 帶讀護欄 → 邊緣 TTS → 簡轉繁 → 喇叭(aplay)
```

## 跑之前要知道的三件事

1. **會佔用麥克風**。`talkybuddy-local-client` 是 active 的、用同一支麥克風，
   跑這支的時候**不要按玩偶的按鍵**，否則兩個行程搶麥（`38aa261`），
   症狀跟麥克風壞掉一模一樣。腳本啟動前會檢查，結束後會確認釋放。

2. **已加 half-duplex 閘門**。喇叭與麥克風同在玩偶內、板子裝不了 AEC
   （見記憶 `project-edge-s2s-tuning`）。2026-07-31 真人實測，不加閘門時
   **自我打斷 4 次**——玩偶把自己的聲音判成使用者開口。現已掛上
   `AlwaysUserMuteStrategy`（玩偶講話時一律不聽）。代價是孩子**無法插話打斷**，
   與現行 `PlaybackGate` 的取捨相同。

3. **對話無狀態**。llama-server `--ctx-size 512`，累積歷史會直接爆
   （實測 516→579→642 tokens）。`StatelessContextProcessor` 每輪把 context
   清成只剩 system，與現行 `EdgeLLM.generate` 的行為一致。代價是玩偶不記得
   上一輪。

4. **不會動決賽路徑**。全部跑在 `/root/pipecat-lab/`，用的是同一份模型檔（symlink）。

## 用法

    cd /root/pipecat-lab
    PYTHONPATH=/root/pipecat-lab ./.venv/bin/python probe_live_conversation.py [秒數]

預設 60 秒，Ctrl-C 可提前結束。
"""

import asyncio
import subprocess
import sys
import time

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.turns.user_mute.always_user_mute_strategy import AlwaysUserMuteStrategy

from edge.runtime.live_client import PlaybackGate
from edge.runtime.pipecat_adapters.alsa_transport import AlsaTransport, AlsaTransportParams
from edge.runtime.pipecat_adapters.edge_tts import EdgeVitsTTSService
from edge.runtime.pipecat_adapters.lesson_prompt import LessonPromptInjector
from edge.runtime.pipecat_adapters.opencc_processor import OpenCCProcessor
from edge.runtime.pipecat_adapters.playback_gate import (
    PlaybackGateFilter,
    PlaybackGateSink,
)
from edge.runtime.pipecat_adapters.readalong_guard import ReadalongGuardProcessor
from edge.runtime.pipecat_adapters.safety_gate import SafetyGateProcessor
from edge.runtime.pipecat_adapters.sensevoice_stt import SenseVoiceSTTService
from edge.runtime.pipecat_adapters.stateless_context import StatelessContextProcessor

MIC_DEVICE = "plughw:1,0"
SPEAKER_DEVICE = "plughw:0,0"
STT_RATE = 16000
TTS_RATE = 22050
LLAMA_BASE_URL = "http://127.0.0.1:8080/v1"
TARGET_SENTENCE = "I want an apple."

try:
    from server.llm import EdgeLLM

    SYSTEM_PROMPT = EdgeLLM._SYSTEM_PROMPT
except Exception:
    SYSTEM_PROMPT = "你是陪伴孩子學英文的玩偶。用一句話回答。"


class Narrator(FrameProcessor):
    """把對話過程即時印出來，讓人看得懂玩偶在想什麼。"""

    def __init__(self, tag: str):
        super().__init__()
        self._tag = tag
        self._llm_buf: list[str] = []
        self.turns = 0
        self.audio_chunks = 0
        self.said: list[str] = []
        self.self_interrupts = 0
        self._bot_speaking_since: float | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, UserStartedSpeakingFrame):
            if self._bot_speaking_since is not None:
                self.self_interrupts += 1
                print("   ⚠️  玩偶還在講話時偵測到「使用者開始說話」——可能是聽到自己的聲音")
            print("🎤 偵測到你開始說話…")
        elif isinstance(frame, UserStoppedSpeakingFrame):
            print("🎤 你說完了，辨識中…")
        elif isinstance(frame, TranscriptionFrame):
            original = frame.result if isinstance(frame.result, str) else frame.text
            print(f"👂 聽成：{original}")
            self.turns += 1
        elif isinstance(frame, LLMTextFrame):
            self._llm_buf.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            text = "".join(self._llm_buf).strip()
            if text:
                print(f"🗣  玩偶說：{text}")
                self.said.append(text)
            self._llm_buf.clear()
        elif isinstance(frame, TTSAudioRawFrame):
            if self.audio_chunks == 0 or self._bot_speaking_since is None:
                self._bot_speaking_since = time.perf_counter()
                text = "".join(self._llm_buf).strip()
                if text:
                    print(f"🗣  玩偶說：{text}")
                self._llm_buf.clear()
            self.audio_chunks += 1
        await self.push_frame(frame, direction)


def _pids(name: str) -> list[str]:
    r = subprocess.run(["pgrep", "-x", name], capture_output=True, text=True)
    return [p for p in r.stdout.split() if p]


async def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0

    busy = _pids("arecord")
    if busy:
        print(f"❌ 已有 arecord 在跑（pid {busy}）——很可能是 local-client 正在錄音。")
        print("   請等它結束，或先確認沒有人在按玩偶按鍵。")
        return 2

    print("載入模型中（SenseVoice 約 2 秒、TTS voice 約 2 秒）…")
    from server.tts import TTSEngine

    tts_engine = TTSEngine()
    tts_engine.synth([("zh", "暖機")])  # 把冷啟動吃掉，不要讓第一輪特別慢

    transport = AlsaTransport(
        AlsaTransportParams(
            audio_in_enabled=True,
            audio_in_sample_rate=STT_RATE,
            input_device=MIC_DEVICE,
            audio_out_enabled=True,
            audio_out_sample_rate=TTS_RATE,
            output_device=SPEAKER_DEVICE,
        )
    )
    vad = VADProcessor(vad_analyzer=SileroVADAnalyzer())
    stt = SenseVoiceSTTService(sample_rate=STT_RATE)
    llm = OpenAILLMService(model="qwen", api_key="none", base_url=LLAMA_BASE_URL)
    tts = EdgeVitsTTSService(engine=tts_engine)
    narrator = Narrator("out")
    narrator_in = Narrator("in")
    narrator_llm = Narrator("llm")   # LLMTextFrame 會被 TTS 消費，探針必須在 TTS 之前
    # 上下行共享同一個 gate：sink 記下播放時長，filter 立刻據此關閘。
    gate = PlaybackGate(rate=TTS_RATE)

    context = LLMContext(messages=[{"role": "system", "content": SYSTEM_PROMPT}])
    # AlwaysUserMuteStrategy：玩偶講話時一律不聽使用者。
    # 2026-07-31 真人實測，沒有它會自我打斷 4 次——喇叭與麥克風同在玩偶內、
    # 板子裝不了 AEC，玩偶會把自己的聲音判成使用者開口。
    agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_mute_strategies=[AlwaysUserMuteStrategy()]
        ),
    )

    worker = PipelineWorker(
        Pipeline(
            [
                transport.input(),
                PlaybackGateFilter(gate),   # 玩偶講話時上行換靜音，攔在 VAD 之前
                vad,
                stt,
                narrator_in,        # 探針要在 agg.user() 之前，否則看不到逐字稿
                StatelessContextProcessor(context=context),
                LessonPromptInjector(target=TARGET_SENTENCE),
                agg.user(),
                llm,
                SafetyGateProcessor(),
                ReadalongGuardProcessor(target=TARGET_SENTENCE),
                narrator_llm,
                tts,
                PlaybackGateSink(gate),     # 記錄下行時長給 gate
                OpenCCProcessor(),
                narrator,
                transport.output(),
                agg.assistant(),
            ]
        )
    )
    runner = PipelineRunner()

    print("=" * 62)
    print(f"🟢 開始了，請對著玩偶說話（{seconds:.0f} 秒後自動結束，Ctrl-C 可提前停）")
    print(f"   今天的目標句：{TARGET_SENTENCE}")
    print("   建議說：我想要蘋果")
    print("=" * 62)

    async def stop_after():
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            pass
        await worker.queue_frames([EndFrame()])

    try:
        await asyncio.gather(runner.run(worker), stop_after())
    except KeyboardInterrupt:
        print("\n收到 Ctrl-C，收尾中…")
    finally:
        await asyncio.sleep(0.5)
        for name in ("arecord", "aplay"):
            leftover = _pids(name)
            if leftover:
                print(f"⚠️  {name} 仍在跑（pid {leftover}），強制收掉")
                subprocess.run(["kill", "-9", *leftover])

    print("=" * 62)
    print(f"完成的對話輪數　：{narrator_in.turns}")
    print(f"玩偶回覆　　　　：{' | '.join(narrator_llm.said) or '(無)'}")
    print(f"輸出音訊 chunk　：{narrator.audio_chunks}")
    print(f"疑似自我打斷次數：{narrator.self_interrupts + narrator_in.self_interrupts}")
    mic_left, spk_left = _pids("arecord"), _pids("aplay")
    if mic_left or spk_left:
        print(f"❌ 裝置未釋放：arecord={mic_left} aplay={spk_left}")
        return 1
    print("✅ 麥克風與喇叭都已釋放")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="WARNING")  # 只留警告，免得刷版蓋掉對話
    sys.exit(asyncio.run(main()))
