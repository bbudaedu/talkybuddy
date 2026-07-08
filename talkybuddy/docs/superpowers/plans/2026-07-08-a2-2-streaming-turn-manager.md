# A2-2 StreamingTurnManager + Barge-in 迴路 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改動 `_process_text` / `run_turn_audio` 的前提下，於 `server/streaming/` 新增一條線上串流全雙工迴路：Silero VAD 偵測開口即 barge-in → 句界乾淨中止進行中的回覆，用 canned wav harness 二元淨判驗收。

**Architecture:** Pipecat 1.5.0 pipeline，四個框架無關/薄封裝元件 + 一個編排 processor。核心 `InterruptibleSynth`（sherpa callback 中止）與 `SpeechGate`（Silero VAD 邊緣偵測）保持純 Python、可獨立單元測試；`StreamingTurnManager` 消費 `TranscriptionFrame` → 逐句經可抽換 `ReplySource` → 逐句 `TTSSpeakFrame`，偵測「說話中的第二次開口」即 barge-in。`ReplySource` 介面隔離大腦形態，對 research/16 決策免疫。

**Tech Stack:** Python 3.12、Pipecat 1.5.0、sherpa-onnx 1.13.4（TTS）、Silero VAD（`pipecat.audio.vad.silero`）、FunASRSTTService（SenseVoiceSmall）、pytest。全部跑在**已驗的 spike venv**。

## Global Constraints

- **環境**：所有程式與測試跑在 `spike/a2_pipecat/.venv`（venv 決策 2026-07-08：共用 spike venv，不併入正式 `.venv`）。pytest 一律從 `talkybuddy/` 根目錄執行：`spike/a2_pipecat/.venv/bin/python -m pytest ...`。
- **落點**：新程式一律在 `server/streaming/`，非 `spike/`。
- **零改動不變式**：`server/pipeline.py` 的 `_process_text` / `run_turn_audio` 一行不動；`server/streaming/` 不得 import `server.pipeline` 的熱路徑函式（`VoicePipeline` / `_process_text` / `run_turn_audio`）。此為 grep 驗收項。
- **產物型別**：一輪產出沿用既有 `TurnResult`（`server/pipeline.py:36`），本 spec 只填 `asr_text` / `reply_text` / `state_events` / `latency_ms`。
- **Barge-in 併發順序**（spec 定案）：barge-in 觸發時**先**令合成中止（push `InterruptionFrame` → TTS service `synth.interrupt()`），**後** cancel 上游 reply-streaming task。
- **中斷輪產物**：`reply_text` = 中斷前已合成的部分句；`state_events` 須含 `"barge_in"`。錯誤降級輪 `reply_text` = 空字串。
- **Pipecat 1.5.0 實測事實**（承 A2-1 spike，勿再犯）：`FrameProcessor.process_frame` **不自動轉發** frame，每個 processor 自行 `push_frame`；interruption frame 類名＝`InterruptionFrame`（非 `StartInterruptionFrame`）；`FunASRSTTService`＝`SegmentedSTTService`，只由 `VADUserStartedSpeakingFrame`→audio→`VADUserStoppedSpeakingFrame` 前綴框段落觸發 `run_stt`。

---

### Task 0: 建 `server/streaming/` 套件 + 畢業 `InterruptibleSynth`

把 spike 驗過的 `interruptible_synth.py`（含 `split_sentences`）原封搬入正式落點，連同其 5 個 pytest。**只搬位置、不改邏輯**。這一步鎖定套件結構與測試執行慣例。

**Files:**
- Create: `server/streaming/__init__.py`
- Create: `server/streaming/interruptible_synth.py`（內容＝`spike/a2_pipecat/interruptible_synth.py` 原封搬入 + 補來源 docstring）
- Create: `server/streaming/tests/__init__.py`
- Create: `server/streaming/tests/test_interruptible_synth.py`（內容＝`spike/a2_pipecat/tests/test_interruptible_synth.py`，import 路徑改為 `server.streaming.*`）
- Create: `server/streaming/tests/sherpa_voice.py`（測試用 zh voice 載入，內容＝`spike/a2_pipecat/sherpa_voice.py` 原封搬入）

**Interfaces:**
- Consumes: 無（起點）。
- Produces:
  - `server.streaming.interruptible_synth.split_sentences(text: str) -> list[str]`
  - `server.streaming.interruptible_synth.InterruptibleSynth(voice)`，方法 `interrupt()` / `reset()` / `is_interrupted() -> bool` / `synth_one(sentence: str) -> object | None` / `synth_sentences(sentences) -> Iterator[tuple[int, object]]`
  - `server.streaming.tests.sherpa_voice.load_zh_voice() -> sherpa_onnx.OfflineTts`

- [ ] **Step 1: 建套件骨架**

```bash
mkdir -p server/streaming/tests
: > server/streaming/__init__.py
: > server/streaming/tests/__init__.py
```

- [ ] **Step 2: 搬入 `interruptible_synth.py`（原封 + 補來源 docstring）**

複製 `spike/a2_pipecat/interruptible_synth.py` 全文到 `server/streaming/interruptible_synth.py`，僅在檔首 docstring 末補一行來源註記。完整檔案內容：

