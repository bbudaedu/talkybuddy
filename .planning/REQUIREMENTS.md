# Requirements: TalkyBuddy（說說學伴）

**Defined:** 2026-07-18
**Core Value:** 孩子能喊出「說說學伴」，進行一段自然、可即時打斷（barge-in）的口說繁體中文對話，這段對話同時教學並評估其語言能力，且自架串流路徑與即時 Nova Sonic 路徑皆能完整達成。

> **雙模式共存原則**：Path 1（自架串流回合式）與 Path 2（Nova Sonic 即時 S2S）為兩條一等公民路徑，各自獨立成需求。跨切面關注（ASR、喚醒、雲端 LLM、雲端 TTS、隱私、部署）明確標示其作用範圍。刻意的降級鏈（CloudLLM→EdgeLLM→scaffold、ElevenLabs→edge、SenseVoice→whisper）為容錯設計，非衝突。

## v1 Requirements

### ASR — 繁中語音辨識基礎（共用輸入層）

- [ ] **ASR-01**: 以 sherpa-onnx + SenseVoice-Small int8 為主要 ASR，經 OpenCC s2twp 輸出繁體中文（台灣用語）；固定 `ASREngine` 介面（available/transcribe/_ensure_model）與 backend factory
- [ ] **ASR-02**: 當 SenseVoice 不可用時，透過 `ASR_BACKEND` feature flag 降級到 faster-whisper 仍可辨識
- [ ] **ASR-03**: 低信心辨識（conf < 門檻）時回傳友善的 fallback 語句，而非錯誤逐字稿

### WAKE — 喚醒層（依客戶端模式分流）

- [ ] **WAKE-01**: Path 1 回合式客戶端支援 Porcupine 裝置端語音喚醒 + tap-to-toggle 推播，餵入既有單回合 pipeline（WakeController / MicRouter）
- [ ] **WAKE-02**: Path 2 即時客戶端以 sherpa-onnx KWS 喚醒詞「說說學伴」進入 live 模式；告別語（matchFarewell）結束並返回 IDLE（live-wake.js 協調器）
- [ ] **WAKE-03**: 提供 `/api/wake-config` 端點選擇 wake backend，並在喚醒不可用時降級為手動 push-to-talk

### STREAM — Path 1 自架串流全雙工對話

- [ ] **STREAM-01**: Pipecat 整合 spike 驗證（go/no-go）：可託管 batch SenseVoice STT（FunASRSTTService）+ 句級可打斷 sherpa-onnx TTS，於程式化 barge-in 下運作
- [ ] **STREAM-02**: `StreamingTurnManager` 全雙工回合迴圈，含 Silero VAD barge-in 與逐句可打斷 TTS（InterruptibleSynth / ReplySource，Pipecat 1.5.0）
- [ ] **STREAM-03**: `SpeechGate` 作為獨立可調的 barge-in 偵測器（BargeInGate → BargeInDetectedFrame），與回合式 Silero VAD 解耦
- [ ] **STREAM-04**: 透過 Pipecat LocalAudioTransport 將 barge-in 迴圈接上真實麥克風 / 喇叭（run_realwire），非僅罐頭 WAV

### PRIV — 隱私護欄（所有雲端路徑的跨切面前置條件）

- [ ] **PRIV-01**: 音檔絕不持久化；任何上雲前對文字做去識別化（de-identification）
- [ ] **PRIV-02**: 所有雲端路徑（relay / Bedrock / Nova Sonic）皆須通過家長同意閘門（consent gate）與分層 guardrails；遵循 PDPA/COPPA

### LLM — 雲端大腦（Path 1 回合式回覆生成）

- [ ] **LLM-01**: 雲端模式下陪聊 / 導師 LLM 經 Amazon Bedrock Converse 推論，採 EdgeLLM-compatible 契約（CloudLLM.generate → str | None）、8.0s timeout SLO，並優雅降級到本地
- [ ] **LLM-02**: 可改走自架 Anthropic-compatible relay（cloud brain），含 consent gate、去識別化、guardrails 與 cloud→edge→scaffold 降級，維持相同 CloudLLM 契約

### TTS — 雲端情感語音（Path 1 回合式語音輸出）

