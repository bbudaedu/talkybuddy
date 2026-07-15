# 喚醒詞→hands-free 全語音迴圈 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 hands-free 即時對話全語音免手——喊「說說學伴」開始、說「掰掰」結束，中間 barge-in 已具備。

**Architecture:** 新增純邏輯協調器 `web/live-wake.js`（IDLE↔CONVERSING 兩態、麥克風分時互斥、依賴注入可 node --test）；sherpa KWS 喚醒引擎由 master **vendor drop-in**；結束詞用前端 USER 逐字稿純函式比對；`enterLiveMode` 改由協調器驅動，點企鵝與喚醒等效。degrade-safe：sherpa 掛掉退回點企鵝 hands-free（不退半雙工）。

**Tech Stack:** vanilla JS ESM（web/）、node --test、FastAPI（server/）、pytest、sherpa-onnx KWS WASM。

**Spec:** `docs/superpowers/specs/2026-07-14-nova-sonic-wake-handsfree-design.md`

## Global Constraints

- 分支 `feat/cloud-llm-bedrock`；工作目錄 = 本 worktree 的 `talkybuddy/`（git repo root 在上一層，檔案 commit 路徑帶 `talkybuddy/` 前綴）。
- 只動 live_s2s 模式；半雙工路徑、`WakeController`、Porcupine `initWakeLayer`、Nova/barge-in 邏輯**零改動**。
- 喚醒詞「說說學伴」token＝`sh uō sh uō x ué b àn @說說學伴`；門檻 0.25、score 1.0（照 master，勿改）。
- sherpa engine 介面固定：`loadSherpaModule(base_url)→Promise<Module>`、`createSherpaEngine(cfg,deps)→{available(),start(onDetected),stop(),release()}`。
- node 測試指定檔跑（例：`node --test web/live-wake.test.mjs`），勿 `node --test web/`（會掃到需瀏覽器 API 的檔）。
- 語音提示 `playLiveCue(text,onDone)` 已存在於 index.html（onDone 於播完/出錯/2.5s 保底/無 API 觸發一次）。
- 現有工作樹已有**未 commit 的語音提示改動**（index.html：`playLiveCue` + 點企鵝 cue 呼叫 + `liveCueBusy`），真機驗過。Task 1 先獨立 commit 它。

---

### Task 1: Commit 既有語音提示改動

**Files:**
- Commit: `talkybuddy/web/index.html`（現有未 commit 改動，已真機驗證）

**Interfaces:**
- Produces: `playLiveCue(text, onDone)` 全域函式（Task 5 的 onCue 會包裝它）。

- [ ] **Step 1: 確認未 commit 內容為語音提示**

Run: `git -C .. diff --stat -- talkybuddy/web/index.html`
Expected: 顯示 index.html 有改動（`playLiveCue` 相關）。

- [ ] **Step 2: 語法檢查**

Run:
```bash
python3 -c "import re;s=open('web/index.html').read();open('/tmp/idx.js','w').write('\n'.join(re.findall(r'<script>(.*?)</script>',s,re.S)))" && node --check /tmp/idx.js && echo OK
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git -C .. add talkybuddy/web/index.html
git -C .. commit -m "feat(live): hands-free 開麥/休息語音提示（playLiveCue 播完再開麥）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Vendor drop-in — sherpa 引擎檔 + config + wake-config

**Files:**
- Create（drop-in from master）: `talkybuddy/web/sherpa-engine.js`、`talkybuddy/web/sherpa-loader.js`、`talkybuddy/web/sherpa-engine.test.mjs`、`talkybuddy/web/sherpa-loader.test.mjs`、`talkybuddy/scripts/fetch_sherpa_wasm.py`、`talkybuddy/tests/test_fetch_sherpa_wasm.py`
- Modify: `talkybuddy/server/config.py`（加 WAKE_SHERPA_*）
- Modify: `talkybuddy/server/app.py:145-155`（wake-config 加 sherpa 區塊）

**Interfaces:**
- Produces: `/api/wake-config` 回傳新增 `sherpa: {enabled, base_url, keywords, keywords_threshold, keywords_score}`；`createSherpaEngine`/`loadSherpaModule`（Task 5 使用）。

- [ ] **Step 1: 從 master 取 drop-in 檔**

```bash
git -C .. checkout master -- \
  talkybuddy/web/sherpa-engine.js talkybuddy/web/sherpa-loader.js \
  talkybuddy/web/sherpa-engine.test.mjs talkybuddy/web/sherpa-loader.test.mjs \
  talkybuddy/scripts/fetch_sherpa_wasm.py talkybuddy/tests/test_fetch_sherpa_wasm.py
