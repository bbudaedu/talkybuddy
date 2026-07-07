# A1 喚醒層設計書（Porcupine 語音喚醒 + tap-to-toggle push）

- 日期：2026-07-07
- 範圍：`talkybuddy/` 瀏覽器 client 的「喚醒/開麥」互動層（`web/index.html` 為主），server 幾乎不動
- 依據：全雙工+語音喚醒重設計，A 軸拆解出 **A1 喚醒**（push+語音喚醒，單輪）。連續全雙工 barge-in 留給 A2。
- 現況基線：
  - client `web/index.html` 單檔內嵌 vanilla JS（無 build/bundler）。目前 push UX =
    **press-and-hold**（`pointerdown`→`startRecording`，`pointerup`→`stopRecording`），
    MediaRecorder 錄 webm/opus → WS 送單一 binary frame + `{type:"audio_end"}`。
  - server `server/app.py` `/ws/talk`：收 binary 音訊 → `pipeline.run_turn_audio(data, emit)` 跑一輪。

## 目標

在現有 client 加上 **語音喚醒**（喊喚醒詞免持開麥）與把 push 從 press-hold 改為
**tap-to-toggle**，兩者都導向**既有單輪 pipeline**（喚醒→錄一輪→回覆→回到待命）。
喚醒詞偵測**全程在瀏覽器裝置端**，喚醒前音訊不離開裝置、不上傳。
**不破壞任何現有可運行狀態**（最高原則）：語音喚醒不可用時，退回純 push 仍完整可用。

## 非目標（本次不做）

- **A2 全雙工 / barge-in / 連續對話**：VAD、AEC、串流上傳、講話中插話打斷，全部留給 A2。
  A1 只做「喚醒 → 一輪 → 回待命」。
- **中文喚醒詞「小企鵝」**：先做英文「Hey Penguin」/「Hey Buddy」，中文詞留待引擎抽象穩定後再加 `.ppn`。
- **Genio 520 上板**：先做 PC/瀏覽器原型；上板改用 Porcupine ARM/C SDK（同一顆 `.ppn`）屬移植階段。
- **server pipeline 狀態機改動**：喚醒後沿用 `run_turn_audio`，一輪語意不變。

## 契約（維持不變 / 最小新增）

喚醒層是 client 端狀態機，喚醒/開麥後**沿用現有音訊上傳契約**（CONTRACTS.md `/ws/talk`）：

```
喚醒（wakeword 或 push tap）
  → 開麥錄一輪（MediaRecorder，webm/opus）
  → ws.send(binary)  +  ws.send({type:"audio_end"})
  → server run_turn_audio 跑既有一輪（ASR→scaffold/LLM→TTS）
```

- server 端 `run_turn_audio` 呼叫路徑、frame 協定**完全不動**。
- **（定案：一併做）**新增一個純記錄用的上行事件 `{"type":"wake","source":"wakeword"|"push"}`：
  喚醒瞬間由 client 送出，server 端 `/ws/talk` 收到後記 log（供日後統計誤觸/使用），不改變一輪對話行為。
  A1 一併實作此事件的 client 發送與 server 記錄兩端。

## 狀態機

client 端單一狀態機，四態單向循環：

```
ARMED ──(porcupine 偵測到喚醒詞 | push tap)──▶ ACTIVE（錄並跑一輪）
ACTIVE ──(一輪回覆播放結束 或 錄音逾時)──▶ COOLDOWN
COOLDOWN ──(閒置 N 秒，預設 ~1.5s 去抖)──▶ ARMED
```

- **ARMED**：待命。若語音喚醒可用，Porcupine 在背景 always-listening（僅本地）；push 按鈕可點。
- **ACTIVE**：麥克風錄音送 WS 跑一輪。此期間 Porcupine 暫停（避免自我喚醒/搶麥）。
- **COOLDOWN**：一輪剛結束的短去抖窗，避免 TTS 尾音或殘響立刻二次誤觸；結束回 ARMED。
- 任何錯誤（mic 權限拒絕、WS 斷線）→ 安全回 ARMED 並提示；語音喚醒失效不影響 push。

## 元件架構（client 為主）

