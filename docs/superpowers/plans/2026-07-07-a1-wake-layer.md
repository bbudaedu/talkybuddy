# A1 喚醒層 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 為 TalkyBuddy 瀏覽器 client 加上 Porcupine 語音喚醒與 tap-to-toggle push，兩者都導向既有單輪 pipeline，喚醒詞偵測全程在裝置端、喚醒前不上傳。

**Architecture:** client 端新增純狀態機 `WakeController`（協調者）＋ `WakeWordEngine` 抽象（Porcupine 實作）＋ `MicRouter`（一輪錄音），三者以注入介面解耦、可離線 `node --test`。喚醒後沿用既有 `/ws/talk` 音訊上傳；server 僅新增 `/api/wake-config`（下發 AccessKey）與 `{"type":"wake"}` 記錄。麥克風在喚醒引擎與錄音器間**循序切換**（不共用 stream）。

**Tech Stack:** vanilla JS ESM（無 build，CDN 動態 import）｜`@picovoice/porcupine-web@4.0.1` + `@picovoice/web-voice-processor@4.0.10`｜node --test（JS 單元測）｜FastAPI + pytest（httpx ASGITransport / starlette TestClient）。

## Global Constraints

- 引擎版本 pin：`@picovoice/porcupine-web@4.0.1`、`@picovoice/web-voice-processor@4.0.10`（Apache-2.0）。
- 無 build：新 JS 檔為 ESM 模組，client 以 `import(...)` 動態載入；CDN 用 jsDelivr `+esm`。
- 喚醒生命週期：**無 start/stop/pause**，靠 `WebVoiceProcessor.subscribe(worker)`（開始聽）/ `unsubscribe(worker)`（停、釋放麥）。
- 麥克風**循序切換**：WVP 不接受外部 MediaStream，不可與 MediaRecorder 共用同一 stream；每步 `await`。
- AccessKey 存 server 環境變數 `PICOVOICE_ACCESS_KEY`，經 `/api/wake-config` 下發，**不 commit、不硬編**。
- **不破壞現有可運行狀態**：喚醒層任何失敗都退回 push→text，push 永遠可用。
- 漸進喚醒詞：預設內建 `BuiltInKeyword`（免 .ppn）跑通，之後換自訂「Hey Penguin」`.ppn`。
- 新 ESM 檔用 class/現代語法；`web/index.html` 內接線沿用現有 `var`/`function` 風格與繁中註解。

---

### Task 1: server `/api/wake-config` 端點 + config 常數

**Files:**
- Modify: `talkybuddy/server/config.py`（結尾新增 A1 區塊）
- Modify: `talkybuddy/server/app.py`（`/api/status` 之後新增端點，約 :115）
- Test: `talkybuddy/tests/test_wake_config.py`

**Interfaces:**
- Produces: `GET /api/wake-config` → `{enabled: bool, access_key: str, keyword_builtin: str, keyword_label: str, keyword_public_path: str, model_public_path: str, sensitivity: float}`
- Produces（config 常數）：`config.PICOVOICE_ACCESS_KEY`、`WAKE_KEYWORD_BUILTIN`、`WAKE_KEYWORD_LABEL`、`WAKE_KEYWORD_PUBLIC_PATH`、`WAKE_MODEL_PUBLIC_PATH`、`WAKE_SENSITIVITY`

- [ ] **Step 1: 寫失敗測試**

Create `talkybuddy/tests/test_wake_config.py`：

```python
# -*- coding: utf-8 -*-
"""/api/wake-config 端點契約測試（不需真模型/瀏覽器）。anyio_backend 由 conftest 提供。"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from server import config
from server.app import app

pytestmark = pytest.mark.anyio


async def _client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver")


async def test_wake_config_shape_and_enabled_true(monkeypatch):
    monkeypatch.setattr(config, "PICOVOICE_ACCESS_KEY", "test-key-123")
    async with await _client() as client:
        resp = await client.get("/api/wake-config")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "enabled", "access_key", "keyword_builtin", "keyword_label",
        "keyword_public_path", "model_public_path", "sensitivity",
    }
    assert body["enabled"] is True
    assert body["access_key"] == "test-key-123"
    assert isinstance(body["sensitivity"], float)


async def test_wake_config_disabled_when_no_key(monkeypatch):
    monkeypatch.setattr(config, "PICOVOICE_ACCESS_KEY", "")
    async with await _client() as client:
        resp = await client.get("/api/wake-config")
    body = resp.json()
    assert body["enabled"] is False
    assert body["access_key"] == ""
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd talkybuddy && .venv/bin/python -m pytest tests/test_wake_config.py -v`
Expected: FAIL（404 或 AttributeError：端點/常數未定義）

- [ ] **Step 3: 加 config 常數**

在 `talkybuddy/server/config.py` 結尾（`OPENCC_CONFIG` 之後）新增：

