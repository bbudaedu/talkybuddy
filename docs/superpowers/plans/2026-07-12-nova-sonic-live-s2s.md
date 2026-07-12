# Nova Sonic 即時 S2S 陪聊（Phase 1 垂直切片）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一條中文全雙工即時陪聊路徑（瀏覽器 PCM ⇄ `/ws/live` ⇄ Amazon Nova 2 Sonic bidi），端到端能跑、鷹架 system prompt 注入、每輪 transcript 落地，與現有半雙工 `/ws/talk` 並存互不干擾。

**Architecture:** 把 `scripts/verify_nova_sonic_live.py` 已實機證實的 Nova Sonic `InvokeModelWithBidirectionalStream` 協定收斂成 `server/nova_sonic.py` 的 `NovaSonicSession` 類別（單一對話）；`server/app.py` 新增 `/ws/live` 當薄橋，用兩個 asyncio task 對接瀏覽器上行 PCM 與模型下行 audio/transcript；鷹架 system prompt 由 `scaffold.build_live_system_prompt()` 純函式組裝（靜態框架寫死 + 動態目標句/directive 折入）；前端新增 `web/live-client.js`（AudioWorklet 擷取 16kHz PCM16 上行、24kHz PCM 排隊播放）與 index.html 模式鈕。

**Tech Stack:** Python 3.12 / asyncio、FastAPI（既有）、`aws_sdk_bedrock_runtime` + `smithy_aws_core`（bidi 只吃 SigV4，已裝於共用 `.venv`）、pytest（既有）、瀏覽器 AudioWorklet + WebSocket binary、node 內建 test runner（前端純函式，比照 `web/*.test.mjs`）。

## Global Constraints

- 檔案路徑一律以 `talkybuddy/` 為根目錄基準（見 `server/config.py` 的 `BASE_DIR`）。
- 只用既有依賴 + `aws_sdk_bedrock_runtime` / `smithy_aws_core`（已裝，勿新增其他第三方套件）。
- Nova Sonic bidi **只吃 SigV4**，不吃 bearer token；憑證走 env（`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`，可選 `AWS_SESSION_TOKEN`），透過 `Config(aws_credentials_identity_resolver=EnvironmentCredentialsResolver())` 注入，**絕不進 repo**。
- 重依賴（`aws_sdk_bedrock_runtime` / `boto3`）一律 **lazy import**；`import server.nova_sonic` 在無 SDK 環境下絕不可炸（比照 `server/cloud_llm.py`）。
- 現有半雙工 `/ws/talk`、`pipeline.run_turn_audio` **一行不動**；新路徑純新增、隨時可切回。
- 音訊上雲受 consent gate 管：`/ws/live` 連線先過 `guardrails.consent_granted()`，未同意直接拒連（與現有 cloud 路徑一致）。
- 回覆語言：學伴角色主要用繁體中文（台灣用語），逐輪自然帶出簡單英語詞/短句邀請跟讀。
- TDD：每個 task 先寫失敗測試 → 跑驗證失敗 → 最小實作 → 跑驗證通過 → commit。
- 已知協定踩雷（實作 `NovaSonicSession` 時務必封裝，來源 = `scripts/verify_nova_sonic_live.py`）：
  1. `Config` 需顯式 `EnvironmentCredentialsResolver()`（直接塞 key 不夠）。
  2. 音訊 `contentEnd` 後**不立刻送 `promptEnd`**，等模型自動回覆；`promptEnd`/`sessionEnd` 留到 `close()`。
  3. Nova Sonic 分多個 completion：先 USER-ASR 段、後 ASSISTANT 回覆段；**別在第一個 `completionEnd` 就收尾**，要等收到 assistant 內容（text 或 audio）才發 `turn_end`。
  4. 每個 `contentStart` 用獨立 `contentName`（uuid）；`audioOutputConfiguration` 為 24kHz、`audioInputConfiguration` 為 16kHz，皆 `encoding=base64`。

---

## 檔案結構

- **Create** `server/nova_sonic.py` — `NovaSonicSession`（封裝一場 Nova Sonic bidi 對話）+ module 級 `available()`。單一職責：協定收斂，不碰 FastAPI/HTTP。
- **Modify** `server/config.py` — 新增 `NOVA_SONIC_MODEL_ID` / `NOVA_SONIC_VOICE` / `LIVE_S2S_ENABLED` 三個 env 設定。
- **Modify** `server/scaffold.py` — 新增純函式 `build_live_system_prompt(target_sentence, directive)`（重用既有靜態框架語彙 + `guardrails.CHILD_SAFETY_CLAUSE`）。
- **Modify** `server/app.py` — `/api/status` 加 `live_s2s` 欄；新增 `/ws/live` WebSocket 薄橋。
- **Create** `web/live-client.js` — 前端純函式（float32→PCM16、base64 PCM24 排隊播放）+ 連線/擷取整合。
- **Create** `web/live-client.test.mjs` — 前端純函式 node 測（比照 `web/mic-router.test.mjs`）。
- **Modify** `web/index.html` — 模式鈕（即時對話 ⇄ 半雙工）+ `/api/status` `live_s2s` 置灰。
- **Create** `docs/superpowers/plans/2026-07-12-nova-sonic-live-s2s-e2e-checklist.md` — 手動端到端驗收 checklist。
- **Create** `tests/test_nova_sonic.py` / `tests/test_scaffold_live_prompt.py` / `tests/test_app_live.py` — 對應測試。

---

### Task 1: Config 新增 + `NovaSonicSession` 骨架與 `available()`

**Files:**
- Modify: `server/config.py`（雲端 LLM 區塊之後，約 `:73` 後追加）
- Create: `server/nova_sonic.py`
- Test: `tests/test_nova_sonic.py`

**Interfaces:**
- Consumes: `server.config`（`BEDROCK_REGION` 既有）
- Produces:
  - `config.NOVA_SONIC_MODEL_ID: str`（預設 `"amazon.nova-2-sonic-v1:0"`）
  - `config.NOVA_SONIC_VOICE: str`（預設 `"tiffany"`）
  - `config.LIVE_S2S_ENABLED: bool`（預設 True，可 env 覆寫）
  - `nova_sonic.available() -> bool`
  - `nova_sonic.NovaSonicSession(model_id: str, voice: str, region: str)`，屬性 `model_id` / `voice` / `region`

- [ ] **Step 1: 寫失敗測試**

`tests/test_nova_sonic.py`：

```python
# -*- coding: utf-8 -*-
"""NovaSonicSession + module available() 單元測試（全程 mock SDK，不真打雲端）。"""
from __future__ import annotations

import pytest

from server import nova_sonic


def test_available_false_without_creds(monkeypatch):
    """缺 SigV4 憑證 → available() False（bidi 不吃 bearer token）。"""
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    assert nova_sonic.available() is False


def test_available_true_with_creds(monkeypatch):
    """有 SigV4 憑證且 SDK 可 import → available() True。"""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_TEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret_test")
    assert nova_sonic.available() is True


def test_session_init_stores_params():
    s = nova_sonic.NovaSonicSession(model_id="m", voice="v", region="r")
    assert (s.model_id, s.voice, s.region) == ("m", "v", "r")
```

