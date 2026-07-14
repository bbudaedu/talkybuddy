# 設計：喚醒詞→hands-free 全語音迴圈（Nova Sonic + sherpa KWS）

日期：2026-07-14
分支：`feat/cloud-llm-bedrock`
母脈絡：`2026-07-14-nova-sonic-handsfree-fullduplex-design.md`（barge-in 已完成）、記憶 `talkybuddy-nova-sonic-live`、`talkybuddy-openwakeword-spike`

## 1. 目標

讓 hands-free 即時對話「全語音免手」：

- Idle 時喊喚醒詞「**說說學伴**」→ 自動進 hands-free 對話（等同現在點企鵝開始）。
- 對話中隨時可 barge-in 插話（已完成）。
- 說「**掰掰**」（或再見/拜拜/結束/bye）→ 收掉對話、回到聽喚醒詞。
- 點企鵝全程仍是手動起訖 override；喚醒只是多一條入口。
- 初次進頁需點一下企鵝授權麥克風並喚醒 AudioContext（行動裝置無法免手勢），之後即進入全語音迴圈。

非目標（YAGNI）：半雙工路徑改動、對話中同時聽喚醒詞、多喚醒詞、伺服器端結束判斷。

## 2. 現況與前提（已探索確認）

- 所有 Nova Sonic live/hands-free 工作在 `feat/cloud-llm-bedrock`；此分支從 master 在 sherpa merge **之前**分岔（merge-base `f697799`），故 `initWakeLayer` 仍是 **Porcupine 版**（`/api/wake-config` 走 Picovoice key，`PICOVOICE_ACCESS_KEY` 預設空 → 實務只剩 push/點擊）。
- 可用的 sherpa「說說學伴」喚醒引擎在 **master**：`web/sherpa-engine.js`、`sherpa-loader.js`、wake-config `sherpa` 區塊、`config.py` 的 `WAKE_SHERPA_*`（threshold 0.25、keywords token `sh uō sh uō x ué b àn @說說學伴`）。真人聲門檻 0.25 已於筆電驗過（模型 `sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01`）。
- 兩分支 index.html 大幅分歧（87 insert / 92 delete）；master 改 initWakeLayer(Porcupine→sherpa)、本分支改 enterLiveMode/hands-free。故採 **vendor drop-in**，不整枝 merge。
- 本 worktree **無** WASM 資產（`web/vendor/sherpa-kws/` 不存在，gitignore），需 fetch。

### sherpa engine 介面（沿用 master，不改）

`loadSherpaModule(base_url) → Promise<Module>`；`createSherpaEngine(cfg, deps) → { available(), start(onDetected), stop(), release() }`。engine 自持 `AudioContext(16k)` + `ScriptProcessorNode` 餵 PCM 進 WASM KWS，`onDetected(r)` 帶 `r.keyword`。deps 可注入供 node --test。

## 3. 架構

### 3.1 新增協調器 `web/live-wake.js`（純邏輯、可 node --test）

仿 `wake-controller.js`：零瀏覽器依賴、依賴由建構子注入，管兩態迴圈。

狀態：`IDLE`（KWS 聽喚醒）↔ `CONVERSING`（LiveSession 常開對話）。

注入依賴：
- `engine`：sherpa KWS engine（`available/start/stop/release`）。
- `startSession()`：回傳已開始的 `LiveSession`（呼叫端負責 `new LiveSession(cb)` + `start()`）。
- `onCue(kind)`：播語音提示（`"start"`→我在／`"stop"`→先休息）。
- `onStateChange(state)`：驅動視覺（idle/listen/…）。
- `matchFarewell(text)`：純函式結束詞比對（見 3.2）。

轉移：
- `arm()`：進 IDLE，`engine.available()` 則 `engine.start(onDetected)`；失敗維持 IDLE（degrade-safe，點企鵝仍可 `wake("push")`）。
- `wake(source)`（KWS onDetected 或點企鵝）：僅 IDLE 有效（重入保護）。先 `await engine.stop()/release()` 放麥 → `session = await startSession()` → `onCue("start")` 播完 → `session.startConversation()` → 轉 CONVERSING。
- `onTranscript(role, text)`：CONVERSING 且 `role==="USER"` 且 `matchFarewell(text)` → `end("farewell")`。
- `end(source)`（結束詞或點企鵝）：僅 CONVERSING 有效。`session.stopConversation()` → `onCue("stop")` → `await session.close()` 放麥+WS → `arm()` 重回 IDLE。

麥克風分時互斥：IDLE 只 KWS 持麥；CONVERSING 只 LiveSession 持麥。**每場對話新建 LiveSession**（WS 每場重連），交棒點各自 stop/release/close，絕不雙麥重疊。

### 3.2 結束詞偵測 `matchFarewell(text)`（純函式，可測）

放 `live-client.js`（與其他純函式同處）。正規化後命中 `掰掰|拜拜|再見|結束|bye|掰啦|bye bye` 任一回 `true`，否則 `false`。**僅由協調器對 USER role 呼叫**（不誤判企鵝回話）。

