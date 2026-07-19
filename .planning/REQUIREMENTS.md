# Requirements: TalkyBuddy（說說學伴）

**Core Value:** 孩子能喊「說說學伴」，進行一段自然、可 barge-in 的口說繁中對話——同時教學並評估——且自架串流與即時 Nova Sonic 兩路徑皆能達成。

---

# Milestone 2 — Genio 520 決賽 Edge MVP（Active）

**Defined:** 2026-07-19
**Goal:** 在 MediaTek Genio 520（Hti hub G520，MT8371 / MDLA 5.3 / Android 14 / 4GB）上跑出決賽可上台的**離線**「聽 ASR → 想 LLM → 說 TTS」中英雙語鷹架帶讀 MVP——NPU 管感知、CPU 管生成、現場斷網橋段，並以雲端教師閉環加值。約剩 12 天（決賽 ≈2026-07-30）。**原則：POC 過關且驚豔優先，效能/品質優化列下一步。**

> **優先序原則**：demo 存亡關鍵（離線迴路 + 斷網橋段）排最前並先用已驗證的 CPU 引擎；NPU 加速與 Nova Sonic 為 time-boxed 加值、含 stop-loss，落後時 Nova Sonic 第一個可犧牲。研究來源見 `.planning/research/SUMMARY.md`。

## v2 Requirements

### ELOOP — 邊緣離線對話迴路（CPU-first，存亡關鍵）

- [ ] **ELOOP-01**: 裝置端 FastAPI 以 `TALKYBUDDY_PIPELINE_PROFILE=edge` 跑完整**離線** 聽ASR→想LLM→說TTS 迴路，全 CPU 引擎（SenseVoice int8 + llama.cpp Qwen2.5-1.5B Q4 + sherpa-onnx TTS），不依賴雲端
- [ ] **ELOOP-02**: llama.cpp 以 native binary over localhost 生成（非 llama-cpp-python wheel），build flag `-march=armv8.2-a+dotprod+i8mm`；離線真生成中英雙語鷹架帶讀（follow-along）簡短回覆
- [ ] **ELOOP-03**: on-device 首字延遲 / 每回合延遲實測，訂出舞台可接受延遲 go/no-go 門檻（硬體實測，非假設）
- [ ] **ELOOP-04**: 4GB 記憶體驗證閘——三引擎鏈於真機同時載入的峰值 < 4GB 並留 headroom（含 `n_ctx` 收斂）

### EDGE — 裝置 runtime + 部署管線

- [x] **EDGE-01**: Day-0 零硬體風險整備——`n_ctx` 改 config-driven（edge=512）、移除 ffmpeg/WebM 子行程轉檔，改 ALSA 直接擷取 16kHz mono（退既有技術債）
- [ ] **EDGE-02**: Board bring-up spike——嘗試燒官方 IoT Yocto v25.1（Genio Tools v1.7+）到 Hti G520 第三方載板；~2 天內未過則 fallback Android 14 並記錄新增成本（Java/NDK shim）——go/no-go 決策點
- [ ] **EDGE-03**: adb-based 部署管線（build → push → run on-device），可分攤工作丟 NB
- [ ] **EDGE-04**: edge 產物集中於頂層 `edge/`（`edge/deploy`、`edge/models`、`edge/runtime`）+ 部署文件 `docs/DEPLOY_EDGE.md`（對稱 `docs/DEPLOY_CLOUD.md`）

### NETCUT — 現場斷網橋段（demo 勝負手）

- [ ] **NETCUT-01**: 主持人手動 kill-switch 為主要斷網機制（非僅依賴自動網路偵測）；切斷雲端 uplink 時裝置持續離線對話（瀏覽器↔本機 server loopback 不受影響）
- [ ] **NETCUT-02**: 縮短 / race 雲端 timeout 並暫停背景輪詢，避免斷網時多秒靜默 hang；提供可見的「offline mode」切換 UI / badge
- [ ] **NETCUT-03**: 實體斷網彩排腳本（重複實機演練，非只自動偵測）

### NPU — NPU 加速感知（加值、time-boxed、stop-loss）