新增一層薄封裝，掛在現有 `web/index.html` 的 `<script>` 內（或抽為同層小模組），
不動既有 `connectWS` / `handleServerMsg` / `playTTS` 主幹：

| 元件 | 職責 |
|---|---|
| `WakeController` | 持有狀態機（ARMED/ACTIVE/COOLDOWN），統一入口 `onWake(source)`；串接 MicRouter、回饋、既有錄音上傳流程。是唯一協調者。 |
| `WakeWordEngine`（抽象介面） | 封裝喚醒詞引擎，對 Controller 只暴露 `start(onDetected)` / `stop()` / `available()`。目前實作 = Porcupine；日後換 openWakeWord 或加中文詞**不動 Controller**。 |
| `PorcupineEngine`（實作） | 封裝 `@picovoice/porcupine-web` + `@picovoice/web-voice-processor`，載入 AccessKey + `.ppn`，偵測到喚醒詞回呼 Controller。 |
| `PushButton` | 把現有 press-hold 綁定改為 **tap-to-toggle**：第一次 tap → `WakeController.onWake("push")` 開始錄音；第二次 tap → 結束該輪（等同現有 `stopRecording`）。 |
| `MicRouter` | 麥克風去向的單一裁決點：ARMED 時只把音訊餵給 Porcupine（不建立 MediaRecorder、不上傳）；ACTIVE 時走既有 MediaRecorder→WS。確保「喚醒前音訊不出裝置」。 |
| 喚醒回饋 | 喚醒瞬間：視覺（沿用 `setVisualState` 亮燈/企鵝動效）+ 聽覺（短「我在！」提示音，可用既有 TTS 或內建短音檔）。 |

### WakeWordEngine 抽象介面（關鍵：可換引擎）

```
interface WakeWordEngine {
  available(): boolean            // 瀏覽器支援 + AccessKey/ppn 就緒
  start(onDetected: () => void): Promise<void>   // 開始 always-listening（本地）
  stop(): Promise<void>          // ACTIVE 期間 / 關閉時暫停
}
```

Controller 只依賴此介面。Porcupine 與未來 openWakeWord/中文詞各自實作，抽換不外溢。

## Porcupine 引擎細節（`PorcupineEngine`）

- **套件**：`@picovoice/porcupine-web`（喚醒詞推論，WASM）+ `@picovoice/web-voice-processor`
  （抓 mic 原始 16kHz PCM 餵給 Porcupine）。兩者皆裝置端推論、無雲端往返。
- **引入方式（待確認，因 client 無 build）**：現況是無 bundler 的單檔 vanilla JS。
  預設用 **ESM CDN 動態 import**（`import('https://cdn.jsdelivr.net/npm/@picovoice/porcupine-web@.../dist/...+esm')`）
  或改在 `web/` 引一個 `<script type="module">` 側掛。實作時先驗證 CDN ESM 路徑與 WASM/worker
  資產能在 `localhost` 正常載入；若 CDN 方案受阻，退而把套件 vendored 到 `web/vendor/`。
  **此為 A1 唯一較不確定的工程點，實作首步先打通最小 PoC。**
- **AccessKey（定案：由 server 下發）**：Picovoice Console 免費申請（有用量上限；產品化需授權方案）。
  key 存在 **server 端環境變數/本地未追蹤設定檔**，經 **`/api/status`（或新增 `/api/wake-config`）下發**給 client，
  **不 commit 進 repo、不硬編在 `web/index.html`**。client 啟動喚醒引擎前先取回 key。
- **喚醒詞 `.ppn`**：於 Picovoice Console 產（Web target，自訂關鍵詞免訓練資料）。
  先做英文「Hey Penguin」/「Hey Buddy」。`.ppn` 檔放 `web/assets/` 或 CDN base64 載入。
- **隱私**：Porcupine 全程在 WASM 內比對，喚醒前**不建立 WS 音訊上傳**（由 MicRouter 保證）；
  只有偵測到喚醒詞後才進 ACTIVE 開始錄音上傳。

## Push UX：press-hold → tap-to-toggle（行為變更）

- **現況**：`penguinWrap` `pointerdown`→`startRecording`、`pointerup`/`pointercancel`→`stopRecording`
  （按住講、放開停）。
