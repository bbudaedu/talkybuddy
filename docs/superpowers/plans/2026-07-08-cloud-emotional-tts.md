# 雲端情緒 TTS（ElevenLabs）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 線上場景（`network_mode == "cloud"`）改用雲端 ElevenLabs 合成自然情緒中文語音，取代邊緣 Piper `zh_CN-huayan` 的大陸腔平板聽感；雲端失敗/離線靜默降級回邊緣。

**Architecture:** 新增 `server/cloud_tts.py`（`CloudTTS`，與邊緣 `TTSEngine` 同 `available()/synth()` 契約）。路由放在 `pipeline._synth_tts`：`network_mode=="cloud"` 且 `cloud_tts.available()` 才試雲端，回 `None` 就 fall back 邊緣 `TTSEngine`。ElevenLabs 單一 voice 原生中英混讀 → segments 併成一句、單一 API 呼叫、raw PCM 包成與邊緣同規格 WAV（前端零改動）。

**Tech Stack:** Python 3.12、stdlib `urllib.request`（HTTP client，對齊 `server/diagnose.py` 呼叫 Anthropic 的既有慣例）、stdlib `wave`（WAV 封裝，對齊 `server/tts.py`）、pytest。

母脈絡 spec：`docs/superpowers/specs/2026-07-08-cloud-emotional-tts-design.md`。

## Global Constraints

- 邊緣 `TTSEngine`（`server/tts.py`）契約與程式**一字不動**；仍是 `pipeline.self.tts`。
- WAV 輸出規格固定 **22050Hz / 16-bit PCM / mono**（`TARGET_RATE = 22050`），與邊緣一致 → 前端播放零改動。
- 金鑰與設定**只走環境變數、絕不 commit**（比照 `PICOVOICE_ACCESS_KEY` / `ANTHROPIC_API_KEY`）。
- HTTP client = stdlib **`urllib.request`**（對齊 `diagnose.py:_call_anthropic_api`，不新增依賴）。這是對 spec §4.1「httpx 或 requests」的**刻意偏移**：urllib 滿足同一契約、為 stdlib 恆可用、邊緣佈署更穩，故 `available()` 不需探測 HTTP client。
- 降級全程**不 `raise`、不阻斷回覆**：`CloudTTS.synth` 任何失敗回 `None`，交由 pipeline fall back（比照現有 DB 寫入失敗處理）。
- `import` 期不可炸：外部依賴（本案僅 stdlib，無重依賴）維持既有防禦風格。
- 測試放 `tests/`，跑法（working dir = `talkybuddy/`）：`.venv/bin/python -m pytest tests/<file> -v`。
- ElevenLabs `pcm_22050` 回傳的是 **raw headerless 16-bit LE mono PCM**，需自行包 WAV header；`CloudTTS` 另加 `RIFF` 偵測做保險（萬一回傳已含 header 則原樣通過）。

---

### Task 1: config 新增雲端 TTS 環境變數

**Files:**
- Modify: `talkybuddy/server/config.py`（在檔尾 `CONSENT_GRANTED` 區塊之後新增）
- Test: `talkybuddy/tests/test_cloud_tts_config.py`

**Interfaces:**
- Consumes: 無（僅 stdlib `os`，`config.py` 已 `import os`）。
- Produces:
  - `config.ELEVENLABS_API_KEY: str`（預設 `""`）
  - `config.ELEVENLABS_VOICE_ID: str`（預設 `""`）
  - `config.ELEVENLABS_MODEL: str`（預設 `"eleven_flash_v2_5"`）
  - `config.CLOUD_TTS_TIMEOUT_S: float`（預設 `6.0`）

- [ ] **Step 1: Write the failing test**

建立 `talkybuddy/tests/test_cloud_tts_config.py`：

```python
# -*- coding: utf-8 -*-
"""雲端情緒 TTS 的 config 環境變數：預設值與型別。"""

from __future__ import annotations

from server import config


def test_cloud_tts_defaults_present():
    # 未設環境變數時的預設值（金鑰/voice 空字串、模型與逾時有預設）
    assert config.ELEVENLABS_API_KEY == ""
    assert config.ELEVENLABS_VOICE_ID == ""
    assert config.ELEVENLABS_MODEL == "eleven_flash_v2_5"
    assert isinstance(config.CLOUD_TTS_TIMEOUT_S, float)
    assert config.CLOUD_TTS_TIMEOUT_S == 6.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cloud_tts_config.py -v`
