# -*- coding: utf-8 -*-
"""真機驗證 `AlsaInputTransport`：真的開 arecord 讀麥克風，然後確認有還回去。

## 這支 probe 為什麼要這麼小心

`alsa_transport` 是唯一必須自寫、也是唯一沒在真機跑過的元件。而它碰的是
**決賽當天要用的那支麥克風**：

- `talkybuddy-local-client` 是 **active** 的，用的是同一個裝置（`plughw:1,0`），
  按鍵時才會開 arecord——所以碰撞窗口是「使用者剛好在這 3 秒內按了 power 鍵」
- 沒還回麥克風的後果是「玩偶不會回話」，而那個症狀跟按鍵故障、麥克風壞掉
  長得一模一樣（`38aa261`、`92fb4c8`）

所以本 probe：**執行前確認沒有 arecord 在跑、執行後確認 arecord 已消失**，
兩個檢查都失敗就非零退出。錄音窗口固定 3 秒。

不驗證「聽到了什麼」——沒有人對著麥克風講話，環境噪音就足以證明資料流通。
"""

import asyncio
import subprocess
import sys
import time

from pipecat.frames.frames import EndFrame, Frame, InputAudioRawFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from edge.runtime.pipecat_adapters.alsa_transport import AlsaTransport, AlsaTransportParams

MIC_DEVICE = "plughw:1,0"
RECORD_SECONDS = 3.0
UPLINK_RATE = 16000


class AudioCounter(FrameProcessor):
    """數收到的音訊，並記錄第一個 frame 的到達時間。"""

    def __init__(self):
        super().__init__()
        self.frames = 0
        self.total_bytes = 0
        self.sample_rates: set[int] = set()
        self.first_at: float | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            if self.first_at is None:
                self.first_at = time.perf_counter()
            self.frames += 1
            self.total_bytes += len(frame.audio)
            self.sample_rates.add(frame.sample_rate)
        await self.push_frame(frame, direction)


def _arecord_pids() -> list[str]:
    r = subprocess.run(["pgrep", "-x", "arecord"], capture_output=True, text=True)
    return [p for p in r.stdout.split() if p]