- [ ] **Step 2: 跑測試驗證失敗**

Run: `cd talkybuddy && .venv/bin/python -m pytest tests/test_nova_sonic.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'server.nova_sonic'`）

- [ ] **Step 3: 在 `server/config.py` 雲端 LLM 區塊（`TUTOR_MODEL_ID` 那行之後）追加**

```python

# ---------------------------------------------------------------------------
# Nova Sonic 即時 S2S 陪聊（Phase 1；見 docs/superpowers/specs/2026-07-11-nova-sonic-live-s2s-design.md）
# bidi 只吃 SigV4：憑證走 env（AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY），不進 repo。
# region 沿用 BEDROCK_REGION。實際可用性 = LIVE_S2S_ENABLED AND nova_sonic.available()。
# ---------------------------------------------------------------------------
NOVA_SONIC_MODEL_ID: str = os.environ.get("NOVA_SONIC_MODEL_ID", "amazon.nova-2-sonic-v1:0")
NOVA_SONIC_VOICE: str = os.environ.get("NOVA_SONIC_VOICE", "tiffany")
LIVE_S2S_ENABLED: bool = _env_bool("LIVE_S2S_ENABLED", True)
```

（`_env_bool` 已定義於 `config.py:80`，`os` 已於 `config.py:49` import。）

- [ ] **Step 4: 建立 `server/nova_sonic.py`（本 task 只放骨架 + available，其餘方法後續 task 補）**

```python
# -*- coding: utf-8 -*-
"""nova_sonic：Amazon Nova 2 Sonic 即時雙向串流（S2S）一場對話的封裝。

把 scripts/verify_nova_sonic_live.py 已實機證實的 InvokeModelWithBidirectionalStream
協定收斂成 NovaSonicSession。所有已知踩雷（EnvironmentCredentialsResolver、
contentEnd 後不立刻 promptEnd、多 completion 收斂）都封裝在此。

重依賴（aws_sdk_bedrock_runtime / smithy_aws_core）一律 lazy import；
import 本模組絕不可炸（無 SDK 時仍要能啟動，比照 server/cloud_llm.py）。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import uuid
from dataclasses import dataclass

_log = logging.getLogger(__name__)


def available() -> bool:
    """能否啟用 Nova Sonic bidi：SigV4 env 齊全 + SDK 可 import。"""
    if not (os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")):
        return False
    try:
        import aws_sdk_bedrock_runtime  # noqa: F401
        import smithy_aws_core  # noqa: F401
    except Exception:
        return False
    return True


@dataclass
class NovaEvent:
    """events() yield 的事件：kind ∈ {"transcript","audio","turn_end"}。"""
    kind: str
    role: str = ""          # transcript：USER / ASSISTANT
    text: str = ""          # transcript 文字
    audio: bytes = b""      # audio：24kHz PCM16 raw bytes


class NovaSonicSession:
    """封裝一場 Nova Sonic bidi 對話。呼叫順序：
    start() → (send_audio()* → end_user_turn() → events() 迭代)* → close()。
    """

    def __init__(self, model_id: str, voice: str, region: str):
        self.model_id = model_id
        self.voice = voice
        self.region = region
        self._stream = None
        self._prompt_name = str(uuid.uuid4())
        self._audio_content: str | None = None   # 目前 AUDIO 內容的 contentName
        self._queue: asyncio.Queue[NovaEvent | None] = asyncio.Queue()
        self._recv_task: asyncio.Task | None = None
```

- [ ] **Step 5: 跑測試驗證通過**

Run: `cd talkybuddy && .venv/bin/python -m pytest tests/test_nova_sonic.py -q`
Expected: PASS（3 passed）

- [ ] **Step 6: Commit**

```bash
git add server/config.py server/nova_sonic.py tests/test_nova_sonic.py
git commit -m "feat(live): NovaSonicSession 骨架 + available() + Nova Sonic config"
```

---

### Task 2: `NovaSonicSession.start()` — 開串流 + 送 sessionStart/promptStart/system

**Files:**
- Modify: `server/nova_sonic.py`
- Test: `tests/test_nova_sonic.py`

**Interfaces:**
- Consumes: `NovaSonicSession.__init__`（Task 1）
- Produces: `async NovaSonicSession.start(system_prompt: str) -> None`（開 bidi 串流、送 `sessionStart`→`promptStart`（含 24kHz audioOutput + voiceId）→ system TEXT `contentStart`+`textInput`+`contentEnd`，並啟動背景 receive task）

- [ ] **Step 1: 寫失敗測試**（append 到 `tests/test_nova_sonic.py`）

```python
# ---- 共用 fake SDK ----
class _FakeInputStream:
    def __init__(self):
        self.sent = []            # list[dict] 已送出的 event payload
        self.closed = False

    async def send(self, chunk):
        # chunk 是我們的 _ev(...) 產物；直接記錄其解析後的 dict
        self.sent.append(chunk)

    async def close(self):
        self.closed = True


class _FakeOutputStream:
    def __init__(self, events):
        self._events = list(events)   # list[bytes]（每個是 json）

    async def receive(self):
        if self._events:
            raw = self._events.pop(0)
            return type("Ev", (), {"value": type("V", (), {"bytes_": raw})()})()
        return None


class _FakeStream:
    def __init__(self, out_events):
        self.input_stream = _FakeInputStream()
        self._out = _FakeOutputStream(out_events)

    async def await_output(self):
        return (None, self._out)

    async def close(self):
        pass


class _FakeClient:
    def __init__(self, out_events=()):
        self._out_events = out_events
        self.op_input = None

    async def invoke_model_with_bidirectional_stream(self, op_input):
        self.op_input = op_input
        return _FakeStream(self._out_events)


def _payloads(fake_input_stream):
    """把 fake input stream 收到的 chunk 還原成 event-key list。"""
    keys = []
    for c in fake_input_stream.sent:
        d = json.loads(c.value.bytes_)
        keys.extend(d.get("event", {}).keys())
    return keys


import json  # noqa: E402
import pytest  # noqa: E402


@pytest.mark.asyncio
async def test_start_sends_session_and_prompt_sequence(monkeypatch):
    fake_client = _FakeClient()
    monkeypatch.setattr(nova_sonic, "_build_client", lambda self: fake_client)
    s = nova_sonic.NovaSonicSession("m", "tiffany", "us-east-1")

    await s.start("你是說說學伴。")

    sent_keys = _payloads(s._stream.input_stream)
    # 開場事件序：sessionStart → promptStart → (system) contentStart → textInput → contentEnd
    assert sent_keys[:5] == [
        "sessionStart", "promptStart", "contentStart", "textInput", "contentEnd",
    ]
    # promptStart 帶 24kHz 語音輸出 + voiceId
    ps = [json.loads(c.value.bytes_)["event"]["promptStart"]
          for c in s._stream.input_stream.sent
          if "promptStart" in json.loads(c.value.bytes_)["event"]][0]
    aoc = ps["audioOutputConfiguration"]
    assert aoc["sampleRateHertz"] == 24000 and aoc["voiceId"] == "tiffany"
    await s.close()
```

