# Roadmap: TalkyBuddy（說說學伴）

## Overview

從共用的繁中「感知層」（ASR + 喚醒）出發，接著平行交付兩條一等公民對話路徑——先建自架串流全雙工回合式對話（Path 1），在導入雲端能力時同步立起隱私護欄與雲端大腦 / 情感語音，再交付即時 Nova Sonic S2S hands-free 對話（Path 2）。兩路徑就緒後，把 B1/B3 教學內容與本地發音評估掛上 live 路徑形成自適應學習閉環，最後完成跨平台雲端 VM 部署。全程遵循「音檔不落地、上雲前去識別化、家長同意」的隱私原則。

## Milestones

- **Milestone 1 — Delivered Baseline** (Phases 1–6): 由 30 份既有設計/計畫文件 ingest 而成，經 2026-07-18 對照 codebase 逐 phase 驗證確認**功能已實作**。4、5 完整交付；1、2、3、6 交付但有已登錄缺口（見下方標記與 STATE.md「Known-Gaps Backlog」）。此 milestone 視為 baseline，不再新開發，缺口以 backlog 追蹤。
- **Milestone 2 — (待規劃)**: 新功能開發（`/gsd-new-milestone`）。

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

### Milestone 1 — Delivered Baseline (verified 2026-07-18)

- [x] **Phase 1: Perception & Wake Foundation** - 繁中 ASR（SenseVoice + OpenCC + whisper fallback）與喚醒層基礎 — 🟢 DELIVERED（🟡 gap: SenseVoice→whisper 為手動 flag，非自動 fallback；by-design）
- [x] **Phase 2: Self-Hosted Streaming Conversation (Path 1)** - 自架全雙工串流回合式對話與 barge-in（真實麥克風 / 喇叭）— 🟡 DELIVERED w/ gap: `run_realwire.py` 漏接 `BargeInGate`，真機 barge-in 不觸發；無實機執行證據
- [x] **Phase 3: Cloud Brain, Emotional Voice & Privacy Guardrails** - 雲端 LLM / relay + ElevenLabs 情感語音，含家長同意與去識別化護欄 — 🟡 DELIVERED w/ gap: cloud-TTS 合成點缺 consent 檢查（cloud-profile 開機可繞過家長同意）；無原生 Bedrock Converse 後端，僅 relay
- [x] **Phase 4: Live Nova Sonic S2S Conversation (Path 2)** - 「說說學伴」喚醒進入 Nova Sonic hands-free 全雙工即時對話 — 🟢 DELIVERED（caveat: AEC 僅瀏覽器原生）
- [x] **Phase 5: Adaptive Teaching Loop & Pronunciation Assessment** - B1/B3 教學串接與本地聲學發音評估（route A 閉環）— 🟢 DELIVERED
- [x] **Phase 6: Cross-Platform Cloud Deployment** - 雲端 VM 部署（TLS/WSS、pipeline profiles、edge doll sync）— 🟡 DELIVERED w/ gap: TLS/WSS reverse proxy 僅文件化，無提交的 proxy 設定 / VM 實跑驗證

## Phase Details

### Phase 1: Perception & Wake Foundation
**Goal**: 說說學伴能可靠地被喚醒，並準確地把孩子的繁體中文口語轉成文字——這是兩條路徑共用的輸入基礎。
**Depends on**: Nothing (first phase)
**Requirements**: ASR-01, ASR-02, ASR-03, WAKE-01, WAKE-03
**Success Criteria** (what must be TRUE):
  1. 使用者喊喚醒詞或 tap-to-toggle 後，回合式客戶端開始聆聽
  2. 口說國語被辨識為繁體中文（台灣用語）文字（SenseVoice-Small + OpenCC s2twp）
  3. 當 SenseVoice 不可用時，系統透明降級到 faster-whisper 仍能辨識
  4. 低信心音訊回傳友善提示語，而非錯誤逐字稿
**Plans**: TBD
**UI hint**: yes

