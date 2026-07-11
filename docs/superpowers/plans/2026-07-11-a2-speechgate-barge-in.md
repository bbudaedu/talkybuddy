# SpeechGate → turn_manager barge-in 專用門檻縫 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Context

A2-2 的 `StreamingTurnManager` 目前用 Pipecat transport 內建 VAD 發出的 `VADUserStartedSpeakingFrame` 觸發 barge-in（`turn_manager.py:48`）。但「bot 說話中的插話偵測」與 turn-taking 需要不同靈敏度（double-talk）——barge-in 偵測要能單獨調門檻。`SpeechGate`（`server/streaming/vad.py`）已是一顆框架無關、可調門檻的 VAD 閘門，但目前只有單元測試、未接入管線。

本 plan 把 `SpeechGate` 接成 `StreamingTurnManager` 的**獨立可調 barge-in 偵測器**：新增 `BargeInGate` 處理器（包 SpeechGate、發專用 `BargeInDetectedFrame`），turn_manager 改聽此 frame、**移除**對 transport `VADUserStartedSpeakingFrame` 的 barge-in 耦合。**只建縫**（機制＋可配置門檻＋canned wav 驗接線）；真兒童門檻調校與 AEC double-talk 場域驗證留 A2-3/A2-6。

Spec：`talkybuddy/docs/superpowers/specs/2026-07-10-a2-speechgate-barge-in-design.md`（已使用者複審）。

**Goal:** 讓 barge-in 由獨立可調的 `SpeechGate`（經 `BargeInGate`→`BargeInDetectedFrame`）觸發，與 turn-taking VAD 解耦，canned wav 端到端驗接通。

**Architecture:** 方案 A——新增 `BargeInGate` FrameProcessor（pipeline 靠輸入端）觀察 `InputAudioRawFrame`、餵 `SpeechGate`、"started" 時往 downstream 發 `BargeInDetectedFrame`；`StreamingTurnManager` barge-in 分支改判 `BargeInDetectedFrame`、移除 `VADUserStartedSpeakingFrame` 分支。`BargeInGate` 位於 manager 上游、往 downstream 發訊即達，免除 A2-2 harness 的 UPSTREAM hack（生產路徑）。

**Tech Stack:** Python 3.12、Pipecat 1.5.0、Silero VAD（onnxruntime）、sherpa-onnx、pytest + pytest-asyncio（皆在主 `.venv`）。

## Global Constraints

- 只 import `server.pipeline.TurnResult`（dataclass）；**不碰** `_process_text`/`run_turn_audio`/`server/pipeline.py`/`server/app.py`/`config.py`/`cloud_tts.py`。
- 不改 `server/streaming/vad.py`（`SpeechGate`/`build_analyzer` 沿用）與 `server/streaming/tests/test_vad.py`（既有單測不變）。
- `build_analyzer` 簽章（沿用）：`build_analyzer(sample_rate: int, *, confidence=0.7, start_secs=0.2, stop_secs=0.8, min_volume=0.6) -> SileroVADAnalyzer`；`SpeechGate(analyzer).push(pcm: bytes) -> list[str]`（事件字串 `"started"`/`"stopped"`）。
- 自訂 Frame 最小規範：**必須** `@dataclass`、繼承 `SystemFrame`（`id`/`name`/`metadata` 等由 base `__post_init__` 自動賦值，子類不宣告）。
- 所有 `FrameProcessor.process_frame` override 首行必須 `await super().process_frame(frame, direction)`。
- barge-in 門檻預設沿用 `build_analyzer` 預設值（本切片不追求正確門檻）；門檻是**獨立旋鈕**，A2-3 依真兒童錄音調高 `confidence`/`min_volume`。不動 `config.py`。
- 測試指令：於 `talkybuddy/` 執行 `.venv/bin/python -m pytest -q server/streaming/tests/`（全 suite＝`./run_tests.sh`）。維持既有 189 pass 全綠。
- 每個 commit 訊息結尾加 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。分支：`feat/a2-speechgate-barge-in`。