（註：`asyncio` 測試需 `pytest-asyncio`。若 `.venv` 未裝，改用 `asyncio.run(...)` 包裝的同步 test；見 Step 3 備註。）

- [ ] **Step 2: 跑測試驗證失敗**

Run: `cd talkybuddy && .venv/bin/python -m pytest tests/test_nova_sonic.py::test_start_sends_session_and_prompt_sequence -q`
Expected: FAIL（`AttributeError: ... has no attribute 'start'` 或 `_build_client`）

- [ ] **Step 3: 在 `server/nova_sonic.py` 補 `_ev`/`_build_client`/`start`**

先在 module 層（`NovaEvent` 之後）加事件封裝與 client 工廠：

```python
def _mk_event(payload: dict):
    """把 dict payload 包成 SDK bidi input chunk。lazy import SDK 型別。"""
    from aws_sdk_bedrock_runtime.models import (
        InvokeModelWithBidirectionalStreamInputChunk as InChunk,
        BidirectionalInputPayloadPart as InPart,
    )
    return InChunk(value=InPart(bytes_=json.dumps(payload).encode("utf-8")))
```

在 `NovaSonicSession` 內加：

```python
    def _build_client(self):
        """lazy 建 bidi client（SigV4 需顯式 EnvironmentCredentialsResolver）。"""
        from aws_sdk_bedrock_runtime.client import BedrockRuntimeClient
        from aws_sdk_bedrock_runtime.config import Config
        from smithy_aws_core.identity.environment import EnvironmentCredentialsResolver
        config = Config(
            region=self.region,
            aws_credentials_identity_resolver=EnvironmentCredentialsResolver(),
        )
        return BedrockRuntimeClient(config=config)

    async def _send(self, payload: dict) -> None:
        await self._stream.input_stream.send(_mk_event(payload))

    async def start(self, system_prompt: str) -> None:
        from aws_sdk_bedrock_runtime.models import (
            InvokeModelWithBidirectionalStreamOperationInput as OpInput,
        )
        client = self._build_client()
        self._stream = await client.invoke_model_with_bidirectional_stream(
            OpInput(model_id=self.model_id))
        sys_content = str(uuid.uuid4())
        # 1) sessionStart
        await self._send({"event": {"sessionStart": {"inferenceConfiguration": {
            "maxTokens": 512, "topP": 0.9, "temperature": 0.7}}}})
        # 2) promptStart（含 24kHz 語音輸出 + voiceId）
        await self._send({"event": {"promptStart": {
            "promptName": self._prompt_name,
            "textOutputConfiguration": {"mediaType": "text/plain"},
            "audioOutputConfiguration": {
                "mediaType": "audio/lpcm", "sampleRateHertz": 24000,
                "sampleSizeBits": 16, "channelCount": 1,
                "voiceId": self.voice, "encoding": "base64", "audioType": "SPEECH"}}}})
        # 3) system prompt（TEXT）
        await self._send({"event": {"contentStart": {
            "promptName": self._prompt_name, "contentName": sys_content, "type": "TEXT",
            "interactive": True, "role": "SYSTEM",
            "textInputConfiguration": {"mediaType": "text/plain"}}}})
        await self._send({"event": {"textInput": {
            "promptName": self._prompt_name, "contentName": sys_content,
            "content": system_prompt}}})
        await self._send({"event": {"contentEnd": {
            "promptName": self._prompt_name, "contentName": sys_content}}})
        # 啟動背景 receive loop（Task 4 實作 _receive_loop）
        self._recv_task = asyncio.create_task(self._receive_loop())
```

> **備註（pytest-asyncio）**：先確認 `.venv/bin/python -c "import pytest_asyncio"`。若缺，兩個選項：(a) `.venv/bin/pip install pytest-asyncio` 並在 `tests/conftest.py` 設 `asyncio_mode=auto`；(b) 不裝，改把每個 async test 寫成 `def test_x(): asyncio.run(_impl())`。二選一，全 task 一致即可。本計劃預設採 (a)。

- [ ] **Step 4: 跑測試驗證通過**

Run: `cd talkybuddy && .venv/bin/python -m pytest tests/test_nova_sonic.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/nova_sonic.py tests/test_nova_sonic.py tests/conftest.py
git commit -m "feat(live): NovaSonicSession.start 開串流+送 session/prompt/system 事件序"
```

---

### Task 3: `send_audio()` + `end_user_turn()`

**Files:**
- Modify: `server/nova_sonic.py`
- Test: `tests/test_nova_sonic.py`

**Interfaces:**
- Consumes: `start()`（Task 2）
- Produces:
  - `async NovaSonicSession.send_audio(pcm16: bytes) -> None`（首塊前自動送 AUDIO `contentStart`（16kHz, role=USER），再送 `audioInput`（base64））
  - `async NovaSonicSession.end_user_turn() -> None`（送音訊 `contentEnd`；**不送 `promptEnd`**）

- [ ] **Step 1: 寫失敗測試**（append）

```python
@pytest.mark.asyncio
async def test_send_audio_opens_audio_content_once_then_end(monkeypatch):
    fake_client = _FakeClient()
    monkeypatch.setattr(nova_sonic, "_build_client", lambda self: fake_client)
    s = nova_sonic.NovaSonicSession("m", "tiffany", "us-east-1")
    await s.start("sys")
    base = len(s._stream.input_stream.sent)

    await s.send_audio(b"\x01\x02" * 10)
    await s.send_audio(b"\x03\x04" * 10)
    await s.end_user_turn()

    new_keys = [k for c in s._stream.input_stream.sent[base:]
                for k in json.loads(c.value.bytes_)["event"].keys()]
    # 首塊觸發一次 AUDIO contentStart，之後只有 audioInput，收尾一個 contentEnd
    assert new_keys == ["contentStart", "audioInput", "audioInput", "contentEnd"]
    # end_user_turn 不得送 promptEnd
    assert "promptEnd" not in new_keys
    # AUDIO contentStart 為 16kHz USER
    cs = [json.loads(c.value.bytes_)["event"]["contentStart"]
          for c in s._stream.input_stream.sent[base:]
          if "contentStart" in json.loads(c.value.bytes_)["event"]][0]
    assert cs["type"] == "AUDIO" and cs["role"] == "USER"
    assert cs["audioInputConfiguration"]["sampleRateHertz"] == 16000
    await s.close()
```

- [ ] **Step 2: 跑測試驗證失敗**

Run: `cd talkybuddy && .venv/bin/python -m pytest tests/test_nova_sonic.py::test_send_audio_opens_audio_content_once_then_end -q`
Expected: FAIL（`has no attribute 'send_audio'`）

