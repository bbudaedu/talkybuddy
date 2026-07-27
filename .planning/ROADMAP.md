# Roadmap: TalkyBuddy（說說學伴）

## Overview

從共用的繁中「感知層」（ASR + 喚醒）出發，接著平行交付兩條一等公民對話路徑——先建自架串流全雙工回合式對話（Path 1），在導入雲端能力時同步立起隱私護欄與雲端大腦 / 情感語音，再交付即時 Nova Sonic S2S hands-free 對話（Path 2）。兩路徑就緒後，把 B1/B3 教學內容與本地發音評估掛上 live 路徑形成自適應學習閉環，最後完成跨平台雲端 VM 部署。全程遵循「音檔不落地、上雲前去識別化、家長同意」的隱私原則。

Milestone 2（Genio 520 決賽 Edge MVP）在既有雲端/PC 原型上新增一條**邊緣離線**路徑：先於 PC 完成零硬體風險的技術債整備與板卡到手 spike（Phase 7），接著用已驗證的 CPU 引擎跑出離線聽→想→說迴圈作為存亡關鍵（Phase 8），再做斷網橋段的話劇化硬化（Phase 9）；NPU 加速感知（Phase 10）與雲端教師閉環（Phase 11）為可平行、可獨立交付的加值軌道，Nova Sonic 連網 staging（Phase 12）殿後，且為進度落後時第一個可犧牲項。

## Milestones

- **Milestone 1 — Delivered Baseline** (Phases 1–6): 由 30 份既有設計/計畫文件 ingest 而成，經 2026-07-18 對照 codebase 逐 phase 驗證確認**功能已實作**。4、5 完整交付；1、2、3、6 交付但有已登錄缺口（見下方標記與 STATE.md「Known-Gaps Backlog」）。此 milestone 視為 baseline，不再新開發，缺口以 backlog 追蹤。
- **Milestone 2 — Genio 520 決賽 Edge MVP** (Phases 7–12): 12 天衝刺（決賽 ≈2026-07-30），交付邊緣離線 MVP + NPU 感知加速 + 現場斷網橋段 + 雲端教師閉環；Nova Sonic 連網 staging 為最低優先、進度落後時第一個可犧牲。詳見下方 Phase Details。

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)
- Milestone 2 continues the sequence from Milestone 1 (Phase 7 onward); numbering is not reset per milestone.

### Milestone 1 — Delivered Baseline (verified 2026-07-18)

- [x] **Phase 1: Perception & Wake Foundation** - 繁中 ASR（SenseVoice + OpenCC + whisper fallback）與喚醒層基礎 — 🟢 DELIVERED（🟡 gap: SenseVoice→whisper 為手動 flag，非自動 fallback；by-design）
- [x] **Phase 2: Self-Hosted Streaming Conversation (Path 1)** - 自架全雙工串流回合式對話與 barge-in（真實麥克風 / 喇叭）— 🟡 DELIVERED w/ gap: `run_realwire.py` 漏接 `BargeInGate`，真機 barge-in 不觸發；無實機執行證據
- [x] **Phase 3: Cloud Brain, Emotional Voice & Privacy Guardrails** - 雲端 LLM / relay + ElevenLabs 情感語音，含家長同意與去識別化護欄 — 🟡 DELIVERED w/ gap: cloud-TTS 合成點缺 consent 檢查（cloud-profile 開機可繞過家長同意）；無原生 Bedrock Converse 後端，僅 relay
- [x] **Phase 4: Live Nova Sonic S2S Conversation (Path 2)** - 「說說學伴」喚醒進入 Nova Sonic hands-free 全雙工即時對話 — 🟢 DELIVERED（caveat: AEC 僅瀏覽器原生）
- [x] **Phase 5: Adaptive Teaching Loop & Pronunciation Assessment** - B1/B3 教學串接與本地聲學發音評估（route A 閉環）— 🟢 DELIVERED
- [x] **Phase 6: Cross-Platform Cloud Deployment** - 雲端 VM 部署（TLS/WSS、pipeline profiles、edge doll sync）— 🟡 DELIVERED w/ gap: TLS/WSS reverse proxy 僅文件化，無提交的 proxy 設定 / VM 實跑驗證