---

### Task 1: `BargeInGate` + `BargeInDetectedFrame`（含單元判準 A/B）

新增獨立 barge-in 偵測處理器與專用 frame，並用 mini-pipeline 驗「語音→發 frame、靜音→不發」。此 task 不動 turn_manager/harness，不影響既有測試。

**Files:**
- Create: `talkybuddy/server/streaming/barge_in_gate.py`
- Test: `talkybuddy/server/streaming/tests/test_barge_in_gate.py`

**Interfaces:**
- Consumes: `server.streaming.vad.SpeechGate`、`build_analyzer`；`pipecat.frames.frames.{InputAudioRawFrame, SystemFrame}`；`pipecat.processors.frame_processor.{FrameProcessor, FrameDirection}`。
- Produces（後續 task 依賴）：
  - `BargeInDetectedFrame`（`@dataclass`，`SystemFrame` 子類，無額外欄位）。
  - `BargeInGate(FrameProcessor)`，建構子 `BargeInGate(gate_params: dict | None = None, **kwargs)`；於首個 `InputAudioRawFrame` 依 `frame.sample_rate` 惰性建 `SpeechGate(build_analyzer(rate, **gate_params))`；每個 `"started"` 事件往 `FrameDirection.DOWNSTREAM` push 一個 `BargeInDetectedFrame`；原 frame 照原 direction 轉發。

- [ ] **Step 1: 寫失敗測試** — `talkybuddy/server/streaming/tests/test_barge_in_gate.py`

```python
"""BargeInGate 單元判準：真 SileroVAD，canned 語音→發 BargeInDetectedFrame；靜音→不發。"""
import asyncio
import wave
from pathlib import Path

import pytest

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.pipeline.runner import PipelineRunner
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.frames.frames import InputAudioRawFrame, EndFrame

from server.streaming.barge_in_gate import BargeInGate, BargeInDetectedFrame

_TB_ROOT = Path(__file__).resolve().parents[3]
_WAV = _TB_ROOT / "models" / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17" / "test_wavs" / "zh.wav"


class DetectSink(FrameProcessor):
    """計數下行的 BargeInDetectedFrame。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.detected = 0

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, BargeInDetectedFrame):
            self.detected += 1
        await self.push_frame(frame, direction)


def _speech_audio_frames(chunk_ms: int = 200):
    with wave.open(str(_WAV), "rb") as wf:
        rate, ch = wf.getframerate(), wf.getnchannels()
        pcm = wf.readframes(wf.getnframes())
    step = int(rate * chunk_ms / 1000) * 2 * ch
    for i in range(0, len(pcm), step):
        yield InputAudioRawFrame(audio=pcm[i:i + step], sample_rate=rate, num_channels=ch)


async def _run(frames):
    sink = DetectSink()
    task = PipelineTask(Pipeline([BargeInGate(), sink]))

    async def feed():
        for f in frames:
            await task.queue_frame(f)
        await asyncio.sleep(1)
        await task.queue_frame(EndFrame())

    await asyncio.gather(PipelineRunner().run(task), feed())
    return sink


@pytest.mark.asyncio
async def test_speech_emits_barge_in_detected():
    # 判準 A：canned 語音經 gate → ≥1 個 BargeInDetectedFrame
    sink = await _run(list(_speech_audio_frames()))
    assert sink.detected >= 1


@pytest.mark.asyncio
async def test_silence_emits_nothing():
    # 判準 B：靜音 → 0 個 BargeInDetectedFrame
    silence = [InputAudioRawFrame(audio=b"\x00" * 640, sample_rate=16000, num_channels=1) for _ in range(30)]
    sink = await _run(silence)
    assert sink.detected == 0
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd talkybuddy && .venv/bin/python -m pytest -q server/streaming/tests/test_barge_in_gate.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.streaming.barge_in_gate'`

