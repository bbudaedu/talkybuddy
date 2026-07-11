# A2 SpeechGate 接入 turn_manager — barge-in 專用門檻縫

**日期**：2026-07-10
**分支**：`feat/a2-speechgate-barge-in`
**前置**：A2-1 spike＝GO、A2-2 `StreamingTurnManager` 迴路已實作（`server/streaming/`，15 pytest 綠燈）、A2-4 venv 併入主 `.venv`（commit `f697799`）

## 目標與範圍

把 `SpeechGate`（`server/streaming/vad.py`）接成 `StreamingTurnManager` 的**獨立可調 barge-in 偵測器**，讓「bot 說話中的插話偵測」擁有自己一套門檻，與 transport 的 turn-taking VAD 分離。

**動機（使用者簽核）**：turn-taking VAD（Pipecat transport 內建，驅動 STT 分段與輪偵測）與「bot 說話中的 barge-in 偵測」需要**不同靈敏度**（double-talk）——barge-in 偵測要夠保守，避免被 bot 自己的 TTS 外洩回麥克風誤觸發，同時仍抓得到真的兒童插話。

**本次只建縫（使用者簽核）**：建立機制＋可配置參數＋以 canned wav 驗接線（二元淨判）。

**明確不做（延後）**：
- 真兒童錄音調門檻 → A2-3/A2-6。
- AEC 下 double-talk 場域驗證（不吃 bot 自聲）→ A2-3（瀏覽器 AEC3）／A2-6（XMOS XVF3800）。
- 真麥/`LocalAudioTransport` 端到端接線 → A2-3 realwire（另 spec）。
- 不動 `config.py`（語速工作屬另一 session，避免衝突）。

## 背景：目前兩條 VAD 路徑

1. **Pipecat 內建 VAD**：掛在 transport input 的 `SileroVADAnalyzer`，往下游發 `VADUserStartedSpeakingFrame`。**`turn_manager.py:48` 目前的 barge-in 就是聽這個 frame**。此 frame 同時驅動 `FunASRSTTService` 的段落轉錄。
2. **SpeechGate（vad.py）**：把「同一顆」`SileroVADAnalyzer` 包成**框架無關的同步**閘門，吃 raw PCM bytes、吐 `"started"/"stopped"` 邊緣事件字串，含針對繁中兒童可調參數（`confidence`/`stop_secs`/`min_volume`）。**目前只有單元測試（`test_vad.py`），未接入任何管線。**

問題：靠 transport 那顆靈敏的 turn-taking VAD 觸發 barge-in，無法對「插話偵測」單獨調門檻 → 專用門檻的動機無法實現。

## 架構（方案 A：獨立 BargeInGate 處理器）

```
transport.input() ─▶ [Pipecat 內建 VAD] ─▶ BargeInGate ─▶ FunASRSTTService
  ─TranscriptionFrame─▶ StreamingTurnManager ─逐句 TTSSpeakFrame─▶ SherpaInterruptibleTTSService ─▶ output
```

- **`BargeInGate`（新 `FrameProcessor`）**：內含一顆用「barge-in 專用參數」建的 `SpeechGate`。觀察下行的 `InputAudioRawFrame`（**同時原封轉發**給下游 STT），把 PCM 餵給 SpeechGate；SpeechGate 吐 `"started"` 時，往 **downstream** 發一個新的 `BargeInDetectedFrame`。
- **`StreamingTurnManager`（改）**：barge-in 只由 `BargeInDetectedFrame` 觸發（仍以 `_bot_speaking` 守衛）；**移除**對 `VADUserStartedSpeakingFrame` 的 barge-in 分支——該 frame 繼續原封轉發，供 STT 分段用。

**設計淨化紅利**：`BargeInGate` 位於 turn_manager 上游，往 downstream 發 frame 即可到達，**免除** A2-2 harness `BargeInDriver` 的 UPSTREAM 回注 hack。

## 元件與職責

| 元件 | 職責 | 依賴 | 狀態 |
|---|---|---|---|
| `SpeechGate` / `build_analyzer`（`vad.py`） | PCM→started/stopped，參數化門檻 | Silero VAD | 沿用、不改 |
| `BargeInDetectedFrame`（新） | 專用 barge-in 訊號 frame（自 SpeechGate "started" 產生） | Pipecat `Frame` | 新增 |
| `BargeInGate`（新，`server/streaming/barge_in_gate.py`） | 觀察 InputAudioRawFrame→餵 SpeechGate→發 BargeInDetectedFrame；原音訊照轉 | `SpeechGate` | 新增 |
| `StreamingTurnManager`（`turn_manager.py`） | barge-in 改聽 BargeInDetectedFrame、移除 VAD frame 分支 | `ReplySource`、`TurnResult` | 改 |
| `harness.py` | 新增「真音訊驅動 barge-in」路徑 | 以上 | 改 |

**brain 免疫縫**：`StreamingTurnManager` 仍只依賴 `ReplySource` 介面與 `TurnResult`；本切片不改該契約。

## 關鍵設計決策

- **專用門檻 vs turn-taking 分離**：`BargeInGate` 自己呼 `build_analyzer(...)`，預設比 turn-taking 更保守（暫定較高 `confidence` 與 `min_volume`）。**這些預設值＝A2-3 前的暫定佔位，非最終值**；重點是它們是**獨立旋鈕**，透過 `BargeInGate` 建構參數傳入。不動 `config.py`。
- **sample rate 惰性初始化**：`BargeInGate` 於**首個 `InputAudioRawFrame`** 依 `frame.sample_rate` 建 `SpeechGate`（`build_analyzer` 內部需 `set_sample_rate` 才初始化位元緩衝）。
- **bot 說話守衛**：`BargeInGate` 每次 "started" 都發 frame（本身無 bot 狀態）；由 `turn_manager._bot_speaking` 決定是否成 barge-in。回合起始使用者首次開口時 `_bot_speaking=False` → `BargeInDetectedFrame` 被忽略、不誤觸；此時 turn-taking VAD 的 `VADUserStartedSpeakingFrame` 照常驅動 STT。

