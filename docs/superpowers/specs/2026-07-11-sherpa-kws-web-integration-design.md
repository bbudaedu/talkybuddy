# Spec：sherpa-onnx KWS 瀏覽器整合（最小 drop-in）

- 日期：2026-07-11
- 分支：`feat/a1-sherpa-kws-web`（自 `origin/master`）
- 範圍決策：**最小 drop-in**（保留半雙工循序麥克風）；WASM 交付＝**fetch-on-setup 腳本**

## 1. 目標與非目標

**目標**：把依賴 Picovoice 雲端 AccessKey 的 Porcupine 喚醒引擎，換成自架 sherpa-onnx KWS，喚醒詞「說說學伴」、門檻 0.25，達成免 key、離線、裝置端。以最小變更接進 `talkybuddy/web` 原型。

**非目標（YAGNI，明確排除）**：
- 共用 AudioContext / 全雙工 barge-in（延後；與 A2 server 端 SpeechGate 工作重疊）。
- TTS 播放中喚醒（`canWake` 仍 gate `!playingTTS`，維持今日行為）。
- 從源碼自建 WASM（預建 bundle 已含所需模型，喚醒詞可執行期傳入）。

## 2. 前提（已驗證）

- 引擎：sherpa-onnx KWS，模型 `sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01`。
- 喚醒詞 token（來自 laptop_pack 真人聲驗證）：`sh uō sh uō x ué b àn @說說學伴`。
- 門檻 0.25 於真人聲穩定觸發、無誤觸發（Windows/Realtek 麥克風實測）。
- sherpa WASM API：`createKws(Module, myConfig)` 的 `keywords` 為**執行期字串**、`keywordsThreshold` 執行期可設；預設模型路徑 `encoder/decoder/joiner-epoch-12-avg-2-chunk-16-left-64.onnx`（fp32，＝spike 驗證那顆）由 Emscripten 虛擬 FS（`.data`）提供。

## 3. 核心洞見（決定架構）

現有 `index.html` 的 `initWakeLayer()` 已有乾淨升級縫：**先掛一個以 `noopEngine` 建、可 push 的 controller 並立刻 arm，再事後嘗試升級為喚醒引擎；升級失敗不拆已可用的 push controller**。因此整合＝**只替換「事後升級」那一段的引擎**。`wake-controller.js` 與 `mic-router.js` **完全不動**。

新引擎只要實作與 `porcupine-engine.js` 相同的介面契約：
- `available(): boolean`
- `start(onDetected): Promise<void>` — 訂閱麥克風、偵測到關鍵詞時呼叫 `onDetected(result)`。
- `stop(): Promise<void>` — 釋放麥克風（供 MicRouter 接手，滿足 I4），保留引擎供重用。
- `release(): Promise<void>` — 完整拆除。

## 4. 元件（每個小而有界）

### 4.1 `web/sherpa-engine.js`（新）
WakeWordEngine 的 sherpa 實作，工廠 `createSherpaEngine(cfg, deps)`：
- `cfg`：`{ keywords, keywordsThreshold, keywordsScore, numThreads }`（keywords＝「說說學伴」token 字串、threshold＝0.25）。
- `deps`（可注入，供單元測）：`{ Module, getUserMedia, createAudioContext }`；省略時用真實瀏覽器 API。
- 內部：自持一組 AudioContext(sampleRate 16000) + getUserMedia + ScriptProcessorNode，`onaudioprocess` 取 channel0 float32 →（必要時 downsample 至 16k）→ `stream.acceptWaveform(16000, samples)`；`while (kws.isReady(stream)) { kws.decode(stream); const r = kws.getResult(stream); if (r.keyword) { kws.reset(stream); onDetected(r); } }`。
- `available()`：`!!Module`（WASM 已初始化）。
- `start(onDetected)`：未啟動則 getUserMedia + 建 AudioContext + ScriptProcessor 接上；`createKws(Module, {...cfg})` 只建一次（idempotent）；接上偵測迴圈。未 available → no-op。
- `stop()`：斷開 processor、`track.stop()` 釋放麥；保留 kws / stream 供重用。
- `release()`：`stop()` + `kws.free()` + `stream.free()` + `AudioContext.close()`，歸零。

### 4.2 `web/sherpa-loader.js`（新，極小）
把非-ESM 的 Emscripten glue（`sherpa-onnx-kws.js` + `sherpa-onnx-wasm-kws-main.js`）用動態注入 `<script>` + `Module` bootstrap 載入：
- 設 `window.Module = { locateFile: (p) => baseUrl + p, onRuntimeInitialized: () => resolve(Module) }`。
- 依序注入兩支 glue script（先 `sherpa-onnx-kws.js` 再 main）。
- 回傳 `Promise<Module>`；載入/初始化失敗則 reject。
- 目的：把「全域 Module 髒載入 + DOM 依賴」隔離在此，讓 `sherpa-engine.js` 保持可 `node --test`（測試注入 fake Module，不碰此 loader）。