- [ ] **Step 3: 寫最小實作** — `talkybuddy/server/streaming/barge_in_gate.py`

```python
"""BargeInGate：把 SpeechGate 接成 Pipecat 處理器，偵測 bot 說話中的插話。

觀察下行 InputAudioRawFrame（原封轉發給下游），把 PCM 餵給 SpeechGate；
SpeechGate 吐 "started" 時往 downstream 發 BargeInDetectedFrame，供 StreamingTurnManager
做 barge-in。門檻參數與 transport turn-taking VAD 分離、獨立可調。

門檻預設沿用 build_analyzer 預設；A2-3 將依真兒童錄音調高 confidence/min_volume 抗 double-talk。
BargeInDetectedFrame 繼承 SystemFrame（高優先即時傳遞，語意同 VADUserStartedSpeakingFrame），
確保能穿過中間處理器即時抵達 turn_manager。
"""
from __future__ import annotations

from dataclasses import dataclass

from pipecat.frames.frames import InputAudioRawFrame, SystemFrame
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

from server.streaming.vad import SpeechGate, build_analyzer


@dataclass
class BargeInDetectedFrame(SystemFrame):
    """VAD 偵測到使用者於 bot 播放期間開口（barge-in 訊號）。"""
    pass


class BargeInGate(FrameProcessor):
    """獨立可調 barge-in 偵測器：InputAudioRawFrame → SpeechGate → BargeInDetectedFrame。

    SpeechGate 於首個 InputAudioRawFrame 依其 sample_rate 惰性建立（build_analyzer 內部
    需 set_sample_rate 初始化位元緩衝）。門檻經 gate_params 傳入 build_analyzer。
    """

    def __init__(self, gate_params: dict | None = None, **kwargs):
        super().__init__(**kwargs)
        self._gate_params = gate_params or {}
        self._gate: SpeechGate | None = None

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InputAudioRawFrame):
            if self._gate is None:
                self._gate = SpeechGate(build_analyzer(frame.sample_rate, **self._gate_params))
            for event in self._gate.push(frame.audio):
                if event == "started":
                    await self.push_frame(BargeInDetectedFrame(), FrameDirection.DOWNSTREAM)

        await self.push_frame(frame, direction)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd talkybuddy && .venv/bin/python -m pytest -q server/streaming/tests/test_barge_in_gate.py`
Expected: PASS（2 passed）。
若 `test_speech_emits_barge_in_detected` 未觸發（Silero 對此 wav 偏保守）：fallback＝把 gate 建成較寬鬆 `BargeInGate(gate_params={"confidence": 0.5})` 於測試，或加大餵入時長；zh.wav 為清晰成人語音，預設 0.7 應可觸發。

- [ ] **Step 5: Commit**

```bash
cd /home/budaedu/hackathon
git add talkybuddy/server/streaming/barge_in_gate.py talkybuddy/server/streaming/tests/test_barge_in_gate.py
git commit -m "feat(a2): BargeInGate + BargeInDetectedFrame（SpeechGate 接 Pipecat）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: turn_manager barge-in 來源改判 `BargeInDetectedFrame`（含判準 C/D）

把 barge-in 觸發從 `VADUserStartedSpeakingFrame` 換成 `BargeInDetectedFrame`、移除舊耦合；改 harness 讓 `run_once` 的 barge-in 走新 frame，並支援注入任意 frame 以驗「舊 VAD frame 不再觸發」。

**Files:**
- Modify: `talkybuddy/server/streaming/turn_manager.py`（import 段 14-20、barge-in 分支 48-56、相關註解）
- Modify: `talkybuddy/server/streaming/harness.py`（將 `BargeInDriver` 一般化為可注入任意 frame 的 driver；`run_once` 加 `inject_frame_factory` 參數）
- Modify: `talkybuddy/server/streaming/tests/test_turn_manager.py`（新增判準 D 測試）

**Interfaces:**
- Consumes: `server.streaming.barge_in_gate.BargeInDetectedFrame`（Task 1）。
- Produces：
  - `StreamingTurnManager` barge-in 只由 `BargeInDetectedFrame`（且 `_bot_speaking`）觸發；`VADUserStartedSpeakingFrame` 不再觸發（照常轉發）。
  - `harness.run_once(reply_source, *, barge_in, no_stt=True, inject_frame_factory=None)`：`barge_in=True` 時於第 2 個 `TTSSpeakFrame` 回注 `inject_frame_factory()`（預設 `BargeInDetectedFrame`）到 UPSTREAM。
  - `harness.InjectAtSentenceDriver(frame_factory, at_sentence=2)`。

- [ ] **Step 1: 寫失敗測試** — 在 `talkybuddy/server/streaming/tests/test_turn_manager.py` 末尾新增

```python
from pipecat.frames.frames import VADUserStartedSpeakingFrame