async def main() -> int:
    before = _arecord_pids()
    if before:
        print(f"❌ 執行前已有 arecord 在跑（pid {before}）——不介入，直接中止。")
        print("   很可能是 local-client 正在錄音；請等它結束再試。")
        return 2
    print("✅ 前置檢查：沒有 arecord 在跑")

    params = AlsaTransportParams(
        audio_in_enabled=True,
        audio_in_sample_rate=UPLINK_RATE,
        input_device=MIC_DEVICE,
    )
    transport = AlsaTransport(params)
    counter = AudioCounter()

    worker = PipelineWorker(Pipeline([transport.input(), counter]))
    runner = PipelineRunner()

    started = time.perf_counter()

    async def stop_after():
        await asyncio.sleep(RECORD_SECONDS)
        await worker.queue_frames([EndFrame()])

    try:
        await asyncio.gather(runner.run(worker), stop_after())
    finally:
        # 不管上面發生什麼，都要確認麥克風還回去了。
        await asyncio.sleep(0.5)
        leftover = _arecord_pids()
        if leftover:
            print(f"⚠️  arecord 仍在跑（pid {leftover}），強制收掉——這是 bug，要修 teardown")
            subprocess.run(["kill", "-9", *leftover])
            await asyncio.sleep(0.3)

    elapsed = time.perf_counter() - started
    after = _arecord_pids()

    expected_bytes = UPLINK_RATE * 2 * RECORD_SECONDS
    ratio = counter.total_bytes / expected_bytes if expected_bytes else 0

    print("=" * 62)
    print(f"裝置　　　　：{MIC_DEVICE}（USB 麥克風，與 local-client 同一支）")
    print(f"錄音時間　　：{elapsed:.1f}s")
    print(f"收到 frame　：{counter.frames}")
    print(f"音訊總量　　：{counter.total_bytes} bytes（預期約 {expected_bytes:.0f}，比例 {ratio:.0%}）")
    print(f"取樣率　　　：{counter.sample_rates or '（無）'}")
    if counter.first_at:
        print(f"首個 frame　：開始後 {(counter.first_at - started) * 1000:.0f} ms")
    print("-" * 62)
    ok = True
    if counter.frames == 0:
        print("❌ 沒有收到任何音訊——arecord 沒起來或 transport 接線有問題")
        ok = False
    elif ratio < 0.5:
        print(f"⚠️  音訊量偏少（{ratio:.0%}）——可能有掉幀")
    else:
        print("✅ 音訊持續流入")
    if counter.sample_rates and counter.sample_rates != {UPLINK_RATE}:
        print(f"❌ 取樣率不符：{counter.sample_rates}")
        ok = False
    if after:
        print(f"❌ 麥克風沒有釋放（pid {after}）")
        ok = False
    else:
        print("✅ 後置檢查：arecord 已終止，麥克風已釋放")
    print("=" * 62)
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# 輸出側：真的讓玩偶發出聲音
# ---------------------------------------------------------------------------
async def check_output() -> int:
    """把一段 TTS 合成的音訊送進 AlsaOutputTransport，玩偶會真的出聲。

    驗證的是「aplay 收得下、正常結束、沒有斷管」，不驗證音質——那要人耳。
    播放裝置用 `plughw:0,0`（3.5mm Lineout）：USB 麥克風沒有播放能力，
    這條在 `audio_io.py` 已經寫明。
    """
    from pipecat.frames.frames import OutputAudioRawFrame, StartFrame

    from server.tts import TTSEngine

    engine = TTSEngine()
    wav = engine.synth([("zh", "測試播放")])
    if not wav:
        print("❌ TTS 合成失敗，無法測輸出")
        return 1
    import io
    import wave as _wave

    with _wave.open(io.BytesIO(wav), "rb") as wf:
        pcm = wf.readframes(wf.getnframes())
        rate = wf.getframerate()

    params = AlsaTransportParams(
        audio_out_enabled=True, audio_out_sample_rate=rate, output_device="plughw:0,0"
    )
    transport = AlsaTransport(params)
    out = transport.output()

    worker = PipelineWorker(Pipeline([out]))
    runner = PipelineRunner()

    written = {"ok": 0, "fail": 0}

    async def feed():
        await asyncio.sleep(0.8)
        chunk = 4410
        for i in range(0, len(pcm), chunk):
            frame = OutputAudioRawFrame(
                audio=pcm[i : i + chunk], sample_rate=rate, num_channels=1
            )
            if await out.write_audio_frame(frame):
                written["ok"] += 1
            else:
                written["fail"] += 1
        await asyncio.sleep(2.0)
        await worker.queue_frames([EndFrame()])

    await asyncio.gather(runner.run(worker), feed())
    await asyncio.sleep(0.5)

    leftover = subprocess.run(["pgrep", "-x", "aplay"], capture_output=True, text=True)
    stuck = [p for p in leftover.stdout.split() if p]

    print("=" * 62)
    print(f"播放裝置　　：plughw:0,0（3.5mm Lineout）")
    print(f"音訊長度　　：{len(pcm) / (rate * 2):.2f}s（{len(pcm)} bytes @{rate}Hz）")
    print(f"寫入成功／失敗：{written['ok']} / {written['fail']}")
    ok = written["fail"] == 0 and written["ok"] > 0 and not stuck
    if written["fail"]:
        print("❌ 有 chunk 寫入失敗（aplay 斷管？）")
    if stuck:
        print(f"❌ aplay 沒有結束（pid {stuck}）")
        subprocess.run(["kill", "-9", *stuck])
    if ok:
        print("✅ 音訊全部寫入 aplay，且行程已正常結束")
    print("=" * 62)
    return 0 if ok else 1


async def _entry() -> int:
    if "--output" in sys.argv:
        return await check_output()
    return await main()


if __name__ == "__main__":
    sys.exit(asyncio.run(_entry()))