- [ ] **NPU-01**: spike 定案 NPU 路徑——ORT-NeuronEP（Genio 520 可能預設開啟 MDLA）vs TFLite 轉檔（NP8 Converter 公版 → Neuron Stable Delegate）；1–2 天內決策，排除 NDA-gated 路徑（ncc-tflite/DLA、GAI Toolkit）
- [ ] **NPU-02**: ASR（SenseVoice）經 NPU delegate 加速，含 per-op 放置 logging；算子不支援時退 CPU，**不得靜默偽成功**
- [ ] **NPU-03**: 中文 INT8 品質閘——以真實繁中決賽腳本音訊 + 母語聽測驗證 ASR/TTS 品質，防「淪為音箱」

### TCLOUD — 雲端非同步教師閉環（連網加值，多為既有復用）

- [ ] **TCLOUD-01**: edge→cloud 機會式上傳端點：只上傳衍生文字 / 分數（音檔不出裝置）；補上 `sync_client.push_pending()` 目前漏接的 `guardrails.deidentify()` 與 `consent_granted()`（順帶收斂 G1 consent 缺口）
- [ ] **TCLOUD-02**: 雲端 4 維診斷經 direct `boto3 bedrock-runtime.converse()`（不走 Hermes Agent，依 2026-07-04 內部架構評審）→ 教師儀表板

### NOVA — Nova Sonic 連網 S2S（最低優先，落後先砍）

- [ ] **NOVA-01**: 連網時 Nova Sonic S2S 可於 demo 環境展示（staging + 最終彩排）；時間不足時為第一個可犧牲項

## Out of Scope（v2）

明確排除，記錄以防範圍蔓延。

| Feature | Reason |
|---------|--------|
| on-device 音素級發音評分 | 沿用 28 天 MVP 砍除清單；改 LLM 整體評語或退雲端 |
| NPU TTS 加速 | P2；ASR-on-NPU 先落地，TTS 時間有餘再做，不足則 CPU |
| GAI Toolkit / ncc-tflite DLA 路徑（LLM 上 NPU） | NDA-gated；且 Genio 520 on-device LLM via LiteRT 官方要 Q2 2026 |
| 三源 RAG、雙雲 LLM | 12 天內收斂風險 |
| 裝置端多用戶、多裝置同步 | 決賽單機單使用者情境 |
| 教師儀表板即時推播 | 現況 5 秒輪詢可接受 |
| 自建 OS | 只用官方 Yocto BSP 映像 |

## Traceability（M2）

由 roadmapper 於 2026-07-19 建立 roadmap 時填入；對應 `.planning/ROADMAP.md` Phase 7–12。

| Requirement | Phase | Status |
|-------------|-------|--------|
| EDGE-01 | Phase 7 | Complete |
| EDGE-02 | Phase 7 | Pending |
| EDGE-03 | Phase 7 | Pending |
| EDGE-04 | Phase 7 | Pending |
| ELOOP-01 | Phase 8 | Pending |
| ELOOP-02 | Phase 8 | Pending |
| ELOOP-03 | Phase 8 | Pending |
| ELOOP-04 | Phase 8 | Pending |
| NETCUT-01 | Phase 9 | Pending |
| NETCUT-02 | Phase 9 | Pending |
| NETCUT-03 | Phase 9 | Pending |
| NPU-01 | Phase 10 | Pending |
| NPU-02 | Phase 10 | Pending |
| NPU-03 | Phase 10 | Pending |
| TCLOUD-01 | Phase 11 | Pending |
| TCLOUD-02 | Phase 11 | Pending |
| NOVA-01 | Phase 12 | Pending |

**Coverage:**

- v2 requirements: 17 total
- Mapped to phases: 17/17 ✓ (no orphans)

---

# Milestone 1 — Delivered Baseline（歷史記錄，verified 2026-07-18）

> 由 30 份既有設計/計畫文件 ingest 而成，經 2026-07-18 對照 codebase 逐 phase 驗證確認功能已實作（Phases 1–6）。缺口見 STATE.md「Known-Gaps Backlog」。此段保留為交付記錄，不再新開發。