@pytest.mark.asyncio
async def test_transport_vad_frame_no_longer_triggers_bargein():
    # 判準 D：舊耦合已移除——bot 說話中收 transport VADUserStartedSpeakingFrame → 不 barge-in
    sink, manager = await run_once(
        StubReplySource(_FOUR),
        barge_in=True,
        inject_frame_factory=lambda: VADUserStartedSpeakingFrame(start_secs=0.2),
    )
    assert sink.frame_count >= len(_FOUR)  # 4 句全合成
    assert "barge_in" not in manager.result.state_events
```

（判準 C＝既有 `test_bargein_at_second_sentence_stops_clean`，改動後會經 `BargeInDetectedFrame` 路徑仍 ≤2、含 `barge_in`，不需新增。）

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd talkybuddy && .venv/bin/python -m pytest -q "server/streaming/tests/test_turn_manager.py::test_transport_vad_frame_no_longer_triggers_bargein"`
Expected: FAIL — `TypeError: run_once() got an unexpected keyword argument 'inject_frame_factory'`

- [ ] **Step 3a: 改 harness `BargeInDriver`→`InjectAtSentenceDriver` 並改 `run_once`** — `talkybuddy/server/streaming/harness.py`

在 import 段補：
```python
from server.streaming.barge_in_gate import BargeInDetectedFrame
```

把既有 `BargeInDriver`（49-74 行）整段替換為一般化版本：
```python
class InjectAtSentenceDriver(FrameProcessor):
    """看到第 N 個 TTSSpeakFrame（bot 已開講到第 N 句）後回注 frame_factory() 到 UPSTREAM。

    plumbing（A2-2 執行期定案）：TTSSpeakFrame 由 manager 往 DOWNSTREAM push，本 driver 須排
    manager **之後**才看得到；回注的 frame 往 **UPSTREAM** 才回得到 manager（其 process_frame
    不分方向判斷 barge-in frame）。frame_factory 決定注入哪種 frame（BargeInDetectedFrame＝
    真 barge-in；VADUserStartedSpeakingFrame＝驗舊耦合已移除）。
    """

    def __init__(self, frame_factory, at_sentence: int = 2, **kwargs):
        super().__init__(**kwargs)
        self._make = frame_factory
        self._at = at_sentence
        self._seen = 0
        self._fired = False

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSSpeakFrame):
            self._seen += 1
            if not self._fired and self._seen >= self._at:
                self._fired = True
                await self.push_frame(self._make(), FrameDirection.UPSTREAM)
        await self.push_frame(frame, direction)
```