```

- [ ] **Step 2: config.py 加 WAKE_SHERPA_*（接在 `WAKE_SENSITIVITY` 那行之後）**

```python
# --- sherpa-onnx KWS（中文喚醒詞「說說學伴」，取代 Porcupine；裝置端、免 key）---
WAKE_SHERPA_ENABLED: bool = os.environ.get("WAKE_SHERPA_ENABLED", "1") not in ("", "0", "false", "False")
WAKE_SHERPA_BASE_URL: str = os.environ.get("WAKE_SHERPA_BASE_URL", "/static/vendor/sherpa-kws/")
WAKE_SHERPA_KEYWORDS: str = os.environ.get("WAKE_SHERPA_KEYWORDS", "sh uō sh uō x ué b àn @說說學伴")
WAKE_SHERPA_THRESHOLD: float = float(os.environ.get("WAKE_SHERPA_THRESHOLD", "0.25"))
WAKE_SHERPA_SCORE: float = float(os.environ.get("WAKE_SHERPA_SCORE", "1.0"))
```

- [ ] **Step 3: app.py wake-config 加 sherpa 區塊**

把 `api_wake_config` 的 return dict 改為（在 `"sensitivity"` 行後加 `"sherpa"` key）：
```python
    return {
        "enabled": bool(config.PICOVOICE_ACCESS_KEY),
        "access_key": config.PICOVOICE_ACCESS_KEY,
        "keyword_builtin": config.WAKE_KEYWORD_BUILTIN,
        "keyword_label": config.WAKE_KEYWORD_LABEL,
        "keyword_public_path": config.WAKE_KEYWORD_PUBLIC_PATH,
        "model_public_path": config.WAKE_MODEL_PUBLIC_PATH,
        "sensitivity": config.WAKE_SENSITIVITY,
        "sherpa": {
            "enabled": bool(config.WAKE_SHERPA_ENABLED),
            "base_url": config.WAKE_SHERPA_BASE_URL,
            "keywords": config.WAKE_SHERPA_KEYWORDS,
            "keywords_threshold": config.WAKE_SHERPA_THRESHOLD,
            "keywords_score": config.WAKE_SHERPA_SCORE,
        },
    }
```

- [ ] **Step 4: 跑 drop-in 的 node 測試**

Run: `node --test web/sherpa-engine.test.mjs web/sherpa-loader.test.mjs`
Expected: PASS（全綠）。

- [ ] **Step 5: 跑 pytest（fetch + wake-config）**

Run: `.venv/bin/python -m pytest tests/test_fetch_sherpa_wasm.py -q`
Expected: PASS。若 `tests/` 有 wake-config 測試，一併跑：`.venv/bin/python -m pytest tests/ -k wake_config -q` Expected: PASS 或無此測試（無則 Step 6 補）。

- [ ] **Step 6: 補一條 wake-config sherpa 斷言（若原本沒有）**

在對應的 app 測試檔（如 `tests/test_app.py`）加：
```python
def test_wake_config_has_sherpa_block(client):
    r = client.get("/api/wake-config")
    assert r.status_code == 200
    sh = r.json()["sherpa"]
    assert set(sh) >= {"enabled", "base_url", "keywords", "keywords_threshold", "keywords_score"}
    assert sh["keywords_threshold"] == 0.25
```
Run: `.venv/bin/python -m pytest tests/ -k wake_config -q`
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git -C .. add talkybuddy/web/sherpa-engine.js talkybuddy/web/sherpa-loader.js \
  talkybuddy/web/sherpa-engine.test.mjs talkybuddy/web/sherpa-loader.test.mjs \
  talkybuddy/scripts/fetch_sherpa_wasm.py talkybuddy/tests/test_fetch_sherpa_wasm.py \
  talkybuddy/server/config.py talkybuddy/server/app.py talkybuddy/tests/
git -C .. commit -m "feat(wake): vendor drop-in sherpa KWS 引擎 + wake-config sherpa 區塊

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `matchFarewell` 結束詞純函式

**Files:**
- Modify: `talkybuddy/web/live-client.js`（加 export `matchFarewell`）
- Test: `talkybuddy/web/live-client.test.mjs`（加測）

**Interfaces:**
- Produces: `matchFarewell(text: string) → boolean`（Task 4 協調器注入使用）。

- [ ] **Step 1: 寫失敗測試（加到 live-client.test.mjs）**

```js
import { matchFarewell } from "./live-client.js";

