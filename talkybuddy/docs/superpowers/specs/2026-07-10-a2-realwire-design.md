# A2 Real-Wiring Slice — 把 barge-in 迴路接上真實麥克風/喇叭

**日期**：2026-07-10
**分支**：`worktree-a2-realwire`（worktree，基於 `origin/master` 683451a）
**前置**：A2-1 spike＝GO、A2-2 `StreamingTurnManager` 迴路已實作（`server/streaming/`，15 pytest，harness 綠燈但未接真實 I/O）

## 目標

把已在 harness 驗證的 `StreamingTurnManager` barge-in 迴路，接上 Pipecat `LocalAudioTransport`（裸麥/喇叭）＋真 Silero VAD，**端到端跑通一次真的插話**。這是把 A2-2 從「孤島」變「真的會動」的最小垂直切片，並落在已簽核的裝置端 Pipecat in-process 部署拓撲上（非拋棄式）。

**明確不做**：AEC（留 A2-3）、網路狀態機/離線切換（留 A2-0/A2-4）、延遲量測、瀏覽器/WebSocket transport、SpeechGate 接入 manager。

## 架構

裝置端 Pipecat in-process pipeline：

```
LocalAudioTransport.input() ─mic─▶ SileroVAD（Pipecat 內建）
  ─VADUserStarted/StoppedSpeakingFrame─▶ FunASRSTTService（SenseVoiceSmall 批次）
  ─TranscriptionFrame─▶ StreamingTurnManager（reply_source=BatchReplySource）
  ─逐句 TTSSpeakFrame─▶ SherpaInterruptibleTTSService
  ─audio frames─▶ LocalAudioTransport.output() ─▶ 喇叭
```

**Barge-in 機制**：bot 說話期間使用者再次開口 → Pipecat 內建 SileroVAD 發 `VADUserStartedSpeakingFrame` → `StreamingTurnManager` 取消當前 reply task、句界中止 TTS（與現有 harness 同機制，已驗證）。

## 元件與職責

| 元件 | 職責 | 依賴 | 狀態 |
|---|---|---|---|
| `LocalAudioTransport` | 裸麥輸入、喇叭輸出 | Pipecat 內建 | 沿用 |
| `SileroVAD` | 產 VAD started/stopped edges、驅動 barge-in | Pipecat 內建 | 沿用 |
| `FunASRSTTService` | 批次 SenseVoice 轉錄 → TranscriptionFrame | Pipecat 內建、SenseVoiceSmall cache | 沿用（spike 已驗） |
| `StreamingTurnManager` | TranscriptionFrame→逐句 TTSSpeakFrame；barge-in 取消 | `ReplySource` | 現成、不改邏輯 |
| `BatchReplySource` | 邊緣 LLM 產句流；無模型→降級 None | `EdgeLLM`＋scaffold | 現成、不改 |
| `SherpaInterruptibleTTSService` | 逐句可中止合成 | sherpa-onnx | 現成、不改 |
| **`run_realwire.py`** | 組 pipeline＋transport 的可跑 entrypoint | 以上全部 | **新增** |
| **`requirements-streaming.txt`** | 釘 streaming 迴路相依、建 `.venv-streaming` | — | **新增** |
| **`test_realwire_synth.py`** | canned WAV 餵完整 pipeline 的自動化驗收 | canned zh.wav | **新增** |

**brain 免疫縫**：`StreamingTurnManager` 只依賴 `ReplySource` 介面；本切片用 `BatchReplySource`（邊緣 LLM）。日後換 Bedrock FM 大腦僅新增一個 `ReplySource` 實作，本切片架構不動。

## 資料流

1. 麥克風音訊 → SileroVAD 偵測語音段（started→stopped）。
2. stopped edge 觸發 FunASRSTTService 對該段做批次 SenseVoice 轉錄 → `TranscriptionFrame`。
3. `StreamingTurnManager` 收 TranscriptionFrame → 呼 `BatchReplySource.stream()` 逐句取回覆 → 每句 push `TTSSpeakFrame`（downstream）。
4. `SherpaInterruptibleTTSService` 逐句合成 → audio frames → transport output → 喇叭。
5. **Barge-in**：步驟 4 播放期間使用者開口 → SileroVAD 發 `VADUserStartedSpeakingFrame` → manager 取消 reply task、TTS 句界停 → 回步驟 1。