把 `run_once` 簽章與 barge-in 分支改為：
```python
async def run_once(reply_source, *, barge_in: bool, no_stt: bool = True, inject_frame_factory=None):
    """跑一輪，回 (OutputSink, StreamingTurnManager)。

    barge_in=True 時於第 2 個 TTSSpeakFrame 回注 inject_frame_factory()（預設 BargeInDetectedFrame）。
    """
    sink = OutputSink()
    manager = StreamingTurnManager(reply_source)

    procs = []
    if not no_stt:
        from pipecat.services.funasr.stt import FunASRSTTService
        procs.append(FunASRSTTService())
    procs.append(manager)
    if barge_in:
        procs.append(InjectAtSentenceDriver(inject_frame_factory or BargeInDetectedFrame, at_sentence=2))
    procs += [SherpaInterruptibleTTSService(), sink]
    task = PipelineTask(Pipeline(procs))
    # ...（feed / gather 區塊維持不變）
```

- [ ] **Step 3b: 改 turn_manager barge-in 來源** — `talkybuddy/server/streaming/turn_manager.py`

import 段（14-20）改為（移除 `VADUserStartedSpeakingFrame`、補 `BargeInDetectedFrame`）：
```python
from pipecat.frames.frames import (
    TranscriptionFrame,
    TTSSpeakFrame,
    InterruptionFrame,
)
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

from server.pipeline import TurnResult
from server.streaming.reply_source import ReplyContext, ReplySource
from server.streaming.barge_in_gate import BargeInDetectedFrame
```

barge-in 分支（48 行）由：
```python
        if isinstance(frame, VADUserStartedSpeakingFrame) and self._bot_speaking:
```
改為：
```python
        if isinstance(frame, BargeInDetectedFrame) and self._bot_speaking:
```
並更新該分支上方註解為「barge-in：由 BargeInGate 的 BargeInDetectedFrame 觸發（獨立門檻）」；模組 docstring 第 4-6 行「收到新的 VADUserStartedSpeakingFrame＝barge-in」改為「收到 BargeInDetectedFrame＝barge-in」。第 58 行「含首個 VADUserStartedSpeakingFrame」註解改為「含 transport VAD/音訊/EndFrame 等照常轉發」。

- [ ] **Step 4: 跑測試確認通過（含既有整合測試回歸）**

Run: `cd talkybuddy && .venv/bin/python -m pytest -q server/streaming/tests/test_turn_manager.py`
Expected: PASS（3 passed：no_bargein、bargein≤2 經新 frame、transport_vad 不觸發）。

- [ ] **Step 5: Commit**

```bash
cd /home/budaedu/hackathon
git add talkybuddy/server/streaming/turn_manager.py talkybuddy/server/streaming/harness.py talkybuddy/server/streaming/tests/test_turn_manager.py
git commit -m "feat(a2): turn_manager barge-in 改判 BargeInDetectedFrame、移除 transport VAD 耦合

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 真音訊經 BargeInGate 端到端整合（判準 E）

加一條真音訊驅動的 barge-in 路徑：pipeline 放入 `BargeInGate`，於 bot 回覆第 2 句時回注真語音 `InputAudioRawFrame`，經 `SpeechGate`→`BargeInDetectedFrame`→manager 淨停。這是「只建縫」的端到端接通證明。

**Files:**
- Modify: `talkybuddy/server/streaming/harness.py`（新增 `AudioAtSentenceDriver` 與 `run_gate`）
- Modify: `talkybuddy/server/streaming/tests/test_barge_in_gate.py`（新增判準 E 整合測試）

**Interfaces:**
- Consumes: `BargeInGate`（Task 1）、`StreamingTurnManager`（Task 2 改後）、`SherpaInterruptibleTTSService`、`OutputSink`、`StubReplySource`。
- Produces：
  - `harness.AudioAtSentenceDriver(at_sentence=2)`：於第 N 個 `TTSSpeakFrame` 回注一段真語音 `InputAudioRawFrame` 到 UPSTREAM。
  - `harness.run_gate(reply_source, *, barge_in, gate_params=None)`：pipeline `[BargeInGate(gate_params), manager, (AudioAtSentenceDriver if barge_in), TTS, sink]`，回 `(sink, manager)`。

- [ ] **Step 1: 寫失敗測試** — 在 `talkybuddy/server/streaming/tests/test_barge_in_gate.py` 末尾新增

```python
from server.streaming.harness import run_gate
from server.streaming.reply_source import StubReplySource