Expected: FAIL with `AttributeError: module 'server.config' has no attribute 'ELEVENLABS_API_KEY'`

- [ ] **Step 3: Write minimal implementation**

在 `talkybuddy/server/config.py` 檔尾（`CONSENT_GRANTED` 定義之後）新增：

```python


# ---------------------------------------------------------------------------
# 雲端情緒 TTS（ElevenLabs；見 docs/superpowers/specs/2026-07-08-cloud-emotional-tts-design.md）
# 全走環境變數、絕不 commit。network_mode=="cloud" 時 pipeline 才會嘗試雲端，
# 失敗/逾時/斷網 → 靜默降級回邊緣 Piper。
# ---------------------------------------------------------------------------
ELEVENLABS_API_KEY: str = os.environ.get("ELEVENLABS_API_KEY", "")
# 合成用 voice_id（先用內建溫柔中文女聲；克隆自選人聲後僅換此值，架構不動）。空=不啟用雲端。
ELEVENLABS_VOICE_ID: str = os.environ.get("ELEVENLABS_VOICE_ID", "")
# 合成模型：eleven_flash_v2_5 低延遲預設；可切 eleven_v3 情緒更強。
ELEVENLABS_MODEL: str = os.environ.get("ELEVENLABS_MODEL", "eleven_flash_v2_5")
# 雲端合成逾時（秒）；逾時即降級回邊緣。
CLOUD_TTS_TIMEOUT_S: float = float(os.environ.get("CLOUD_TTS_TIMEOUT_S", "6.0"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cloud_tts_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add talkybuddy/server/config.py talkybuddy/tests/test_cloud_tts_config.py
git commit -m "feat(tts): add ElevenLabs cloud TTS env config"
```

---

### Task 2: `CloudTTS` 雲端合成模組

**Files:**
- Create: `talkybuddy/server/cloud_tts.py`
- Test: `talkybuddy/tests/test_cloud_tts.py`

**Interfaces:**
- Consumes: `config.ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID / ELEVENLABS_MODEL / CLOUD_TTS_TIMEOUT_S`（Task 1）。
- Produces:
  - `class CloudTTS`
    - `available() -> bool`：`ELEVENLABS_API_KEY` 與 `ELEVENLABS_VOICE_ID` 皆非空 → True。
    - `synth(segments: list[tuple[str, str]]) -> bytes | None`：回完整 WAV（22050Hz/16-bit/mono）或 None。

**契約備註（給 Task 3 使用者）**：`CloudTTS` 與 `server/tts.py:TTSEngine` **同簽名**（`available()` / `synth(segments)`），故 pipeline 可用同一介面呼叫、可互為降級對象。

- [ ] **Step 1: Write the failing tests**

建立 `talkybuddy/tests/test_cloud_tts.py`：