### Phase 2: Self-Hosted Streaming Conversation (Path 1)
**Goal**: 完全自架的全雙工回合迴圈，讓孩子能在說說學伴講話中途即時打斷（barge-in），並跑在真實麥克風 / 喇叭上。
**Depends on**: Phase 1
**Requirements**: STREAM-01, STREAM-02, STREAM-03, STREAM-04
**Success Criteria** (what must be TRUE):
  1. 說說學伴逐句朗讀回覆，且孩子一開口就立即停止（barge-in）
  2. barge-in 靈敏度可獨立於回合式 VAD 調整
  3. 迴圈在真實麥克風與喇叭上端到端運作（非僅罐頭 WAV）
  4. STT（SenseVoice via FunASR）與可打斷 sherpa-onnx TTS 於 Pipecat pipeline 內運作，不依賴雲端
**Plans**: TBD

### Phase 3: Cloud Brain, Emotional Voice & Privacy Guardrails
**Goal**: 雲端模式下，回合式說說學伴能產出更豐富的回覆與情感中文語音，且全程受家長同意與去識別化護欄約束。
**Depends on**: Phase 2
**Requirements**: PRIV-01, PRIV-02, LLM-01, LLM-02, TTS-01
**Success Criteria** (what must be TRUE):
  1. 取得同意後，回覆經雲端（Bedrock Converse 或自架 relay）生成，並於失敗時降級到 edge LLM 再到 scaffold
  2. 雲端模式語音輸出使用 ElevenLabs 情感中文 TTS，並可靜默降級到 edge TTS
  3. 未取得家長同意即不進行任何雲端呼叫；音檔絕不持久化，文字上雲前先去識別化
  4. 操作者可在 Bedrock 與 relay 兩種回覆後端間切換，而不改變 pipeline 契約
**Plans**: TBD

### Phase 4: Live Nova Sonic S2S Conversation (Path 2)
**Goal**: 孩子能喊「說說學伴」進入 hands-free 全雙工口說對話，並支援原生 barge-in。
**Depends on**: Phase 3
**Requirements**: LIVE-01, LIVE-02, WAKE-02
**Success Criteria** (what must be TRUE):
  1. 喊出喚醒詞「說說學伴」進入 live 模式；告別語結束對話並返回 IDLE
  2. 孩子與說說學伴透過 `/ws/live` 免持雙向對話，採 Nova Sonic native VAD 與真實 barge-in
  3. 回音消除（AEC）避免說說學伴的語音重新觸發自己
  4. live 對話逐字稿被持久化以供後續評估
**Plans**: TBD
**UI hint**: yes

### Phase 5: Adaptive Teaching Loop & Pronunciation Assessment
**Goal**: live 對話依教材內容進行，並評估孩子的發音，形成自適應學習閉環。
**Depends on**: Phase 4
**Requirements**: TEACH-01, PRON-01
**Success Criteria** (what must be TRUE):
  1. live 說說學伴依所選 B1/B3 教材，執行 coach-style follow-along 迴圈
  2. 對話收尾時寫回 diagnosis，據以調整下一次 session 的教材
  3. 孩子的口說（英語）被聲學評分（phoneme 級），並餵入 diagnosis
  4. 發音評分在 `/ws/live` PCM 串流上於本地運算，不持久化原始音檔
**Plans**: TBD

### Phase 6: Cross-Platform Cloud Deployment
**Goal**: 系統能在雲端 VM 上以安全 WSS 運行，並可切換 edge / cloud pipeline profile。
**Depends on**: Phase 5
**Requirements**: DEPLOY-01, DEPLOY-02
**Success Criteria** (what must be TRUE):
  1. 伺服器可透過雲端 VM 上的 reverse proxy 以 TLS/WSS 連線
  2. `TALKYBUDDY_PIPELINE_PROFILE` 可切換 edge 與 cloud 行為，且不需改碼
  3. demo seed 帳號與 edge-doll sync 能對部署後端運作
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Perception & Wake Foundation | baseline | Delivered (gap logged) | 2026-07-18 (verified) |
| 2. Self-Hosted Streaming Conversation (Path 1) | baseline | Delivered w/ gap | 2026-07-18 (verified) |
| 3. Cloud Brain, Emotional Voice & Privacy Guardrails | baseline | Delivered w/ gap | 2026-07-18 (verified) |
| 4. Live Nova Sonic S2S Conversation (Path 2) | baseline | Delivered | 2026-07-18 (verified) |
| 5. Adaptive Teaching Loop & Pronunciation Assessment | baseline | Delivered | 2026-07-18 (verified) |
| 6. Cross-Platform Cloud Deployment | baseline | Delivered w/ gap | 2026-07-18 (verified) |
