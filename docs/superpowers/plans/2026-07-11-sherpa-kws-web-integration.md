# sherpa-onnx KWS 瀏覽器整合（最小 drop-in）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 web 原型的 Porcupine 喚醒引擎換成自架 sherpa-onnx KWS（喚醒詞「說說學伴」、門檻 0.25），免雲端 key、離線、裝置端，最小變更。

**Architecture:** 沿用 `index.html` 既有「先掛可 push 的 noopEngine controller、再事後升級喚醒引擎」的升級縫，只替換引擎。新增 `sherpa-engine.js`（同 porcupine 介面 `available/start/stop/release`，內部自持 AudioContext+ScriptProcessorNode 餵 16k PCM 進 sherpa WASM KWS）與 `sherpa-loader.js`（隔離 Emscripten glue 全域載入）。`wake-controller.js`、`mic-router.js` 完全不動。

**Tech Stack:** 前端原生 ES module + `node --test`（於 `talkybuddy/web/`）；sherpa-onnx WASM KWS 預建 bundle；FastAPI（`/api/wake-config`）+ pytest（`.venv`）；Python fetch 腳本。

## Global Constraints

- 喚醒詞 token（真人聲驗證，逐字照抄）：`sh uō sh uō x ué b àn @說說學伴`
- 門檻預設：`0.25`；boosting score 預設：`1.0`
- 模型：`sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01`，WASM 內模型路徑固定為 `./encoder-epoch-12-avg-2-chunk-16-left-64.onnx` / `./decoder-...onnx` / `./joiner-...onnx` / `./tokens.txt`（fp32，＝spike 驗證那顆）
- WASM 資產交付：fetch-on-setup 到 `talkybuddy/web/vendor/sherpa-kws/`（gitignore，不進版控）
- 半雙工循序麥克風不變；`canWake` 仍 gate `!playingTTS`（TTS 播放中不喚醒）
- 不動檔案：`wake-controller.js`、`mic-router.js` 及其 `.test.mjs`
- 不碰工作區既有的 `talkybuddy/verify_v3_tts.py` 刪除（與本任務無關）
- web JS 測試命令：`cd talkybuddy/web && node --test`
- pytest 命令：`cd talkybuddy && .venv/bin/python -m pytest -q`
- 分支：`feat/a1-sherpa-kws-web`

---

### Task 1: `/api/wake-config` 新增 sherpa 區塊

**Files:**
- Modify: `talkybuddy/server/config.py`（新增 sherpa env 變數）
- Modify: `talkybuddy/server/app.py:123-146`（`api_wake_config` 回傳新增 `sherpa`）
- Test: `talkybuddy/tests/test_wake_config.py`（新增 sherpa 契約測試 + 更新既有精確 key 集合斷言）

**Interfaces:**
- Produces: `GET /api/wake-config` 回傳 JSON 多一個 `sherpa` 物件：
  `{ "enabled": bool, "base_url": str, "keywords": str, "keywords_threshold": float, "keywords_score": float }`

- [ ] **Step 1: 寫失敗測試**

在 `talkybuddy/tests/test_wake_config.py` 末尾新增：

```python
async def test_wake_config_has_sherpa_block(monkeypatch):
    monkeypatch.setattr(config, "WAKE_SHERPA_ENABLED", True)
    async with await _client() as client:
        resp = await client.get("/api/wake-config")
    assert resp.status_code == 200
    sherpa = resp.json()["sherpa"]
    assert set(sherpa.keys()) == {
        "enabled", "base_url", "keywords", "keywords_threshold", "keywords_score",
    }
    assert sherpa["enabled"] is True
    assert sherpa["base_url"] == "/static/vendor/sherpa-kws/"
    assert sherpa["keywords"] == "sh uō sh uō x ué b àn @說說學伴"
    assert sherpa["keywords_threshold"] == 0.25
    assert isinstance(sherpa["keywords_score"], float)
```