- **改為**：tap 一下 → 開始錄一輪；再 tap 一下（或錄音逾時）→ 結束並上傳。
  免持更適合玩偶情境、也和語音喚醒後的單輪流程一致。
- 實作：改 `penguinWrap` 事件綁定為單一 `click`/`pointerup` toggle，內部維護
  `isRecording` 由 `WakeController` 狀態驅動；`startRecording`/`stopRecording` 主體**沿用不改**。
- 保留 `contextmenu` preventDefault。需補一個「錄音逾時上限」（例如 ~15s）避免忘記結束。

## MicRouter：喚醒前不上傳

- 單一 `getUserMedia` 麥克風串流的去向裁決：
  - **ARMED**：串流交給 WebVoiceProcessor→Porcupine；**不**建立 MediaRecorder、**不**送 WS。
  - **ACTIVE**：暫停 Porcupine 訂閱，串流交給既有 MediaRecorder 錄一輪→WS。
- 需釐清 WebVoiceProcessor 與現有 MediaRecorder 能否共用同一 `getUserMedia` 串流，或需分別取得
  （實作時驗證；多數瀏覽器可對同一 stream 多路訂閱，必要時用兩次 getUserMedia 並在切換時釋放）。
- 目標：任何時刻只有一個消費者在用麥克風，切換乾淨、無回授自我喚醒。

## 降級與相容

- **語音喚醒不可用**（不支援 WASM / AccessKey 缺 / `.ppn` 載入失敗 / mic 權限拒）：
  `WakeWordEngine.available()=False`，UI 只提供 **tap-to-toggle push**，其餘完全正常。
- **不安全上下文 / 無 mic**：沿用現有 `initMic` 的 `micDisabled` 兜底路徑，push 停用、
  文字輸入（quick chips / `sendText`）仍可用。
- 語音喚醒是**加值**，push 是**主幹**；主幹永遠可用，符合「小而可回退」。

## 測試

- **`WakeController` 狀態機單元測試**（注入 stub `WakeWordEngine` + stub MicRouter，不碰真 mic/Porcupine）：
  - ARMED + `onWake("wakeword")` → ACTIVE，且觸發開麥、暫停引擎。
  - ARMED + `onWake("push")` → ACTIVE（同上路徑）。
  - ACTIVE 期間再收到喚醒 → 忽略（不重入）。
  - 一輪結束 → COOLDOWN → 去抖後回 ARMED、重新 arm 引擎。
  - `available()=False` 時：push 仍可進 ACTIVE，引擎相關呼叫被跳過、不炸。
  - 錄音逾時 → 自動結束回 COOLDOWN。
  - client 測試以 client 現有測試方式進行（若無 JS 測試框架，抽 `WakeController` 為純函式/類別，
    用 node 或既有 pytest+子程序皆可；實作時定；核心是狀態轉移可離線斷言）。
- **手動 verify（真喚醒詞）**：`run.sh` 開 `localhost`（HTTPS/secure context 或 localhost 例外），
  對麥克風喊「Hey Penguin」→ 確認亮燈+「我在！」→ 錄一輪 → 收到繁體 `asr_result` + TTS 回覆；
  再測 tap-to-toggle push 同路徑；測誤觸（環境噪音）與漏觸（兒童發音）主觀感受。

## 風險與待確認

- **client 無 build 引入 Porcupine**（最大工程不確定點）：CDN ESM / WASM / worker 資產在 localhost
  的載入方式需 PoC 先打通；備案 vendored 到 `web/vendor/`。
- **AccessKey 用量上限**：免費層有上限、產品化需授權；勿把 key commit 進 repo。
- **WebVoiceProcessor × MediaRecorder 共用 mic**：同串流多路訂閱 vs 兩次 getUserMedia 的切換釋放，
  需實機驗證無回授、無裝置佔用衝突。
- **兒童發音誤觸/漏觸**：無文獻數據，需自錄樣本主觀 A/B；喚醒靈敏度（Porcupine sensitivity）留可調參。
- **麥克風 always-on 權限**：需使用者授權常駐 mic；UI 要明示「喚醒前不上傳」以取得信任。
- **Genio 520 上板**：改 Porcupine ARM/C SDK（同 `.ppn`），WakeWordEngine 抽象即為此預留抽換點。