## 驗收（兩者都要）

### 自動化：`test_realwire_synth.py`
把現有 harness 升級為「完整 pipeline」版：以 canned `zh.wav`（16k mono，spike 既有）產生的音訊 frame 餵 **VAD→STT→manager→TTS→sink 全鏈**（非只 manager→TTS），不依賴真音訊裝置。

- **判準 A（有回覆）**：餵一段語音 → sink 收到 ≥1 個合成音訊 frame（且 STT 有非空 TranscriptionFrame）。
- **判準 B（barge-in 淨停）**：在第一次回覆合成期間注入第二次 VADUserStartedSpeakingFrame（模擬插話）→ sink 實際合成句數 ≤2（句界乾淨中止），對照無插話時的完整句數。
- 二元淨判，不量延遲。

### 手動：`run_realwire.py`
跑起來對真麥講話：

- 講一句 → 聽到回覆（二元：有/無）。
- 回覆播到一半插話 → 回覆在句界停下、系統轉去聽新輸入（二元：乾淨停/沒停）。

## 錯誤處理

- **無音訊裝置**：`run_realwire.py` 啟動時 `LocalAudioTransport` 取不到 device → 捕捉並印明確訊息後 exit（非 traceback）。
- **空回覆**（`BatchReplySource` 無 LLM 模型時降級 None）：迴路須優雅處理無句可合成（不 push TTSSpeakFrame、不崩），自動化測試涵蓋此路徑。
- **SenseVoice cache 缺失**：STT 初始化失敗 → entrypoint 明確報「需先備妥 SenseVoiceSmall cache」。
- 斷網/雲端不涉及（純本地）。

## venv 收編

- 新增 `talkybuddy/requirements-streaming.txt`，版本比照 spike venv 實測值：`pipecat-ai[funasr]`、`sherpa-onnx==1.13.4`、`torch`（cpu wheel）、`torchaudio`、`silero-vad`（或 Pipecat 帶入）、`pytest-asyncio`、`numpy`。
- 建立 `talkybuddy/.venv-streaming`（獨立於主 `.venv`）。
- `run_tests.sh` 更新：streaming 測試改跑 `.venv-streaming`（取代目前指向 spike venv）。
- SenseVoiceSmall / sherpa 模型 cache 唯讀沿用既有位置，不重下。

## 檔案落點

- `talkybuddy/requirements-streaming.txt`（新）
- `talkybuddy/server/streaming/run_realwire.py`（新 entrypoint）
- `talkybuddy/server/streaming/tests/test_realwire_synth.py`（新自動化測試）
- `talkybuddy/run_tests.sh`（微調：streaming 指向 `.venv-streaming`）
- 可能微調 `talkybuddy/server/streaming/harness.py`（若自動化測試複用其 pipeline 組裝）

**不動**：`server/pipeline.py`、`server/app.py`、`_process_text`/`run_turn_audio`、`cloud_tts.py`/`config.py`（後者是另一 session 的語速工作，避免衝突）。

## 風險與未知

- **LocalAudioTransport 裸麥接線**：spike 的 `run_spike.py` 已用真 SenseVoice＋playback pacing 跑過近似迴路，但未接 `LocalAudioTransport` 真實裝置 → 首次真麥接線可能有 sample rate / frame size / 裝置權限的實測偏差（entrypoint 需容錯）。
- **無 AEC 下的自體回授**：本切片不做 AEC，真麥外放時 TTS 可能被麥克風收回誤觸發 barge-in。**本切片可用耳機或 PTT-lite 規避**，正式 AEC 留 A2-3。手動驗收時註明測試條件（耳機/外放）。
- **自動化測試的「完整 pipeline」邊界**：若 SileroVAD 在 standalone（無真 transport）注入 frame 下行為與真麥不同，測試改在 STT 之後注入 TranscriptionFrame（退回接近現有 harness 的邊界），並在測試註記此妥協。