```python
"""engine-agnostic 可中止逐句合成核心：sherpa callback 中止 + 句界 break。

與 Pipecat 完全解耦，故可獨立單元測試。Pipecat TTS service（interruptible_tts.py）
只是把本類包一層：非同步 wrapper 逐句以 asyncio.to_thread 呼叫 synth_one，
好讓 event loop 在句間仍能處理 InterruptionFrame。

來源：從 spike/a2_pipecat/interruptible_synth.py 畢業搬入（A2-1 spike 已 5 pytest 驗過），
邏輯一行未改，僅更換模組落點（A2-2 spec 2026-07-08）。
"""
from __future__ import annotations

import re
import threading
from typing import Iterator, Optional

_SENT_SPLIT = re.compile(r"[^。！？!?]*[。！？!?]")


def split_sentences(text: str) -> list[str]:
    """按中英標點切句，去除首尾空白，丟棄純空白句。"""
    out = []
    for m in _SENT_SPLIT.findall(text or ""):
        s = m.strip()
        if s and re.sub(r"[。！？!?\s]", "", s):
            out.append(s)
    return out


class InterruptibleSynth:
    """逐句呼叫 sherpa OfflineTts.generate(text, callback)，被打斷時句界乾淨停止。"""

    def __init__(self, voice) -> None:
        self._voice = voice
        self._interrupted = threading.Event()

    def interrupt(self) -> None:
        self._interrupted.set()

    def reset(self) -> None:
        self._interrupted.clear()

    def is_interrupted(self) -> bool:
        return self._interrupted.is_set()

    def _callback(self, samples, progress) -> int:  # sherpa 契約：回非 0 即中止當前 generate
        return 1 if self._interrupted.is_set() else 0

    def synth_one(self, sentence: str) -> Optional[object]:
        """合成單句；已中止則不開始（回 None），合成中途被打斷也回 None。"""
        if self._interrupted.is_set():
            return None
        audio = self._voice.generate(sentence, callback=self._callback)
        if self._interrupted.is_set():
            return None  # callback 在合成中途回非 0、於句內提早中止
        return audio

    def synth_sentences(self, sentences) -> Iterator[tuple[int, object]]:
        for idx, sentence in enumerate(sentences):
            if self._interrupted.is_set():
                break  # 句界：不再開始下一句
            audio = self.synth_one(sentence)
            if audio is None:
                break
            yield idx, audio
```

- [ ] **Step 3: 搬入測試用 `sherpa_voice.py`（原封）**

複製 `spike/a2_pipecat/sherpa_voice.py` 全文到 `server/streaming/tests/sherpa_voice.py`，僅更新註解中的路徑層數說明（`server/streaming/tests/` 往上 3 層 = `talkybuddy/`）：

```python
"""測試用最小 sherpa-onnx zh voice 載入（指向現成 _sherpa_cache 快取）。

從 spike/a2_pipecat/sherpa_voice.py 搬入。刻意不 import server/tts.py，
避開 onnx/piper 依賴，沿用現成 patched onnx + tokens.txt。
"""
from __future__ import annotations

from pathlib import Path

# server/streaming/tests/ 往上三層 = talkybuddy/
_TB_ROOT = Path(__file__).resolve().parents[3]
_CACHE = _TB_ROOT / "models" / "_sherpa_cache"
_ONNX = _CACHE / "zh_CN-huayan-medium.onnx"
_TOKENS = _CACHE / "zh_CN-huayan-medium.tokens.txt"
_ESPEAK = _TB_ROOT / ".venv" / "lib" / "python3.12" / "site-packages" / "piper" / "espeak-ng-data"


def load_zh_voice():
    """載入 huayan 中文 sherpa OfflineTts；任何檔缺則 raise FileNotFoundError。"""
    import sherpa_onnx

    for p in (_ONNX, _TOKENS, _ESPEAK):
        if not p.exists():
            raise FileNotFoundError(f"sherpa asset missing: {p}")

    vits = sherpa_onnx.OfflineTtsVitsModelConfig(
        model=str(_ONNX), tokens=str(_TOKENS), data_dir=str(_ESPEAK), lexicon=""
    )
    model_cfg = sherpa_onnx.OfflineTtsModelConfig(vits=vits, num_threads=1, provider="cpu")
    tts_cfg = sherpa_onnx.OfflineTtsConfig(model=model_cfg)
    if not tts_cfg.validate():
        raise RuntimeError("invalid sherpa-onnx tts config")
    return sherpa_onnx.OfflineTts(tts_cfg)
```

- [ ] **Step 4: 搬入測試（import 路徑更新）**

`server/streaming/tests/test_interruptible_synth.py` 全文：

```python
"""可中止逐句合成核心的單元測試（真 sherpa，不依賴 Pipecat）。

從 spike/a2_pipecat/tests/test_interruptible_synth.py 搬入，import 路徑改 server.streaming.*。
"""
import numpy as np
import pytest

from server.streaming.interruptible_synth import InterruptibleSynth, split_sentences
from server.streaming.tests.sherpa_voice import load_zh_voice

_REPLY = "你好呀，我是企鵝。今天天氣真好。我們一起來玩遊戲吧。你想先做什麼呢？"


def test_split_sentences_by_punct():
    out = split_sentences(_REPLY)
    assert out == ["你好呀，我是企鵝。", "今天天氣真好。", "我們一起來玩遊戲吧。", "你想先做什麼呢？"]


def test_split_sentences_drops_empty():
    assert split_sentences("  。！  ？ ") == []
    assert split_sentences("") == []


@pytest.fixture(scope="module")
def voice():
    return load_zh_voice()


def test_synth_all_when_not_interrupted(voice):
    synth = InterruptibleSynth(voice)
    produced = list(synth.synth_sentences(split_sentences(_REPLY)))
    assert len(produced) == 4
    for _, audio in produced:
        assert np.asarray(audio.samples).size > 0


def test_callback_returns_nonzero_only_when_interrupted(voice):
    synth = InterruptibleSynth(voice)
    dummy = np.zeros(4, dtype=np.float32)
    assert synth._callback(dummy, 0.5) == 0
    synth.interrupt()
    assert synth._callback(dummy, 0.5) != 0


def test_stops_at_sentence_boundary_after_interrupt(voice):
    """第 1 句合成後 interrupt → 不再合成後續句（句界乾淨停）。"""
    synth = InterruptibleSynth(voice)
    sents = split_sentences(_REPLY)
    seen = []
    for idx, _audio in synth.synth_sentences(sents):
        seen.append(idx)
        if idx == 0:
            synth.interrupt()
    assert seen == [0]
```

- [ ] **Step 5: 跑測試（真 sherpa，5 passed）**

Run: `spike/a2_pipecat/.venv/bin/python -m pytest server/streaming/tests/test_interruptible_synth.py -v`
Expected: PASS，5 passed（`test_split_sentences_by_punct`、`test_split_sentences_drops_empty`、`test_synth_all_when_not_interrupted`、`test_callback_returns_nonzero_only_when_interrupted`、`test_stops_at_sentence_boundary_after_interrupt`）。

- [ ] **Step 6: Commit**

```bash
git add server/streaming/__init__.py server/streaming/interruptible_synth.py server/streaming/tests/
git commit -m "feat(a2-2): graduate InterruptibleSynth into server/streaming/ (5 pytest pass)"
```

---

### Task 1: `ReplySource` 介面 + `StubReplySource`（大腦免疫接縫）