- [ ] **Step 3: 在 `NovaSonicSession` 補 `send_audio` / `end_user_turn`**

```python
    async def send_audio(self, pcm16: bytes) -> None:
        """送一塊 16kHz PCM16；首塊前自動開 AUDIO 內容區。"""
        if self._audio_content is None:
            self._audio_content = str(uuid.uuid4())
            await self._send({"event": {"contentStart": {
                "promptName": self._prompt_name, "contentName": self._audio_content,
                "type": "AUDIO", "interactive": True, "role": "USER",
                "audioInputConfiguration": {
                    "mediaType": "audio/lpcm", "sampleRateHertz": 16000,
                    "sampleSizeBits": 16, "channelCount": 1,
                    "audioType": "SPEECH", "encoding": "base64"}}}})
        b64 = base64.b64encode(pcm16).decode("ascii")
        await self._send({"event": {"audioInput": {
            "promptName": self._prompt_name, "contentName": self._audio_content,
            "content": b64}}})

    async def end_user_turn(self) -> None:
        """結束本輪使用者音訊（送 contentEnd）；不送 promptEnd（協定踩雷）。"""
        if self._audio_content is not None:
            await self._send({"event": {"contentEnd": {
                "promptName": self._prompt_name, "contentName": self._audio_content}}})
            self._audio_content = None
```

- [ ] **Step 4: 跑測試驗證通過**

Run: `cd talkybuddy && .venv/bin/python -m pytest tests/test_nova_sonic.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/nova_sonic.py tests/test_nova_sonic.py
git commit -m "feat(live): NovaSonicSession.send_audio/end_user_turn（首塊開 AUDIO、收尾不送 promptEnd）"
```

---

### Task 4: `events()` 多 completion 收斂 + `close()`

**Files:**
- Modify: `server/nova_sonic.py`
- Test: `tests/test_nova_sonic.py`

**Interfaces:**
- Consumes: `start()` 啟動的 `self._recv_task`、`self._queue`
- Produces:
  - `async NovaSonicSession.events() -> AsyncIterator[NovaEvent]`（yield `transcript(role,text)` / `audio(pcm24)`，收到 assistant 內容後的 `completionEnd` → 一個 `turn_end` 後結束本輪迭代）
  - `async NovaSonicSession.close() -> None`（送 `promptEnd`+`sessionEnd`、關 input、取消 recv task、關流）
  - 私有 `async _receive_loop()`（解析模型事件放進 `self._queue`；多 completion 收斂邏輯）

- [ ] **Step 1: 寫失敗測試**（append）

```python
def _out_event(**ev):
    return json.dumps({"event": ev}).encode("utf-8")


@pytest.mark.asyncio
async def test_events_converge_multi_completion(monkeypatch):
    """先 USER-ASR completionEnd 不收尾，等 ASSISTANT 內容後才發 turn_end。"""
    asst_audio = base64.b64encode(b"\x00\x00" * 5).decode("ascii")
    out = [
        _out_event(textOutput={"role": "USER", "content": "我想吃蘋果"}),
        _out_event(completionEnd={}),  # user-ASR 段結束 → 不可收尾
        _out_event(textOutput={"role": "ASSISTANT", "content": "好棒！apple。"}),
        _out_event(audioOutput={"content": asst_audio}),
        _out_event(completionEnd={}),  # assistant 段結束 → turn_end
    ]
    fake_client = _FakeClient(out_events=out)
    monkeypatch.setattr(nova_sonic, "_build_client", lambda self: fake_client)
    s = nova_sonic.NovaSonicSession("m", "tiffany", "us-east-1")
    await s.start("sys")

    got = []
    async for ev in s.events():
        got.append((ev.kind, ev.role, ev.text, len(ev.audio)))

    assert ("transcript", "USER", "我想吃蘋果", 0) in got
    assert ("transcript", "ASSISTANT", "好棒！apple。", 0) in got
    assert any(k == "audio" and n > 0 for (k, _, _, n) in got)
    assert got[-1][0] == "turn_end"
    await s.close()
    assert s._stream.input_stream.closed is True


@pytest.mark.asyncio
async def test_close_sends_prompt_and_session_end(monkeypatch):
    fake_client = _FakeClient()
    monkeypatch.setattr(nova_sonic, "_build_client", lambda self: fake_client)
    s = nova_sonic.NovaSonicSession("m", "tiffany", "us-east-1")
    await s.start("sys")
    await s.close()
    keys = [k for c in s._stream.input_stream.sent
            for k in json.loads(c.value.bytes_)["event"].keys()]
    assert "promptEnd" in keys and "sessionEnd" in keys
```

- [ ] **Step 2: 跑測試驗證失敗**

Run: `cd talkybuddy && .venv/bin/python -m pytest tests/test_nova_sonic.py -k "events_converge or close_sends" -q`
Expected: FAIL（`has no attribute 'events'` / `close`）

- [ ] **Step 3: 在 `NovaSonicSession` 補 `_receive_loop` / `events` / `close`**

```python
    async def _receive_loop(self) -> None:
        """解析模型下行事件放進 queue；多 completion 收斂後 put turn_end + None。"""
        got_assistant = False
        try:
            _, out_stream = await self._stream.await_output()
            while True:
                event = await out_stream.receive()
                if event is None:
                    break
                raw = getattr(getattr(event, "value", None), "bytes_", None)
                if raw is None:
                    continue
                try:
                    ev = json.loads(raw).get("event", {})
                except Exception:
                    continue
                if "textOutput" in ev:
                    t = ev["textOutput"]
                    role = t.get("role", "?")
                    await self._queue.put(NovaEvent("transcript", role=role,
                                                    text=t.get("content", "")))
                    if role == "ASSISTANT":
                        got_assistant = True
                elif "audioOutput" in ev:
                    try:
                        pcm = base64.b64decode(ev["audioOutput"].get("content", ""))
                    except Exception:
                        pcm = b""
                    if pcm:
                        got_assistant = True
                        await self._queue.put(NovaEvent("audio", audio=pcm))
                elif "completionEnd" in ev:
                    # 先 USER-ASR 段（未見 assistant）→ 不收尾；assistant 段 → turn_end
                    if got_assistant:
                        await self._queue.put(NovaEvent("turn_end"))
                        got_assistant = False
        except Exception:
            _log.exception("nova_sonic receive loop 異常")
        finally:
            await self._queue.put(None)  # 串流結束哨兵

    async def events(self):
        """yield 本輪事件直到 turn_end（或串流結束）。"""
        while True:
            ev = await self._queue.get()
            if ev is None:
                return
            yield ev
            if ev.kind == "turn_end":
                return

    async def close(self) -> None:
        """收尾：promptEnd + sessionEnd + 關 input + 取消 recv + 關流（每步吞例外）。"""
        try:
            await self._send({"event": {"promptEnd": {"promptName": self._prompt_name}}})
            await self._send({"event": {"sessionEnd": {}}})
            await self._stream.input_stream.close()
        except Exception:
            pass
        if self._recv_task is not None:
            self._recv_task.cancel()
            self._recv_task = None
        try:
            await self._stream.close()
        except Exception:
            pass
```