```python
# -*- coding: utf-8 -*-
"""CloudTTS（ElevenLabs）單元測試：available 邏輯、WAV 封裝、降級回 None、請求內容。

以 monkeypatch 假造 urllib.request.urlopen，全程不連網。
"""

from __future__ import annotations

import io
import json
import wave

import pytest

from server import config
from server.cloud_tts import CloudTTS


class _FakeResp:
    """假 urlopen 回應：支援 context manager 與 read()。"""

    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def keyed(monkeypatch):
    """設好金鑰與 voice_id 的環境。"""
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", "test-voice")
    monkeypatch.setattr(config, "ELEVENLABS_MODEL", "eleven_flash_v2_5")
    monkeypatch.setattr(config, "CLOUD_TTS_TIMEOUT_S", 6.0)


# --- available() ---------------------------------------------------------

def test_available_true_with_key_and_voice(keyed):
    assert CloudTTS().available() is True


def test_available_false_without_key(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "")
    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", "test-voice")
    assert CloudTTS().available() is False


def test_available_false_without_voice(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", "")
    assert CloudTTS().available() is False


# --- synth() 成功路徑 ----------------------------------------------------

def test_synth_wraps_raw_pcm_into_valid_wav(keyed, monkeypatch):
    raw_pcm = b"\x01\x00" * 100  # 100 個 int16 樣本
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _FakeResp(raw_pcm),
    )
    wav = CloudTTS().synth([("zh", "你好"), ("en", "hello")])
    assert wav is not None
    with wave.open(io.BytesIO(wav), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 22050
        assert wf.readframes(wf.getnframes()) == raw_pcm


def test_synth_builds_correct_request(keyed, monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeResp(b"\x00\x00")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    CloudTTS().synth([("zh", "你好"), ("en", "hi there")])

    assert "test-voice" in captured["url"]
    assert "output_format=pcm_22050" in captured["url"]
    assert captured["method"] == "POST"
    assert captured["headers"]["xi-api-key"] == "test-key"
    assert captured["headers"]["content-type"] == "application/json"
    assert captured["body"]["model_id"] == "eleven_flash_v2_5"
    # 中英兩段併成單一字串（原生中英混讀）
    assert captured["body"]["text"] == "你好 hi there"
    assert captured["timeout"] == 6.0


def test_synth_passthrough_when_body_already_riff(keyed, monkeypatch):
    riff = b"RIFF" + b"\x00" * 40  # 假裝已是 WAV 容器
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _FakeResp(riff),
    )
    assert CloudTTS().synth([("zh", "你好")]) == riff


# --- synth() 降級回 None -------------------------------------------------

def test_synth_none_on_empty_segments(keyed):
    assert CloudTTS().synth([]) is None


def test_synth_none_on_blank_text(keyed):
    assert CloudTTS().synth([("zh", "   "), ("en", "")]) is None


def test_synth_none_on_http_error(keyed, monkeypatch):
    def boom(req, timeout=None):
        raise RuntimeError("network down / non-2xx")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert CloudTTS().synth([("zh", "你好")]) is None


def test_synth_none_on_empty_body(keyed, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _FakeResp(b""),
    )
    assert CloudTTS().synth([("zh", "你好")]) is None


def test_synth_none_without_key(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "")
    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", "")
    assert CloudTTS().synth([("zh", "你好")]) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cloud_tts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.cloud_tts'`

- [ ] **Step 3: Write minimal implementation**

建立 `talkybuddy/server/cloud_tts.py`：

```python
"""雲端情緒 TTS（ElevenLabs）。

契約與邊緣 TTSEngine（server/tts.py）一致：
- class CloudTTS
  - available() -> bool
  - synth(segments: list[tuple[str, str]]) -> bytes | None  # 完整 WAV (22050Hz/16-bit/mono)

設計（見 docs/superpowers/specs/2026-07-08-cloud-emotional-tts-design.md）：
- network_mode=="cloud" 時由 pipeline 呼叫；任何失敗回 None → pipeline 靜默降級回邊緣 Piper。
- ElevenLabs 單一 voice 原生中英混讀 → segments 併成單一字串、單一 API 呼叫。
- output_format=pcm_22050 回傳 raw 16-bit LE mono PCM（headerless）→ 包成 WAV bytes
  （與邊緣同規格，前端零改動）；保險：若回傳已是 RIFF/WAV 則原樣通過。
- HTTP client 用 stdlib urllib.request（對齊 diagnose.py，不新增依賴、恆可用）。
- 逾時/非2xx/斷網/空 body/任何例外 → 回 None，不 raise。
"""

from __future__ import annotations

import io
import json
import urllib.request
import wave

TARGET_RATE = 22050  # 輸出取樣率（與 server/tts.py 一致）
_API_BASE = "https://api.elevenlabs.io/v1/text-to-speech"


class CloudTTS:
    """ElevenLabs 雲端合成引擎（與 TTSEngine 同契約、降級安全）。"""

    # ------------------------------------------------------------------
    def available(self) -> bool:
        """有金鑰且有 voice_id → 可用。urllib 為 stdlib 恆可 import，無需探測。"""
        try:
            from server.config import ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID
        except Exception:
            return False
        return bool(ELEVENLABS_API_KEY) and bool(ELEVENLABS_VOICE_ID)

    # ------------------------------------------------------------------
    def synth(self, segments: list[tuple[str, str]]) -> bytes | None:
        """把 segments 併成單一字串、呼叫 ElevenLabs、raw PCM 包成 WAV bytes。

        任何失敗（空輸入/無金鑰/逾時/非2xx/斷網/空 body/例外）→ 回 None，不 raise。
        """
        if not segments:
            return None
        text = " ".join(
            (t or "").strip() for _lang, t in segments if (t or "").strip()
        )
        if not text:
            return None

        try:
            from server.config import (
                CLOUD_TTS_TIMEOUT_S,
                ELEVENLABS_API_KEY,
                ELEVENLABS_MODEL,
                ELEVENLABS_VOICE_ID,
            )
        except Exception:
            return None
        if not ELEVENLABS_API_KEY or not ELEVENLABS_VOICE_ID:
            return None

        url = f"{_API_BASE}/{ELEVENLABS_VOICE_ID}?output_format=pcm_22050"
        body = json.dumps(
            {"text": text, "model_id": ELEVENLABS_MODEL}
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "xi-api-key": ELEVENLABS_API_KEY,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=CLOUD_TTS_TIMEOUT_S) as resp:
                raw = resp.read()
        except Exception:
            return None
        if not raw:
            return None
        return _pcm_to_wav(raw)


# ----------------------------------------------------------------------
def _pcm_to_wav(raw: bytes) -> bytes | None:
    """raw 16-bit LE mono PCM(22050Hz) → WAV bytes；已是 RIFF 則原樣回傳；失敗回 None。"""
    if raw[:4] == b"RIFF":
        return raw
    try:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)       # mono
            wf.setsampwidth(2)       # 16-bit
            wf.setframerate(TARGET_RATE)
            wf.writeframes(raw)
        return buf.getvalue()
    except Exception:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cloud_tts.py -v`