- [ ] **TTS-01**: 雲端模式將 TTS 導向 ElevenLabs 情感中文語音，靜默降級到 edge Piper；輸出契約 WAV 22050Hz/16-bit/mono（server/cloud_tts.py available()/synth()）

### LIVE — Path 2 即時 Nova Sonic S2S 對話

- [ ] **LIVE-01**: 經新的 `/ws/live` WebSocket 提供 Nova Sonic 全雙工中文 S2S（bidi 協定、AudioWorklet PCM pipeline、transcript 持久化、build_live_system_prompt）
- [ ] **LIVE-02**: 將 Nova Sonic S2S 由 hold-to-talk 升級為 hands-free 全雙工，採 native VAD + 真實 barge-in + 回音消除（AEC）

### TEACH — 自適應教學迴圈

- [ ] **TEACH-01**: 新增 server/lesson.py 選材，改寫 live system prompt 為 coach follow-along 迴圈（B1/B3 教學內容），並於 live 收尾寫回 diagnosis 形成自適應閉環

### PRON — 發音評估（route A）

- [ ] **PRON-01**: 本地聲學發音評分模組 server/pronunciation.py（wav2vec2 phoneme、g2p_en ARPAbet、CTC decode），經 PCM tee buffer 掛入 /ws/live Nova Sonic pipeline 餵給 diagnose；不持久化原始音檔

### DEPLOY — 跨平台雲端部署

- [ ] **DEPLOY-01**: 於雲端 VM 部署，含環境變數、啟動、TLS/WSS reverse proxy、demo seed 帳號
- [ ] **DEPLOY-02**: 以 `TALKYBUDDY_PIPELINE_PROFILE`（edge / cloud）切換 pipeline profile，並支援 edge doll sync（不需改碼）

## v2 Requirements

延後至未來版本，追蹤但不在當前 roadmap。

### Edge Hardware（Genio 520）

- **EDGE-01**: MediaTek Genio 520 NPU 實機部署（ALSA 直接擷取 16kHz mono WAV，移除 ffmpeg / WebM 依賴）
- **EDGE-02**: LLM `n_ctx` 降至 512 並重新驗證 prompts / 測試假設

### Scale & Sync

- **SYNC-01**: 多裝置跨機同步壓力測試（seq / device_id 去重，斷線重連）
- **DASH-01**: 教師儀表板 WebSocket 即時推播（取代 5 秒輪詢）

## Out of Scope

明確排除，記錄以防範圍蔓延。

| Feature | Reason |
|---------|--------|
| Genio 520 NPU 實機部署 | 未來生產目標，尚未部署；本輪僅保留 edge profile 預留 |
| 商業化 / 帳務 / 家長 / 學校帳號系統 | 研究原型階段不需要 |
| espeak-ng-data GPL 殘留清除 | 授權整備工作，非本輪交付範圍（列為技術債） |
| 音訊 / 影像稽核錄製 | 與「音檔不落地」隱私原則衝突，需另行政策討論 |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| ASR-01 | Phase 1 | Pending |
| ASR-02 | Phase 1 | Pending |
| ASR-03 | Phase 1 | Pending |
| WAKE-01 | Phase 1 | Pending |
| WAKE-03 | Phase 1 | Pending |
| STREAM-01 | Phase 2 | Pending |
| STREAM-02 | Phase 2 | Pending |
| STREAM-03 | Phase 2 | Pending |
| STREAM-04 | Phase 2 | Pending |
| PRIV-01 | Phase 3 | Pending |
| PRIV-02 | Phase 3 | Pending |
| LLM-01 | Phase 3 | Pending |
| LLM-02 | Phase 3 | Pending |
| TTS-01 | Phase 3 | Pending |
| LIVE-01 | Phase 4 | Pending |
| LIVE-02 | Phase 4 | Pending |
| WAKE-02 | Phase 4 | Pending |
| TEACH-01 | Phase 5 | Pending |
| PRON-01 | Phase 5 | Pending |
| DEPLOY-01 | Phase 6 | Pending |
| DEPLOY-02 | Phase 6 | Pending |

**Coverage:**
- v1 requirements: 21 total
- Mapped to phases: 21
- Unmapped: 0 ✓

---
*Requirements defined: 2026-07-18*
*Last updated: 2026-07-18 after new-project-from-ingest bootstrap*