test("matchFarewell 命中常見結束詞", () => {
  for (const s of ["掰掰", "掰掰！", "拜拜", "再見", "我想結束了", "Bye bye", "掰啦"]) {
    assert.equal(matchFarewell(s), true, s);
  }
});
test("matchFarewell 不誤判一般語句與空值", () => {
  for (const s of ["你好嗎", "今天天氣真好", "", null, undefined]) {
    assert.equal(matchFarewell(s), false, String(s));
  }
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `node --test web/live-client.test.mjs`
Expected: FAIL（`matchFarewell` is not exported / not a function）。

- [ ] **Step 3: 實作 matchFarewell（加到 live-client.js 純函式區）**

```js
// 結束詞比對：正規化（轉小寫、去空白標點）後命中任一結束詞回 true。
// 僅供協調器對 USER role 逐字稿呼叫（避免誤判 AI 回話）。
export function matchFarewell(text) {
  if (!text) return false;
  const t = String(text).toLowerCase().replace(/[\s,.!?，。！？、~～]/g, "");
  return /掰掰|拜拜|再見|再见|結束|结束|掰啦|byebye|bye/.test(t);
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `node --test web/live-client.test.mjs`
Expected: PASS（原 6 + 新 2）。

- [ ] **Step 5: Commit**

```bash
git -C .. add talkybuddy/web/live-client.js talkybuddy/web/live-client.test.mjs
git -C .. commit -m "feat(live): matchFarewell 結束詞純函式（掰掰/再見/結束/bye）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `LiveWakeCoordinator` 協調器（`web/live-wake.js`）

**Files:**
- Create: `talkybuddy/web/live-wake.js`
- Test: `talkybuddy/web/live-wake.test.mjs`

**Interfaces:**
- Consumes: `matchFarewell`（Task 3）；注入的 `engine`（sherpa `{available,start,stop,release}`）、`startSession()→Promise<LiveSession>`、`onCue(kind)→Promise|void`、`onStateChange(state)`。
- Produces: `LiveWakeState = {IDLE:"idle", CONVERSING:"conversing"}`；`class LiveWakeCoordinator` 具 `arm()`、`wake(source)`、`end(source)`、`onTranscript(role,text)`、`get state`（Task 5 使用）。

- [ ] **Step 1: 寫失敗測試（`web/live-wake.test.mjs`）**

```js
import { test } from "node:test";
import assert from "node:assert";
import { LiveWakeCoordinator, LiveWakeState } from "./live-wake.js";

function harness(opts = {}) {
  const log = [];
  const engine = {
    _avail: opts.engineAvail !== false,
    available() { return this._avail; },
    async start(cb) { log.push("engine.start"); engine._cb = cb; },
    async stop() { log.push("engine.stop"); },
    async release() { log.push("engine.release"); },
  };
  const session = {
    startConversation() { log.push("session.startConversation"); },
    stopConversation() { log.push("session.stopConversation"); },
    async close() { log.push("session.close"); },
  };
  const c = new LiveWakeCoordinator({
    engine: opts.noEngine ? null : engine,
    startSession: async () => { log.push("startSession"); return session; },
    onCue: async (k) => { log.push("cue:" + k); },
    onStateChange: (s) => { log.push("state:" + s); },
    matchFarewell: (t) => t === "掰掰",
  });
  return { c, log, engine, session };
}

test("arm 進 IDLE 並啟動 KWS", async () => {
  const { c, log } = harness();
  await c.arm();
  assert.equal(c.state, LiveWakeState.IDLE);
  assert.ok(log.includes("engine.start"));
});

test("wake：先放 KWS 麥→開 session→播 cue→開麥", async () => {
  const { c, log } = harness();
  await c.arm();
  await c.wake("wakeword");
  assert.equal(c.state, LiveWakeState.CONVERSING);
  const order = log.filter(x => ["engine.stop","startSession","cue:start","session.startConversation"].includes(x));
  assert.deepEqual(order, ["engine.stop","startSession","cue:start","session.startConversation"]);
});

test("farewell 只認 USER role", async () => {
  const { c, log } = harness();
  await c.arm(); await c.wake("push");
  c.onTranscript("AI", "掰掰");        // 不觸發
  assert.equal(c.state, LiveWakeState.CONVERSING);
  c.onTranscript("USER", "掰掰");      // 觸發 end
  await new Promise(r => setTimeout(r, 0));
  assert.equal(c.state, LiveWakeState.IDLE);
  const order = log.filter(x => ["session.stopConversation","cue:stop","session.close"].includes(x));
  assert.deepEqual(order, ["session.stopConversation","cue:stop","session.close"]);
});

test("end 後重新 arm（可再喚醒）", async () => {
  const { c, log } = harness();
  await c.arm(); await c.wake("push"); await c.end("push");
  assert.equal(c.state, LiveWakeState.IDLE);
  assert.equal(log.filter(x => x === "engine.start").length, 2);  // arm 兩次
});

test("重入保護：CONVERSING 時再 wake 無效", async () => {
  const { c } = harness();
  await c.arm(); await c.wake("push");
  await c.wake("push");
  assert.equal(c.state, LiveWakeState.CONVERSING);
});

test("degrade：engine=null 時 arm 不崩、push 仍可 wake", async () => {
  const { c, log } = harness({ noEngine: true });
  await c.arm();
  assert.equal(c.state, LiveWakeState.IDLE);
  await c.wake("push");
  assert.equal(c.state, LiveWakeState.CONVERSING);
  assert.ok(log.includes("startSession"));
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `node --test web/live-wake.test.mjs`
Expected: FAIL（`live-wake.js` 不存在）。

- [ ] **Step 3: 實作 `web/live-wake.js`**

```js
// LiveWakeCoordinator — 喚醒詞→hands-free 全語音迴圈協調器。
// 純邏輯、零瀏覽器依賴（依賴由建構子注入），可 node --test。
// 狀態：IDLE（KWS 聽喚醒）↔ CONVERSING（LiveSession 常開對話）。麥克風分時互斥：
// IDLE 只 KWS 持麥、CONVERSING 只 LiveSession 持麥，交棒點各自 stop/release/close。

export const LiveWakeState = { IDLE: "idle", CONVERSING: "conversing" };

export class LiveWakeCoordinator {
  constructor(opts) {
    this.engine = opts.engine || null;            // sherpa KWS engine 或 null（無喚醒、僅點企鵝）
    this.startSession = opts.startSession;        // () => Promise<LiveSession>（已 start()）
    this.onCue = opts.onCue || function () { return Promise.resolve(); };
    this.onStateChange = opts.onStateChange || function () {};
    this.matchFarewell = opts.matchFarewell || function () { return false; };
    this._state = LiveWakeState.IDLE;
    this.session = null;
    this._busy = false;                           // 轉移進行中鎖（防 wake/end 重入競態）
    this._onDetected = () => { this.wake("wakeword"); };
  }

  get state() { return this._state; }

  async arm() {
    this._state = LiveWakeState.IDLE;
    this.session = null;
    this.onStateChange("idle");
    if (this.engine && this.engine.available()) {
      try { await this.engine.start(this._onDetected); }
      catch (e) { /* KWS 起不來：維持 IDLE，點企鵝 wake('push') 仍可用 */ }
    }
  }

  async wake(source) {
    if (this._state !== LiveWakeState.IDLE || this._busy) { return; }
    this._busy = true;
    try {
      if (this.engine && this.engine.available()) {
        try { await this.engine.stop(); } catch (e) {}
        try { if (this.engine.release) { await this.engine.release(); } } catch (e) {}
      }
      this.session = await this.startSession();
      this._state = LiveWakeState.CONVERSING;
      await this.onCue("start");                  // 播「我在，請說」（播完才開麥）
      if (this.session) { this.session.startConversation(); }
    } finally { this._busy = false; }
  }

  onTranscript(role, text) {
    if (this._state === LiveWakeState.CONVERSING && role === "USER" && this.matchFarewell(text)) {
      this.end("farewell");
    }
  }

  async end(source) {
    if (this._state !== LiveWakeState.CONVERSING || this._busy) { return; }
    this._busy = true;
    try {
      try { if (this.session) { this.session.stopConversation(); } } catch (e) {}
      await this.onCue("stop");                   // 播「先休息一下」
      try { if (this.session) { await this.session.close(); } } catch (e) {}
      this.session = null;
    } finally { this._busy = false; }
    await this.arm();                             // 回 IDLE、重啟 KWS
  }
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `node --test web/live-wake.test.mjs`
Expected: PASS（6 綠）。

- [ ] **Step 5: Commit**

```bash
git -C .. add talkybuddy/web/live-wake.js talkybuddy/web/live-wake.test.mjs
git -C .. commit -m "feat(wake): LiveWakeCoordinator 兩態迴圈（IDLE↔CONVERSING，麥分時互斥）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: `index.html` 接線改寫（enterLiveMode + 點企鵝）

**Files:**
- Modify: `talkybuddy/web/index.html:836-865`（`enterLiveMode`）、`:648-660`（點企鵝 liveMode 分支）

**Interfaces:**
- Consumes: `loadSherpaModule`、`createSherpaEngine`（Task 2）、`LiveWakeCoordinator`（Task 4）、`matchFarewell`（Task 3）、`playLiveCue`（Task 1）、既有 `LiveSession`/`setVisualState`/`addBubble`/`degradeToHalfDuplex`。
- 整合層無自動測試（設計上靠真機 e2e）；本 Task 僅 `node --check` 保語法。

- [ ] **Step 1: 改寫 `enterLiveMode()`**

將現行 `enterLiveMode`（836-865）整個換成：
```js
function enterLiveMode() {
  liveMode = true;
  /* 停半雙工喚醒常駐麥（若已建、吞例外）；live 模式改用 sherpa KWS + 協調器 */
  try {
    if (window._wakeController && window._wakeController.engine && window._wakeController.engine.stop) {
      window._wakeController.engine.stop();
    }
  } catch (e) { /* 忽略 */ }

  Promise.all([
    import("/static/live-client.js"),
    import("/static/live-wake.js"),
    import("/static/sherpa-loader.js"),
    import("/static/sherpa-engine.js")
  ]).then(function (mods) {
    var LiveSession = mods[0].LiveSession;
    var matchFarewell = mods[0].matchFarewell;
    var LiveWakeCoordinator = mods[1].LiveWakeCoordinator;
    var loadSherpaModule = mods[2].loadSherpaModule;
    var createSherpaEngine = mods[3].createSherpaEngine;

    var coordinator;   /* 先宣告供 startSession 閉包引用 */

    function startSession() {
      var s = new LiveSession({
        continuous: true,
        onTranscript: function (role, text) {
          addBubble(role === "USER" ? "student" : "penguin", text, {});
          if (coordinator) { coordinator.onTranscript(role, text); }
        },
        onError: function (reason) { degradeToHalfDuplex(reason); },
        onStateChange: function (state) { setVisualState(state); }
      });
      return s.start().then(function () { return s; });
    }

    function onCue(kind) {
      return new Promise(function (res) {
        playLiveCue(kind === "start" ? "我在，請說" : "先休息一下，想聊再喊我", res);
      });
    }

    /* 讀 wake-config → 建 sherpa engine（失敗則 engine=null，仍可點企鵝） */
    return fetch("/api/wake-config").then(function (r) { return r.json(); }).then(function (cfg) {
      var sh = cfg && cfg.sherpa;
      var enginePromise = (sh && sh.enabled)
        ? loadSherpaModule(sh.base_url).then(function (Module) {
            return createSherpaEngine({
              Module: Module,
              keywords: sh.keywords,
              keywordsThreshold: sh.keywords_threshold,
              keywordsScore: sh.keywords_score
            });
          }).catch(function () { return null; })
        : Promise.resolve(null);
      return enginePromise.then(function (engine) {
        coordinator = new LiveWakeCoordinator({
          engine: engine,
          startSession: startSession,
          onCue: onCue,
          onStateChange: function (state) { setVisualState(state); },
          matchFarewell: matchFarewell
        });
        window.__liveWake = coordinator;
        connStateEl.textContent = "已連線（即時）";
        connStateEl.className = "ok";
        holdHint.textContent = engine ? "🗣️ 喊「說說學伴」或點我開始，說「掰掰」結束"
                                      : "👆 點企鵝開始對話，再點一下結束";
        holdHint.classList.remove("disabled");
        showToast(engine ? "🎙️ 喊「說說學伴」或點企鵝開始" : "🎙️ 即時對話已就緒，點企鵝開始");
        return coordinator.arm();
      });
    });
  }).catch(function () {
    degradeToHalfDuplex("stream_error");
  });
}
```

- [ ] **Step 2: 改寫點企鵝 liveMode 分支**

將 648-660 的 `if (liveMode) { ... }` 區塊換成：
```js
  if (liveMode) {   /* hands-free：喚醒與點企鵝等效，交給協調器 */
    var lw = window.__liveWake;
    if (!lw) { return; }
    if (lw.state === "conversing") { lw.end("push"); }
    else { lw.wake("push"); }
    return;
  }
```
（移除原本直接呼叫 `startConversation/stopConversation` 與 `liveCueBusy` 判斷；cue 已移入協調器 onCue。`playLiveCue` 函式保留、`liveCueBusy` 變數若無他處引用可一併移除。）

- [ ] **Step 3: 語法檢查**

Run:
```bash
python3 -c "import re;s=open('web/index.html').read();open('/tmp/idx.js','w').write('\n'.join(re.findall(r'<script>(.*?)</script>',s,re.S)))" && node --check /tmp/idx.js && echo OK
```
Expected: `OK`

- [ ] **Step 4: 回歸 node 測試（確認沒動壞純函式）**

Run: `node --test web/live-client.test.mjs web/live-wake.test.mjs`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git -C .. add talkybuddy/web/index.html
git -C .. commit -m "feat(wake): enterLiveMode 改由 LiveWakeCoordinator 驅動（喚醒/點企鵝等效）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: WASM 資產 fetch + 真機 e2e 驗證

**Files:**
- 無程式碼改動；產出 `talkybuddy/web/vendor/sherpa-kws/`（gitignore，不 commit）。

**Interfaces:**
- Consumes: `scripts/fetch_sherpa_wasm.py`（Task 2）、完整接線（Task 5）。

- [ ] **Step 1: fetch WASM 資產（需能連 HF 的網路＝用戶筆電）**

Run: `.venv/bin/python scripts/fetch_sherpa_wasm.py`
Expected: 寫入 `web/vendor/sherpa-kws/`（含 glue/.wasm/.data/onnx/tokens）並通過完整性驗證。若沙箱/網路擋 → 於用戶筆電跑，或 `--base <鏡像 resolve 連結>`。

- [ ] **Step 2: 啟 server（帶 AWS 憑證 + LIVE_S2S_ENABLED）**

依現有方式啟動（帶 SigV4 env 的 uvicorn，HTTPS :8000）。手機/筆電開 `https://<LAN-IP>:8000/` 硬重整。

- [ ] **Step 3: 依 checklist 驗全語音迴圈**

- [ ] 進頁點一次企鵝授權 → 回 IDLE（提示「喊說說學伴或點我」）。
- [ ] 喊「說說學伴」→ 聽到「我在，請說」→ 說一句 → 企鵝正常回覆。
- [ ] 對話中插話 → barge-in 企鵝閉嘴切聽。
- [ ] 說「掰掰」→ 企鵝（可先回一句）→ 聽到「先休息一下」→ 回 IDLE。
- [ ] 再喊「說說學伴」→ 能再次進對話（迴圈可重複多次）。
- [ ] 點企鵝：IDLE=開始、CONVERSING=結束，與語音等效。
- [ ] `WAKE_SHERPA_ENABLED=0` 重啟 → 只點企鵝仍全功能、不崩（degrade 驗證）。

- [ ] **Step 4: 記錄結果**

驗證通過項打勾；若有失敗，記錄現象（哪一步、log）交下一輪修。**此 Task 不 commit 程式**（純驗證 + gitignore 資產）。

---

## Self-Review

- **Spec 覆蓋**：§3.1 協調器→Task 4；§3.2 matchFarewell→Task 3；§3.3 index 接線→Task 5；§3.4 後端 drop-in→Task 2；§4 降級→Task 4(engine=null)/Task 5(holdHint 分支)/Task 6 Step3 最後一項；§5 測試→各 Task 測試步驟 + Task 6 e2e；語音提示既有→Task 1。無遺漏。
- **Placeholder scan**：無 TBD/「add error handling」等；程式碼步驟均含完整程式。
- **Type consistency**：`LiveWakeState.CONVERSING==="conversing"` 與 index.html `lw.state === "conversing"` 一致；`wake/end/arm/onTranscript/state` 名稱跨 Task 4/5 一致；`onCue(kind)` 的 `"start"/"stop"` 與協調器內呼叫一致；`createSherpaEngine` 參數（keywords/keywordsThreshold/keywordsScore）與 wake-config 欄位（keywords/keywords_threshold/keywords_score）對映在 Task 5 Step1 已轉接。