Expected: PASS（11 passed）

- [ ] **Step 5: Commit**

```bash
git add talkybuddy/server/cloud_tts.py talkybuddy/tests/test_cloud_tts.py
git commit -m "feat(tts): add CloudTTS ElevenLabs engine with WAV wrap + graceful None"
```

---

### Task 3: pipeline 路由（cloud 優先、失敗 fall back edge）

**Files:**
- Modify: `talkybuddy/server/pipeline.py:114-118`（`__init__` 新增 `cloud_tts` 參數）
- Modify: `talkybuddy/server/pipeline.py:286-297`（`_synth_tts` 改寫路由）
- Test: `talkybuddy/tests/test_pipeline_cloud_tts.py`

**Interfaces:**
- Consumes: `CloudTTS`（Task 2；透過注入的 `cloud_tts`，只依賴 `available()/synth()` 介面，不 import 具體類別）。
- Produces:
  - `VoicePipeline.__init__(self, asr, llm, tts, cloud_tts=None)`（新增末位選填參數，向後相容）。
  - `self.cloud_tts` 屬性。
  - `_synth_tts` 路由：`network_mode=="cloud" and cloud_tts and cloud_tts.available()` → 試 `cloud_tts.synth`；回 None 才用 `self.tts.synth`；`edge` 模式完全不碰 cloud。

- [ ] **Step 1: Write the failing tests**

建立 `talkybuddy/tests/test_pipeline_cloud_tts.py`：