定義可抽換回覆句流介面 + harness 用的固定多句 Stub。此為對 research/16 大腦決策免疫的關鍵接縫。

**Files:**
- Create: `server/streaming/reply_source.py`
- Create: `server/streaming/tests/test_reply_source.py`

**Interfaces:**
- Consumes: 無（純介面 + Stub）。
- Produces:
  - `server.streaming.reply_source.ReplyContext`（dataclass；欄位 `directive: str | None = None`、`scaffold_result: object | None = None`）
  - `server.streaming.reply_source.ReplySource`（Protocol）：`stream(self, asr_text: str, ctx: ReplyContext) -> AsyncIterator[str]`
  - `server.streaming.reply_source.StubReplySource(sentences: list[str] | None = None)`，實作 `stream`，逐句 yield（預設 4 句）。

- [ ] **Step 1: 寫失敗測試**

`server/streaming/tests/test_reply_source.py`：

```python
"""ReplySource 介面與 StubReplySource 單元測試（不需真模型）。"""
import pytest

from server.streaming.reply_source import ReplyContext, StubReplySource


async def _collect(source, asr_text, ctx):
    return [s async for s in source.stream(asr_text, ctx)]


@pytest.mark.asyncio
async def test_stub_yields_default_four_sentences():
    source = StubReplySource()
    out = await _collect(source, "隨便一句", ReplyContext())
    assert out == ["你好呀，我是企鵝。", "今天天氣真好。", "我們一起來玩遊戲吧。", "你想先做什麼呢？"]


@pytest.mark.asyncio
async def test_stub_yields_custom_sentences():
    source = StubReplySource(sentences=["甲。", "乙。"])
    out = await _collect(source, "x", ReplyContext(directive="測試策略"))
    assert out == ["甲。", "乙。"]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `spike/a2_pipecat/.venv/bin/python -m pytest server/streaming/tests/test_reply_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.streaming.reply_source'`（或 asyncio 標記缺失，見 Step 3 處理）。

- [ ] **Step 3: 確認/安裝 pytest-asyncio（僅一次）**

Run: `spike/a2_pipecat/.venv/bin/python -c "import pytest_asyncio; print(pytest_asyncio.__version__)"`
若 `ModuleNotFoundError`，則：`spike/a2_pipecat/.venv/bin/pip install pytest-asyncio` 並在 `spike/a2_pipecat/requirements.txt` 補一行 `pytest-asyncio`。
於 `server/streaming/tests/` 建 `conftest.py` 設定 asyncio 模式（避免每個測試標 marker 也可，但集中設定較乾淨）：

```python
# server/streaming/tests/conftest.py
import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: async test")


# pytest-asyncio 自動模式：讓 async def 測試免逐一標 marker
import pytest_asyncio  # noqa: E402
```

若 pytest-asyncio ≥0.21，改用更穩妥的設定：於 `server/streaming/tests/conftest.py` 僅放：

```python
import pytest_asyncio  # noqa: F401
```

並於 `server/streaming/tests/pytest.ini` 寫：

```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 4: 寫最小實作**

`server/streaming/reply_source.py`：

```python
"""可抽換回覆句流介面（⚠️ 大腦免疫接縫）。

StreamingTurnManager 只依賴 ReplySource.stream；日後本地串流 EdgeLLM、
雲端 Bedrock FM 各實作一個 ReplySource，turn_manager 不動。對 research/16
（大腦換 Bedrock FM）決策免疫。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol, runtime_checkable

_STUB_REPLY = ["你好呀，我是企鵝。", "今天天氣真好。", "我們一起來玩遊戲吧。", "你想先做什麼呢？"]


@dataclass
class ReplyContext:
    """一輪回覆的上下文；不含任何 Pipecat 框架型別（保持 ReplySource 框架無關）。"""

    directive: str | None = None
    scaffold_result: object | None = None


@runtime_checkable
class ReplySource(Protocol):
    """逐句 yield 回覆文字（一次一句，供 InterruptibleSynth 立即合成）。"""

    def stream(self, asr_text: str, ctx: ReplyContext) -> AsyncIterator[str]:
        ...


class StubReplySource:
    """yield 固定多句中文（harness 用，確保有句界可打斷）。"""

    def __init__(self, sentences: list[str] | None = None) -> None:
        self._sentences = list(sentences) if sentences is not None else list(_STUB_REPLY)

    async def stream(self, asr_text: str, ctx: ReplyContext) -> AsyncIterator[str]:
        for sentence in self._sentences:
            yield sentence
```

- [ ] **Step 5: 跑測試確認通過**

Run: `spike/a2_pipecat/.venv/bin/python -m pytest server/streaming/tests/test_reply_source.py -v`
Expected: PASS，2 passed。

- [ ] **Step 6: Commit**

```bash
git add server/streaming/reply_source.py server/streaming/tests/test_reply_source.py server/streaming/tests/conftest.py server/streaming/tests/pytest.ini spike/a2_pipecat/requirements.txt
git commit -m "feat(a2-2): ReplySource protocol + StubReplySource (brain-immune seam)"
```

---

### Task 2: `BatchReplySource`（包現有 scaffold + 批次 LLM）

過渡實作：讓非串流大腦也能掛上此迴路。包 `scaffold.respond()` + `llm.generate()`，一次吐完整段再 `split_sentences` 切句逐句 yield。`llm.generate` 無模型時回 `None` → 降級用 `scaffold.reply_text`。

**Files:**
- Create: `server/streaming/batch_reply_source.py`
- Create: `server/streaming/tests/test_batch_reply_source.py`

**Interfaces:**
- Consumes:
  - `server.scaffold.respond(student_text: str) -> ScaffoldResult`（`ScaffoldResult.reply_text: str`、`.target_sentence`）
  - `server.llm.LLMEngine`（或現有模組級 `generate`）：`generate(student_text: str, scaffold: ScaffoldResult, directive: str | None) -> str | None`
  - `server.streaming.reply_source.ReplyContext`、`split_sentences`
- Produces:
  - `server.streaming.batch_reply_source.BatchReplySource(llm=None)`，實作 `ReplySource.stream`；`llm` 可注入（測試用 stub），預設用 `server.llm` 的模組級 generate。

- [ ] **Step 1: 確認 llm.generate 呼叫形式**

Run: `spike/a2_pipecat/.venv/bin/python -c "import sys; sys.path.insert(0,'.'); import inspect, server.llm as L; print([n for n in dir(L) if not n.startswith('_')]); print(inspect.signature(L.LLMEngine.generate) if hasattr(L,'LLMEngine') else 'no LLMEngine')"`
Expected: 列出 `server.llm` 公開名稱。以實測結果決定注入點：若有 `LLMEngine` 類，`BatchReplySource` 收一個具 `.generate(student_text, scaffold, directive)` 的物件；若只有模組級函式，則 `llm` 參數預設 `server.llm`（duck-typed，呼叫 `llm.generate(...)`）。下方實作採「注入具 `generate` 的物件、預設 `server.llm` 模組」的 duck-typing 寫法，兩種形態皆容納。

- [ ] **Step 2: 寫失敗測試（stub 掉 scaffold/llm，免真模型）**

`server/streaming/tests/test_batch_reply_source.py`：

```python
"""BatchReplySource 單元測試：包批次後切句正確；LLM 回 None 時降級用 scaffold。"""
from dataclasses import dataclass

import pytest

from server.streaming.batch_reply_source import BatchReplySource
from server.streaming.reply_source import ReplyContext


@dataclass
class _FakeScaffold:
    reply_text: str
    target_sentence: str | None = None


class _FakeLLM:
    def __init__(self, out):
        self._out = out

    def generate(self, student_text, scaffold, directive=None):
        return self._out


async def _collect(source, asr_text, ctx):
    return [s async for s in source.stream(asr_text, ctx)]


@pytest.mark.asyncio
async def test_uses_llm_output_split_into_sentences(monkeypatch):
    import server.streaming.batch_reply_source as mod
    monkeypatch.setattr(mod, "respond", lambda t: _FakeScaffold(reply_text="鷹架句。"))
    source = BatchReplySource(llm=_FakeLLM("你好。今天天氣真好。"))
    out = await _collect(source, "學生說的話", ReplyContext())
    assert out == ["你好。", "今天天氣真好。"]


@pytest.mark.asyncio
async def test_falls_back_to_scaffold_when_llm_none(monkeypatch):
    import server.streaming.batch_reply_source as mod
    monkeypatch.setattr(mod, "respond", lambda t: _FakeScaffold(reply_text="很棒喔。跟我說一遍：apple。"))
    source = BatchReplySource(llm=_FakeLLM(None))
    out = await _collect(source, "蘋果", ReplyContext())
    assert out == ["很棒喔。", "跟我說一遍：apple。"]
```

- [ ] **Step 3: 跑測試確認失敗**

Run: `spike/a2_pipecat/.venv/bin/python -m pytest server/streaming/tests/test_batch_reply_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.streaming.batch_reply_source'`。

- [ ] **Step 4: 寫最小實作**

`server/streaming/batch_reply_source.py`：

```python
"""BatchReplySource：包現有批次大腦（scaffold + 一次性 LLM）成 ReplySource。