_FOUR = ["你好呀，我是企鵝。", "今天天氣真好。", "我們一起來玩遊戲吧。", "你想先做什麼呢？"]


@pytest.mark.asyncio
async def test_gate_bargein_stops_clean():
    # 判準 E：真語音經 BargeInGate → manager barge-in → sink 合成句數 ≤2（句界乾淨停）
    sink, manager = await run_gate(StubReplySource(_FOUR), barge_in=True)
    assert sink.frame_count <= 2
    assert "barge_in" in manager.result.state_events


@pytest.mark.asyncio
async def test_gate_no_bargein_all_sentences():
    # 對照：無插話 → gate 不發 frame → 4 句全合成
    sink, manager = await run_gate(StubReplySource(_FOUR), barge_in=False)
    assert sink.frame_count >= len(_FOUR)
    assert "barge_in" not in manager.result.state_events
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd talkybuddy && .venv/bin/python -m pytest -q "server/streaming/tests/test_barge_in_gate.py::test_gate_bargein_stops_clean"`
Expected: FAIL — `ImportError: cannot import name 'run_gate' from 'server.streaming.harness'`

- [ ] **Step 3: 實作 `AudioAtSentenceDriver` + `run_gate`** — `talkybuddy/server/streaming/harness.py`

import 段補 `BargeInGate`：
```python
from server.streaming.barge_in_gate import BargeInDetectedFrame, BargeInGate
```

新增（放在 `InjectAtSentenceDriver` 之後）：
```python
def _speech_burst(dur_ms: int = 800) -> InputAudioRawFrame:
    """自 canned zh.wav 取一段真語音，包成單一 InputAudioRawFrame（供 gate 觸發 started）。"""
    with wave.open(str(_WAV), "rb") as wf:
        rate, ch = wf.getframerate(), wf.getnchannels()
        pcm = wf.readframes(wf.getnframes())
    n = int(rate * dur_ms / 1000) * 2 * ch
    return InputAudioRawFrame(audio=pcm[:n], sample_rate=rate, num_channels=ch)


class AudioAtSentenceDriver(FrameProcessor):
    """看到第 N 個 TTSSpeakFrame 後回注真語音 InputAudioRawFrame 到 UPSTREAM，

    使其上行穿過 manager（照常轉發）抵達上游 BargeInGate → SpeechGate 偵測 started →
    BargeInGate 往 downstream 發 BargeInDetectedFrame → manager barge-in。
    """

    def __init__(self, at_sentence: int = 2, **kwargs):
        super().__init__(**kwargs)
        self._at = at_sentence
        self._seen = 0
        self._fired = False

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSSpeakFrame):
            self._seen += 1
            if not self._fired and self._seen >= self._at:
                self._fired = True
                await self.push_frame(_speech_burst(), FrameDirection.UPSTREAM)
        await self.push_frame(frame, direction)