```python
# -*- coding: utf-8 -*-
"""VoicePipeline 雲端 TTS 路由：cloud 優先、失敗 fall back edge、edge 模式不碰雲端。"""

from __future__ import annotations

import pytest

from server.pipeline import VoicePipeline

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class StubASR:
    def available(self) -> bool:
        return True

    def transcribe(self, wav_path: str):
        return ("我要一個蘋果", 0.9)


class StubLLM:
    def available(self) -> bool:
        return True

    def generate(self, student_text, scaffold_result, directive=None):
        return "好呀，我們來聊聊。"


class StubTTS:
    """邊緣 TTS：回固定 b"EDGE"，並記錄呼叫次數。"""

    def __init__(self, wav=b"EDGE", available=True):
        self._wav = wav
        self._available = available
        self.calls = 0

    def available(self) -> bool:
        return self._available

    def synth(self, segments):
        self.calls += 1
        return self._wav


class StubCloud:
    """雲端 TTS：可設定回傳與可用性，記錄呼叫次數。"""

    def __init__(self, wav, available=True):
        self._wav = wav
        self._available = available
        self.calls = 0

    def available(self) -> bool:
        return self._available

    def synth(self, segments):
        self.calls += 1
        return self._wav


async def _emit(_payload):  # 忽略事件
    return None


async def test_cloud_mode_uses_cloud_when_available():
    edge = StubTTS(wav=b"EDGE")
    cloud = StubCloud(wav=b"CLOUD")
    vp = VoicePipeline(StubASR(), StubLLM(), edge, cloud_tts=cloud)
    vp.network_mode = "cloud"

    result = await vp.run_turn_text("我要一個蘋果", _emit)

    assert result.tts_wav == b"CLOUD"
    assert cloud.calls == 1
    assert edge.calls == 0          # 雲端成功 → 不碰邊緣


async def test_cloud_mode_falls_back_to_edge_when_cloud_returns_none():
    edge = StubTTS(wav=b"EDGE")
    cloud = StubCloud(wav=None)     # 模擬逾時/斷網
    vp = VoicePipeline(StubASR(), StubLLM(), edge, cloud_tts=cloud)
    vp.network_mode = "cloud"

    result = await vp.run_turn_text("我要一個蘋果", _emit)

    assert result.tts_wav == b"EDGE"
    assert cloud.calls == 1
    assert edge.calls == 1          # 雲端回 None → 靜默降級邊緣


async def test_edge_mode_never_touches_cloud():
    edge = StubTTS(wav=b"EDGE")
    cloud = StubCloud(wav=b"CLOUD")
    vp = VoicePipeline(StubASR(), StubLLM(), edge, cloud_tts=cloud)
    vp.network_mode = "edge"

    result = await vp.run_turn_text("我要一個蘋果", _emit)

    assert result.tts_wav == b"EDGE"
    assert cloud.calls == 0          # edge 模式完全不呼叫雲端


async def test_cloud_unavailable_uses_edge_without_calling_synth():
    edge = StubTTS(wav=b"EDGE")
    cloud = StubCloud(wav=b"CLOUD", available=False)
    vp = VoicePipeline(StubASR(), StubLLM(), edge, cloud_tts=cloud)
    vp.network_mode = "cloud"

    result = await vp.run_turn_text("我要一個蘋果", _emit)

    assert result.tts_wav == b"EDGE"
    assert cloud.calls == 0          # available()=False → 不呼叫 synth


async def test_no_cloud_engine_defaults_to_edge():
    edge = StubTTS(wav=b"EDGE")
    vp = VoicePipeline(StubASR(), StubLLM(), edge)  # 不傳 cloud_tts → 向後相容
    vp.network_mode = "cloud"

    result = await vp.run_turn_text("我要一個蘋果", _emit)

    assert result.tts_wav == b"EDGE"
    assert edge.calls == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_pipeline_cloud_tts.py -v`
Expected: FAIL — `test_cloud_mode_uses_cloud_when_available` 得到 `b"EDGE"`（路由未實作），且傳 `cloud_tts=` 時 `TypeError`（`__init__` 尚不收此參數）。

- [ ] **Step 3a: 修改 `__init__` 接收 `cloud_tts`**

`talkybuddy/server/pipeline.py`，把（第 114-118 行）：

```python
    def __init__(self, asr, llm, tts):
        """依賴注入 ASR / LLM / TTS 引擎實例（測試可傳 stub）。"""
        self.asr = asr
        self.llm = llm
        self.tts = tts
```

改為：

```python
    def __init__(self, asr, llm, tts, cloud_tts=None):
        """依賴注入 ASR / LLM / TTS 引擎實例（測試可傳 stub）。

        cloud_tts：選填的雲端 TTS（CloudTTS，同 available()/synth() 契約）；
        None（預設）→ 只走邊緣 TTS，向後相容既有呼叫端與測試。
        """
        self.asr = asr
        self.llm = llm
        self.tts = tts
        self.cloud_tts = cloud_tts
```

- [ ] **Step 3b: 改寫 `_synth_tts` 路由**

`talkybuddy/server/pipeline.py`，把 `_synth_tts`（第 286-297 行）：