並把既有 `test_wake_config_shape_and_enabled_true` 的精確 key 集合斷言改為包含 `sherpa`：

```python
    assert set(body.keys()) == {
        "enabled", "access_key", "keyword_builtin", "keyword_label",
        "keyword_public_path", "model_public_path", "sensitivity", "sherpa",
    }
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd talkybuddy && .venv/bin/python -m pytest tests/test_wake_config.py -q`
Expected: FAIL（`KeyError: 'sherpa'` 或 key 集合不符）

- [ ] **Step 3: 實作 config + 端點**

在 `talkybuddy/server/config.py` 的 wake 設定區（約第 51-60 行附近）後面新增：

```python
WAKE_SHERPA_ENABLED: bool = os.environ.get("WAKE_SHERPA_ENABLED", "1") not in ("", "0", "false", "False")
WAKE_SHERPA_BASE_URL: str = os.environ.get("WAKE_SHERPA_BASE_URL", "/static/vendor/sherpa-kws/")
WAKE_SHERPA_KEYWORDS: str = os.environ.get("WAKE_SHERPA_KEYWORDS", "sh uō sh uō x ué b àn @說說學伴")
WAKE_SHERPA_THRESHOLD: float = float(os.environ.get("WAKE_SHERPA_THRESHOLD", "0.25"))
WAKE_SHERPA_SCORE: float = float(os.environ.get("WAKE_SHERPA_SCORE", "1.0"))
```

在 `talkybuddy/server/app.py` 的 `api_wake_config` 回傳 dict 中，於既有欄位後加入：

```python
        "sherpa": {
            "enabled": bool(config.WAKE_SHERPA_ENABLED),
            "base_url": config.WAKE_SHERPA_BASE_URL,
            "keywords": config.WAKE_SHERPA_KEYWORDS,
            "keywords_threshold": config.WAKE_SHERPA_THRESHOLD,
            "keywords_score": config.WAKE_SHERPA_SCORE,
        },
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd talkybuddy && .venv/bin/python -m pytest tests/test_wake_config.py -q`
Expected: PASS（含新測試與更新後的既有測試）

- [ ] **Step 5: Commit**

```bash
git add talkybuddy/server/config.py talkybuddy/server/app.py talkybuddy/tests/test_wake_config.py
git commit -m "feat(a1): /api/wake-config 新增 sherpa KWS 設定區塊"
```

---

### Task 2: `sherpa-engine.js`（WakeWordEngine 的 sherpa 實作）

**Files:**
- Create: `talkybuddy/web/sherpa-engine.js`
- Test: `talkybuddy/web/sherpa-engine.test.mjs`

**Interfaces:**
- Produces: `createSherpaEngine(cfg, deps) -> { available(), start(onDetected), stop(), release() }`
  - `cfg`: `{ keywords: string, keywordsThreshold?: number, keywordsScore?: number, numThreads?: number }`
  - `deps`（測試注入，省略時用瀏覽器/全域）：`{ Module, createKws, getUserMedia, createAudioContext }`
  - `available()`: `!!Module && typeof createKws === "function"`
  - `start(onDetected)`: 未 available → no-op；否則取得麥克風、建 AudioContext+ScriptProcessor、`createKws` 只建一次、偵測到關鍵詞呼叫 `onDetected(result)`（result 形如 `{keyword: "說說學伴"}`）
  - `stop()`: 停 mic tracks 釋放麥、斷開節點；保留 kws/stream 供重用
  - `release()`: stop + `stream.free()` + `kws.free()` + `audioCtx.close()`

- [ ] **Step 1: 寫失敗測試**

Create `talkybuddy/web/sherpa-engine.test.mjs`：

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { createSherpaEngine } from "./sherpa-engine.js";