```python

# ---------------------------------------------------------------------------
# A1 喚醒層（Porcupine Web）：AccessKey 由環境變數注入，不寫進 repo；經
# /api/wake-config 下發給瀏覽器。喚醒詞先用內建 BuiltInKeyword 跑通，之後換自訂 .ppn。
# ---------------------------------------------------------------------------
import os

PICOVOICE_ACCESS_KEY: str = os.environ.get("PICOVOICE_ACCESS_KEY", "")
# 內建喚醒詞名稱（BuiltInKeyword 之一，如 "Bumblebee" / "Porcupine" / "Grasshopper"）。
WAKE_KEYWORD_BUILTIN: str = os.environ.get("WAKE_KEYWORD_BUILTIN", "Bumblebee")
WAKE_KEYWORD_LABEL: str = os.environ.get("WAKE_KEYWORD_LABEL", "Bumblebee")
# 自訂 .ppn（Web/WASM target）相對 URL；非空則優先於內建，空字串=用內建。
WAKE_KEYWORD_PUBLIC_PATH: str = os.environ.get("WAKE_KEYWORD_PUBLIC_PATH", "")
# Porcupine 參數模型 .pv（英文）相對 URL。
WAKE_MODEL_PUBLIC_PATH: str = os.environ.get("WAKE_MODEL_PUBLIC_PATH", "/assets/porcupine_params.pv")
# 喚醒靈敏度 0~1（越高越易觸發、也越易誤觸）。
WAKE_SENSITIVITY: float = float(os.environ.get("WAKE_SENSITIVITY", "0.6"))
```

- [ ] **Step 4: 加端點**

在 `talkybuddy/server/app.py` 的 `api_status`（:105-114）之後新增：

```python
@app.get("/api/wake-config")
async def api_wake_config():
    """下發 Porcupine Web 喚醒設定給瀏覽器。

    AccessKey 存在 server 環境變數（不進 repo）。enabled=False（未設 key）時
    client 略過語音喚醒、只用 push；行為明確可預期。
    """
    return {
        "enabled": bool(config.PICOVOICE_ACCESS_KEY),
        "access_key": config.PICOVOICE_ACCESS_KEY,
        "keyword_builtin": config.WAKE_KEYWORD_BUILTIN,
        "keyword_label": config.WAKE_KEYWORD_LABEL,
        "keyword_public_path": config.WAKE_KEYWORD_PUBLIC_PATH,
        "model_public_path": config.WAKE_MODEL_PUBLIC_PATH,
        "sensitivity": config.WAKE_SENSITIVITY,
    }
```

- [ ] **Step 5: 跑測試確認通過**

Run: `cd talkybuddy && .venv/bin/python -m pytest tests/test_wake_config.py -v`
Expected: PASS（2 passed）

- [ ] **Step 6: Commit**

```bash
git add talkybuddy/server/config.py talkybuddy/server/app.py talkybuddy/tests/test_wake_config.py
git commit -m "feat(a1): add /api/wake-config endpoint + Porcupine config constants"
```

---

### Task 2: `/ws/talk` 接受並記錄 `wake` 事件

**Files:**
- Modify: `talkybuddy/server/app.py`（頂部加 logger；ws 迴圈 :294-301 的 `mtype` 分派加分支）
- Test: `talkybuddy/tests/test_wake_event.py`

**Interfaces:**
- Consumes: client 送 `{"type": "wake", "source": "wakeword" | "push"}`
- Produces: server 記 `INFO` log（logger 名 `talkybuddy.wake`），不回訊息、不改一輪對話行為

- [ ] **Step 1: 寫失敗測試**

Create `talkybuddy/tests/test_wake_event.py`：

```python
# -*- coding: utf-8 -*-
"""WS /ws/talk 的 wake 事件：記 log 且不干擾後續對話。"""
from __future__ import annotations

from starlette.testclient import TestClient

from server import app as app_module
from server.app import app


def test_ws_wake_event_logged_and_non_disruptive(monkeypatch, caplog):
    monkeypatch.setattr(app_module.llm_engine, "available", lambda: False)
    monkeypatch.setattr(app_module.tts_engine, "available", lambda: False)
    monkeypatch.setattr(app_module.asr_engine, "available", lambda: False)

    with caplog.at_level("INFO", logger="talkybuddy.wake"):
        with TestClient(app) as client:
            with client.websocket_connect("/ws/talk") as ws:
                ws.send_json({"type": "wake", "source": "wakeword"})
                # wake 不回訊息；接著送文字仍能正常對話
                ws.send_json({"type": "text_input", "text": "嗨"})
                got_reply = False
                for _ in range(6):
                    data = ws.receive_json()
                    if data["type"] == "reply":
                        got_reply = True
                    if data["type"] in ("tts_audio", "tts_unavailable"):
                        break

    assert got_reply
    assert any("source=wakeword" in r.getMessage() for r in caplog.records)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd talkybuddy && .venv/bin/python -m pytest tests/test_wake_event.py -v`
Expected: FAIL（無 `source=wakeword` log record）

- [ ] **Step 3: 加 logger 與分支**

在 `talkybuddy/server/app.py` import 區（`import json` 附近，:17）確認有 `import logging`，若無則新增；並在模組層（:37 `AUDIO_DEBOUNCE_S` 附近）新增：

```python
logger = logging.getLogger("talkybuddy.wake")
```

在 ws 迴圈的 `mtype` 分派（:294 `elif mtype == "text_input":` 之前）新增分支：

```python
            elif mtype == "wake":
                # A1 喚醒事件：純記錄（source = "wakeword" | "push"），不改變一輪對話行為。
                source = str(payload.get("source", "") or "unknown")
                logger.info("wake event: source=%s", source)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd talkybuddy && .venv/bin/python -m pytest tests/test_wake_event.py tests/test_e2e.py -v`