**Core Value:** 孩子能喊出「說說學伴」，進行一段自然、可即時打斷（barge-in）的口說繁體中文對話，這段對話同時教學並評估其語言能力，且自架串流路徑與即時 Nova Sonic 路徑皆能完整達成。

## v1 Requirements

### ASR — 繁中語音辨識基礎（共用輸入層）

- [x] **ASR-01**: 以 sherpa-onnx + SenseVoice-Small int8 為主要 ASR，經 OpenCC s2twp 輸出繁體中文（台灣用語）；固定 `ASREngine` 介面與 backend factory
- [x] **ASR-02**: 當 SenseVoice 不可用時，透過 `ASR_BACKEND` feature flag 降級到 faster-whisper 仍可辨識
- [x] **ASR-03**: 低信心辨識時回傳友善 fallback 語句，而非錯誤逐字稿

### WAKE — 喚醒層（依客戶端模式分流）

- [x] **WAKE-01**: Path 1 回合式客戶端支援 Porcupine 裝置端語音喚醒 + tap-to-toggle
- [x] **WAKE-02**: Path 2 即時客戶端以 sherpa-onnx KWS「說說學伴」進入 live 模式；告別語結束返回 IDLE
- [x] **WAKE-03**: `/api/wake-config` 選擇 wake backend，喚醒不可用時降級手動 push-to-talk

### STREAM — Path 1 自架串流全雙工對話

- [x] **STREAM-01**: Pipecat 整合 spike（go/no-go）：batch SenseVoice STT + 句級可打斷 sherpa-onnx TTS
- [x] **STREAM-02**: `StreamingTurnManager` 全雙工回合迴圈（Silero VAD barge-in + 逐句可打斷 TTS）
- [x] **STREAM-03**: `SpeechGate` 獨立可調 barge-in 偵測器
- [x] **STREAM-04**: 經 Pipecat LocalAudioTransport 接真實麥克風 / 喇叭（run_realwire）

### PRIV — 隱私護欄（所有雲端路徑跨切面前置）

- [x] **PRIV-01**: 音檔絕不持久化；上雲前對文字去識別化
- [x] **PRIV-02**: 所有雲端路徑通過家長同意閘門 + 分層 guardrails（PDPA/COPPA）

### LLM — 雲端大腦（Path 1）

- [x] **LLM-01**: 雲端 LLM 經 Bedrock Converse 推論，EdgeLLM-compatible 契約、8.0s timeout SLO、優雅降級
- [x] **LLM-02**: 可改走自架 Anthropic-compatible relay（含 consent / 去識別化 / guardrails / cloud→edge→scaffold 降級）

### TTS — 雲端情感語音（Path 1）

- [x] **TTS-01**: 雲端 TTS 導向 ElevenLabs 情感中文語音，靜默降級 edge Piper；WAV 22050Hz/16-bit/mono

### LIVE — Path 2 即時 Nova Sonic S2S

- [x] **LIVE-01**: `/ws/live` 提供 Nova Sonic 全雙工中文 S2S（bidi、AudioWorklet PCM、transcript 持久化）
- [x] **LIVE-02**: Nova Sonic 由 hold-to-talk 升級 hands-free 全雙工（native VAD + barge-in + AEC）

### TEACH / PRON — 教學迴圈與發音評估

- [x] **TEACH-01**: lesson.py 選材 + live coach follow-along 迴圈（B1/B3），收尾寫回 diagnosis
- [x] **PRON-01**: 本地聲學發音評分（wav2vec2 phoneme）掛 /ws/live PCM tee buffer，不持久化原始音檔

### DEPLOY — 跨平台雲端部署

- [x] **DEPLOY-01**: 雲端 VM 部署（env、啟動、TLS/WSS reverse proxy、demo seed 帳號）— 🟡 proxy 僅文件化
- [x] **DEPLOY-02**: `TALKYBUDDY_PIPELINE_PROFILE`（edge/cloud）切換 + edge doll sync

---
*Requirements defined: M1 2026-07-18；M2 2026-07-19*
*Last updated: 2026-07-19 — Milestone 2 roadmap 建立完成（Phase 7–12），Traceability 100% 覆蓋（17/17）*