過渡實作，讓非串流大腦也能掛上串流迴路：一次算完整段回覆 → split_sentences
切句 → 逐句 yield。llm.generate 無模型/逾時/安全命中回 None → 降級用 scaffold 文字。
真串流 EdgeLLM / Bedrock FM 各自另立一個 ReplySource（不走本類）。
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

import server.llm as _default_llm
from server.scaffold import respond
from server.streaming.interruptible_synth import split_sentences
from server.streaming.reply_source import ReplyContext


class BatchReplySource:
    """包 scaffold.respond + 批次 llm.generate，切句後逐句 yield。"""

    def __init__(self, llm=None) -> None:
        # llm：任何具 generate(student_text, scaffold, directive) 的物件；預設 server.llm 模組。
        self._llm = llm if llm is not None else _default_llm

    async def stream(self, asr_text: str, ctx: ReplyContext) -> AsyncIterator[str]:
        # scaffold + llm 皆為阻塞呼叫 → 丟 thread，避免卡 event loop。
        scaffold = await asyncio.to_thread(respond, asr_text)
        enriched = await asyncio.to_thread(
            self._llm.generate, asr_text, scaffold, ctx.directive
        )
        full = enriched if (enriched and enriched.strip()) else scaffold.reply_text
        for sentence in split_sentences(full):
            yield sentence
```

- [ ] **Step 5: 跑測試確認通過**

Run: `spike/a2_pipecat/.venv/bin/python -m pytest server/streaming/tests/test_batch_reply_source.py -v`
Expected: PASS，2 passed。

- [ ] **Step 6: Commit**

```bash
git add server/streaming/batch_reply_source.py server/streaming/tests/test_batch_reply_source.py
git commit -m "feat(a2-2): BatchReplySource wrapping scaffold + batch LLM into sentence stream"
```

---

### Task 3: `SpeechGate`（真 Silero VAD 邊緣偵測）

薄封裝 `SileroVADAnalyzer`（實測 API：`analyze_audio(bytes) -> VADState`、`num_frames_required()`），把連續音訊 bytes 轉成 `"started"` / `"stopped"` 說話邊緣事件。框架無關、可用 canned wav 單元測試（criterion #5「真 Silero VAD」）。

**Files:**
- Create: `server/streaming/vad.py`
- Create: `server/streaming/tests/test_vad.py`

**Interfaces:**
- Consumes: `pipecat.audio.vad.silero.SileroVADAnalyzer`、`pipecat.audio.vad.vad_analyzer.VADParams` / `VADState`。
- Produces:
  - `server.streaming.vad.build_analyzer(sample_rate: int, *, confidence: float = 0.7, start_secs: float = 0.2, stop_secs: float = 0.8, min_volume: float = 0.6)`
  - `server.streaming.vad.SpeechGate(analyzer)`，方法 `push(pcm: bytes) -> list[str]`（回 `"started"` / `"stopped"` 事件序列；內部緩衝並以 `num_frames_required()` 切塊餵入）、`reset()`。

- [ ] **Step 1: 寫失敗測試（真 Silero VAD 跑 canned wav）**

`server/streaming/tests/test_vad.py`：

```python
"""SpeechGate 單元測試：真 Silero VAD，canned wav 出現 started→stopped；靜音無事件。"""
import wave
from pathlib import Path

import pytest

from server.streaming.vad import SpeechGate, build_analyzer

_TB_ROOT = Path(__file__).resolve().parents[3]
_WAV = _TB_ROOT / "models" / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17" / "test_wavs" / "zh.wav"