### 4.3 `web/sherpa-engine.test.mjs`（新）
仿 `porcupine-engine.test.mjs`，`node --test`，注入 fake `Module`（含 fake `createKws`/`Kws`/`Stream`）+ fake audio：
- `available()` 依 Module 有無。
- `start`：以正確 keyword/threshold 建 Kws 一次；`onDetected` 接線（fake stream 吐 keyword → 觸發）。
- `stop`：呼叫 track.stop（釋放麥）。
- start 兩次只 create 一次。
- 未 available：`start` 為 no-op。

### 4.4 `index.html` 的 `initWakeLayer()`（改）
- 三個既有 `/static` 動態 import 之一（`porcupine-engine.js`）改為載入 sherpa 相關模組（`sherpa-loader.js` + `sherpa-engine.js`）。
- noop push controller `arm()` 後，改走 sherpa 升級：讀 `/api/wake-config` → 若 `sherpa.enabled` → `sherpaLoader.load(baseUrl)` → `createSherpaEngine({ keywords, keywordsThreshold }, { Module })` → `buildController(engine)` → `arm()`。
- 任一步失敗 → catch → 維持既有 noop push controller（degrade-safe），並 `showToast` 提示喚醒詞暫不可用、push 仍可用。
- `buildController` / `micRouter` / `onWakeEvent`（送 `{type:"wake"}`）/ `playWakeCue` 全部不動。

### 4.5 `server/config.py` + `/api/wake-config`（改）
`/api/wake-config` 回傳新增 `sherpa` 區塊：
```json
{
  "sherpa": {
    "enabled": true,
    "base_url": "/static/vendor/sherpa-kws/",
    "keywords": "sh uō sh uō x ué b àn @說說學伴",
    "keywords_threshold": 0.25,
    "keywords_score": 1.0
  }
}
```
- `config.py` 新增對應 env 變數（`WAKE_SHERPA_ENABLED`、`WAKE_SHERPA_KEYWORDS`、`WAKE_SHERPA_THRESHOLD`…）並帶預設值，讓門檻可調而不改 client。
- Porcupine 舊欄位：保留或移除皆可；本 spec 傾向**保留**（不刪相容欄位，降低破壞面），client 端只是不再讀取。

### 4.6 `scripts/fetch_sherpa_wasm.py`（新）+ `.gitignore` + setup 文件
- 跨平台 fetch 腳本（仿 `laptop_pack/bootstrap.py`）：下載 k2-fsa 預建 WASM KWS bundle（glue `.js` + `.wasm` + 內含 wenetspeech 模型的 `.data`）解壓到 `talkybuddy/web/vendor/sherpa-kws/`。
- `talkybuddy/web/vendor/` 加入 `.gitignore`（比照 spike 模型不進版控）。
- setup 文件：一段 README，說明「跑 `python scripts/fetch_sherpa_wasm.py` 後啟動 server 即可用喚醒詞」。
- 下載來源與檔名於實作階段確認（k2-fsa GitHub release / HF space 的 wasm-kws 產物）。

## 5. 資料流（半雙工，與今日相同）

```
mic → sherpa AudioContext/ScriptProcessor → KWS
  → onDetected → WakeController.onWake("wakeword")
  → engine.stop()（釋放麥）
  → MicRouter.recordTurn()（另起 getUserMedia + MediaRecorder webm/opus）
  → WS 送 server → asr/llm/tts → ...
  → COOLDOWN →（去抖）→ engine.start()（重新 arm）
```
與 Porcupine 流程一模一樣；僅引擎內部（sherpa AudioContext feed vs Porcupine WVP）不同。喚醒詞與該輪錄音在瀏覽器端仍是**兩組**麥克風消費者、循序切換（非共用）。

## 6. 錯誤處理 / degrade-safe

- WASM 載入失敗 / vendor 資產不存在 / `sherpa.enabled=false` → 維持 noop push controller，push-to-talk 仍可用（與今日同保證）。
- engine 內 getUserMedia 被拒 → `start` throw → `wake-controller.arm` 既有 catch → 停在 ARMED、push 仍可用。
- `stop()` 須先完成釋放麥再讓 MicRouter 取得（I4）；沿用 controller 既有 `stop().then(recordTurn)` 序。
- ScriptProcessorNode 為 deprecated 但相容性最廣；MVP 採用，未來可換 AudioWorklet（記錄為後續）。

## 7. 測試

- **單元（此沙箱可跑，`node --test`）**：新增 `sherpa-engine.test.mjs`；既有 `wake-controller.test.mjs` / `mic-router.test.mjs` / `porcupine-engine.test.mjs` 不動、須保持綠。
- **手動（用戶筆電，有麥克風）**：
  1. `python scripts/fetch_sherpa_wasm.py` 取得 vendor 資產。
  2. 啟動 server，開學生端頁。
  3. 講「說說學伴」→ 企鵝進 listen（ACTIVE）、播「我在！」回饋音。
  4. 正常聊天／念文章 → 不誤觸發。
  5. push-to-talk（點企鵝）→ 仍可用。
  6. 移除／未放 vendor 資產 → degrade：push 仍可用、顯示提示。

## 8. 分支與交付

- 分支：`feat/a1-sherpa-kws-web`（自 `origin/master`）。
- 不動：`wake-controller.js`、`mic-router.js` 及其測試。
- 不含：`talkybuddy/verify_v3_tts.py` 的工作區刪除（與本任務無關，保持不動）。