```python
    async def _synth_tts(self, result: TurnResult, emit, segments: list[tuple[str, str]]) -> None:
        """階段：TTS 合成（to_thread；不可用/失敗 → tts_wav=None，前端降級）。"""
        await self._emit_state(emit, result, "tts")
        t_tts = time.monotonic()
        wav: bytes | None = None
        try:
            if self.tts is not None and self.tts.available() and segments:
                wav = await asyncio.to_thread(self.tts.synth, segments)
        except Exception:
            wav = None
        result.latency_ms["tts_first"] = int((time.monotonic() - t_tts) * 1000)
        result.tts_wav = wav
```

改為：

```python
    async def _synth_tts(self, result: TurnResult, emit, segments: list[tuple[str, str]]) -> None:
        """階段：TTS 合成（to_thread；不可用/失敗 → tts_wav=None，前端降級）。

        cloud 模式先試雲端 CloudTTS，回 None（逾時/斷網/錯誤）→ 靜默降級邊緣 TTSEngine；
        edge 模式完全不碰雲端。任一失敗最終 tts_wav=None，維持既有前端降級行為。
        """
        await self._emit_state(emit, result, "tts")
        t_tts = time.monotonic()
        wav: bytes | None = None
        try:
            if segments:
                if (
                    self.network_mode == "cloud"
                    and self.cloud_tts is not None
                    and self.cloud_tts.available()
                ):
                    wav = await asyncio.to_thread(self.cloud_tts.synth, segments)
                if wav is None and self.tts is not None and self.tts.available():
                    wav = await asyncio.to_thread(self.tts.synth, segments)
        except Exception:
            wav = None
        result.latency_ms["tts_first"] = int((time.monotonic() - t_tts) * 1000)
        result.tts_wav = wav
```

- [ ] **Step 4: Run tests to verify they pass（含既有 pipeline 測試不回歸）**

Run: `.venv/bin/python -m pytest tests/test_pipeline_cloud_tts.py tests/test_pipeline.py tests/test_pipeline_directive.py -v`
Expected: PASS（新 5 個 + 既有 pipeline 測試全綠）

- [ ] **Step 5: Commit**

```bash
git add talkybuddy/server/pipeline.py talkybuddy/tests/test_pipeline_cloud_tts.py
git commit -m "feat(tts): route cloud TTS in pipeline with silent edge fallback"
```

---

### Task 4: app.py 接線 + `/api/status` 曝露 cloud_tts

**Files:**
- Modify: `talkybuddy/server/app.py`（import、單例、pipeline 建構、status）
- Test: `talkybuddy/tests/test_app_cloud_tts.py`

**Interfaces:**
- Consumes: `CloudTTS`（Task 2）、`VoicePipeline(..., cloud_tts=...)`（Task 3）。
- Produces:
  - 全域 `cloud_tts_engine = CloudTTS()`。
  - `pipeline` 建構帶入 `cloud_tts=cloud_tts_engine`。
  - `/api/status` 回應新增鍵 `"cloud_tts": bool`。

- [ ] **Step 1: Write the failing test**

建立 `talkybuddy/tests/test_app_cloud_tts.py`：

```python
# -*- coding: utf-8 -*-
"""app 接線：pipeline 帶入 cloud_tts 引擎、/api/status 曝露 cloud_tts 布林。"""

from __future__ import annotations

from starlette.testclient import TestClient

from server import app as app_mod
from server import config


def test_pipeline_has_cloud_tts_engine():
    assert app_mod.pipeline.cloud_tts is app_mod.cloud_tts_engine


def test_status_exposes_cloud_tts_true_when_configured(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", "v")
    client = TestClient(app_mod.app)

    body = client.get("/api/status").json()

    assert body["cloud_tts"] is True


def test_status_exposes_cloud_tts_false_without_key(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "")
    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", "")
    client = TestClient(app_mod.app)

    body = client.get("/api/status").json()

    assert body["cloud_tts"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_app_cloud_tts.py -v`
Expected: FAIL — `AttributeError: module 'server.app' has no attribute 'cloud_tts_engine'`；且 status body 無 `cloud_tts` 鍵。

- [ ] **Step 3a: import CloudTTS**

`talkybuddy/server/app.py:32`，把：

```python
from server.tts import TTSEngine
```

改為：

```python
from server.cloud_tts import CloudTTS
from server.tts import TTSEngine
```

- [ ] **Step 3b: 建立單例並注入 pipeline**

`talkybuddy/server/app.py:48-49`，把：