def _load_pcm():
    with wave.open(str(_WAV), "rb") as wf:
        return wf.getframerate(), wf.readframes(wf.getnframes())


def test_speech_wav_emits_started_then_stopped():
    rate, pcm = _load_pcm()
    gate = SpeechGate(build_analyzer(rate))
    events = []
    step = 640  # 320 samples * 2 bytes；SpeechGate 內部會再依 num_frames_required 切塊
    for i in range(0, len(pcm), step):
        events += gate.push(pcm[i:i + step])
    # 尾端補一段靜音，逼 VAD 收斂到 stopped
    events += gate.push(b"\x00" * rate)  # 0.5s 靜音（int16）
    assert "started" in events
    assert events.index("started") < events.index("stopped")


def test_silence_emits_no_started():
    rate = 16000
    gate = SpeechGate(build_analyzer(rate))
    events = []
    for _ in range(20):
        events += gate.push(b"\x00" * 640)
    assert "started" not in events
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `spike/a2_pipecat/.venv/bin/python -m pytest server/streaming/tests/test_vad.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.streaming.vad'`。

- [ ] **Step 3: 寫最小實作**

`server/streaming/vad.py`：

```python
"""Silero VAD 薄封裝：連續音訊 bytes → started/stopped 說話邊緣事件。

實測 API（Pipecat 1.5.0）：
- SileroVADAnalyzer(sample_rate=int, params=VADParams(confidence, start_secs, stop_secs, min_volume))
- analyze_audio(buffer: bytes) -> VADState ∈ {QUIET, STARTING, SPEAKING, STOPPING}
- num_frames_required() -> int（每次 analyze 需固定樣本數；buffer bytes = frames * 2, int16 mono）

參數集中此處便於日後上機調校（research/12：繁中兒童門檻待實測）。
"""
from __future__ import annotations

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams, VADState


def build_analyzer(
    sample_rate: int,
    *,
    confidence: float = 0.7,
    start_secs: float = 0.2,
    stop_secs: float = 0.8,
    min_volume: float = 0.6,
) -> SileroVADAnalyzer:
    """建 Silero VAD analyzer；stop_secs 保守取值避免把停頓誤判為輪結束。"""
    params = VADParams(
        confidence=confidence,
        start_secs=start_secs,
        stop_secs=stop_secs,
        min_volume=min_volume,
    )
    return SileroVADAnalyzer(sample_rate=sample_rate, params=params)


class SpeechGate:
    """把 SileroVADAnalyzer 的逐塊 VADState 折成 started/stopped 邊緣事件。

    分析器要求固定大小 buffer（num_frames_required），故本類自帶位元緩衝，
    湊滿一塊才 analyze，剩餘位元留待下次 push。
    """

    def __init__(self, analyzer: SileroVADAnalyzer) -> None:
        self._analyzer = analyzer
        self._chunk_bytes = analyzer.num_frames_required() * 2  # int16 mono
        self._buf = bytearray()
        self._speaking = False

    def reset(self) -> None:
        self._buf.clear()
        self._speaking = False

    def push(self, pcm: bytes) -> list[str]:
        """餵入任意長度音訊 bytes，回本次觸發的邊緣事件序列。"""
        self._buf.extend(pcm)
        events: list[str] = []
        while len(self._buf) >= self._chunk_bytes:
            chunk = bytes(self._buf[: self._chunk_bytes])
            del self._buf[: self._chunk_bytes]
            state = self._analyzer.analyze_audio(chunk)
            if state == VADState.SPEAKING and not self._speaking:
                self._speaking = True
                events.append("started")
            elif state == VADState.QUIET and self._speaking:
                self._speaking = False
                events.append("stopped")
        return events
```

- [ ] **Step 4: 跑測試確認通過**

Run: `spike/a2_pipecat/.venv/bin/python -m pytest server/streaming/tests/test_vad.py -v`
Expected: PASS，2 passed。若 `test_speech_wav_emits_started_then_stopped` 因門檻未觸發 `started`，將 `build_analyzer(rate)` 的 `confidence` 調低（如 0.5）重跑；此為 spec 允許的參數上機調校，非邏輯 bug（記錄調整值於 commit 訊息）。

- [ ] **Step 5: Commit**

```bash
git add server/streaming/vad.py server/streaming/tests/test_vad.py
git commit -m "feat(a2-2): SpeechGate wrapping real Silero VAD into started/stopped edges"
```

---

### Task 4: 畢業 `SherpaInterruptibleTTSService`（Pipecat TTS service）

把 spike 的 `interruptible_tts.py` 搬入正式落點，import 改指向 `server.streaming.*`。邏輯不改（run_tts 逐句、process_frame 攔 `InterruptionFrame` 先 `synth.interrupt()`）。

**Files:**
- Create: `server/streaming/interruptible_tts.py`（內容＝`spike/a2_pipecat/interruptible_tts.py`，import 路徑更新）

**Interfaces:**
- Consumes: `server.streaming.interruptible_synth.{InterruptibleSynth, split_sentences}`、`load_zh_voice`（正式 sherpa voice loader，見 Step 1 決策）。
- Produces: `server.streaming.interruptible_tts.SherpaInterruptibleTTSService(voice=None)`，Pipecat `TTSService` 子類；`run_tts(text, context_id)` 逐句 yield `TTSAudioRawFrame`；`process_frame` 收 `InterruptionFrame` → `synth.interrupt()`。

- [ ] **Step 1: 決定 voice loader 來源**

TTS service 建構需一個 sherpa voice。為讓 harness 與正式路徑一致，`SherpaInterruptibleTTSService.__init__` 收可選 `voice` 參數（注入），預設呼叫 `server.streaming.tests.sherpa_voice.load_zh_voice()`。
Run: `spike/a2_pipecat/.venv/bin/python -c "import sys; sys.path.insert(0,'.'); from server.streaming.tests.sherpa_voice import load_zh_voice; v=load_zh_voice(); print('voice ok', v.sample_rate)"`
Expected: 印出 `voice ok <sample_rate>`（如 22050）。

- [ ] **Step 2: 搬入 `interruptible_tts.py`（import 更新）**

`server/streaming/interruptible_tts.py` 全文：