### Milestone 2 — Genio 520 決賽 Edge MVP (in progress)

- [x] **Phase 7: Day-0 Config Hardening & Board Bring-Up Spike** - 結清 n_ctx/ffmpeg 等技術債、立起 edge/ 骨架與 adb 部署管線，並對 Yocto vs Android 14 做出 go/no-go 決策
- [x] **Phase 8: CPU-Only Offline Edge Turn Loop** - 全 CPU 引擎在真機跑出完整離線聽→想→說中英雙語鷹架帶讀迴圈（決賽存亡關鍵）
- [x] **Phase 9: Network-Cut Demo Hardening** - 主持人手動斷網後裝置持續離線對話，無多秒靜默 hang (completed 2026-07-25)
- [ ] **Phase 10: NPU-Accelerated Perception** - ASR 經 NPU delegate 加速並通過繁中品質閘，含停損機制可退回 CPU 基線
- [ ] **Phase 11: Cloud Teacher Closed-Loop** - 邊緣衍生文字/分數機會式同步上雲，經 direct Bedrock Converse 產出診斷並顯示於教師儀表板
- [ ] **Phase 12: Nova Sonic Online Staging & Final Rehearsal** - Nova Sonic 連網 S2S staging 作為斷網橋段前導，含完整彩排與備援影片（最低優先，落後先砍）

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

### Phase 7: Day-0 Config Hardening & Board Bring-Up Spike

**Goal**: Day-0 零硬體風險的技術債與 config 已結清（`n_ctx` config-driven、移除 ffmpeg 轉檔依賴），`edge/` 頂層骨架與 adb 部署管線已就緒，且已對 Hti G520 板卡的作業系統路徑（官方 Yocto BSP vs fallback Android 14）做出有日期的 go/no-go 決策——讓後續所有邊緣工作有穩定地基可以站立。
**Depends on**: Nothing new — 建立於 Milestone 1 既有基線之上（M2 第一個 phase）
**Requirements**: EDGE-01, EDGE-02, EDGE-03, EDGE-04
**Success Criteria** (what must be TRUE):

  1. `LLM_N_CTX` 已改為 profile-driven 設定（edge=512），不再是 `llm.py` 內硬編的 1024
  2. `pipeline.py` 具備 RIFF-sniff fast path：原生 WAV（ALSA 擷取）輸入不再呼叫 ffmpeg 子行程轉檔
  3. 頂層 `edge/`（`edge/deploy`、`edge/models`、`edge/runtime`）資料夾骨架與對稱 `docs/DEPLOY_EDGE.md` 已建立並可被後續 phase 直接使用
  4. adb build → push → run 部署迴圈已在板卡（Android 14 或已燒錄的 Yocto 映像）上完整跑過一次
  5. 已產出一份有日期的 go/no-go 決策紀錄：Yocto BSP 燒錄是否成功、後續走 Yocto 或 fallback Android 14（含新增成本，如 Java/NDK shim）

**Plans**: 3/3 plans executed
**Wave 1**

- [x] 07-01-PLAN.md — Config 退債（EDGE-01）：LLM_N_CTX profile-driven（edge=512）+ pipeline RIFF-sniff WAV fast path（Wave 1）
- [x] 07-02-PLAN.md — edge/ 骨架 + adb 部署腳本 + docs/DEPLOY_EDGE.md（EDGE-03、EDGE-04，Wave 1）

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 07-03-PLAN.md — Board bring-up spike：2026-07-25 Yocto 燒錄成功 GO、SSH/rsync 部署迴圈跑通 health check pass、有日期 go/no-go 決策紀錄（EDGE-02、EDGE-03，Wave 2）— 見 `edge/BOARD_BRINGUP_DECISION.md`

### Phase 8: CPU-Only Offline Edge Turn Loop