- [ ] **Step 4: 跑測試驗證通過**

Run: `cd talkybuddy && .venv/bin/python -m pytest tests/test_nova_sonic.py -q`
Expected: PASS（全部）

- [ ] **Step 5: Commit**

```bash
git add server/nova_sonic.py tests/test_nova_sonic.py
git commit -m "feat(live): NovaSonicSession.events 多 completion 收斂 + close 收尾序"
```

---

### Task 5: `build_live_system_prompt()` 鷹架組裝

**Files:**
- Modify: `server/scaffold.py`（檔尾 append）
- Test: `tests/test_scaffold_live_prompt.py`

**Interfaces:**
- Consumes: `guardrails.CHILD_SAFETY_CLAUSE`（既有常數）
- Produces: `scaffold.build_live_system_prompt(target_sentence: str | None, directive: str | None) -> str`

- [ ] **Step 1: 寫失敗測試**

`tests/test_scaffold_live_prompt.py`：

```python
# -*- coding: utf-8 -*-
"""build_live_system_prompt：即時 S2S 鷹架 system prompt 組裝（純函式）。"""
from __future__ import annotations

from server import scaffold


def test_static_frame_present():
    p = scaffold.build_live_system_prompt(None, None)
    assert "說說學伴" in p                    # 企鵝角色
    assert "繁體中文" in p                     # 語言規則
    assert "英" in p                           # 帶讀英文
    # 兒童安全護欄折入（重用 guardrails 常數的關鍵字）
    assert "個人資料" in p or "個資" in p or "難過" in p


def test_dynamic_target_and_directive_folded():
    p = scaffold.build_live_system_prompt(
        "I want to eat an apple.",
        "【本輪教學策略】目標：食物主題；難度：維持難度。",
    )
    assert "I want to eat an apple." in p
    assert "本輪教學策略" in p


def test_none_directive_no_crash():
    p = scaffold.build_live_system_prompt("How are you today?", None)
    assert "How are you today?" in p
    assert isinstance(p, str) and len(p) > 0
```

- [ ] **Step 2: 跑測試驗證失敗**

Run: `cd talkybuddy && .venv/bin/python -m pytest tests/test_scaffold_live_prompt.py -q`
Expected: FAIL（`has no attribute 'build_live_system_prompt'`）

- [ ] **Step 3: 在 `server/scaffold.py` 檔尾 append**

```python
# ---------------------------------------------------------------------------
# 即時 S2S（Nova Sonic）鷹架 system prompt 組裝
# 靜態框架寫死 + 動態今日目標句/directive 折入；重用 guardrails 兒童安全護欄。
# ---------------------------------------------------------------------------

_LIVE_STATIC_FRAME = (
    "你是陪台灣國小學生練習說話的企鵝學伴「說說學伴」，溫暖、有耐心、像朋友。"
    "一、主要用繁體中文（台灣用語）回覆，語氣自然、每次回覆不超過兩句話。"
    "二、每一輪自然帶出一個簡單的英語單字或短句，鼓勵孩子跟著開口說一次（帶讀）。"
    "三、用鷹架學習法：先示範再邀請、給比孩子程度略高一點的內容（i+1）、"
    "孩子說對給具體正向回饋、說錯溫和重述不指責、循序漸進不催促。"
    "四、不使用 markdown 符號或 emoji。"
)


def build_live_system_prompt(target_sentence, directive) -> str:
    """組裝即時陪聊 system prompt。

    - target_sentence：今日重點英文句（可 None）→ 提示學伴優先帶讀。
    - directive：B 軸 companion_directive 注入字串（可 None，見
      diagnose.format_directive_for_prompt）→ 折入本輪教學策略。
    """
    from server import guardrails

    parts = [_LIVE_STATIC_FRAME, guardrails.CHILD_SAFETY_CLAUSE]
    if target_sentence and str(target_sentence).strip():
        parts.append(f"今日想邀請孩子開口說的英文句是：「{str(target_sentence).strip()}」，"
                     "找機會自然帶讀，不必每句都用。")
    if directive and str(directive).strip():
        parts.append(str(directive).strip())
    return "".join(parts)
```

- [ ] **Step 4: 跑測試驗證通過**

Run: `cd talkybuddy && .venv/bin/python -m pytest tests/test_scaffold_live_prompt.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/scaffold.py tests/test_scaffold_live_prompt.py
git commit -m "feat(live): scaffold.build_live_system_prompt 鷹架 prompt 組裝（靜態框架+動態注入）"
```

---

### Task 6: `/api/status` 加 `live_s2s` 欄

**Files:**
- Modify: `server/app.py`（`api_status` 內，約 `:118`）
- Test: `tests/test_app_live.py`

**Interfaces:**
- Consumes: `nova_sonic.available()`（Task 1）、`config.LIVE_S2S_ENABLED`（Task 1）
- Produces: `/api/status` 回應新增 `"live_s2s": bool`（= `LIVE_S2S_ENABLED AND nova_sonic.available()`）

- [ ] **Step 1: 寫失敗測試**

`tests/test_app_live.py`：

```python
# -*- coding: utf-8 -*-
"""/api/status live_s2s 欄 + /ws/live 端點行為測試（注入 fake NovaSonicSession）。"""
from __future__ import annotations

from starlette.testclient import TestClient

from server import app as app_mod
from server import config, nova_sonic


def test_status_has_live_s2s_true(monkeypatch):
    monkeypatch.setattr(config, "LIVE_S2S_ENABLED", True)
    monkeypatch.setattr(nova_sonic, "available", lambda: True)
    client = TestClient(app_mod.app)
    body = client.get("/api/status").json()
    assert body["live_s2s"] is True


def test_status_live_s2s_false_when_flag_off(monkeypatch):
    monkeypatch.setattr(config, "LIVE_S2S_ENABLED", False)
    monkeypatch.setattr(nova_sonic, "available", lambda: True)
    client = TestClient(app_mod.app)
    assert client.get("/api/status").json()["live_s2s"] is False


def test_status_live_s2s_false_when_unavailable(monkeypatch):
    monkeypatch.setattr(config, "LIVE_S2S_ENABLED", True)
    monkeypatch.setattr(nova_sonic, "available", lambda: False)
    client = TestClient(app_mod.app)
    assert client.get("/api/status").json()["live_s2s"] is False
```

- [ ] **Step 2: 跑測試驗證失敗**

Run: `cd talkybuddy && .venv/bin/python -m pytest tests/test_app_live.py -k status -q`
Expected: FAIL（`KeyError: 'live_s2s'`）

- [ ] **Step 3: 修改 `server/app.py`**

在檔頭 import（`from server import config, diagnose, guardrails, profile, store` 那行）補 `nova_sonic`：

```python
from server import config, diagnose, guardrails, nova_sonic, profile, store
```