```python
"""把 InterruptibleSynth 包成 Pipecat TTS service（從 spike 畢業搬入）。

Pipecat 1.5.0 實測：
- 基類＝pipecat.services.tts_service.TTSService（純本地同步 TTS）。
- run_tts(self, text, context_id) 為 async generator，yield TTSAudioRawFrame。
- interruption frame＝InterruptionFrame；base.process_frame 收到走 _handle_interruption
  並取消 run_tts task。額外覆寫 process_frame，在 super 之前令核心 interrupt()。
- sherpa generate 阻塞 → 逐句 asyncio.to_thread，event loop 才能句間處理 InterruptionFrame。
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
```

- [ ] **Step 3: 冒煙驗證（可 import + 建構）**

Run: `spike/a2_pipecat/.venv/bin/python -c "import sys; sys.path.insert(0,'.'); from server.streaming.interruptible_tts import SherpaInterruptibleTTSService; s=SherpaInterruptibleTTSService(); print('tts service ok')"`
Expected: 印出 `tts service ok`（無 exception）。

- [ ] **Step 4: Commit**

```bash
git add server/streaming/interruptible_tts.py
git commit -m "feat(a2-2): graduate SherpaInterruptibleTTSService into server/streaming/"
```

---

### Task 5: `StreamingTurnManager`（核心編排 + barge-in）

Pipecat processor：消費 `TranscriptionFrame` → `ReplySource.stream()` 逐句 → 逐句 push `TTSSpeakFrame`（供下游 TTS service 逐句合成）；偵測「回覆進行中的第二次 `VADUserStartedSpeakingFrame`」＝barge-in → **先** push `InterruptionFrame`（令 TTS 句界停）、**後** cancel reply task；產出 `TurnResult`（`state_events` 記 `"barge_in"`）。

**Files:**
- Create: `server/streaming/turn_manager.py`

**Interfaces:**
- Consumes:
  - `server.streaming.reply_source.{ReplySource, ReplyContext}`
  - `server.pipeline.TurnResult`（只 import 這個 dataclass，非熱路徑函式）
  - Pipecat：`FrameProcessor` / `FrameDirection`、`TranscriptionFrame` / `TTSSpeakFrame` / `InterruptionFrame` / `VADUserStartedSpeakingFrame`
- Produces:
  - `server.streaming.turn_manager.StreamingTurnManager(reply_source: ReplySource, ctx: ReplyContext | None = None)`，Pipecat `FrameProcessor` 子類。
  - 屬性 `result: TurnResult`（跑完一輪後可讀：`asr_text` / `reply_text` / `state_events`）。

- [ ] **Step 1: 寫實作**

`server/streaming/turn_manager.py`：

```python
"""StreamingTurnManager：串流全雙工一輪的編排 + barge-in 取消。

資料流：TranscriptionFrame → ReplySource.stream() 逐句 → 逐句 push TTSSpeakFrame
（下游 SherpaInterruptibleTTSService 逐句合成）。回覆進行中收到新的
VADUserStartedSpeakingFrame ＝ barge-in → 先 push InterruptionFrame（TTS 句界停）、
後 cancel reply task（停止上游餵句）。產出 TurnResult。

只 import server.pipeline.TurnResult（dataclass），不碰 _process_text/run_turn_audio 熱路徑。
"""
from __future__ import annotations

import asyncio

from pipecat.frames.frames import (
    TranscriptionFrame,
    TTSSpeakFrame,
    InterruptionFrame,
    VADUserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

from server.pipeline import TurnResult
from server.streaming.reply_source import ReplyContext, ReplySource


class StreamingTurnManager(FrameProcessor):
    def __init__(self, reply_source: ReplySource, ctx: ReplyContext | None = None, **kwargs):
        super().__init__(**kwargs)
        self._reply_source = reply_source
        self._ctx = ctx if ctx is not None else ReplyContext()
        self._reply_task: asyncio.Task | None = None
        self._bot_speaking = False
        self.result = TurnResult()

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            # 一輪開始：記 asr_text，啟動逐句回覆 task（不轉發轉錄，避免 TTS 合成轉錄文字）
            self.result.asr_text = getattr(frame, "text", "") or ""
            self._reply_task = asyncio.create_task(self._stream_reply())
            return

        if isinstance(frame, VADUserStartedSpeakingFrame) and self._bot_speaking:
            # barge-in：先中止合成（句界停），後取消上游餵句 task
            await self.push_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
            if self._reply_task is not None and not self._reply_task.done():
                self._reply_task.cancel()
            if "barge_in" not in self.result.state_events:
                self.result.state_events.append("barge_in")
            self._bot_speaking = False
            return

        # 其餘 frame（含首個 VADUserStartedSpeakingFrame、音訊、EndFrame 等）照常轉發
        await self.push_frame(frame, direction)

    async def _stream_reply(self):
        """逐句從 ReplySource 取回覆，push TTSSpeakFrame；被 cancel 時保留已合成部分句。"""
        self._bot_speaking = True
        try:
            async for sentence in self._reply_source.stream(self.result.asr_text, self._ctx):
                await self.push_frame(TTSSpeakFrame(text=sentence), FrameDirection.DOWNSTREAM)
                self.result.reply_text += sentence
        except asyncio.CancelledError:
            # barge-in 中斷：reply_text 已含中斷前的部分句，直接收斂
            raise
        finally:
            self._bot_speaking = False
```

- [ ] **Step 2: 冒煙驗證（可 import + 建構，不碰熱路徑）**

Run: `spike/a2_pipecat/.venv/bin/python -c "import sys; sys.path.insert(0,'.'); from server.streaming.turn_manager import StreamingTurnManager; from server.streaming.reply_source import StubReplySource; m=StreamingTurnManager(StubReplySource()); print('turn manager ok', type(m.result).__name__)"`
Expected: 印出 `turn manager ok TurnResult`。

- [ ] **Step 3: Commit**

```bash
git add server/streaming/turn_manager.py
git commit -m "feat(a2-2): StreamingTurnManager orchestration + barge-in cancel"
```

---

### Task 6: 驗收 harness + 二元淨判整合測試（criteria #1-#5）

組 Pipecat pipeline（模仿 spike `run_spike.py` 的 plumbing），跑兩個場景：無 barge-in → N 句全合成；第 2 句期間注入 barge-in → sink 句數 ≤ 2。加隔離不變式 grep 測試與 `TurnResult` 欄位測試。