**Goal**: 在 Genio 520 真機上，全 CPU 引擎（不倚賴 NPU）即可離線跑完一次完整聽ASR→想LLM→說TTS 的中英雙語鷹架帶讀對話，且速度落在舞台可接受範圍內——這是決賽全案存亡的關鍵一步，若淪為音箱則全案失敗。
**Depends on**: Phase 7
**Requirements**: ELOOP-01, ELOOP-02, ELOOP-03, ELOOP-04
**Success Criteria** (what must be TRUE):

  1. 在真機以 `TALKYBUDDY_PIPELINE_PROFILE=edge` 完成一次完整聽→想→說迴圈，全程零雲端網路呼叫（經封包/log 稽核驗證，非僅程式碼審閱）
  2. llama.cpp native binary（`-march=armv8.2-a+dotprod+i8mm`，非 `llama-cpp-python`）離線生成非樣板的中英雙語鷹架回覆，可被現場觀眾感知為「即時生成」而非預錄
  3. on-device 首字延遲與每回合延遲已實測，並訂出舞台可接受的 go/no-go 門檻（硬體實測數字，非假設）
  4. 三引擎鏈（ASR + LLM + TTS）於真機同時載入之峰值記憶體 < 4GB 並留有 headroom（含 `n_ctx` 收斂後的實測數字）

**Plans**: 5/5 plans executed
**Wave 1**

- [x] 08-01-PLAN.md — config llama-server 設定 + run_llama_server.py argv builder（ELOOP-02）
- [x] 08-03-PLAN.md — 裝置端 ALSA audio_io + local_client 離線對話 client（ELOOP-01）

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 08-02-PLAN.md — EdgeLLM 改 stdlib urllib HTTP client 打 llama-server（ELOOP-02）

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 08-04-PLAN.md — llama.cpp 交叉編譯 + binary/GGUF 部署 + run_edge.sh 接線 llama-server/health-gating（ELOOP-02）

**Wave 4** *(blocked on Wave 3 completion)*

- [x] 08-05-PLAN.md — 真機延遲 go/no-go + 跨行程記憶體峰值 + 零雲端稽核 + 綁定驗證（ELOOP-01/03/04）：A（延遲）GO 穩態 2.96–2.99s／冷啟動暖身後 5.85s 仍 NO-GO（根因已查明，殘餘缺口列 Phase 9 前待辦）；B（記憶體）PASS ≈2723MB／33.5% 餘裕；C（零雲端）PASS；D（綁定）PASS

### Phase 9: Network-Cut Demo Hardening

**Goal**: 現場主持人可隨時手動切斷裝置的雲端連線，孩子與說說學伴的對話完全不受影響地持續離線進行，且不會出現多秒靜默 hang——這是決賽創意與可行性評分最高槓桿的記憶點。
**Depends on**: Phase 8
**Requirements**: NETCUT-01, NETCUT-02, NETCUT-03
**Success Criteria** (what must be TRUE):

  1. 主持人可用手動 kill-switch 切斷雲端 uplink 作為主要斷網機制；瀏覽器↔本機 server 的 loopback 不受影響，裝置持續離線對話
  2. 雲端呼叫 timeout 已縮短 / race，且已具備主動網路偵測，斷網瞬間不會出現多秒靜默 hang；背景輪詢於離線視窗暫停
  3. UI 提供明確可見的 online/offline 狀態切換（badge），讓觀眾能親眼確認離線宣稱
  4. 斷網彩排腳本已完成 ≥3 次實體斷網重複演練（含講話中途斷網），且每次恢復時間 <1–2 秒

**Plans**: 4/4 plans complete

Plans:

- [x] 09-01-PLAN.md — 每回合再同步 conn_pipe.network_mode（kill-switch 對進行中連線生效）＋ /api/network_mode JWT 閘門
- [x] 09-02-PLAN.md — 只縮短雲端專屬內層逾時（LLM/TTS 1.5s），LLM_TIMEOUT_S 保持 8.0；背景診斷出境側通道加 network_mode 閘門
- [x] 09-03-PLAN.md — modeBadge 舞台可辨識強化（padding/色點/一次性 pulse）＋ 斷網敘事 toast 文案
- [x] 09-04-PLAN.md — 斷網彩排腳本（M1/M2 操作定義、兩種演練型態、結果表）＋ 裝置端量測工具

**UI hint**: yes