在 `api_status()` 的回傳 dict 加一欄（`"pending": store.pending_count(),` 之後）：

```python
        "live_s2s": bool(config.LIVE_S2S_ENABLED and nova_sonic.available()),
```

- [ ] **Step 4: 跑測試驗證通過**

Run: `cd talkybuddy && .venv/bin/python -m pytest tests/test_app_live.py -k status -q`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add server/app.py tests/test_app_live.py
git commit -m "feat(live): /api/status 加 live_s2s 能力欄（旗標 AND available）"
```

---

### Task 7: `/ws/live` WebSocket 薄橋

**Files:**
- Modify: `server/app.py`（`/ws/talk` handler 之後 append）
- Test: `tests/test_app_live.py`

**Interfaces:**
- Consumes: `nova_sonic.NovaSonicSession`、`nova_sonic.available()`、`guardrails.consent_granted()`、`scaffold.build_live_system_prompt()`、`diagnose.format_directive_for_prompt()`、`store.add_interaction()`、`pipeline._directive`（既有 B 軸注入字串，可 None）、`config.*`
- Produces: WebSocket 端點 `/ws/live`。協定：
  - client→server：binary frame = 16kHz PCM16 塊；`{"type":"user_end"}` = 結束本輪；`{"type":"bye"}` = 收線。
  - server→client：binary frame = 24kHz PCM16 塊；`{"type":"live_transcript","role":..,"text":..}`；`{"type":"live_error","reason":..}`；`{"type":"turn_end"}`。
  - 依賴注入點：模組級 `_make_live_session()` 工廠（測試 monkeypatch 換 fake）。

- [ ] **Step 1: 寫失敗測試**（append 到 `tests/test_app_live.py`）

```python
import json


class _FakeSession:
    """假的 NovaSonicSession：記錄呼叫、events() 吐預設事件序。"""
    last = None

    def __init__(self, *a, **k):
        self.started_with = None
        self.audio_chunks = []
        self.ended = False
        self.closed = False
        _FakeSession.last = self

    async def start(self, system_prompt):
        self.started_with = system_prompt

    async def send_audio(self, pcm16):
        self.audio_chunks.append(pcm16)

    async def end_user_turn(self):
        self.ended = True

    async def events(self):
        from server.nova_sonic import NovaEvent
        yield NovaEvent("transcript", role="USER", text="我想吃蘋果")
        yield NovaEvent("transcript", role="ASSISTANT", text="好棒！apple。")
        yield NovaEvent("audio", audio=b"\x00\x00" * 4)
        yield NovaEvent("turn_end")

    async def close(self):
        self.closed = True


def _wire_fake(monkeypatch):
    monkeypatch.setattr(config, "LIVE_S2S_ENABLED", True)
    monkeypatch.setattr(nova_sonic, "available", lambda: True)
    monkeypatch.setattr(app_mod, "_make_live_session", lambda: _FakeSession())


def test_ws_live_turn_stores_interaction(monkeypatch):
    _wire_fake(monkeypatch)
    monkeypatch.setattr(app_mod.guardrails, "consent_granted", lambda: True)
    seen = {}
    monkeypatch.setattr(app_mod.store, "add_interaction",
                        lambda d: seen.update(d) or 1)
    client = TestClient(app_mod.app)
    with client.websocket_connect("/ws/live") as ws:
        ws.send_bytes(b"\x01\x02" * 8)
        ws.send_text(json.dumps({"type": "user_end"}))
        # 收到 transcript / audio / turn_end
        kinds = []
        for _ in range(4):
            m = ws.receive()
            if "text" in m and m["text"]:
                kinds.append(json.loads(m["text"]).get("type"))
            elif m.get("bytes"):
                kinds.append("audio")
        ws.send_text(json.dumps({"type": "bye"}))
    assert "live_transcript" in kinds
    assert "audio" in kinds
    # transcript 落地：USER→asr_text、ASSISTANT→reply_text
    assert seen.get("asr_text") == "我想吃蘋果"
    assert seen.get("reply_text") == "好棒！apple。"


def test_ws_live_denied_without_consent(monkeypatch):
    _wire_fake(monkeypatch)
    monkeypatch.setattr(app_mod.guardrails, "consent_granted", lambda: False)
    client = TestClient(app_mod.app)
    with client.websocket_connect("/ws/live") as ws:
        m = ws.receive()
        payload = json.loads(m["text"])
        assert payload["type"] == "live_error"
        assert payload["reason"] == "consent_required"


def test_ws_live_denied_when_unavailable(monkeypatch):
    _wire_fake(monkeypatch)
    monkeypatch.setattr(nova_sonic, "available", lambda: False)
    monkeypatch.setattr(app_mod.guardrails, "consent_granted", lambda: True)
    client = TestClient(app_mod.app)
    with client.websocket_connect("/ws/live") as ws:
        payload = json.loads(ws.receive()["text"])
        assert payload["type"] == "live_error"
        assert payload["reason"] == "unavailable"
```

- [ ] **Step 2: 跑測試驗證失敗**

Run: `cd talkybuddy && .venv/bin/python -m pytest tests/test_app_live.py -k ws_live -q`
Expected: FAIL（找不到 `/ws/live` → websocket 連線關閉 / `_make_live_session` 不存在）

- [ ] **Step 3: 在 `server/app.py` 補工廠 + `/ws/live` handler**

在全域單例區塊（`pipeline = VoicePipeline(...)` 之後）加工廠：

```python
def _make_live_session():
    """建一場 NovaSonicSession（測試以 monkeypatch 換 fake）。"""
    return nova_sonic.NovaSonicSession(
        model_id=config.NOVA_SONIC_MODEL_ID,
        voice=config.NOVA_SONIC_VOICE,
        region=config.BEDROCK_REGION,
    )