**Files:**
- Create: `server/streaming/harness.py`（可重用的 pipeline 組裝 + 驅動）
- Create: `server/streaming/tests/test_turn_manager.py`（criteria #1/#2/#4）
- Create: `server/streaming/tests/test_isolation.py`（criterion #3）

**Interfaces:**
- Consumes: Task 1-5 全部產出；Pipecat `Pipeline` / `PipelineTask` / `PipelineRunner`、`InputAudioRawFrame` / `VADUserStartedSpeakingFrame` / `VADUserStoppedSpeakingFrame` / `TranscriptionFrame` / `TTSAudioRawFrame` / `EndFrame`。
- Produces:
  - `server.streaming.harness.OutputSink`（`FrameProcessor`；`frame_count` / `total_bytes`）
  - `server.streaming.harness.BargeInDriver(delay_s, at_frame='TTSSpeakFrame')`（看到 bot 開講後計時 push `VADUserStartedSpeakingFrame`）
  - `server.streaming.harness.run_once(reply_source, *, barge_in: bool, no_stt: bool = True) -> tuple[OutputSink, StreamingTurnManager]`

- [ ] **Step 1: 寫 harness**

`server/streaming/harness.py`：

```python
"""A2-2 驗收 harness：組 pipeline 跑二元淨判（模仿 spike run_spike.py plumbing）。

no_stt=True：直接注入 TranscriptionFrame，專驗編排/barge-in（免載 1GB SenseVoice）。
no_stt=False：走真 FunASRSTTService（VAD 前綴 frame 觸發段落轉錄）。
"""
from __future__ import annotations

import asyncio
import wave
from pathlib import Path

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.pipeline.runner import PipelineRunner
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.frames.frames import (
    InputAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    EndFrame,
)

from server.streaming.turn_manager import StreamingTurnManager
from server.streaming.interruptible_tts import SherpaInterruptibleTTSService

_TB_ROOT = Path(__file__).resolve().parents[2]
_WAV = _TB_ROOT / "models" / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17" / "test_wavs" / "zh.wav"


class OutputSink(FrameProcessor):
    """吞 TTS 音訊 frame、計數，不需真喇叭。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.total_bytes = 0
        self.frame_count = 0

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSAudioRawFrame):
            self.total_bytes += len(frame.audio)
            self.frame_count += 1
        await self.push_frame(frame, direction)


class BargeInDriver(FrameProcessor):
    """看到第 N 個 TTSSpeakFrame（bot 已開講到第 N 句）後 push VADUserStartedSpeakingFrame。

    注入點釘在第 2 句（at_sentence=2）→ 淨判：sink 合成句數 ≤ 2。
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
                await self.push_frame(
                    VADUserStartedSpeakingFrame(start_secs=0.2), FrameDirection.DOWNSTREAM
                )
        await self.push_frame(frame, direction)


def _load_wav_frames(chunk_ms: int = 200):
    with wave.open(str(_WAV), "rb") as wf:
        rate, ch = wf.getframerate(), wf.getnchannels()
        pcm = wf.readframes(wf.getnframes())
    step = int(rate * chunk_ms / 1000) * 2 * ch
    for i in range(0, len(pcm), step):
        yield InputAudioRawFrame(audio=pcm[i:i + step], sample_rate=rate, num_channels=ch)


async def run_once(reply_source, *, barge_in: bool, no_stt: bool = True):
    """跑一輪，回 (OutputSink, StreamingTurnManager)。"""
    from pipecat.services.funasr.stt import FunASRSTTService

    sink = OutputSink()
    manager = StreamingTurnManager(reply_source)

    procs = [] if no_stt else [FunASRSTTService()]
    # barge-in driver 置於 manager 之前，攔 TTSSpeakFrame 計句、回注 VAD 開口給 manager
    if barge_in:
        procs.append(BargeInDriver(at_sentence=2))
    procs.append(manager)
    procs += [SherpaInterruptibleTTSService(), sink]
    task = PipelineTask(Pipeline(procs))

    async def feed():
        if no_stt:
            await task.queue_frame(TranscriptionFrame(text="測試一句", user_id="u", timestamp="0"))
        else:
            await task.queue_frame(VADUserStartedSpeakingFrame(start_secs=0.2))
            for f in _load_wav_frames():
                await task.queue_frame(f)
            await task.queue_frame(VADUserStoppedSpeakingFrame(stop_secs=0.5))
        await asyncio.sleep(12)
        await task.queue_frame(EndFrame())

    await asyncio.gather(PipelineRunner().run(task), feed())
    return sink, manager
```

> **注意（plumbing 風險）**：`BargeInDriver` push 的 `VADUserStartedSpeakingFrame` 需被下游 `manager` 收到。Pipecat pipeline 中 `push_frame(DOWNSTREAM)` 會送至下一個 processor，故 driver 必須排在 manager **之前**（上方已如此）。若實測發現 manager 未收到（frame 被 TTS/其他消費），改用 spike `InterruptDriver` 直接 push `InterruptionFrame` 的等價路徑驗 criterion #2，並在測試註記偏離原因。此為執行期唯一保留的 plumbing 不確定點。

- [ ] **Step 2: 寫失敗測試（criteria #1/#2/#4）**

`server/streaming/tests/test_turn_manager.py`：

```python
"""StreamingTurnManager 整合測試（真 sherpa + StubReplySource，免真 LLM）。"""
import pytest

from server.streaming.harness import run_once
from server.streaming.reply_source import StubReplySource

_FOUR = ["你好呀，我是企鵝。", "今天天氣真好。", "我們一起來玩遊戲吧。", "你想先做什麼呢？"]


@pytest.mark.asyncio
async def test_no_bargein_synthesizes_all_sentences():
    # criterion #1：pipeline 不 crash；無 barge-in → N 句全合成
    sink, manager = await run_once(StubReplySource(_FOUR), barge_in=False)
    assert sink.frame_count >= len(_FOUR)  # 每句至少 1 個 TTSAudioRawFrame
    # criterion #4：無 barge-in 輪 TurnResult 欄位非空
    assert manager.result.asr_text != ""
    assert manager.result.reply_text != ""
    assert "barge_in" not in manager.result.state_events


@pytest.mark.asyncio
async def test_bargein_at_second_sentence_stops_clean():
    # criterion #2：第 2 句期間注入開口 → sink 句數 ≤ 2（句界乾淨停），比照 spike 4→1
    sink, manager = await run_once(StubReplySource(_FOUR), barge_in=True)
    assert sink.frame_count <= 2
    # criterion #4：barge-in 輪 state_events 含 barge_in；reply_text 為部分句
    assert "barge_in" in manager.result.state_events
    assert manager.result.reply_text != ""
    assert manager.result.reply_text != "".join(_FOUR)  # 未吐完整段
```

