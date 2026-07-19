---
phase: 07-day-0-config-hardening-board-bring-up-spike
plan: 02
subsystem: infra
tags: [edge, adb, proot-distro, deployment, bash, uvicorn]

# Dependency graph
requires:
  - phase: 07-day-0-config-hardening-board-bring-up-spike (plan 01)
    provides: profile-driven LLM_N_CTX and RIFF-sniff WAV fast path (TALKYBUDDY_PIPELINE_PROFILE consumers this plan's scripts inject)
provides:
  - "頂層 edge/ 骨架（edge/deploy、edge/models、edge/runtime）"
  - "edge/runtime/run_edge.sh：proot Debian launcher，注入 TALKYBUDDY_PIPELINE_PROFILE=edge，exec uvicorn server.app:app port 8787"
  - "edge/deploy build→push→run adb 部署腳本（可執行、非空殼）"
  - "docs/DEPLOY_EDGE.md，對稱 docs/DEPLOY_CLOUD.md"
affects: [07-03-PLAN.md (board bring-up spike, 需真機跑本 plan 的 deploy 腳本), phase-08 (CPU-only 離線迴路，將把實際模型放入 edge/models)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "bash launcher/部署腳本：set -euo pipefail + 自身位置相對定位（BASH_SOURCE dirname）避免硬編個人 home 絕對路徑"
    - "adb 部署腳本可覆寫目標路徑：以環境變數（TALKYBUDDY_EDGE_*）宣告預設值，供不同裝置/proot-distro 安裝路徑覆寫"

key-files:
  created:
    - edge/README.md
    - edge/runtime/run_edge.sh
    - edge/runtime/README.md
    - edge/deploy/build.sh
    - edge/deploy/push.sh
    - edge/deploy/run.sh
    - edge/deploy/README.md
    - edge/models/README.md
    - docs/DEPLOY_EDGE.md
  modified: []

key-decisions:
  - "run_edge.sh 以 BASH_SOURCE 自身位置相對定位部署根目錄（上兩層），不硬編個人開發者 home 絕對路徑（呼應 D-02 YAGNI：只做 Android 14 proot 這一條路徑）"
  - "push.sh/run.sh 的裝置端 proot rootfs 與部署目標路徑，用 TALKYBUDDY_EDGE_PROOT_ROOTFS / TALKYBUDDY_EDGE_DEVICE_ROOT 環境變數宣告預設值並可覆寫，因實際路徑依裝置上 proot-distro 安裝方式而異，且尚無真機驗證"
  - "run.sh 以背景 nohup 啟動 server 並輪詢 curl health-check（逾時可調），符合 D-03『只驗 server 起來 + health check』範圍，不含完整聲音迴路"
  - "G520 SDK 專屬 provisioning/燒錄/proot 喚起指令一律以 TODO 註解標示並指向 ~/hackathon/ SDK 文件，不在 repo 臆造未經驗證的指令（canonical_refs 邊界）"

patterns-established:
  - "邊緣部署腳本三段式（build/push/run）+ README 對稱文件的骨架，供 Phase 8 起後續模型/native binary 部署沿用"

requirements-completed: [EDGE-03, EDGE-04]

coverage:
  - id: D1
    description: "edge/ 頂層骨架三子目錄（deploy/models/runtime）存在"
    requirement: "EDGE-04"
    verification:
      - kind: unit
        ref: "test -d edge/deploy && test -d edge/models && test -d edge/runtime"
        status: pass
    human_judgment: false
  - id: D2
    description: "edge/runtime/run_edge.sh 可執行、語法正確、注入 TALKYBUDDY_PIPELINE_PROFILE=edge、起 port 8787、無硬編個人絕對路徑"
    requirement: "EDGE-03"
    verification:
      - kind: unit
        ref: "bash -n edge/runtime/run_edge.sh; grep -c TALKYBUDDY_PIPELINE_PROFILE=edge; grep -c 8787; grep -c /home/budaedu (== 0)"
        status: pass
    human_judgment: false
  - id: D3
    description: "edge/deploy build/push/run 三腳本可執行、語法正確、走 adb"
    requirement: "EDGE-03"
    verification:
      - kind: unit
        ref: "bash -n edge/deploy/{build,push,run}.sh + test -x; grep -c 'adb ' push.sh/run.sh"
        status: pass
    human_judgment: false
  - id: D4
    description: "docs/DEPLOY_EDGE.md 對稱 docs/DEPLOY_CLOUD.md 結構，含環境變數表、run_edge.sh 啟動指令、adb 部署迴圈、health-check 驗證章節"
    requirement: "EDGE-04"
    verification:
      - kind: unit
        ref: "grep -c '| 變數 |'; grep -c TALKYBUDDY_PIPELINE_PROFILE=edge; grep -c run_edge.sh; grep -ic health"
        status: pass
    human_judgment: false
  - id: D5
    description: "adb build→push→run 部署管線真機實跑一次（含 proot-distro provisioning 與 health check 成功）"
    verification: []
    human_judgment: true
    rationale: "本 plan 只驗『可執行 + 語法正確 + 走 adb』，無實體 Genio 520 裝置可用；真機 adb 實跑一次由 07-03（board bring-up spike）承接，需硬體與人工操作裝置授權"

# Metrics
duration: 15min
completed: 2026-07-19
status: complete
---

# Phase 7 Plan 2: Edge Skeleton & adb Deploy Pipeline Summary

**頂層 edge/ 骨架（deploy/models/runtime）+ proot-distro launcher + adb build→push→run 部署腳本 + 對稱 docs/DEPLOY_EDGE.md，全為新檔、可執行且語法正確，為 07-03 真機 adb 跑一次提供載體**

## Performance

- **Duration:** 15 min
- **Started:** 2026-07-19T12:58:14Z
- **Completed:** 2026-07-19T13:02:06Z
- **Tasks:** 2
- **Files modified:** 9 (all new)

## Accomplishments
- 建立頂層 `edge/` 骨架（`edge/deploy`、`edge/models`、`edge/runtime` 三子目錄）
- `edge/runtime/run_edge.sh`：proot Debian launcher，以自身位置相對定位（不硬編個人 home 絕對路徑），注入 `TALKYBUDDY_PIPELINE_PROFILE=edge`，exec 既有 `server.app:app` 於 port 8787
- `edge/deploy` build→push→run 三個可執行 adb 部署腳本（非空殼），對應 adb push 既有 `server/` 與 `edge/runtime` 到裝置 proot rootfs 後啟動並 health-check
- `edge/models/README.md` placeholder，說明未來放 INT8 tflite/GGUF、與頂層 `models/` 分離
- `docs/DEPLOY_EDGE.md` 對稱 `docs/DEPLOY_CLOUD.md` 結構：環境變數表、啟動指令、adb 部署迴圈、health-check 驗證（D-03 範圍）、proot-distro provisioning

## Task Commits

Each task was committed atomically:

1. **Task 1: 建 edge/ 骨架 + runtime launcher + models placeholder** - `8175b4d` (feat)
2. **Task 2: edge/deploy adb 腳本（build→push→run）+ docs/DEPLOY_EDGE.md** - `a81593a` (feat)

**Plan metadata:** (final commit — see below)

## Files Created/Modified
- `edge/README.md` - 三子目錄總覽，指向 docs/DEPLOY_EDGE.md
- `edge/runtime/run_edge.sh` - proot Debian launcher（可執行、set -euo pipefail、相對路徑定位）
- `edge/runtime/README.md` - 說明引用既有 server/、proot-distro 選擇理由（D-01）、不裝 ffmpeg
- `edge/models/README.md` - placeholder，未來放 INT8 tflite/GGUF，與頂層 models/ 分離（D-04）
- `edge/deploy/build.sh` - 確認 server/、edge/runtime 載荷齊備
- `edge/deploy/push.sh` - adb push server/ + edge/runtime 到裝置 proot rootfs
- `edge/deploy/run.sh` - adb shell 背景啟動 run_edge.sh + health-check（D-03）
- `edge/deploy/README.md` - 三腳本用途、執行順序、adb 前置、provisioning 說明
- `docs/DEPLOY_EDGE.md` - 對稱 docs/DEPLOY_CLOUD.md，環境變數表/啟動指令/adb 迴圈/驗證/provisioning

## Decisions Made
- `run_edge.sh` 用 `BASH_SOURCE` 自身位置相對定位部署根目錄（上兩層），避免硬編個人開發者 home 絕對路徑，符合 acceptance criteria「無硬編個人絕對路徑」且滿足 D-02 只做 Android 14 proot 路徑（不做 dual-host 抽象）
- 裝置端 proot rootfs 與部署目標路徑用 `TALKYBUDDY_EDGE_PROOT_ROOTFS` / `TALKYBUDDY_EDGE_DEVICE_ROOT` 環境變數宣告預設值並可覆寫——因目前無真機驗證實際 proot-distro 安裝路徑，先給合理預設（Termux proot-distro 慣例路徑）並留覆寫空間，待 07-03 真機驗證後可能需調整預設值
- `run.sh` health-check 採輪詢（`until curl ...; do sleep 2; done` + 逾時），而非單次 curl，因裝置端 server 啟動需要時間（proot + Python 啟動），單次 curl 容易誤判失敗
- 涉及 G520 SDK 具體燒錄/proot 喚起指令處，一律以 `TODO` 註解標示並指向 `~/hackathon/` SDK 文件，不臆造未經驗證的 SDK 專屬指令（遵守 canonical_refs 邊界與「不靜默偽成功」原則）

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required. 真機 adb 部署驗證（proot-distro 安裝、USB 偵錯授權、實際 health-check 跑通）留給 07-03（board bring-up spike），需要實體 Genio 520 裝置與人工操作裝置端授權提示。

## Next Phase Readiness

- `edge/` 骨架、launcher 與 adb 部署腳本皆已就緒且通過語法/可執行檢查，07-03 board bring-up spike 可直接用 `edge/deploy/{build,push,run}.sh` 在真機上跑一次
- `docs/DEPLOY_EDGE.md` 的環境變數預設值（proot rootfs 路徑等）尚未經真機驗證，07-03 執行時若路徑不符實際裝置佈局，需用對應 `TALKYBUDDY_EDGE_*` 環境變數覆寫或回頭修正腳本預設值
- `edge/models` 仍為空 + README placeholder，符合本 phase 範圍（實際量化模型 Phase 8/10 產出）

---
*Phase: 07-day-0-config-hardening-board-bring-up-spike*
*Completed: 2026-07-19*

## Self-Check: PASSED

All 9 created files verified present on disk; both task commits (`8175b4d`, `a81593a`) verified present in git log.