## 資料流（barge-in 一次）

1. bot 回覆合成中（`turn_manager._bot_speaking=True`）。
2. 使用者開口 → `InputAudioRawFrame` 流經 `BargeInGate` → SpeechGate 累積達 start 門檻 → 吐 `"started"`。
3. `BargeInGate` 發 `BargeInDetectedFrame`（downstream），並照常轉發原 audio frame。
4. `turn_manager` 收到 `BargeInDetectedFrame` 且 `_bot_speaking` → push `InterruptionFrame`（TTS 句界停）、cancel reply task、記 `state_events += "barge_in"`、`_bot_speaking=False`。

## 錯誤處理

- **無音訊 / 只有靜音**：SpeechGate 不吐 "started" → 不發 frame → 無 barge-in（既有單測已涵蓋靜音路徑）。
- **未收到 `InputAudioRawFrame` 就先收其他 frame**：SpeechGate 尚未建（惰性）→ 照常轉發、不崩。
- **SpeechGate 跨回合狀態**：連續麥克風音訊會自然 started→stopped 自我重置（SpeechGate 見 `VADState.QUIET` 即翻回 `_speaking=False`）；harness 離散餵音時以「stopped 後可再 started」覆核不會卡死。
- 斷網/雲端/AEC 不涉及（純本地機制）。

## 驗收（TDD，二元淨判，不量延遲）

### 1. `BargeInGate` 單元測試（`test_barge_in_gate.py`，新）
- **判準 A（語音觸發）**：以 canned `zh.wav`（16k mono，既有）產生的 `InputAudioRawFrame` 餵入 `BargeInGate` → 捕捉到 **≥1 個 `BargeInDetectedFrame`**。
- **判準 B（靜音不觸發）**：餵靜音 frame → **0 個 `BargeInDetectedFrame`**。
- 用捕捉式下游 sink 記錄 emit。

### 2. `turn_manager` 解耦單元測試（`test_turn_manager.py`，補）— **鎖行為變更**
- **判準 C（新來源觸發）**：bot 說話中（`_bot_speaking=True`）收 `BargeInDetectedFrame` → 觸發 barge-in（`state_events` 含 `barge_in`、reply task 被 cancel）。
- **判準 D（舊耦合已移除）**：bot 說話中收 `VADUserStartedSpeakingFrame` → **不** barge-in（照常轉發，`state_events` 不含 `barge_in`）。

### 3. harness 整合（`harness.py` + `test_turn_manager.py` 整合案）
把 barge-in 改由**真音訊**經 `BargeInGate` 觸發（取代/並列現有合成式 `BargeInDriver`）：
- **判準 E（barge-in 淨停）**：bot 回覆合成期間注入真語音 `InputAudioRawFrame` → `BargeInGate` 發 `BargeInDetectedFrame` → sink 實際合成句數 **≤2**（句界乾淨中止），對照無插話時的完整句數。

## 檔案落點

- `talkybuddy/server/streaming/barge_in_gate.py`（新：`BargeInDetectedFrame` + `BargeInGate`）
- `talkybuddy/server/streaming/turn_manager.py`（改：barge-in 來源換 frame、移除 VAD 分支）
- `talkybuddy/server/streaming/tests/test_barge_in_gate.py`（新）
- `talkybuddy/server/streaming/tests/test_turn_manager.py`（補判準 C/D + 整合判準 E）
- `talkybuddy/server/streaming/harness.py`（改：真音訊驅動 barge-in 路徑）

**不動**：`server/pipeline.py`、`server/app.py`、`_process_text`/`run_turn_audio`、`config.py`、`cloud_tts.py`、`vad.py`（SpeechGate 沿用）、`test_vad.py`（既有單測不變）。

## 約束

- 只 import `server.pipeline.TurnResult`（dataclass）；不碰 `_process_text`/`run_turn_audio` 熱路徑（criterion：grep 驗 `turn_manager.py`/`barge_in_gate.py` 不 import 其它 server 熱路徑）。
- 全程單一主 `.venv`（A2-4 已併 `pipecat-ai==1.5.0`+`pytest-asyncio==1.4.0`），`./run_tests.sh` 維持全綠（原 189 pass + 本次新增）。

## 風險與未知

- **`BargeInDetectedFrame` 自訂 frame 於 Pipecat 1.5.0 的傳遞行為**：需確認自訂 `Frame` 子類經 `push_frame` 能穿越中間處理器抵達 turn_manager（A2-2 已驗自訂處理器 + downstream 傳遞可行，風險低）。若自訂 frame 有非預期攔截，fallback＝改用既有輕量 frame 型別攜帶 barge-in 語意並在 turn_manager 以旗標區分。
- **harness 離散餵音時序**：真音訊需在 bot 回覆「合成中」窗口注入才觀察得到淨停（A2-2 已知需 `asyncio.sleep` 模擬播放節奏）；沿用該 plumbing。
- **暫定門檻值**：barge-in 專用預設門檻未經真兒童錄音驗證，可能偏鬆或偏緊；本切片不追求正確門檻，只驗機制接通，最終值留 A2-3/A2-6。