Expected: PASS（既有 e2e 不受影響）

- [ ] **Step 5: Commit**

```bash
git add talkybuddy/server/app.py talkybuddy/tests/test_wake_event.py
git commit -m "feat(a1): log {type:wake} events on /ws/talk without disrupting turns"
```

---

### Task 3: `WakeController` 純狀態機

**Files:**
- Create: `talkybuddy/web/wake-controller.js`
- Test: `talkybuddy/web/wake-controller.test.mjs`

**Interfaces:**
- Produces: `export class WakeController`、`export const WakeState = {ARMED, ACTIVE, COOLDOWN}`
- Constructor opts：`{engine, micRouter, onState?, onWakeEvent?, canWake?, cooldownMs?, turnTimeoutMs?, setTimeout?, clearTimeout?}`
  - `engine`（WakeWordEngine，Task 5）：`{available(): bool, start(onDetected): Promise, stop(): Promise}`
  - `micRouter`（Task 4）：`{recordTurn(): Promise, stopTurn(): void}`
  - `onState(state)` / `onWakeEvent(source)` / `canWake(): bool`（預設回 true）
- Produces 方法：`arm(): Promise`、`onWake(source): Promise`、`triggerPush(): void`、`get state`

- [ ] **Step 1: 寫失敗測試**

Create `talkybuddy/web/wake-controller.test.mjs`：

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { WakeController, WakeState } from "./wake-controller.js";

function manualTimers() {
  let seq = 0;
  const pending = new Map();
  return {
    setTimeout: (fn) => { const id = ++seq; pending.set(id, fn); return id; },
    clearTimeout: (id) => { pending.delete(id); },
    flushAll: () => { for (const [id, fn] of [...pending]) { pending.delete(id); fn(); } },
    size: () => pending.size,
  };
}

function makeStubs(opts = {}) {
  const calls = [];
  const engine = {
    _available: opts.available !== false,
    available() { return this._available; },
    async start(onDetected) { calls.push("engine.start"); this._onDetected = onDetected; },
    async stop() { calls.push("engine.stop"); },
  };
  const micRouter = {
    _resolve: null,
    async recordTurn() { calls.push("mic.recordTurn"); return new Promise((r) => { this._resolve = r; }); },
    stopTurn() { calls.push("mic.stopTurn"); if (this._resolve) { const r = this._resolve; this._resolve = null; r(); } },
  };
  return { engine, micRouter, calls };
}

test("arm 進 ARMED 並在引擎可用時開始 always-listening", async () => {
  const { engine, micRouter, calls } = makeStubs();
  const t = manualTimers();
  const c = new WakeController({ engine, micRouter, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout });
  await c.arm();
  assert.equal(c.state, WakeState.ARMED);
  assert.ok(calls.includes("engine.start"));
});

test("喚醒 → ACTIVE：先停引擎再錄音（循序）", async () => {
  const { engine, micRouter, calls } = makeStubs();
  const t = manualTimers();
  const c = new WakeController({ engine, micRouter, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout });
  await c.arm();
  await c.onWake("wakeword");
  assert.equal(c.state, WakeState.ACTIVE);
  const stopIdx = calls.indexOf("engine.stop");
  const recIdx = calls.indexOf("mic.recordTurn");
  assert.ok(stopIdx >= 0 && recIdx > stopIdx, "engine.stop 必須在 mic.recordTurn 之前");
});

test("ACTIVE 期間再收到喚醒 → 忽略（不重入）", async () => {
  const { engine, micRouter, calls } = makeStubs();
  const t = manualTimers();
  const c = new WakeController({ engine, micRouter, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout });
  await c.arm();
  await c.onWake("wakeword");
  const before = calls.length;
  await c.onWake("wakeword");
  assert.equal(calls.length, before, "重入喚醒不應再觸發任何呼叫");
});

test("一輪結束 → COOLDOWN → 去抖後回 ARMED 並重新 arm", async () => {
  const { engine, micRouter, calls } = makeStubs();
  const t = manualTimers();
  const c = new WakeController({ engine, micRouter, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout });
  await c.arm();
  const wake = c.onWake("wakeword");
  micRouter.stopTurn();           // 觸發 recordTurn resolve
  await wake;
  assert.equal(c.state, WakeState.COOLDOWN);
  t.flushAll();                   // 去抖計時器到期
  await Promise.resolve();
  assert.equal(c.state, WakeState.ARMED);
  assert.equal(calls.filter((x) => x === "engine.start").length, 2);
});

test("引擎不可用：arm 不呼叫 engine.start，push 仍可進 ACTIVE", async () => {
  const { engine, micRouter, calls } = makeStubs({ available: false });
  const t = manualTimers();
  const c = new WakeController({ engine, micRouter, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout });
  await c.arm();
  assert.ok(!calls.includes("engine.start"));
  c.triggerPush();
  await Promise.resolve();
  assert.equal(c.state, WakeState.ACTIVE);
});

test("push tap-to-toggle：ACTIVE 時第二次 tap 結束該輪", async () => {
  const { engine, micRouter, calls } = makeStubs();
  const t = manualTimers();
  const c = new WakeController({ engine, micRouter, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout });
  await c.arm();
  c.triggerPush();                // 第一次：ARMED → ACTIVE
  await Promise.resolve();
  c.triggerPush();                // 第二次：停止錄音
  assert.ok(calls.includes("mic.stopTurn"));
});

