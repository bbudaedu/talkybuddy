---
gsd_state_version: 1.0
milestone: 2
milestone_name: Genio 520 決賽 Edge MVP
current_phase: 07
current_phase_name: Day-0 Config Hardening & Board Bring-Up Spike
status: complete
stopped_at: null
last_updated: "2026-07-25T16:30:00.000Z"
last_activity: 2026-07-25
last_activity_desc: Phase 07 complete — Yocto board bring-up GO, real health-check pass, ready for Phase 8
progress:
  total_phases: 12
  completed_phases: 1
  total_plans: 3
  completed_plans: 3
  percent: 8
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-07-18)

**Core value:** 孩子能喊「說說學伴」進行自然、可 barge-in 的口說繁中對話，同時教學並評估，且自架串流與 Nova Sonic 兩路徑皆能達成。
**Current focus:** Phase 07 — Day-0 Config Hardening & Board Bring-Up Spike

## Current Position

Phase: 07 (Day-0 Config Hardening & Board Bring-Up Spike) — EXECUTING
Plan: 3 of 3
Status: Ready to execute
Last activity: 2026-07-19 — Phase 07 execution started

## Performance Metrics

**Velocity:**

- Total plans completed: 0
- Average duration: - min
- Total execution time: 0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

**Recent Trend:**

- Last 5 plans: -
- Trend: -

*Updated after each plan completion*
**Per-Plan Metrics:**

| Plan | Duration | Tasks | Files |
|------|----------|-------|-------|
| Phase 07 P01 | 20min | 2 tasks | 3 files |
| Phase 07 P02 | 15min | 2 tasks | 9 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- 雙模式共存：Path 1 自架串流 + Path 2 Nova Sonic S2S 皆一等公民，不合併（本輪使用者鎖定）
- 喚醒引擎依客戶端模式分流：Porcupine（Path 1）/ sherpa-onnx KWS「說說學伴」（Path 2）
- Route A（/ws/live 為發音評估主線）、ElevenLabs 情感 TTS + 靜默降級、Nova Sonic Phase 1 vertical slice 皆為 proposed（未鎖定 ADR）
- **[M2] Roadmap 排序（2026-07-19）**：Phase 7（config/board bring-up spike）→ 8（CPU-only 離線迴路，決賽存亡關鍵）→ 9（斷網橋段硬化）→ 10／11（NPU 加速、雲端教師閉環，可平行、屬加值/可犧牲軌道，不阻擋彼此）→ 12（Nova Sonic staging，最低優先，依賴 9，進度落後時第一個整體犧牲）
- [Phase ?]: LLM_N_CTX profile-driven（edge=512/cloud=1024，TALKYBUDDY_LLM_N_CTX 可覆寫，覆寫優先於 profile）
- [Phase ?]: pipeline RIFF-sniff fast path：原生 16k mono WAV 走 soundfile 直讀零 ffmpeg；規格不符於 edge 明確 raise WavSpecMismatchError，不靜默偽成功
- [Phase ?]: run_edge.sh 以 BASH_SOURCE 自身位置相對定位部署根目錄，不硬編個人 home 絕對路徑（D-02）
- [Phase ?]: edge/deploy push.sh/run.sh 的裝置端 proot rootfs 與部署目標路徑用環境變數宣告預設值並可覆寫，待真機驗證後調整

### Pending Todos

- 下一步：`/gsd-plan-phase 7`（Day-0 Config Hardening & Board Bring-Up Spike）

### Known-Gaps Backlog (baseline verified 2026-07-18)

登錄自基線驗證，不阻擋新功能；可擇期併入未來 milestone 或單獨修補。

| # | Phase | 缺口 | 嚴重度 | 證據 |
|---|-------|------|--------|------|
| ~~G1~~ | 3 | ~~cloud-TTS 合成點缺 `consent_granted()` 檢查~~ — **已修復（2026-07-20，out-of-band）**：`_synth_tts` 雲端分支補上 `guardrails.consent_granted()` 守門，比照既有 LLM 分支 pattern；新增回歸測試 `test_cloud_mode_without_consent_never_calls_cloud_tts` | ✅ 已解 | server/pipeline.py:357-363（修復點）；tests/test_pipeline_cloud_tts.py（回歸測試） |
| G2 | 2 | `run_realwire.py` build_processors 漏接 `BargeInGate` → 真實麥克風/喇叭上 barge-in 不觸發；無實機執行證據 | 🟠 中 | server/streaming/run_realwire.py:45-52 |
| G3 | 6 | TLS/WSS reverse proxy 僅 DEPLOY_CLOUD.md 文件化，無提交的 Caddyfile/nginx 設定或 VM 實跑驗證 | 🟠 中（部署時處理） | docs/DEPLOY_CLOUD.md:35-50 |
| G4 | 3 | 無原生 Bedrock Converse 回覆後端，只有 config 切換的 Anthropic-Messages relay（LLM-02「Bedrock/relay 切換」為 config-fronting） | 🟡 低（by-design 可接受） | server/config.py:70-71；server/anthropic_relay.py:32-55 |
| G5 | 1 | SenseVoice→whisper 為手動 `ASR_BACKEND` flag，非自動 fallback | 🟡 低（符合 ASR-02 原文） | server/asr.py:12；asr_base.py:17 |

### Blockers/Concerns

- 隱私為跨切面硬限制：Phase 3 起任何雲端路徑皆須先立起 consent gate + 去識別化（PRIV-01/02）
- 既有技術債影響未來 Genio 520 移植：ffmpeg 音訊轉檔、LLM n_ctx=1024、espeak-ng-data GPL 殘留（見 .planning/codebase/CONCERNS.md）
- 尚無 .planning/config.json：後續 GSD 工作流可能需要初始化（granularity 預設 standard、sequential 編號）

## Deferred Items

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| Edge Hardware | Genio 520 NPU 實機部署（EDGE-01/02） | v2（已於 2026-07-19 排入 M2 Phase 7/10） | 2026-07-18 |
| Scale & Sync | 多裝置同步壓測、教師儀表板即時推播（SYNC-01/DASH-01） | v2 out-of-scope | 2026-07-18 |
| M2 Scope Cuts | on-device 音素發音評分、NPU TTS 加速、GAI Toolkit/ncc-tflite DLA 路徑、三源 RAG、雙雲 LLM、裝置端多用戶/多裝置同步、教師儀表板即時推播、自建 OS — 詳見 REQUIREMENTS.md v2 Out of Scope | v2 out-of-scope | 2026-07-19 |

## Session Continuity

Last session: 2026-07-21T11:51:16.494Z
Stopped at: context exhaustion at 75% (2026-07-21)
Resume file: None
