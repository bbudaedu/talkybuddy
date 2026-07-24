---
phase: 07-day-0-config-hardening-board-bring-up-spike
plan: 03
subsystem: infra
tags: [yocto, ssh, rsync, genio-520, edge-deploy, board-bringup]

requires:
  - phase: 07-day-0-config-hardening-board-bring-up-spike (07-02)
    provides: edge/deploy adb 部署骨架、edge/runtime/run_edge.sh launcher、docs/DEPLOY_EDGE.md
provides:
  - "Hti G520 board bring-up 決策紀錄：Yocto GO，取代 07-20 暫定的 Android 14 fallback"
  - "SSH/rsync 部署管線（取代 adb/proot-distro），已對真機實測跑通並 health-check pass"
  - "裝置端 Python 環境 provisioning 實測確認：無 gcc/cmake 但 pip 可抓 manylinux aarch64 wheel 免編譯安裝"
affects: [08-cpu-only-offline-edge-turn-loop, 10-npu-acceleration]

tech-stack:
  added: []
  patterns:
    - "邊緣部署改用 SSH/rsync（非 adb），裝置 IP 經環境變數 TALKYBUDDY_EDGE_SSH_HOST 注入，不寫死於 repo"
    - "health-check 從開發端直接對裝置 IP 發出 curl（非裝置本機/adb shell curl）"

key-files:
  created:
    - edge/BOARD_BRINGUP_DECISION.md
    - .planning/phases/07-day-0-config-hardening-board-bring-up-spike/07-03-SUMMARY.md
  modified:
    - edge/deploy/push.sh
    - edge/deploy/run.sh
    - edge/deploy/README.md
    - edge/runtime/provision_device.sh
    - docs/DEPLOY_EDGE.md
    - .planning/REQUIREMENTS.md
    - .planning/ROADMAP.md
    - .planning/phases/07-day-0-config-hardening-board-bring-up-spike/.continue-here.md

key-decisions:
  - "Yocto GO：官方 IoT Yocto 燒錄 2026-07-25 成功（Rity Demo Layer 25.1.1-release scarthgap），推翻 07-20 暫定的 Android 14 主線決策，不需要 fallback，也無新增成本（proot-distro/Java/NDK shim 皆不需要）"
  - "部署傳輸層改 SSH/rsync：Yocto 板卡無 adb 介面，07-02 規劃的 adb push/shell 改為 SSH 登入 + rsync 推送 + 背景啟動；edge/runtime/run_edge.sh 本身無需修改（原本就無 proot 特定邏輯）"
  - "裝置端無 C 編譯器（無 gcc/cmake）不構成阻塞：fastapi/uvicorn[standard]/websockets/pydantic 皆有 manylinux aarch64 cp312 預編譯 wheel，pip install 全數免編譯成功"

patterns-established:
  - "裝置 IP 一律經 TALKYBUDDY_EDGE_SSH_HOST 環境變數注入，腳本內不寫死（DHCP 配發會變動）"

requirements-completed: [EDGE-02, EDGE-03]

coverage:
  - id: D1
    description: "Yocto board bring-up 燒錄成功並產出有日期的 go/no-go 決策紀錄"
    requirement: "EDGE-02"
    verification:
      - kind: manual_procedural
        ref: "edge/BOARD_BRINGUP_DECISION.md §1（SSH 實測 /etc/os-release、uname、CPU/記憶體資訊）"
        status: pass
    human_judgment: false
  - id: D2
    description: "部署迴圈（build→push→run）在真機上完整跑過一次，server 起來並回應 health check"
    requirement: "EDGE-03"
    verification:
      - kind: manual_procedural
        ref: "edge/BOARD_BRINGUP_DECISION.md §3（實際 curl 輸出：HTTP 200，connect=0.057s total=0.277s）"
        status: pass
    human_judgment: false

duration: 45min
completed: 2026-07-25
status: complete
---

# Phase 7 Plan 3: Board Bring-Up Spike Summary

**Yocto 燒錄成功並實測 GO，部署管線由 adb/proot 改為 SSH/rsync 跑通真機 health check（HTTP 200）**

## Performance

- **Duration:** ~45 min（自使用者回報 Yocto 燒錄成功、Tailscale 打通、SSH 免密碼可連起算）
- **Completed:** 2026-07-25
- **Tasks:** 3（checkpoint 1 回報、checkpoint 2 實測執行、決策紀錄撰寫 + 部署腳本同步更新）
- **Files modified:** 8