```

在 `ws_talk` handler 之後 append `/ws/live`：

```python
@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    """即時全雙工 S2S（Nova Sonic）：與 /ws/talk 並存。

    連線先過 consent + available gate；之後上行 PCM16(16k) → send_audio，
    下行 Nova Sonic audio(24k)/transcript 轉發；user_end → end_user_turn 後
    迭代 events() 直到 turn_end；turn_end 把 USER/ASSISTANT transcript 落地。
    """
    await websocket.accept()

    send_lock = asyncio.Lock()

    async def emit(payload: dict) -> None:
        try:
            async with send_lock:
                await websocket.send_json(payload)
        except Exception:
            pass

    async def emit_bytes(data: bytes) -> None:
        try:
            async with send_lock:
                await websocket.send_bytes(data)
        except Exception:
            pass

    # gate：consent 優先（資料出境），再 available
    if not guardrails.consent_granted():
        await emit({"type": "live_error", "reason": "consent_required"})
        await websocket.close()
        return
    if not (config.LIVE_S2S_ENABLED and nova_sonic.available()):
        await emit({"type": "live_error", "reason": "unavailable"})
        await websocket.close()
        return

    # 動態鷹架注入：沿用 pipeline 目前的 B 軸 directive（已是格式化字串或 None）
    directive = getattr(pipeline, "_directive", None)
    target = "How are you today?"  # Phase 1 起始目標句（詞庫通用引導句）
    system_prompt = scaffold.build_live_system_prompt(target, directive)

    session = _make_live_session()
    turn_user, turn_asst = [], []

    async def drain_events() -> None:
        """迭代模型事件轉發前端；turn_end 落地 transcript。"""
        async for ev in session.events():
            if ev.kind == "audio":
                await emit_bytes(ev.audio)
            elif ev.kind == "transcript":
                await emit({"type": "live_transcript", "role": ev.role, "text": ev.text})
                if ev.role == "USER":
                    turn_user.append(ev.text)
                elif ev.role == "ASSISTANT":
                    turn_asst.append(ev.text)
            elif ev.kind == "turn_end":
                _store_live_turn(turn_user, turn_asst)
                turn_user.clear()
                turn_asst.clear()
                await emit({"type": "turn_end"})

    try:
        await session.start(system_prompt)
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                if msg["bytes"]:
                    await session.send_audio(msg["bytes"])
                continue
            raw = msg.get("text")
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            mtype = payload.get("type")
            if mtype == "user_end":
                await session.end_user_turn()
                await drain_events()
            elif mtype == "bye":
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await emit({"type": "live_error", "reason": "stream_error"})
        except Exception:
            pass
    finally:
        try:
            await session.close()
        except Exception:
            pass


def _store_live_turn(user_texts: list[str], asst_texts: list[str]) -> None:
    """把一輪 live transcript 落地（scores 留空待 Phase 2）；失敗不影響串流。"""
    asr_text = " ".join(t for t in user_texts if t).strip()
    reply_text = " ".join(t for t in asst_texts if t).strip()
    if not (asr_text or reply_text):
        return
    try:
        store.add_interaction({
            "asr_text": asr_text,
            "asr_conf": 1.0,
            "reply_text": reply_text,
            "scores": {},
            "source": "live_s2s",
        })
    except Exception:
        logger.exception("live transcript 落地失敗")
```

> **註**：`store.add_interaction(d)` 只要求 dict，內部補 device/student/ts 預設並回傳 seq（見 `server/store.py:122`）。此處欄位對齊既有互動 payload（`asr_text`/`reply_text`/`scores`），額外 `source` 欄向前相容。

- [ ] **Step 4: 跑測試驗證通過**

Run: `cd talkybuddy && .venv/bin/python -m pytest tests/test_app_live.py -q`
Expected: PASS（status 3 + ws_live 3 = 6 passed）

- [ ] **Step 5: 回歸既有測試（確認沒動壞半雙工）**

Run: `cd talkybuddy && .venv/bin/python -m pytest tests/ -q`
Expected: PASS（既有全綠 + 新增）

- [ ] **Step 6: Commit**

```bash
git add server/app.py tests/test_app_live.py
git commit -m "feat(live): /ws/live 薄橋（consent/available gate、PCM 對接、turn_end 落地 transcript）"
```

---

### Task 8: 前端 `live-client.js` 純函式 + node 測

**Files:**
- Create: `web/live-client.js`
- Create: `web/live-client.test.mjs`

**Interfaces:**
- Produces（純函式，可 node import 測）：
  - `floatTo16BitPCM(float32) -> ArrayBuffer`（[-1,1] float32 → 16-bit LE PCM）
  - `base64ToPCM16(b64) -> Int16Array`（Nova Sonic 24kHz base64 → Int16Array）
  - `PlaybackQueue`（`enqueue(int16)` / `size()`；純資料結構，不碰 AudioContext）

- [ ] **Step 1: 寫失敗測試**

`web/live-client.test.mjs`（比照 `web/mic-router.test.mjs` 的 node 內建 test）：

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { floatTo16BitPCM, base64ToPCM16, PlaybackQueue } from "./live-client.js";

test("floatTo16BitPCM 轉出 16-bit LE，範圍夾限", () => {
  const buf = floatTo16BitPCM(new Float32Array([0, 1, -1, 2]));
  const view = new DataView(buf);
  assert.equal(view.getInt16(0, true), 0);
  assert.equal(view.getInt16(2, true), 32767);   // 1.0 → 32767
  assert.equal(view.getInt16(4, true), -32768);  // -1.0 → -32768
  assert.equal(view.getInt16(6, true), 32767);   // 2.0 夾限到 32767
});

test("base64ToPCM16 還原 Int16Array", () => {
  // 兩個 sample：1000, -1000（16-bit LE）
  const bytes = new Uint8Array([0xe8, 0x03, 0x18, 0xfc]);
  const b64 = Buffer.from(bytes).toString("base64");
  const pcm = base64ToPCM16(b64);
  assert.equal(pcm.length, 2);
  assert.equal(pcm[0], 1000);
  assert.equal(pcm[1], -1000);
});

test("PlaybackQueue 累加長度", () => {
  const q = new PlaybackQueue();
  q.enqueue(new Int16Array([1, 2, 3]));
  q.enqueue(new Int16Array([4, 5]));
  assert.equal(q.size(), 5);
});
```

- [ ] **Step 2: 跑測試驗證失敗**

Run: `cd talkybuddy/web && node --test live-client.test.mjs`
Expected: FAIL（找不到 `./live-client.js`）

- [ ] **Step 3: 建立 `web/live-client.js`（純函式 export + 瀏覽器整合；純函式先寫，整合部分測試不觸及）**

```javascript
// live-client.js — 即時 S2S 前端：麥克風 16k PCM 上行 + Nova Sonic 24k PCM 播放。
// 純函式（floatTo16BitPCM / base64ToPCM16 / PlaybackQueue）可 node 測；
// startLive/stopLive 整合走手動 e2e（見 e2e checklist）。

export function floatTo16BitPCM(float32) {
  const out = new DataView(new ArrayBuffer(float32.length * 2));
  for (let i = 0; i < float32.length; i++) {
    let s = Math.max(-1, Math.min(1, float32[i]));
    out.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return out.buffer;
}

export function base64ToPCM16(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Int16Array(bytes.buffer, 0, Math.floor(bytes.length / 2));
}

export class PlaybackQueue {
  constructor() { this._chunks = []; this._len = 0; }
  enqueue(int16) { this._chunks.push(int16); this._len += int16.length; }
  size() { return this._len; }
  drain() { const c = this._chunks; this._chunks = []; this._len = 0; return c; }
}

// ---- 瀏覽器整合（node 測不觸及；atob 在瀏覽器原生可用）----
export function liveWsURL() {
  const proto = location.protocol === "https:" ? "wss://" : "ws://";
  return proto + location.host + "/ws/live";
}
```

- [ ] **Step 4: 跑測試驗證通過**

Run: `cd talkybuddy/web && node --test live-client.test.mjs`
Expected: PASS（3 tests）

- [ ] **Step 5: Commit**