**Planner reconciliations**（規劃期調和，執行前請確認）：

- 成功條件 #2 的「已具備主動網路偵測」由 09-CONTEXT.md D-02（不加自動偵測安全網）取代：kill-switch 為純軟體 toggle，`pipeline.network_mode` 已是確定性的雲端呼叫閘門，無可偵測之物。本 phase 不建立偵測子系統。
- D-03 點名的三個逾時常數中，`server/pipeline.py::LLM_TIMEOUT_S` **不縮短**（維持 8.0）：它是 cloud/edge 共用的外層包裝，Phase 8 真機實測 edge LLM 單階段可達 4170ms，縮短它會讓每次離線回覆退化成 scaffold。D-03 的意圖由兩個雲端專屬內層逾時（1.5s）達成。

### Phase 10: NPU-Accelerated Perception

**Goal**: 在不威脅 Phase 8 既有 CPU 基線的前提下，語音感知（至少 ASR）真正加速跑在 Genio 520 的 NPU 上、有紀錄可證非靜默偽成功，且繁中辨識/合成品質通過母語聽測——讓「國產晶片加速」從口號變成可驗證的事實。此 phase 為加值、time-boxed，含明確停損點，CPU 保底路徑全程可用。
**Depends on**: Phase 8（加值層，不阻擋、可與 Phase 9、11 平行進行）
**Requirements**: NPU-01, NPU-02, NPU-03
**Success Criteria** (what must be TRUE):

  1. 已完成 1–2 天 spike 並產出書面決策（ADR）：ORT-NeuronEP vs TFLite 轉檔（NP8 Converter 公版 → Neuron Stable Delegate），排除 NDA-gated 路徑（ncc-tflite/DLA、GAI Toolkit）
  2. ASR（SenseVoice）經 NPU delegate 加速，並附 per-op 放置 logging，可證明真實 NPU op 執行比例（而非「跑了就當作成功」）
  3. 算子不支援時自動退 CPU，且此 fallback 可在 log/HUD 被觀察到，不得靜默偽成功
  4. 以真實繁中決賽腳本音訊完成母語聽測 A/B（FP32 vs INT8），品質達到「有感但可上台」的驗收門檻，簽核後此 phase 才視為完成
  5. **停損點**：若中途檢查點未能展示可運作的 NPU 加速，直接以 Phase 8 的 CPU-only 基線作為完整可展示的 demo 收尾，不影響其他 phase 的交付

**Plans**: 4/6 executed — 2026-07-26 觸發停損，2026-07-27 經使用者授權重開

- [x] 10-01-PLAN.md — `inspect_model` / `fix_shape` 診斷工具（NPU-01）
- [x] 10-02-PLAN.md — `server/npu_placement.py` EP placement log parser + fd 層 stderr 擷取（NPU-02）
- [x] 10-03-PLAN.md — raw `NeuronExecutionProvider` Day-1 probe（NPU-01/02）
- [x] 10-04-PLAN.md — 真機 Day-1 判定：`DAY1_NPU_PROBE: FAIL 0/0 ops`（兩輪重現），依 D-02 觸發停損並簽核 ADR
- [ ] 10-05-PLAN.md — `server/asr_npu.py` NPU ASR wiring（gate 曾為「不執行」，2026-07-27 改為待二分診斷結果）
- [ ] 10-06-PLAN.md — NPU-03 FP32 vs INT8 繁中品質閘（同上）

**重開紀錄（2026-07-27）**：停損只測過 `model.int8.fixed.onnx`，該模型含 281 個 `DynamicQuantizeLinear` + 281 個 `MatMulInteger`（NPU delegate 典型不支援算子），因此「環境不可用」與「此模型算子不支援」在 Day-1 證據下無法區分。已備妥 toy 二分診斷（`edge/npu_spike/make_toy_model.py`）與 FP32 SenseVoice（零量化算子）作為下一步；PASS 條件維持 per-op placement NPU ops > 0，不放寬。詳見 `edge/npu_spike/ADR-npu-path.md` §7 與 `edge/npu_spike/REOPEN-RUNBOOK.md`。**真機驗證尚未執行**（Tailscale 已登出，裝置不可達）。