### 3.3 index.html 接線改寫

`enterLiveMode()` 改為：
1. 動態載入 `sherpa-loader.js` + `sherpa-engine.js` + `live-wake.js`。
2. 讀 `/api/wake-config`，取 `cfg.sherpa`；`sh.enabled` 則 `loadSherpaModule(sh.base_url)` + `createSherpaEngine({keywords, keywordsThreshold, keywordsScore})`。
3. 建 `LiveWakeCoordinator`，注入 engine、`startSession`（內部 `new LiveSession({continuous:true, onTranscript, onError, onStateChange})` + `start()`，並把 `onTranscript` 轉呼協調器的 `onTranscript` 以驅動結束詞）、`onCue`（= 既有 `playLiveCue` 包裝）、`onStateChange`（= `setVisualState`）。
4. `coordinator.arm()` 進 IDLE。
5. 點企鵝：`liveMode` 分支改呼 `coordinator.wake("push")`（IDLE 時）或 `coordinator.end("push")`（CONVERSING 時）取代現行直接 `startConversation/stopConversation`。語音提示的 START「播完才開麥」語意移進協調器 `wake()`。

`window.__liveWake` 曝露供除錯/測。

### 3.4 後端（vendor drop-in）

- `git checkout master -- web/sherpa-engine.js web/sherpa-loader.js web/sherpa-engine.test.mjs web/sherpa-loader.test.mjs scripts/fetch_sherpa_wasm.py tests/test_fetch_sherpa_wasm.py`。
- `config.py` 補 `WAKE_SHERPA_*`（照 master：ENABLED 預設 1、BASE_URL `/static/vendor/sherpa-kws/`、KEYWORDS「說說學伴」token、THRESHOLD 0.25、SCORE 1.0）。
- `app.py` `/api/wake-config` 回傳補 `sherpa` 區塊（照 master）。

## 4. 降級與相容（degrade-safe）

- sherpa 載入/WASM 建置/wake-config 失敗 → 協調器以 `engine=null` 建立、`arm()` 停在 IDLE 但無 KWS；**點企鵝 `wake("push")` 仍全功能**（即退回現有點企鵝 hands-free），**不**退半雙工。
- live 連線失敗（`onError`）→ 沿用既有 `degradeToHalfDuplex(reason)`。
- `live_s2s=false` 或 `forceHalfDuplex` → 完全不碰，走既有半雙工 + Porcupine `initWakeLayer`（本次不動）。

## 5. 測試

- **node 單元**：
  - `matchFarewell`：命中/不命中/大小寫/含標點（加進 `live-client.test.mjs`）。
  - `live-wake.js`：注入假 engine/session/cue，斷言 IDLE→CONVERSING→IDLE 轉移、麥交棒次序（engine.stop 先於 startSession；stopConversation/close 先於 re-arm）、重入保護、farewell 只認 USER、degrade（engine=null 時 push 仍可 wake）。新 `web/live-wake.test.mjs`。
  - drop-in 的 `sherpa-*.test.mjs`、`test_fetch_sherpa_wasm.py` 帶入即應綠。
- **pytest**：wake-config 含 sherpa 區塊斷言（drop-in 或補一條）。
- **手動 e2e（真機，依 checklist）**：
  1. 進頁點一次企鵝授權 → 回 IDLE（提示「喊說說學伴或點我」）。
  2. 喊「說說學伴」→ 聽到「我在，請說」→ 說一句 → 企鵝正常回覆。
  3. 對話中插話 barge-in → 企鵝閉嘴切聽。
  4. 說「掰掰」→ 企鵝（可先回一句）→ 聽到「先休息一下」→ 回 IDLE。
  5. 再喊「說說學伴」→ 能再次進對話（迴圈可重複）。
  6. 點企鵝在 IDLE=開始、CONVERSING=結束，與語音等效。
  7. sherpa 停用（`WAKE_SHERPA_ENABLED=0`）→ 只點企鵝仍全功能、不崩。

## 6. 風險

- 行動裝置 KWS `AudioContext` 需初次手勢才 running → 用初次點企鵝解，之後全語音。
- KWS 與 Nova 不可同時持麥 → 分時互斥架構已避開；交棒 await 次序須正確（測試覆蓋）。
- 結束詞靠 Nova ASR 正確聽出「掰掰」→ 用多同義詞 + 正規化降低漏判；漏判時點企鵝為保底。
- WASM 資產需在能連 HF 的網路 fetch（本 worktree 缺）→ setup 步驟明列。

## 7. 交付範圍

前端：`live-wake.js`（新）、`live-client.js`（加 `matchFarewell`）、`index.html`（enterLiveMode 改寫）、`live-wake.test.mjs`（新）、`live-client.test.mjs`（加測）。
後端：`config.py`、`app.py`（wake-config sherpa）。
drop-in：`sherpa-engine.js`/`sherpa-loader.js`＋測試、`fetch_sherpa_wasm.py`＋測試。
setup：fetch WASM 到 `web/vendor/sherpa-kws/`。
