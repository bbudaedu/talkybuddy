"""組 Pipecat pipeline 跑 A2-1 spike 判準（Pipecat 1.5.0，API 依 probe 實測）。

判準：
 #1 pipeline 不 crash；#2 批次 SenseVoice 把 canned wav 整段轉出文字；
 #3 stub 多句回覆被 sherpa 逐句合成（OutputSink 有音訊 frame）；
 #4 SPIKE_INTERRUPT=1 時第 1 句播放中注入 InterruptionFrame → 後續句不再合成（frames 明顯變少）。

未知 #3 實測（更正）：FunASRSTTService＝SegmentedSTTService，段落轉錄**只**由
VAD 前綴 frame 觸發：VADUserStartedSpeakingFrame→audio→VADUserStoppedSpeakingFrame。
非 VAD 版 UserStartedSpeaking/UserStoppedSpeaking 不會啟動 run_stt（buffer 被裁到 1s），
故 spike 用 VAD 前綴 frame 模擬真實 VAD 段界。
"""
from __future__ import annotations

import asyncio
import os
import wave
from pathlib import Path

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.pipeline.runner import PipelineRunner
from pipecat.services.funasr.stt import FunASRSTTService
from pipecat.frames.frames import (
    InputAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
    TranscriptionFrame,
    EndFrame,
)

from spike.a2_pipecat.interruptible_tts import SherpaInterruptibleTTSService
from spike.a2_pipecat.spike_parts import StubLLMService, InterruptDriver, OutputSink

_TB_ROOT = Path(__file__).resolve().parents[2]
_WAV = _TB_ROOT / "models" / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17" / "test_wavs" / "zh.wav"


def _load_wav_frames(chunk_ms: int = 200):
    with wave.open(str(_WAV), "rb") as wf:
        rate, ch = wf.getframerate(), wf.getnchannels()
        pcm = wf.readframes(wf.getnframes())
    step = int(rate * chunk_ms / 1000) * 2 * ch
    for i in range(0, len(pcm), step):
        yield InputAudioRawFrame(audio=pcm[i:i + step], sample_rate=rate, num_channels=ch)


async def main(interrupt: bool = False, no_stt: bool = False):
    sink = OutputSink()
    # no_stt：跳過需 1GB 模型的 FunASRSTTService，直接注入 TranscriptionFrame，
    # 專驗判準 #1/#3/#4（pipeline 跑得起來、多句合成、barge-in 句界停）。
    procs = [] if no_stt else [FunASRSTTService()]
    procs.append(StubLLMService())
    if interrupt:
        procs.append(InterruptDriver(delay_s=1.5))  # 看到 TTSSpeakFrame 後 1.5s 打斷
    procs += [SherpaInterruptibleTTSService(), sink]
    task = PipelineTask(Pipeline(procs))

    async def feed():
        if no_stt:
            await task.queue_frame(
                TranscriptionFrame(text="測試一句", user_id="u", timestamp="0")
            )
        else:
            # SegmentedSTTService 只認 VAD 前綴 frame 才框段落並觸發 run_stt。
            await task.queue_frame(VADUserStartedSpeakingFrame(start_secs=0.2))
            for f in _load_wav_frames():
                await task.queue_frame(f)
            await task.queue_frame(VADUserStoppedSpeakingFrame(stop_secs=0.5))
        await asyncio.sleep(12)  # 等 STT→LLM→TTS 走完
        await task.queue_frame(EndFrame())

    await asyncio.gather(PipelineRunner().run(task), feed())

    print("\n===== 判準觀察 =====")
    print("[#1 pipeline 未 crash] 走到這行即成立")
    print("[#2 STT 整段轉錄] 見上方 [StubLLM] got transcript 那行文字是否非空")
    print(f"[#3/#4 interrupt={interrupt}] OutputSink frames={sink.frame_count}, bytes={sink.total_bytes}")


if __name__ == "__main__":
    asyncio.run(main(
        interrupt=os.environ.get("SPIKE_INTERRUPT") == "1",
        no_stt=os.environ.get("SPIKE_NO_STT") == "1",
    ))