```python
tts_engine = TTSEngine()
pipeline = VoicePipeline(asr_engine, llm_engine, tts_engine)
```

改為：

```python
tts_engine = TTSEngine()
cloud_tts_engine = CloudTTS()
pipeline = VoicePipeline(asr_engine, llm_engine, tts_engine, cloud_tts=cloud_tts_engine)
```

- [ ] **Step 3c: `/api/status` 新增 cloud_tts**

`talkybuddy/server/app.py:111-117`，把：

```python
    return {
        "asr": bool(asr_engine.available()),
        "llm": bool(llm_engine.available()),
        "tts": bool(tts_engine.available()),
        "network_mode": pipeline.network_mode,
        "pending": store.pending_count(),
    }
```

改為：

```python
    return {
        "asr": bool(asr_engine.available()),
        "llm": bool(llm_engine.available()),
        "tts": bool(tts_engine.available()),
        "cloud_tts": bool(cloud_tts_engine.available()),
        "network_mode": pipeline.network_mode,
        "pending": store.pending_count(),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_app_cloud_tts.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 全測試回歸**

Run: `.venv/bin/python -m pytest -q`
Expected: 全綠（新增測試 + 既有測試皆通過）

- [ ] **Step 6: Commit**

```bash
git add talkybuddy/server/app.py talkybuddy/tests/test_app_cloud_tts.py
git commit -m "feat(tts): wire CloudTTS into app + expose cloud_tts in /api/status"
```

---

## 手動驗收（verify，實作全部完成後）

需真 `ELEVENLABS_API_KEY` 與一個中文 `ELEVENLABS_VOICE_ID`（先用 ElevenLabs 內建溫柔中文女聲）：

1. **雲端聽感**：
   ```bash
   cd talkybuddy
   ELEVENLABS_API_KEY=<真金鑰> ELEVENLABS_VOICE_ID=<中文voice_id> .venv/bin/python -m uvicorn server.app:app --reload
   ```
   瀏覽器開 UI → `/api/status` 應見 `"cloud_tts": true` → 切到 cloud 模式 → 說一句 → 應聽到自然、有情緒的中文（非 huayan 平板腔）。
2. **降級韌性（錯金鑰）**：用故意錯誤的 `ELEVENLABS_API_KEY` 啟動 → 切 cloud → 說一句 → 應**自動降級**回邊緣 Piper 聲音、對話不中斷。
3. **降級韌性（拔網）**：正常啟動後中途斷網 → 說一句 → 逾時（`CLOUD_TTS_TIMEOUT_S`）後降級邊緣，無崩潰。
4. **edge 模式**：切 edge → 說一句 → 走邊緣 Piper，`/api/status` 的 `cloud_tts` 不影響行為。

## Self-Review（對照 spec）

- **spec §3 路由** → Task 3 `_synth_tts` 改寫（cloud 試→None fall back edge；edge 不碰雲端）。✅
- **spec §4.1 CloudTTS（available/synth/WAV 封裝/例外回 None）** → Task 2。✅（HTTP client 由 httpx 改 stdlib urllib，已於 Global Constraints 標註偏移理由）
- **spec §4.2 config 四個 env** → Task 1。✅
- **spec §4.3 pipeline `__init__` + `_synth_tts`** → Task 3。✅
- **spec §4.4 app.py 單例 + status** → Task 4。✅
- **spec §5 相容性（型別/協定/WAV 規格不變）** → 邊緣未動、WAV 22050/16-bit/mono 沿用、Task 3 向後相容測試。✅
- **spec §6 錯誤處理與降級表** → Task 2（synth 回 None）＋ Task 3（路由 fall back）＋ 測試 `test_synth_none_*` / `test_cloud_mode_falls_back_*`。✅
- **spec §7 測試** → Task 2/3/4 單元 + 手動 verify。✅
- **spec §8 風險（output_format header）** → `_pcm_to_wav` RIFF 偵測 + 手動聽感 verify 收斂。✅
- **placeholder 掃描**：無 TBD/TODO；每個 code step 均含完整程式碼。✅
- **型別一致性**：`CloudTTS.available()/synth(segments)` 與 `TTSEngine` 同簽名；`cloud_tts` 參數/屬性命名跨 Task 3、4 一致。✅