function fakeKws(results) {
  // results: 每次 getResult 依序回傳的陣列；耗盡後回 {keyword:""}
  const calls = [];
  let i = 0;
  const stream = { freed: false, _wave: [], acceptWaveform(sr, s) { calls.push(["accept", sr, s.length]); }, free() { this.freed = true; } };
  const kws = {
    freed: false,
    createStream() { calls.push("createStream"); return stream; },
    _ready: true,
    isReady() { const r = i < results.length; return r; },
    decode() { calls.push("decode"); },
    getResult() { const r = results[i] || { keyword: "" }; i += 1; return r; },
    reset() { calls.push("reset"); },
    free() { this.freed = true; },
  };
  return { kws, stream, calls };
}

function fakeAudio() {
  const tracks = [{ stopped: false, stop() { this.stopped = true; } }];
  const micStream = { getTracks() { return tracks; } };
  const node = { connected: [], disconnect() { this.disconnected = true; }, connect(x) { this.connected.push(x); } };
  const ctx = {
    sampleRate: 16000, closed: false,
    createMediaStreamSource() { return node; },
    createScriptProcessor() { return { onaudioprocess: null, connect() {}, disconnect() {} }; },
    createJavaScriptNode() { return this.createScriptProcessor(); },
    destination: {},
    close() { this.closed = true; return Promise.resolve(); },
  };
  return { micStream, tracks, ctx };
}

const baseCfg = { keywords: "sh uō sh uō x ué b àn @說說學伴", keywordsThreshold: 0.25 };

test("available()：Module 與 createKws 皆備才 true", () => {
  const { kws } = fakeKws([]);
  const a = fakeAudio();
  const eng = createSherpaEngine(baseCfg, { Module: {}, createKws: () => kws, getUserMedia: async () => a.micStream, createAudioContext: () => a.ctx });
  assert.equal(eng.available(), true);
  const eng2 = createSherpaEngine(baseCfg, { Module: null, createKws: () => kws });
  assert.equal(eng2.available(), false);
});

test("start：createKws 帶正確 keywords/threshold、只建一次", async () => {
  const { kws } = fakeKws([]);
  const a = fakeAudio();
  let seenCfg = null; let n = 0;
  const createKws = (m, c) => { n += 1; seenCfg = c; return kws; };
  const eng = createSherpaEngine(baseCfg, { Module: {}, createKws, getUserMedia: async () => a.micStream, createAudioContext: () => a.ctx });
  await eng.start(() => {});
  await eng.stop();
  await eng.start(() => {});
  assert.equal(n, 1);                                  // 只建一次
  assert.equal(seenCfg.keywords, baseCfg.keywords);
  assert.equal(seenCfg.keywordsThreshold, 0.25);
  assert.ok(seenCfg.modelConfig && seenCfg.modelConfig.transducer.encoder.includes("epoch-12-avg-2"));
});

test("偵測：getResult 有 keyword → reset 並呼叫 onDetected", async () => {
  const { kws } = fakeKws([{ keyword: "說說學伴" }]);
  const a = fakeAudio();
  const detected = [];
  // 用可控 processor 手動觸發一次 onaudioprocess
  let proc = null;
  a.ctx.createScriptProcessor = () => { proc = { onaudioprocess: null, connect() {}, disconnect() {} }; return proc; };
  const eng = createSherpaEngine(baseCfg, { Module: {}, createKws: () => kws, getUserMedia: async () => a.micStream, createAudioContext: () => a.ctx });
  await eng.start((r) => detected.push(r));
  proc.onaudioprocess({ inputBuffer: { getChannelData: () => new Float32Array(160) } });
  assert.deepEqual(detected, [{ keyword: "說說學伴" }]);
});

test("stop：停 mic tracks 釋放麥", async () => {
  const { kws } = fakeKws([]);
  const a = fakeAudio();
  const eng = createSherpaEngine(baseCfg, { Module: {}, createKws: () => kws, getUserMedia: async () => a.micStream, createAudioContext: () => a.ctx });
  await eng.start(() => {});
  await eng.stop();
  assert.equal(a.tracks[0].stopped, true);
});