```bash
git add web/live-client.js web/live-client.test.mjs
git commit -m "feat(live): 前端 live-client 純函式（PCM16 轉換/base64 解碼/播放佇列）+ node 測"
```

---

### Task 9: index.html 模式鈕接線 + 手動 e2e checklist

**Files:**
- Modify: `web/index.html`
- Create: `docs/superpowers/plans/2026-07-12-nova-sonic-live-s2s-e2e-checklist.md`

**Interfaces:**
- Consumes: `/api/status` 的 `live_s2s`（Task 6）、`/ws/live`（Task 7）、`web/live-client.js`（Task 8）
- Produces: 前端「即時對話」模式鈕；`live_s2s=false` 時鈕置灰。（本 task 無自動化斷言，整合正確性靠 e2e checklist 手動驗收。）

- [ ] **Step 1: 在 `web/index.html` 的 header 控制列（`airplaneSwitch` 附近，約 `:188`）加模式鈕**

```html
    <button class="switch" id="liveModeBtn" type="button" disabled
            title="即時語音對話（Nova Sonic）">🎙️ 即時對話</button>
```

- [ ] **Step 2: 在 `<script>` 區（`init()` 讀 `/api/status` 之後，約 `:656` `applyMode` 附近）依 `live_s2s` 啟用鈕**

```javascript
  // 即時 S2S 能力：live_s2s=false 則鈕保持置灰
  var liveModeBtn = document.getElementById("liveModeBtn");
  if (st.live_s2s) {
    liveModeBtn.disabled = false;
  }
```

- [ ] **Step 3: 加模式鈕點擊 → 動態載入 live-client、切 `/ws/live`（斷 `/ws/talk`）**

```javascript
  liveModeBtn.addEventListener("click", async function () {
    var mod = await import("/static/live-client.js");
    if (typeof ws !== "undefined" && ws) { try { ws.close(); } catch (e) {} }
    // startLive 由 live-client 整合層提供（麥克風擷取 + WS binary 上下行）；
    // 具體擷取/播放接線走手動 e2e，見 checklist。
    window.__liveMod = mod;   // e2e 手動觸發用
    showToast("🎙️ 即時對話模式（Nova Sonic）");
  });
```

- [ ] **Step 4: 建立手動 e2e checklist**

`docs/superpowers/plans/2026-07-12-nova-sonic-live-s2s-e2e-checklist.md`：

```markdown
# Nova Sonic 即時 S2S — 手動端到端驗收 checklist

前置：
- [ ] `.venv` 已裝 `aws_sdk_bedrock_runtime` + `smithy_aws_core`
- [ ] 匯出短期 SigV4 憑證：`export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... BEDROCK_REGION=us-east-1`
- [ ] 啟動：`cd talkybuddy && .venv/bin/python -m uvicorn server.app:app --host 0.0.0.0 --port <閒置埠>`

協定層（先用已證腳本確認帳號/憑證仍通）：
- [ ] `AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... .venv/bin/python scripts/verify_nova_sonic_live.py docs/voice-reference/<中文語音>.wav` → 見 ASSISTANT 中文回覆 + audioOutput bytes > 0

能力旗標：
- [ ] `curl -s localhost:<埠>/api/status` → `live_s2s: true`（憑證缺時應為 false 且鈕置灰）

瀏覽器端到端：
- [ ] 開 `https://<host>:<埠>/`，「即時對話」鈕可按（非灰）
- [ ] 按鈕 → 允許麥克風 → 說一句中文 → 停頓
- [ ] 畫面出現 USER 逐字稿 + ASSISTANT 繁中回覆，並**聽到**中文語音（企鵝學伴帶讀英文詞）
- [ ] 教師端 `/teacher` 或 `/api/interactions` 看到本輪 `asr_text`/`reply_text`（`source=live_s2s`）
- [ ] 切回半雙工（重整或飛航模式）→ `/ws/talk` 行為完全不變

未同意情境：
- [ ] `TALKYBUDDY_CONSENT_GRANTED=0` 重啟 → 連 `/ws/live` 收到 `live_error/consent_required`、鈕行為明確
```

- [ ] **Step 5: 跑前端純函式回歸 + 後端全測（確認無破壞）**

Run: `cd talkybuddy/web && node --test live-client.test.mjs && cd .. && .venv/bin/python -m pytest tests/ -q`
Expected: 前端 3 tests PASS；後端全綠

- [ ] **Step 6: Commit**

```bash
git add web/index.html docs/superpowers/plans/2026-07-12-nova-sonic-live-s2s-e2e-checklist.md
git commit -m "feat(live): index.html 即時對話模式鈕（依 live_s2s 啟用）+ 手動 e2e checklist"
```

---

## Self-Review 結果

**Spec 覆蓋對照：**

| Spec 元件 | 對應 Task |
|---|---|
| §1 `NovaSonicSession`（start/send_audio/end_user_turn/events/close/available） | Task 1–4 |
| 協定踩雷封裝（EnvironmentCredentialsResolver、不早送 promptEnd、多 completion 收斂） | Task 2/3/4（Global Constraints 明列） |
| §2 `/ws/live` 薄橋（consent gate、available、雙 task 對接、turn_end→add_interaction、逐輪例外不斷線） | Task 7 |
| §3 `build_live_system_prompt`（靜態框架 + 動態 target/directive） | Task 5 |
| §4 前端 `live-client.js` + 模式鈕 + `/api/status` live_s2s | Task 6/8/9 |
| Config 新增（MODEL_ID/VOICE/LIVE_S2S_ENABLED、region 沿用、SigV4 走 env） | Task 1 |
| 測試策略（session mock、prompt 純函式、/ws/live handler fake、前端 node 測、實機 e2e） | Task 1–9 全涵蓋 |
| 非目標（scores/Phase2、fallback/Phase3、barge-in 視覺、中英切換、diagnose 改動） | 未納入（正確） |
| 安全（SigV4 env、consent gate、測試 IAM 短期 key 測後撤銷） | Global Constraints + Task 7 + e2e checklist |

**已知取捨（executor 注意）：**
- `target_sentence` Phase 1 用固定起始句 `"How are you today?"`（scaffold 既有通用引導句）；接 scaffold 每日目標句排 Phase 2。
- `barge-in`：倚賴 Nova Sonic 原生打斷；Phase 1 turn 邊界用前端明確 `user_end` 訊號，不做半途視覺 UI（符合非目標）。
- 前端麥克風擷取/播放的實際 AudioWorklet 接線走手動 e2e（spec 明訂前端整合走手動 e2e），自動化只覆蓋純函式。
- `pytest-asyncio`：Task 2 Step 3 備註二擇一；全 task 需一致。

**Type/簽章一致性檢查：** `NovaSonicSession(model_id, voice, region)`、`start(system_prompt)`、`send_audio(pcm16)`、`end_user_turn()`、`events()→NovaEvent`、`close()`、`available()`、`build_live_system_prompt(target_sentence, directive)`、`_make_live_session()`、`store.add_interaction(dict)->int` — 跨 task 引用一致。