- [ ] **Step 3: 跑測試確認失敗**

Run: `spike/a2_pipecat/.venv/bin/python -m pytest server/streaming/tests/test_turn_manager.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.streaming.harness'`。

- [ ] **Step 4: 讓測試通過（harness 已於 Step 1 寫好）**

Run: `spike/a2_pipecat/.venv/bin/python -m pytest server/streaming/tests/test_turn_manager.py -v`
Expected: PASS，2 passed。若 `test_bargein_at_second_sentence_stops_clean` 的 `sink.frame_count` > 2，先確認 `SherpaInterruptibleTTSService.run_tts` 的擬真 `asyncio.sleep`（播放節奏）有生效——沒有它 4 句會在 barge-in 前全合成完；此為 spike 已定案的關鍵，勿移除。若 driver 注入的 VAD frame 未達 manager，改走註記中的 `InterruptionFrame` 等價路徑。

- [ ] **Step 5: 寫隔離不變式測試（criterion #3）**

`server/streaming/tests/test_isolation.py`：

```python
"""criterion #3：server/streaming/ 未 import server.pipeline 熱路徑函式、未改 _process_text。"""
import ast
from pathlib import Path

_STREAMING = Path(__file__).resolve().parents[1]
_HOT = {"VoicePipeline", "_process_text", "run_turn_audio"}


def _py_files():
    for p in _STREAMING.glob("*.py"):
        yield p


def test_streaming_does_not_import_pipeline_hotpath():
    offenders = []
    for p in _py_files():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "server.pipeline":
                names = {a.name for a in node.names}
                bad = names & _HOT
                if bad:
                    offenders.append((p.name, bad))
    assert offenders == [], f"streaming imported pipeline hot-path: {offenders}"


def test_process_text_unchanged_signature():
    # 確認熱路徑函式仍存在且簽章未被 A2-2 動到（防呆：只驗存在 + 參數名）
    import inspect
    from server.pipeline import VoicePipeline
    sig = inspect.signature(VoicePipeline._process_text)
    assert list(sig.parameters)[:1] == ["self"]
```

- [ ] **Step 6: 跑隔離測試 + 全套件回歸**

Run: `spike/a2_pipecat/.venv/bin/python -m pytest server/streaming/tests/ -v`
Expected: PASS，全數通過（Task 0 的 5 + Task 1 的 2 + Task 2 的 2 + Task 3 的 2 + Task 6 的 2 + 2 = 15 passed）。

- [ ] **Step 7: 手動確認熱路徑零改動（grep + git）**

Run: `git diff --stat HEAD~7 -- server/pipeline.py`
Expected: 無輸出（`server/pipeline.py` 未被本 plan 任何 task 修改）。
Run: `grep -rn "import.*run_turn_audio\|import.*_process_text\|VoicePipeline" server/streaming/ | grep -v tests`
Expected: 無輸出（除 `turn_manager.py` 的 `from server.pipeline import TurnResult` 外，不 import 熱路徑）。

- [ ] **Step 8: Commit**

```bash
git add server/streaming/harness.py server/streaming/tests/test_turn_manager.py server/streaming/tests/test_isolation.py
git commit -m "feat(a2-2): acceptance harness + binary barge-in judge (criteria #1-#5, 15 pytest pass)"
```

---

## Self-Review

**1. Spec coverage（逐段對照 spec）：**
- `server/streaming/` 新套件 → Task 0。
- `interruptible_synth.py` 畢業搬入 → Task 0。
- `ReplySource` 介面 + `StubReplySource` + `ReplyContext` → Task 1。
- `BatchReplySource` 包批次 → Task 2。
- `vad.py` Silero VAD 封裝（參數集中）→ Task 3。
- `SherpaInterruptibleTTSService` 畢業 → Task 4。
- `StreamingTurnManager`（輪邊界 + barge-in 取消 + 串接）→ Task 5。
- 資料流 / barge-in 語意 / 併發順序 → Task 5 實作 + Global Constraints。
- 驗收 criteria #1-#5 → Task 6（#1 不 crash、#2 ≤2 淨判、#3 隔離 grep、#4 TurnResult 欄位含中斷輪、#5 真 sherpa+真 VAD+Stub）。
- 錯誤降級（例外回退空回覆）→ `BatchReplySource` 降級 + `TurnResult` 中斷輪語意；純例外韌性可於執行期補（spec「demo 韌性優先」），若要顯式測試可加一則 `test_reply_source_exception_yields_empty`（非阻塞項）。
- venv 決策 → Global Constraints + 全 Task 執行指令。

**2. Placeholder scan：** 無 TBD/TODO；每個 code step 附完整程式碼；每個 test step 附完整測試碼與預期輸出。唯二執行期不確定點已顯式標記並各給 fallback：(a) Task 3 VAD 門檻參數可能需調（spec 允許）、(b) Task 6 `BargeInDriver` 的 VAD frame 是否達 manager（附 `InterruptionFrame` 等價路徑）。此二為誠實的實測相依，非佔位符。

**3. Type consistency：**
- `split_sentences` / `InterruptibleSynth` 方法名跨 Task 0/2/4 一致。
- `ReplySource.stream(asr_text, ctx)` 簽章跨 Task 1/2/5 一致；`ReplyContext.directive` 跨 Task 1/2/5 一致。
- `TurnResult` 欄位（`asr_text` / `reply_text` / `state_events`）跨 Task 5/6 與 `server/pipeline.py:36` 一致。
- `SherpaInterruptibleTTSService(voice=None)` 跨 Task 4/6 一致。
- `run_once(reply_source, *, barge_in, no_stt)` / `OutputSink.frame_count` 跨 harness 與 Task 6 測試一致。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-08-a2-2-streaming-turn-manager.md`. Two execution options:

1. **Subagent-Driven (recommended)** — 每個 Task 派一個新鮮 subagent、Task 間兩階段複審、快速迭代。
2. **Inline Execution** — 於本 session 用 executing-plans 批次執行、checkpoint 複審。

Which approach?