test("未 available：start 為 no-op（不取麥、不建 kws）", async () => {
  let gum = 0;
  const eng = createSherpaEngine(baseCfg, { Module: null, createKws: null, getUserMedia: async () => { gum += 1; return {}; } });
  await eng.start(() => {});
  assert.equal(gum, 0);
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd talkybuddy/web && node --test sherpa-engine.test.mjs`
Expected: FAIL（`Cannot find module './sherpa-engine.js'`）

- [ ] **Step 3: 實作 engine**

Create `talkybuddy/web/sherpa-engine.js`：

```javascript
// WakeWordEngine 的 sherpa-onnx KWS 實作，介面同 porcupine-engine.js：
// available()/start(onDetected)/stop()/release()。內部自持 AudioContext + ScriptProcessorNode
// 把 16k PCM 餵進 sherpa WASM KWS。deps 可注入供 node --test（不碰真實瀏覽器 API）。

// WASM .data 內建的模型檔路徑（fp32 epoch-12-avg-2，＝spike 驗證那顆）。
function buildKwsConfig(cfg) {
  return {
    featConfig: { samplingRate: 16000, featureDim: 80 },
    modelConfig: {
      transducer: {
        encoder: "./encoder-epoch-12-avg-2-chunk-16-left-64.onnx",
        decoder: "./decoder-epoch-12-avg-2-chunk-16-left-64.onnx",
        joiner: "./joiner-epoch-12-avg-2-chunk-16-left-64.onnx",
      },
      tokens: "./tokens.txt",
      provider: "cpu", modelType: "", numThreads: cfg.numThreads || 1,
      debug: 0, modelingUnit: "cjkchar", bpeVocab: "",
    },
    maxActivePaths: 4, numTrailingBlanks: 1,
    keywordsScore: cfg.keywordsScore != null ? cfg.keywordsScore : 1.0,
    keywordsThreshold: cfg.keywordsThreshold != null ? cfg.keywordsThreshold : 0.25,
    keywords: cfg.keywords,
  };
}

// 平均法降採樣到 16k（沿用 sherpa-onnx wasm 範例 downsampleBuffer 作法）。
function downsample(buffer, inRate, outRate) {
  if (outRate === inRate) { return buffer; }
  var ratio = inRate / outRate;
  var newLen = Math.round(buffer.length / ratio);
  var out = new Float32Array(newLen);
  var oi = 0, bi = 0;
  while (oi < newLen) {
    var next = Math.round((oi + 1) * ratio);
    var acc = 0, cnt = 0;
    for (var i = bi; i < next && i < buffer.length; i++) { acc += buffer[i]; cnt++; }
    out[oi] = cnt ? acc / cnt : 0; oi++; bi = next;
  }
  return out;
}

export function createSherpaEngine(cfg, deps) {
  deps = deps || {};
  var hasWin = typeof window !== "undefined";
  var Module = deps.Module != null ? deps.Module : (hasWin ? window.Module : null);
  var createKws = deps.createKws != null ? deps.createKws : (hasWin ? window.createKws : null);
  var getUserMedia = deps.getUserMedia || function () { return navigator.mediaDevices.getUserMedia({ audio: true }); };
  var createAudioContext = deps.createAudioContext || function () {
    var AC = window.AudioContext || window.webkitAudioContext;
    return new AC({ sampleRate: 16000 });
  };
  var kwsConfig = buildKwsConfig(cfg);

  var kws = null, stream = null, audioCtx = null, micStream = null, source = null, processor = null, started = false;

  function pump(onDetected) {
    while (kws.isReady(stream)) {
      kws.decode(stream);
      var r = kws.getResult(stream);
      if (r && r.keyword && r.keyword.length > 0) { kws.reset(stream); onDetected(r); }
    }
  }

  return {
    available: function () { return !!Module && typeof createKws === "function"; },

    start: async function (onDetected) {
      if (!Module || typeof createKws !== "function") { return; }   // no-op（未 available）
      if (started) { return; }
      micStream = await getUserMedia();
      audioCtx = createAudioContext();
      if (!kws) { kws = createKws(Module, kwsConfig); }             // 只建一次
      if (!stream) { stream = kws.createStream(); }
      source = audioCtx.createMediaStreamSource(micStream);
      processor = (audioCtx.createScriptProcessor
        ? audioCtx.createScriptProcessor(4096, 1, 1)
        : audioCtx.createJavaScriptNode(4096, 1, 1));
      var sr = audioCtx.sampleRate;
      processor.onaudioprocess = function (e) {
        var s = e.inputBuffer.getChannelData(0);
        if (sr !== 16000) { s = downsample(s, sr, 16000); }
        stream.acceptWaveform(16000, s);
        pump(onDetected);
      };
      source.connect(processor);
      processor.connect(audioCtx.destination);
      started = true;
    },

    stop: async function () {
      if (!started) { return; }
      if (processor) { try { processor.disconnect(); } catch (e) {} processor.onaudioprocess = null; }
      if (source) { try { source.disconnect(); } catch (e) {} }
      if (micStream) { micStream.getTracks().forEach(function (t) { t.stop(); }); }   // 釋放麥（I4）
      micStream = null; source = null; processor = null; started = false;
    },

    release: async function () {
      await this.stop();
      if (stream && stream.free) { stream.free(); }
      if (kws && kws.free) { kws.free(); }
      if (audioCtx && audioCtx.close) { try { await audioCtx.close(); } catch (e) {} }
      stream = null; kws = null; audioCtx = null;
    },
  };
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd talkybuddy/web && node --test sherpa-engine.test.mjs`
Expected: PASS（5 個測試全過）

- [ ] **Step 5: Commit**

```bash
git add talkybuddy/web/sherpa-engine.js talkybuddy/web/sherpa-engine.test.mjs
git commit -m "feat(a1): sherpa-engine.js（WakeWordEngine 的 sherpa WASM KWS 實作 + node --test）"
```

---

### Task 3: `sherpa-loader.js`（隔離 Emscripten glue 全域載入）

**Files:**
- Create: `talkybuddy/web/sherpa-loader.js`
- Test: `talkybuddy/web/sherpa-loader.test.mjs`

**Interfaces:**
- Produces: `loadSherpaModule(baseUrl, deps) -> Promise<Module>`
  - 依序注入 `baseUrl + "sherpa-onnx-kws.js"` 與 `baseUrl + "sherpa-onnx-wasm-kws-main.js"`
  - 於注入前設 `scope.Module = { locateFile, onRuntimeInitialized }`；runtime 初始化後 resolve(`scope.Module`)
  - `deps`（測試注入）：`{ scope, appendScript }`；`appendScript(src) -> Promise<void>`

- [ ] **Step 1: 寫失敗測試**

Create `talkybuddy/web/sherpa-loader.test.mjs`：

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadSherpaModule } from "./sherpa-loader.js";

test("依序注入兩支 glue、locateFile 前綴 baseUrl、runtime init 後 resolve Module", async () => {
  const scope = {};
  const srcs = [];
  const appendScript = (src) => { srcs.push(src); return Promise.resolve(); };
  const p = loadSherpaModule("/static/vendor/sherpa-kws/", { scope, appendScript });
  // 注入鏈跑完後，模擬 Emscripten runtime 就緒
  await Promise.resolve();
  assert.deepEqual(srcs, [
    "/static/vendor/sherpa-kws/sherpa-onnx-kws.js",
    "/static/vendor/sherpa-kws/sherpa-onnx-wasm-kws-main.js",
  ]);
  assert.equal(scope.Module.locateFile("foo.wasm"), "/static/vendor/sherpa-kws/foo.wasm");
  scope.Module.onRuntimeInitialized();
  const mod = await p;
  assert.equal(mod, scope.Module);
});

test("appendScript 失敗 → promise reject", async () => {
  const scope = {};
  const appendScript = () => Promise.reject(new Error("load fail"));
  await assert.rejects(() => loadSherpaModule("/x/", { scope, appendScript }), /load fail/);
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd talkybuddy/web && node --test sherpa-loader.test.mjs`
Expected: FAIL（`Cannot find module './sherpa-loader.js'`）

- [ ] **Step 3: 實作 loader**

Create `talkybuddy/web/sherpa-loader.js`：

```javascript
// 隔離 sherpa-onnx WASM 的 Emscripten glue 載入（非 ESM、會操作全域 Module）。
// 讓 sherpa-engine.js 保持可 node --test（測試注入 fake Module，不經此 loader）。

export function loadSherpaModule(baseUrl, deps) {
  deps = deps || {};
  var scope = deps.scope || (typeof window !== "undefined" ? window : {});
  var appendScript = deps.appendScript || function (src) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = src;
      s.onload = function () { resolve(); };
      s.onerror = function () { reject(new Error("script load failed: " + src)); };
      document.head.appendChild(s);
    });
  };
  return new Promise(function (resolve, reject) {
    scope.Module = {
      locateFile: function (path) { return baseUrl + path; },
      onRuntimeInitialized: function () { resolve(scope.Module); },
    };
    appendScript(baseUrl + "sherpa-onnx-kws.js")
      .then(function () { return appendScript(baseUrl + "sherpa-onnx-wasm-kws-main.js"); })
      .catch(reject);
  });
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd talkybuddy/web && node --test sherpa-loader.test.mjs`
Expected: PASS（2 個測試全過）

- [ ] **Step 5: Commit**

```bash
git add talkybuddy/web/sherpa-loader.js talkybuddy/web/sherpa-loader.test.mjs
git commit -m "feat(a1): sherpa-loader.js（隔離 WASM glue 全域載入，可測）"
```

---

### Task 4: `index.html` 的 `initWakeLayer()` 接上 sherpa（取代 Porcupine 升級分支）

**Files:**
- Modify: `talkybuddy/web/index.html:714-801`（`initWakeLayer` 內的 import 清單與「事後升級」分支）

**Interfaces:**
- Consumes: Task 1 的 `/api/wake-config` 的 `sherpa` 區塊；Task 2 `createSherpaEngine`；Task 3 `loadSherpaModule`
- 不動：`buildController` / `micRouter` / `noopEngine` / `onWakeEvent` / `playWakeCue` / push 路徑

- [ ] **Step 1: 改 import 清單**

在 `initWakeLayer()` 開頭的 `Promise.all([...])`，把第三個 import 由 porcupine 換成 sherpa 兩支模組：

```javascript
  Promise.all([
    import("/static/wake-controller.js"),
    import("/static/mic-router.js"),
    import("/static/sherpa-loader.js"),
    import("/static/sherpa-engine.js")
  ]).then(function (mods) {
    var WakeController = mods[0].WakeController;
    var WakeState = mods[0].WakeState;
    var MicRouter = mods[1].MicRouter;
    var loadSherpaModule = mods[2].loadSherpaModule;
    var createSherpaEngine = mods[3].createSherpaEngine;
```

（`micRouter`、`noopEngine`、`buildController`、`pushController` 那幾段維持原樣不動。）

- [ ] **Step 2: 換掉「事後升級」分支**

把原本 `fetch("/api/wake-config")...createPorcupineEngine(...)` 那整段（約 782-796 行）替換為：

```javascript
    /* 2) 事後嘗試升級為 sherpa KWS 引擎；此鏈失敗也不動 window._wakeController（push 仍可用）。 */
    fetch("/api/wake-config").then(function (r) { return r.json(); }).then(function (cfg) {
      var sh = cfg && cfg.sherpa;
      if (!sh || !sh.enabled) { return; }
      return loadSherpaModule(sh.base_url).then(function (Module) {
        var engine = createSherpaEngine({
          keywords: sh.keywords,
          keywordsThreshold: sh.keywords_threshold,
          keywordsScore: sh.keywords_score
        }, { Module: Module });
        var wakeController = buildController(engine);
        window._wakeController = wakeController;
        return wakeController.arm();
      });
    }).catch(function () {
      /* wake-config 讀取或 sherpa WASM 建置失敗：維持既有 noopEngine controller，push 仍可用。 */
      showToast("語音喚醒詞暫不可用，仍可點企鵝或用下方快速語句");
    });
```

- [ ] **Step 3: 確認既有 web 測試仍全綠（未動的模組沒被破壞）**

Run: `cd talkybuddy/web && node --test`
Expected: PASS（wake-controller / mic-router / porcupine-engine / sherpa-engine / sherpa-loader 全過；總數 = 原 18 + Task2 5 + Task3 2 = 25）

- [ ] **Step 4: 靜態語法檢查抽出的模組**

Run: `cd talkybuddy/web && node --check sherpa-engine.js && node --check sherpa-loader.js && node --check wake-controller.js && node --check mic-router.js && echo OK`
Expected: `OK`（無語法錯）

- [ ] **Step 5: Commit**

```bash
git add talkybuddy/web/index.html
git commit -m "feat(a1): initWakeLayer 改用 sherpa KWS（取代 Porcupine 升級分支，degrade-safe 不變）"
```

---

### Task 5: `fetch_sherpa_wasm.py` 取得 WASM 資產 + gitignore + setup 文件

**Files:**
- Create: `talkybuddy/scripts/fetch_sherpa_wasm.py`
- Modify: `talkybuddy/.gitignore`（若無則 Create）
- Create: `talkybuddy/web/vendor/README.md`（setup 說明）

**Interfaces:**
- Produces: 跑腳本後 `talkybuddy/web/vendor/sherpa-kws/` 內含 `sherpa-onnx-kws.js`、`sherpa-onnx-wasm-kws-main.js`、`sherpa-onnx-wasm-kws-main.wasm`、`sherpa-onnx-wasm-kws-main.data`（gitignore，不進版控）

- [ ] **Step 1: 寫 gitignore（讓 vendor 不進版控）**

在 `talkybuddy/.gitignore` 追加（檔案不存在則新建）：

```
# sherpa-onnx WASM 資產（fetch-on-setup，勿進版控）
web/vendor/sherpa-kws/
```

- [ ] **Step 2: 寫 fetch 腳本**

Create `talkybuddy/scripts/fetch_sherpa_wasm.py`：

```python
#!/usr/bin/env python3
"""取得 sherpa-onnx WASM KWS 預建資產放進 web/vendor/sherpa-kws/。
用法：  python scripts/fetch_sherpa_wasm.py
需在有 outbound 網路的機器上跑一次；資產不進版控（見 .gitignore）。
"""
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.normpath(os.path.join(HERE, "..", "web", "vendor", "sherpa-kws"))

# k2-fsa 官方 WASM KWS demo 產物（含 wenetspeech-3.3M 模型的 .data）。
# 若下列 URL 失效，改用官方 HF Space「k2-fsa/web-assembly-kws-sherpa-onnx」
# 的 Files 頁確認同名檔的 resolve 連結後替換 BASE。
BASE = "https://huggingface.co/spaces/k2-fsa/web-assembly-kws-sherpa-onnx/resolve/main/"
FILES = [
    "sherpa-onnx-kws.js",
    "sherpa-onnx-wasm-kws-main.js",
    "sherpa-onnx-wasm-kws-main.wasm",
    "sherpa-onnx-wasm-kws-main.data",
]


def main():
    os.makedirs(DEST, exist_ok=True)
    for name in FILES:
        url = BASE + name
        out = os.path.join(DEST, name)
        print(f"下載 {name} ...")
        try:
            urllib.request.urlretrieve(url, out)
        except Exception as e:
            sys.exit(f"下載失敗 {url}\n  {e}\n"
                     f"請確認網路，或到 k2-fsa HF Space Files 頁核對檔名/連結後改 BASE。")
    print(f"\n✅ 完成，資產在 {DEST}")
    print("   啟動 server 後開學生端頁，講「說說學伴」測試喚醒。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 寫 setup 文件**

Create `talkybuddy/web/vendor/README.md`：

```markdown
# web/vendor — 第三方前端資產（不進版控）

## sherpa-kws（喚醒詞 WASM）

瀏覽器喚醒詞引擎（sherpa-onnx KWS，喚醒詞「說說學伴」）需要預建 WASM 資產，
體積約 10–15MB，依 fetch-on-setup 交付、不進 git。

### 安裝（在有網路的機器跑一次）

```bash
cd talkybuddy
python scripts/fetch_sherpa_wasm.py
```

完成後 `web/vendor/sherpa-kws/` 應有：
`sherpa-onnx-kws.js`、`sherpa-onnx-wasm-kws-main.js`、`*.wasm`、`*.data`。

### 沒安裝會怎樣？

前端 degrade-safe：喚醒詞不可用，但點企鵝（push-to-talk）與快速語句照常運作。
```

- [ ] **Step 4: 手動驗證腳本結構（不需真下載）**

Run: `cd talkybuddy && .venv/bin/python -c "import ast; ast.parse(open('scripts/fetch_sherpa_wasm.py').read()); print('parse OK')"`
Expected: `parse OK`

Run: `cd talkybuddy && git check-ignore web/vendor/sherpa-kws/x.wasm && echo IGNORED`
Expected: `IGNORED`（gitignore 生效）

- [ ] **Step 5: Commit**

```bash
git add talkybuddy/scripts/fetch_sherpa_wasm.py talkybuddy/.gitignore talkybuddy/web/vendor/README.md
git commit -m "feat(a1): fetch_sherpa_wasm.py 取得 WASM 資產 + gitignore + setup 文件"
```

---

## 手動驗收（用戶筆電，有麥克風；不在此沙箱）

1. `cd talkybuddy && python scripts/fetch_sherpa_wasm.py` 取得 vendor 資產。
2. 啟動 server，開學生端頁面。
3. 講「說說學伴」→ 企鵝進 listen（ACTIVE）、播「我在！」回饋音、亮橘燈。
4. 正常聊天／念文章 → 不誤觸發（門檻 0.25）。
5. 點企鵝（push-to-talk）→ 仍正常錄音送出。
6. 把 `web/vendor/sherpa-kws/` 移走重試 → degrade：push 仍可用、顯示「語音喚醒詞暫不可用」提示。
7. 誤觸發率過高／太鈍 → 調 `WAKE_SHERPA_THRESHOLD` env（0.2~0.35）不必改前端。

## Self-Review 記錄

- **Spec 覆蓋**：§4.1 engine→Task2；§4.2 loader→Task3；§4.3 engine 測試→Task2；§4.4 index.html→Task4；§4.5 config→Task1；§4.6 fetch/gitignore/doc→Task5。§6 degrade-safe 由 Task4 catch 分支 + 手動驗收#6 覆蓋。§7 測試由各 Task Step + 手動驗收覆蓋。無缺口。
- **Placeholder 掃描**：唯一外部未知＝WASM 下載 URL（Task5 BASE），已以「確認/替換」步驟與 fallback 指示明確化，非邏輯 placeholder。
- **型別一致**：`createSherpaEngine(cfg, deps)`、`loadSherpaModule(baseUrl, deps)`、`sherpa` 區塊欄位（`base_url`/`keywords`/`keywords_threshold`/`keywords_score`）於 Task1↔Task4 一致；engine `available/start/stop/release` 與 porcupine 契約一致。