async def run_gate(reply_source, *, barge_in: bool, gate_params: dict | None = None):
    """真音訊經 BargeInGate 的端到端一輪，回 (OutputSink, StreamingTurnManager)。"""
    sink = OutputSink()
    manager = StreamingTurnManager(reply_source)
    procs = [BargeInGate(gate_params=gate_params), manager]
    if barge_in:
        procs.append(AudioAtSentenceDriver(at_sentence=2))
    procs += [SherpaInterruptibleTTSService(), sink]
    task = PipelineTask(Pipeline(procs))

    async def feed():
        await task.queue_frame(TranscriptionFrame(text="測試一句", user_id="u", timestamp="0"))
        await asyncio.sleep(12)
        await task.queue_frame(EndFrame())

    await asyncio.gather(PipelineRunner().run(task), feed())
    return sink, manager
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd talkybuddy && .venv/bin/python -m pytest -q server/streaming/tests/test_barge_in_gate.py`
Expected: PASS（4 passed）。
若 `test_gate_bargein_stops_clean` 的 `frame_count` 偶發 >2：barge-in 觸發稍晚於 TTS 合成——把 `AudioAtSentenceDriver` 的 `at_sentence` 保持 2、或 `_speech_burst` 時長加大以更快跨門檻；若真語音未觸發，改 `run_gate(..., gate_params={"confidence": 0.5})` 於該測試。

- [ ] **Step 5: 跑全 suite 確認無回歸 + Commit**

```bash
cd /home/budaedu/hackathon/talkybuddy && ./run_tests.sh
```
Expected: 全綠（原 189 + 本次新增：test_barge_in_gate 4 + test_turn_manager +1）。

```bash
cd /home/budaedu/hackathon
git add talkybuddy/server/streaming/harness.py talkybuddy/server/streaming/tests/test_barge_in_gate.py
git commit -m "test(a2): 真音訊經 BargeInGate 端到端 barge-in 淨停（判準 E）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Verification（端到端）

1. **全套測試**：`cd talkybuddy && ./run_tests.sh` → 全綠。
2. **五項判準對照 spec**：
   - A/B（gate 單元）＝`test_barge_in_gate.py::test_speech_emits_barge_in_detected` / `test_silence_emits_nothing`。
   - C（新來源觸發）＝`test_turn_manager.py::test_bargein_at_second_sentence_stops_clean`（現經 `BargeInDetectedFrame` 路徑）。
   - D（舊耦合已移除）＝`test_turn_manager.py::test_transport_vad_frame_no_longer_triggers_bargein`。
   - E（端到端真音訊淨停）＝`test_barge_in_gate.py::test_gate_bargein_stops_clean` + 對照 `test_gate_no_bargein_all_sentences`。
3. **不碰熱路徑 grep 驗**：
   `grep -nE "_process_text|run_turn_audio" talkybuddy/server/streaming/turn_manager.py talkybuddy/server/streaming/barge_in_gate.py` → 無輸出（僅 import `TurnResult`）。
4. **手動確認 SystemFrame 傳遞**：測試 A（gate→sink 收到 `BargeInDetectedFrame`）即證自訂 `SystemFrame` 子類能穿越處理器；若異常，見 spec 風險節 fallback（改用既有輕量 frame 帶旗標）。

## Self-Review（對 spec 逐項）

- **spec §驗收 判準 A/B/C/D/E** → 全部有對應測試（見 Verification #2）。✓
- **spec §架構「BargeInGate 上游、downstream 發訊」** → Task 1 生產路徑 downstream；harness 測試用 UPSTREAM 注入僅為**確定性計時**（沿用 A2-2 已驗手法），不改生產語意。✓（已於 driver docstring 註明）
- **spec §關鍵決策「惰性建 SpeechGate、門檻獨立旋鈕、不動 config.py」** → Task 1 `gate_params` + 惰性建立涵蓋。✓
- **spec §不動清單** → Global Constraints 鎖定；grep 驗證。✓
- **Placeholder scan** → 無 TBD/TODO；每步含完整程式碼與確切指令。✓
- **Type consistency** → `BargeInGate(gate_params=...)`、`BargeInDetectedFrame`、`run_once(..., inject_frame_factory=...)`、`run_gate(..., gate_params=...)` 跨 task 一致。✓

## 執行注意（本專案）

- **勿用 workflow**（memory `talkybuddy-a2-handoff`：workflow 巢狀 sonnet subagent + 嚴格 schema 反覆失敗）。若採 subagent-driven-development，用一般 Task subagent 前景執行即可。
- 執行完成後：把本 plan 另存 `talkybuddy/docs/superpowers/plans/2026-07-10-a2-speechgate-barge-in.md` 並 commit（plan mode 下暫存於 `~/.claude/plans/`）。