test("錄音逾時：turnTimeout 到期自動停止該輪", async () => {
  const { engine, micRouter, calls } = makeStubs();
  const t = manualTimers();
  const c = new WakeController({ engine, micRouter, turnTimeoutMs: 15000, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout });
  await c.arm();
  const wake = c.onWake("wakeword");
  t.flushAll();                   // turn timer 到期 → stopTurn
  await wake;
  assert.ok(calls.includes("mic.stopTurn"));
});

test("canWake=false 時忽略喚醒（半雙工鎖）", async () => {
  const { engine, micRouter, calls } = makeStubs();
  const t = manualTimers();
  const c = new WakeController({ engine, micRouter, canWake: () => false, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout });
  await c.arm();
  await c.onWake("wakeword");
  assert.equal(c.state, WakeState.ARMED);
  assert.ok(!calls.includes("mic.recordTurn"));
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd talkybuddy/web && node --test wake-controller.test.mjs`
Expected: FAIL（`Cannot find module './wake-controller.js'`）

- [ ] **Step 3: 實作 WakeController**

Create `talkybuddy/web/wake-controller.js`：

```javascript
// A1 喚醒層狀態機（純邏輯、零瀏覽器依賴，可 node --test）。
// 唯一協調者：串接注入的 WakeWordEngine 與 MicRouter，保證麥克風循序切換。
// 狀態：ARMED ──(喚醒詞|push)──▶ ACTIVE(一輪) ──(結束|逾時)──▶ COOLDOWN ──(去抖)──▶ ARMED

export const WakeState = { ARMED: "armed", ACTIVE: "active", COOLDOWN: "cooldown" };

export class WakeController {
  constructor(opts) {
    this.engine = opts.engine;
    this.micRouter = opts.micRouter;
    this.onState = opts.onState || function () {};
    this.onWakeEvent = opts.onWakeEvent || function () {};
    this.canWake = opts.canWake || function () { return true; };
    this.cooldownMs = opts.cooldownMs != null ? opts.cooldownMs : 1500;
    this.turnTimeoutMs = opts.turnTimeoutMs != null ? opts.turnTimeoutMs : 15000;
    this._setTimeout = opts.setTimeout || setTimeout;
    this._clearTimeout = opts.clearTimeout || clearTimeout;
    this._state = WakeState.ARMED;
    this._turnTimer = null;
    this._cooldownTimer = null;
    this._onDetected = () => { this.onWake("wakeword"); };
  }

  get state() { return this._state; }

  async arm() {
    this._state = WakeState.ARMED;
    this.onState(WakeState.ARMED);
    if (this.engine && this.engine.available()) {
      await this.engine.start(this._onDetected);
    }
  }

  async onWake(source) {
    if (this._state !== WakeState.ARMED) { return; }   // 重入保護
    if (!this.canWake()) { return; }                    // 半雙工鎖
    this._state = WakeState.ACTIVE;
    this.onWakeEvent(source);
    this.onState(WakeState.ACTIVE);
    if (this.engine && this.engine.available()) {
      await this.engine.stop();                         // 先釋放麥（循序，關鍵）
    }
    this._turnTimer = this._setTimeout(() => { this._safeStopTurn(); }, this.turnTimeoutMs);
    await this.micRouter.recordTurn();                  // resolves 當錄音停止+送出+釋放麥
    await this._endTurn();
  }

  triggerPush() {
    if (this._state === WakeState.ARMED) { this.onWake("push"); }
    else if (this._state === WakeState.ACTIVE) { this._safeStopTurn(); }
  }

  _safeStopTurn() {
    if (this._state === WakeState.ACTIVE) { this.micRouter.stopTurn(); }
  }

  async _endTurn() {
    if (this._turnTimer != null) { this._clearTimeout(this._turnTimer); this._turnTimer = null; }
    this._state = WakeState.COOLDOWN;
    this.onState(WakeState.COOLDOWN);
    this._cooldownTimer = this._setTimeout(() => { this.arm(); }, this.cooldownMs);
  }
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd talkybuddy/web && node --test wake-controller.test.mjs`
Expected: PASS（8 tests）

- [ ] **Step 5: Commit**

```bash
git add talkybuddy/web/wake-controller.js talkybuddy/web/wake-controller.test.mjs
git commit -m "feat(a1): add WakeController state machine with node --test coverage"
```

---

### Task 4: `MicRouter` 一輪錄音（getUserMedia → MediaRecorder → 上傳 → 釋放麥）

**Files:**
- Create: `talkybuddy/web/mic-router.js`
- Test: `talkybuddy/web/mic-router.test.mjs`

**Interfaces:**
- Produces: `export class MicRouter`
- Constructor opts：`{getUserMedia, createRecorder, sendBlob, mimeType?, minBytes?}`
  - `getUserMedia(): Promise<MediaStream>`；`createRecorder(stream, mimeType): MediaRecorder`；`sendBlob(blob): void|Promise`
- Produces 方法：`recordTurn(): Promise`（錄音停止+上傳+`track.stop()` 後 resolve）、`stopTurn(): void`

- [ ] **Step 1: 寫失敗測試**

Create `talkybuddy/web/mic-router.test.mjs`：

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { MicRouter } from "./mic-router.js";

function fakeStream() {
  const track = { stopped: false, stop() { this.stopped = true; } };
  return { _track: track, getTracks: () => [track] };
}

function fakeRecorder() {
  return {
    state: "recording",
    ondataavailable: null,
    onstop: null,
    start() { this.state = "recording"; },
    stop() { this.state = "inactive"; if (this.onstop) { this.onstop(); } },
    _emit(size) { if (this.ondataavailable) { this.ondataavailable({ data: { size } }); } },
  };
}

test("recordTurn 開麥錄音，stopTurn 後上傳且釋放麥", async () => {
  const calls = [];
  const stream = fakeStream();
  const recorder = fakeRecorder();
  let sent = null;
  const router = new MicRouter({
    getUserMedia: async () => { calls.push("getUserMedia"); return stream; },
    createRecorder: (s, mime) => { calls.push("createRecorder:" + mime); return recorder; },
    sendBlob: (blob) => { calls.push("sendBlob"); sent = blob; },
    mimeType: "audio/webm;codecs=opus",
    minBytes: 200,
  });
  const p = router.recordTurn();
  await Promise.resolve();
  recorder._emit(5000);          // 收到一段音訊
  router.stopTurn();             // 觸發停止
  await p;
  assert.deepEqual(calls, ["getUserMedia", "createRecorder:audio/webm;codecs=opus", "sendBlob"]);
  assert.ok(sent && sent.size >= 200);
  assert.equal(stream._track.stopped, true, "錄畢必須 track.stop() 釋放麥");
});

test("錄音過短（< minBytes）視為誤觸，不上傳但仍釋放麥", async () => {
  const stream = fakeStream();
  const recorder = fakeRecorder();
  let sendCount = 0;
  const router = new MicRouter({
    getUserMedia: async () => stream,
    createRecorder: () => recorder,
    sendBlob: () => { sendCount += 1; },
    minBytes: 200,
  });
  const p = router.recordTurn();
  await Promise.resolve();
  recorder._emit(50);            // 太短
  router.stopTurn();
  await p;
  assert.equal(sendCount, 0);
  assert.equal(stream._track.stopped, true);
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd talkybuddy/web && node --test mic-router.test.mjs`
Expected: FAIL（`Cannot find module './mic-router.js'`）

- [ ] **Step 3: 實作 MicRouter**

Create `talkybuddy/web/mic-router.js`：

```javascript
// 一輪錄音的麥克風管理：每輪重新 getUserMedia（循序切換所需，不常駐 stream），
// 錄音停止後把 blob 交給 sendBlob，並 track.stop() 釋放麥，讓喚醒引擎能重新接手。

export class MicRouter {
  constructor(opts) {
    this.getUserMedia = opts.getUserMedia;
    this.createRecorder = opts.createRecorder;
    this.sendBlob = opts.sendBlob;
    this.mimeType = opts.mimeType || "audio/webm;codecs=opus";
    this.minBytes = opts.minBytes != null ? opts.minBytes : 200;
    this._recorder = null;
    this._stream = null;
  }

  recordTurn() {
    const self = this;
    return new Promise(function (resolve, reject) {
      self.getUserMedia().then(function (stream) {
        self._stream = stream;
        const chunks = [];
        const recorder = self.createRecorder(stream, self.mimeType);
        self._recorder = recorder;
        recorder.ondataavailable = function (ev) {
          if (ev && ev.data && ev.data.size > 0) { chunks.push(ev.data); }
        };
        recorder.onstop = function () {
          const blob = new Blob(chunks, { type: self.mimeType });
          self._release();
          if (blob.size >= self.minBytes) {
            Promise.resolve(self.sendBlob(blob)).then(resolve, resolve);
          } else {
            resolve();   // 太短視為誤觸，不送
          }
        };
        recorder.start();
      }).catch(reject);
    });
  }

  stopTurn() {
    if (this._recorder && this._recorder.state !== "inactive") {
      this._recorder.stop();
    }
  }

  _release() {
    if (this._stream) {
      this._stream.getTracks().forEach(function (t) { t.stop(); });
      this._stream = null;
    }
    this._recorder = null;
  }
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd talkybuddy/web && node --test mic-router.test.mjs`
Expected: PASS（2 tests）

- [ ] **Step 5: Commit**

```bash
git add talkybuddy/web/mic-router.js talkybuddy/web/mic-router.test.mjs
git commit -m "feat(a1): add MicRouter (per-turn getUserMedia + upload + mic release)"
```

---

### Task 5: `PorcupineEngine` 封裝（WakeWordEngine 實作）

**Files:**
- Create: `talkybuddy/web/porcupine-engine.js`
- Test: `talkybuddy/web/porcupine-engine.test.mjs`

**Interfaces:**
- Produces: `export async function createPorcupineEngine(cfg, deps?)` → `{available(): bool, start(onDetected): Promise, stop(): Promise, release(): Promise}`
  - `cfg`：`{accessKey, keywordBuiltin, keywordLabel, keywordPublicPath, modelPublicPath, sensitivity}`（對應 Task 1 的 `/api/wake-config`）
  - `deps`（測試注入）：`{PorcupineWorker, BuiltInKeyword, WebVoiceProcessor}`；省略時從 CDN `+esm` 動態載入
- 實作契約：`start` = `PorcupineWorker.create(...)` 一次 + `WebVoiceProcessor.subscribe(worker)`；`stop` = `unsubscribe(worker)`；無 accessKey 時 `available()=false` 且 `start` no-op

- [ ] **Step 1: 寫失敗測試**

Create `talkybuddy/web/porcupine-engine.test.mjs`：

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { createPorcupineEngine } from "./porcupine-engine.js";

function fakeDeps() {
  const calls = [];
  const worker = { released: false, async release() { this.released = true; }, terminate() { calls.push("terminate"); } };
  const PorcupineWorker = {
    async create(key, keywords, cb, model) { calls.push(["create", key, keywords, model]); worker._cb = cb; return worker; },
  };
  const BuiltInKeyword = { Bumblebee: "BUMBLEBEE_BYTES", Porcupine: "PORCUPINE_BYTES" };
  const WebVoiceProcessor = {
    async subscribe(w) { calls.push(["subscribe", w === worker]); },
    async unsubscribe(w) { calls.push(["unsubscribe", w === worker]); },
  };
  return { deps: { PorcupineWorker, BuiltInKeyword, WebVoiceProcessor }, calls, worker };
}

const baseCfg = {
  accessKey: "k-1", keywordBuiltin: "Bumblebee", keywordLabel: "Bumblebee",
  keywordPublicPath: "", modelPublicPath: "/assets/porcupine_params.pv", sensitivity: 0.6,
};

test("available() 依 accessKey", async () => {
  const { deps } = fakeDeps();
  const eng = await createPorcupineEngine(baseCfg, deps);
  assert.equal(eng.available(), true);
  const eng2 = await createPorcupineEngine({ ...baseCfg, accessKey: "" }, deps);
  assert.equal(eng2.available(), false);
});

test("start：以內建喚醒詞 create 一次並 subscribe", async () => {
  const { deps, calls, worker } = fakeDeps();
  const detected = [];
  const eng = await createPorcupineEngine(baseCfg, deps);
  await eng.start((d) => detected.push(d));
  const createCall = calls.find((c) => Array.isArray(c) && c[0] === "create");
  assert.ok(createCall);
  assert.equal(createCall[1], "k-1");
  assert.deepEqual(createCall[2], [{ builtin: "BUMBLEBEE_BYTES", sensitivity: 0.6 }]);
  assert.deepEqual(createCall[3], { publicPath: "/assets/porcupine_params.pv" });
  assert.ok(calls.some((c) => Array.isArray(c) && c[0] === "subscribe" && c[1] === true));
  worker._cb({ index: 0, label: "Bumblebee" });   // 引擎回呼 → onDetected
  assert.deepEqual(detected, [{ index: 0, label: "Bumblebee" }]);
});

test("自訂 .ppn（keywordPublicPath）時用 publicPath+label", async () => {
  const { deps, calls } = fakeDeps();
  const eng = await createPorcupineEngine(
    { ...baseCfg, keywordPublicPath: "/assets/hey-penguin_wasm.ppn", keywordLabel: "Hey Penguin" }, deps);
  await eng.start(() => {});
  const createCall = calls.find((c) => Array.isArray(c) && c[0] === "create");
  assert.deepEqual(createCall[2], [{ publicPath: "/assets/hey-penguin_wasm.ppn", label: "Hey Penguin", sensitivity: 0.6 }]);
});

test("stop 呼叫 unsubscribe；start 兩次只 create 一次", async () => {
  const { deps, calls } = fakeDeps();
  const eng = await createPorcupineEngine(baseCfg, deps);
  await eng.start(() => {});
  await eng.stop();
  await eng.start(() => {});
  assert.equal(calls.filter((c) => Array.isArray(c) && c[0] === "create").length, 1);
  assert.ok(calls.some((c) => Array.isArray(c) && c[0] === "unsubscribe" && c[1] === true));
});

test("無 accessKey：start 為 no-op（不 create/subscribe）", async () => {
  const { deps, calls } = fakeDeps();
  const eng = await createPorcupineEngine({ ...baseCfg, accessKey: "" }, deps);
  await eng.start(() => {});
  assert.equal(calls.length, 0);
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd talkybuddy/web && node --test porcupine-engine.test.mjs`
Expected: FAIL（`Cannot find module './porcupine-engine.js'`）

- [ ] **Step 3: 實作 PorcupineEngine**

Create `talkybuddy/web/porcupine-engine.js`：

```javascript
// WakeWordEngine 的 Porcupine 實作，封裝 @picovoice/porcupine-web@4.0.1 +
// @picovoice/web-voice-processor@4.0.10。生命週期無 start/stop：靠 WVP subscribe/unsubscribe。
// deps 可注入（單元測用 fake），省略時從 jsDelivr +esm 動態載入。

const CDN_PORCUPINE = "https://cdn.jsdelivr.net/npm/@picovoice/porcupine-web@4.0.1/+esm";
const CDN_WVP = "https://cdn.jsdelivr.net/npm/@picovoice/web-voice-processor@4.0.10/+esm";

async function loadDeps() {
  const p = await import(CDN_PORCUPINE);
  const w = await import(CDN_WVP);
  return { PorcupineWorker: p.PorcupineWorker, BuiltInKeyword: p.BuiltInKeyword, WebVoiceProcessor: w.WebVoiceProcessor };
}

export async function createPorcupineEngine(cfg, deps) {
  const d = deps || (await loadDeps());
  const PorcupineWorker = d.PorcupineWorker;
  const BuiltInKeyword = d.BuiltInKeyword;
  const WebVoiceProcessor = d.WebVoiceProcessor;
  let worker = null;

  function keywords() {
    if (cfg.keywordPublicPath) {
      return [{ publicPath: cfg.keywordPublicPath, label: cfg.keywordLabel, sensitivity: cfg.sensitivity }];
    }
    return [{ builtin: BuiltInKeyword[cfg.keywordBuiltin], sensitivity: cfg.sensitivity }];
  }

  return {
    available: function () { return !!cfg.accessKey; },
    start: async function (onDetected) {
      if (!cfg.accessKey) { return; }
      if (!worker) {
        worker = await PorcupineWorker.create(
          cfg.accessKey,
          keywords(),
          function (detection) { onDetected(detection); },
          { publicPath: cfg.modelPublicPath }
        );
      }
      await WebVoiceProcessor.subscribe(worker);
    },
    stop: async function () {
      if (worker) { await WebVoiceProcessor.unsubscribe(worker); }
    },
    release: async function () {
      if (worker) {
        await WebVoiceProcessor.unsubscribe(worker);
        await worker.release();
        worker.terminate();
        worker = null;
      }
    },
  };
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd talkybuddy/web && node --test porcupine-engine.test.mjs`
Expected: PASS（5 tests）

- [ ] **Step 5: Commit**

```bash
git add talkybuddy/web/porcupine-engine.js talkybuddy/web/porcupine-engine.test.mjs
git commit -m "feat(a1): add PorcupineEngine wrapper (subscribe/unsubscribe lifecycle)"
```

---

### Task 6: `index.html` 接線 — tap-to-toggle + 喚醒層 + 回饋（手動 verify）

**Files:**
- Modify: `talkybuddy/web/index.html`（push 事件綁定 :694-716；`holdHint` 提示 :630；script 末尾新增接線區）

**Interfaces:**
- Consumes: `WakeController`/`WakeState`（Task 3）、`MicRouter`（Task 4）、`createPorcupineEngine`（Task 5）、`GET /api/wake-config`（Task 1）、`{"type":"wake"}`（Task 2）
- 沿用既有全域：`ws`、`wsReady`、`busy`、`playingTTS`、`setVisualState`、`pickMime`、`showToast`

- [ ] **Step 1: 改 push 為 tap-to-toggle**

移除 `talkybuddy/web/index.html` :694-716 舊的 press-hold 綁定（`pointerdown`→`startRecording`、`pointerup`/`pointercancel`/`pointerleave`→`stopRecording`、touch 後援），改為（保留 `contextmenu` preventDefault）：

```javascript
/* pointer 事件：tap-to-toggle（點一下開始、再點結束）；防長按選單 */
penguinWrap.addEventListener("contextmenu", function (e) { e.preventDefault(); });
penguinWrap.addEventListener("click", function (e) {
  e.preventDefault();
  if (!window._wakeController) { showToast("喚醒功能載入中，請稍候…"); return; }
  if (!micAvailable) { showToast("麥克風不可用，請點下方快速語句"); return; }
  window._wakeController.triggerPush();
});
```

- [ ] **Step 2: 改提示文字**

`talkybuddy/web/index.html` :630 `holdHint.textContent`：

```javascript
    holdHint.textContent = "👆 點一下企鵝開始說話，說完再點一下（或喊喚醒詞）";
```

- [ ] **Step 3: 新增喚醒層接線區**

在 `talkybuddy/web/index.html` 現有 `<script>` 末尾（`initMic()` 呼叫附近的初始化區）新增。注意 `initMic` 仍用來偵測 `micAvailable`，但不再常駐 `mediaStream`（改由 MicRouter 每輪 getUserMedia）：

```javascript
/* ---------- A1 喚醒層接線 ---------- */
function playWakeCue() {
  /* 喚醒回饋音「我在！」：優先 speechSynthesis，無則略過（視覺已亮燈） */
  try {
    if (typeof speechSynthesis !== "undefined") {
      var u = new SpeechSynthesisUtterance("我在！");
      u.lang = "zh-TW"; u.rate = 1.05;
      speechSynthesis.speak(u);
    }
  } catch (e) { /* 忽略 */ }
}

function initWakeLayer() {
  fetch("/api/wake-config").then(function (r) { return r.json(); }).then(function (cfg) {
    return Promise.all([
      import("./wake-controller.js"),
      import("./mic-router.js"),
      import("./porcupine-engine.js")
    ]).then(function (mods) {
      var WakeController = mods[0].WakeController;
      var WakeState = mods[0].WakeState;
      var MicRouter = mods[1].MicRouter;
      var createPorcupineEngine = mods[2].createPorcupineEngine;

      var micRouter = new MicRouter({
        getUserMedia: function () { return navigator.mediaDevices.getUserMedia({ audio: true }); },
        createRecorder: function (stream, mime) { return new MediaRecorder(stream, { mimeType: mime }); },
        sendBlob: function (blob) {
          return blob.arrayBuffer().then(function (buf) {
            if (wsReady) {
              interimBubble = addBubble("student", "（辨識中…）", { interim: true });
              ws.send(buf);
              ws.send(JSON.stringify({ type: "audio_end" }));
            }
          });
        },
        mimeType: pickMime() || "audio/webm;codecs=opus"
      });

      function noopEngine() {
        return { available: function () { return false; }, start: function () { return Promise.resolve(); }, stop: function () { return Promise.resolve(); } };
      }
      var enginePromise = cfg.enabled
        ? createPorcupineEngine({
            accessKey: cfg.access_key, keywordBuiltin: cfg.keyword_builtin,
            keywordLabel: cfg.keyword_label, keywordPublicPath: cfg.keyword_public_path,
            modelPublicPath: cfg.model_public_path, sensitivity: cfg.sensitivity
          }).catch(function () { return noopEngine(); })
        : Promise.resolve(noopEngine());

      return enginePromise.then(function (engine) {
        var controller = new WakeController({
          engine: engine,
          micRouter: micRouter,
          canWake: function () { return wsReady && !busy && !playingTTS; },
          onState: function (state) {
            if (state === WakeState.ACTIVE) { busy = true; setVisualState("listen"); }
            else if (state === WakeState.COOLDOWN) { setVisualState("think"); }
            else { busy = false; if (!playingTTS) { setVisualState("idle"); } }
          },
          onWakeEvent: function (source) {
            playWakeCue();
            if (wsReady) { ws.send(JSON.stringify({ type: "wake", source: source })); }
          }
        });
        window._wakeController = controller;
        return controller.arm();
      });
    });
  }).catch(function () {
    /* 喚醒層失敗不影響 push→text：仍可用快速語句；push 需 controller，故提示 */
    showToast("語音喚醒暫不可用，可用下方快速語句");
  });
}

initWakeLayer();
```

- [ ] **Step 4: 移除常駐 mediaStream（改每輪取得）**

`talkybuddy/web/index.html` :627-634 的 `initMic`：保留 `navigator.mediaDevices.getUserMedia` 權限探測與 `micAvailable=true`，但**立即釋放**探測用 stream（不再常駐），避免佔用麥克風妨礙 Porcupine：

```javascript
  navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
    stream.getTracks().forEach(function (t) { t.stop(); });   /* 只探測權限，不常駐；每輪由 MicRouter 重新取得 */
    micAvailable = true;
    holdHint.textContent = "👆 點一下企鵝開始說話，說完再點一下（或喊喚醒詞）";
    holdHint.classList.remove("disabled");
  }).catch(function () {
    micDisabled();
  });
```

舊的 `startRecording`/`stopRecording`（:643-691）不再被 push 綁定呼叫，可保留為死碼或刪除；本計畫**刪除**它們以免混淆（MicRouter 已接手錄音）。刪除 :606（`/* Push-to-talk */` 註解）至 :691（`stopRecording` 結尾）之間的 `startRecording`/`stopRecording` 兩函式定義。

- [ ] **Step 5: 手動 verify（真麥克風）**

準備：於 shell 設定免費 AccessKey（Picovoice Console 申請）與內建喚醒詞，並提供 `.pv` 模型（放 `talkybuddy/web/assets/porcupine_params.pv`，來源見下）：

```bash
mkdir -p talkybuddy/web/assets
# 英文 .pv：取自 Picovoice porcupine repo lib/common/porcupine_params.pv
curl -L -o talkybuddy/web/assets/porcupine_params.pv \
  https://raw.githubusercontent.com/Picovoice/porcupine/master/lib/common/porcupine_params.pv
export PICOVOICE_ACCESS_KEY="<你的-AccessKey>"
export WAKE_KEYWORD_BUILTIN="Bumblebee"
export WAKE_KEYWORD_LABEL="Bumblebee"
cd talkybuddy && bash scripts/run.sh
```

驗證清單（瀏覽器開 http://localhost:8787）：
1. Console 無載入錯誤；`GET /api/wake-config` 回 `enabled:true`。
2. 對麥克風喊「Bumblebee」→ 聽到「我在！」、企鵝亮起（listen）→ 說一句話 → 停頓後（或第二次點）→ 收到繁體 `asr_result` + 企鵝回覆 + TTS。
3. server log 出現 `wake event: source=wakeword`。
4. tap-to-toggle push：點一下企鵝 → 開始 → 說話 → 再點一下 → 上傳並回覆；log 出現 `source=push`。
5. TTS 播放中喊喚醒詞不觸發（半雙工鎖，`canWake` 擋下）。
6. 一輪結束後約 1.5s 自動回到待命、可再次喚醒（COOLDOWN→ARMED）。
7. 未設 `PICOVOICE_ACCESS_KEY` 重跑：`enabled:false`，仍可 tap-to-toggle push（喚醒詞停用）。

- [ ] **Step 6: Commit**

```bash
git add talkybuddy/web/index.html
git commit -m "feat(a1): wire wake layer into index.html (tap-to-toggle + Porcupine + cue)"
```

---

## 完成後（本計畫範圍外）

- 自訂喚醒詞：Picovoice Console 產「Hey Penguin」Web/WASM `.ppn` → 放 `web/assets/` → 設 `WAKE_KEYWORD_PUBLIC_PATH` 即切換（程式不動）。
- 中文「小企鵝」喚醒詞、A2 全雙工 barge-in、Genio 520 上板（Porcupine ARM/C SDK，同 `.ppn`）皆為後續。