### Phase 11: Cloud Teacher Closed-Loop

**Goal**: 孩子完成一次邊緣對話後，衍生文字與分數能在裝置重新連網時機會式同步上雲，經雲端 LLM 產出四維診斷並顯示在既有教師儀表板上，讓「邊緣對話 → 教師洞察」的敘事閉環成立，同時收斂既有 G1 consent 缺口。
**Depends on**: Phase 8（可與 Phase 9、10 平行進行）
**Requirements**: TCLOUD-01, TCLOUD-02
**Success Criteria** (what must be TRUE):

  1. `sync_client.push_pending()` 上傳前已補上 `guardrails.deidentify()` 與 `guardrails.consent_granted()` 閘門（收斂 G1 缺口）；只上傳衍生文字/分數，音檔絕不出裝置
  2. 裝置重新連網後，`/api/sync` 機會式上傳成功，不需人工介入
  3. `diagnose.py` 經 direct `boto3 bedrock-runtime.converse()` 產出四維診斷（不走 Hermes Agent，依 2026-07-04 內部架構評審）
  4. 既有教師儀表板（5 秒輪詢，維持不變）顯示源自邊緣 session 的真實（非 mock）診斷資料

**Plans**: TBD

### Phase 12: Nova Sonic Online Staging & Final Rehearsal

**Goal**: 在決賽現場，Nova Sonic 連網 S2S 已完成 staging，可作為斷網橋段前「連網」半場的可靠演出；此為本 milestone 最低優先項目，若進度落後為第一個可整體犧牲者，且被砍不影響核心離線迴路與斷網橋段的可展示性。
**Depends on**: Phase 9（彩排需涵蓋已硬化的斷網橋段）
**Requirements**: NOVA-01
**Success Criteria** (what must be TRUE):

  1. Nova Sonic S2S 可在 demo 網路環境下連線展示，作為斷網橋段的「連網」前導橋段
  2. 已完成至少兩次含斷網橋段的完整端到端彩排
  3. 已錄製 60–90 秒備援 demo 影片，作為現場單點失效的備援
  4. **書面中途停損（cutline）**：Nova Sonic staging 為進度落後時第一個可犧牲項目；若 Phase 8/9 尚未穩定，本 phase 可整體砍除而不影響前述 phase 的交付與決賽可上台性

**Plans**: TBD

## Progress

**Execution Order:**

- Milestone 1（已交付基線）：1 → 2 → 3 → 4 → 5 → 6
- Milestone 2（本輪衝刺）：7 → 8 →（9、10、11 可平行推進；10、11 為加值/可犧牲軌道，不阻擋其他 phase）→ 12（依賴 9；本身為最低優先、進度落後時第一個可犧牲）

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Perception & Wake Foundation | baseline | Delivered (gap logged) | 2026-07-18 (verified) |
| 2. Self-Hosted Streaming Conversation (Path 1) | baseline | Delivered w/ gap | 2026-07-18 (verified) |
| 3. Cloud Brain, Emotional Voice & Privacy Guardrails | baseline | Delivered w/ gap | 2026-07-18 (verified) |
| 4. Live Nova Sonic S2S Conversation (Path 2) | baseline | Delivered | 2026-07-18 (verified) |
| 5. Adaptive Teaching Loop & Pronunciation Assessment | baseline | Delivered | 2026-07-18 (verified) |
| 6. Cross-Platform Cloud Deployment | baseline | Delivered w/ gap | 2026-07-18 (verified) |
| 7. Day-0 Config Hardening & Board Bring-Up Spike | 3/3 | Delivered | 2026-07-25 |
| 8. CPU-Only Offline Edge Turn Loop | 5/5 | Delivered | 2026-07-25 |
| 9. Network-Cut Demo Hardening | 4/4 | Complete   | 2026-07-25 |
| 10. NPU-Accelerated Perception | 4/6 | In progress — 停損後重開（2026-07-27） | - |
| 11. Cloud Teacher Closed-Loop | 0/4 | Planning | - |
| 12. Nova Sonic Online Staging & Final Rehearsal | 0/? | Not started | - |