## Accomplishments
- 確認 Hti G520 官方 IoT Yocto 燒錄成功（`Rity Demo Layer 25.1.1-release scarthgap`，kernel 6.6.92-mtk aarch64，Python 3.12.11 原生 glibc），OS 路徑決策從 07-20 暫定的 Android 14 改為 **Yocto GO**，無新增 fallback 成本。
- 透過 Tailscale subnet router 打通開發端與裝置區網（不同網段），以 SSH 免密碼登入板卡（發現並記錄此為安全風險）。
- 實測跑通「rsync push → 裝置端建 venv + pip install → 背景啟動 uvicorn → 開發端直接 curl health check」全流程，取得真實 `HTTP 200` 輸出（非模擬）。
- 把 `edge/deploy/{push,run}.sh`、`edge/runtime/provision_device.sh`、`docs/DEPLOY_EDGE.md`、`edge/deploy/README.md` 從 adb/proot-distro 假設同步更新為實測驗證過的 SSH/rsync 路徑。

## Task Commits

尚未 commit（依專案慣例不主動提交，待使用者確認）。本次變更涵蓋：
1. G1 consent gate 修復（上一輪工作，`server/pipeline.py` + 測試）。
2. Android 14 provisioning 腳本（上一輪工作，後於本輪改寫為 Yocto 通用版）。
3. Board bring-up 實測 + 決策紀錄 + 部署腳本改寫（本輪）。

## Files Created/Modified
- `edge/BOARD_BRINGUP_DECISION.md` - 有日期 go/no-go 決策紀錄，含真實 SSH/curl 輸出
- `edge/deploy/push.sh` - adb push → rsync/SSH push（`TALKYBUDDY_EDGE_SSH_HOST` 必填環境變數）
- `edge/deploy/run.sh` - adb shell 啟動 + adb shell curl → SSH 背景啟動 + 開發端直接 curl health check
- `edge/deploy/README.md` - 反映 SSH/rsync 流程與已知 SSH 無驗證風險
- `edge/runtime/provision_device.sh` - 移除 proot-Debian 框架用語，改為 Yocto 原生 shell 描述
- `docs/DEPLOY_EDGE.md` - 全文改為 SSH/rsync 部署指南，含 Tailscale 連線前置與已知風險章節
- `.planning/REQUIREMENTS.md` - EDGE-02/EDGE-03 標記完成，反映真實決策與傳輸層變更
- `.planning/ROADMAP.md` - 07-03-PLAN.md checkbox 打勾，Phase 7 三個 plan 全部完成

## Decisions Made
- Yocto GO，取代 07-20 暫定的 Android 14 主線（見 key-decisions）。
- 部署傳輸層改 SSH/rsync（見 key-decisions）。
- 裝置端無 C 編譯器不構成阻塞（見 key-decisions）。

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule: 假設失效] 07-02 的 adb-based 部署管線假設不成立，改寫為 SSH/rsync**
- **Found during:** Checkpoint 2 實測執行
- **Issue:** 07-02 規劃時假設走 Android 14 + adb + proot-distro；Yocto 燒錄成功後裝置是原生 Linux，無 adb 介面可用
- **Fix:** `edge/deploy/{push,run}.sh` 改用 SSH + rsync；health-check 改為開發端直接對裝置 IP 發出 curl（比原本「adb shell 內 curl」更貼近真實使用情境）
- **Files modified:** `edge/deploy/push.sh`, `edge/deploy/run.sh`, `edge/deploy/README.md`, `edge/runtime/provision_device.sh`, `docs/DEPLOY_EDGE.md`
- **Verification:** 三腳本 `bash -n` 語法通過；實際對真機執行 push 流程手動跑過一次（腳本邏輯與手動指令一致），health-check 段落取得真實 `HTTP 200`
- **Committed in:** 尚未 commit

---

**Total deviations:** 1 auto-fixed（部署傳輸層假設失效，非疏漏，屬 07-20 時點資訊不足下的合理暫定被後續實測推翻）
**Impact on plan:** 不影響 D-03 驗證範圍（仍只驗 server 起來 + health check）；提升後續 Phase 8/10 部署管線的真實可用性。

## Issues Encountered
- 開發端與裝置初期不同網段無法直連 → 透過使用者 NB 開 Tailscale subnet router 並於 admin console 核准後解決。
- 板卡 sshd 無任何驗證機制（`root` 免密碼免 key）→ 已記錄為已知風險（`edge/BOARD_BRINGUP_DECISION.md` §4），非本 phase 阻塞項，留待決賽前處理。

## User Setup Required
None - 本輪部署驗證已直接對接使用者提供的真機與網路，無需額外外部服務設定。

## Next Phase Readiness
- Phase 8（CPU-only 離線迴路）地基已確認：Yocto 原生 glibc aarch64 環境，`.planning/research/STACK.md` 的 llama.cpp 交叉編譯結論持續有效。
- 裝置端無 gcc/cmake，若 Phase 8/10 需原生編譯元件，須採「開發機交叉編譯 → rsync push 執行檔」路徑（與既有 llama-server native binary 規劃一致，非新問題）。
- 已知風險待後續處理：SSH 無驗證（決賽前應補 key-only 登入或限制網段）。

---
*Phase: 07-day-0-config-hardening-board-bring-up-spike*
*Completed: 2026-07-25*
