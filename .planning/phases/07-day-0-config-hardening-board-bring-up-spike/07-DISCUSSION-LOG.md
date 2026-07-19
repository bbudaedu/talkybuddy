# Phase 7: Day-0 Config Hardening & Board Bring-Up Spike - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-19
**Phase:** 7-day-0-config-hardening-board-bring-up-spike
**Areas discussed:** 裝置端 Python runtime 策略, edge/ 骨架範圍與現有目錄關係, ffmpeg 移除策略與 WAV 直讀
**Areas offered but not selected:** Board spike go/no-go 準則（採 success criteria 預設方向）

---

## 裝置端 Python Runtime 策略

### Q1 — Android 14 上跑 FastAPI + 引擎的方式

| Option | Description | Selected |
|--------|-------------|----------|
| proot-distro (Debian) | 完整 glibc + apt，native build 可行，最接近 Yocto 環境 | ✓ |
| Termux 原生 | 最少層，但 bionic libc 破壞 manylinux wheel，與 Yocto 不一致 | |
| chroot (need root) | 無 proot overhead 但需 root，風險高 | |

**User's choice:** proot-distro (Debian)

### Q2 — edge/runtime 啟動腳本抽象程度

| Option | Description | Selected |
|--------|-------------|----------|
| 先只針對 Android 14 | YAGNI；Yocto 過了再補 native 路徑 | ✓ |
| 一開始就雙 host 抽象 | 更通用但多花時間，且 Yocto 尚未驗證 | |

**User's choice:** 先只針對 Android 14

### Q3 — 「adb 跑一次」驗證範圍

| Option | Description | Selected |
|--------|-------------|----------|
| 只驗 server 起來 + health check | success criteria #4 最小達成；聲音迴路留 Phase 8 | ✓ |
| 驗到前端 loopback 對話 | 超出 Phase 7 骨架目標 | |

**User's choice:** 只驗 server 起來 + health check

---

## edge/ 骨架範圍與現有目錄關係

### Q1 — edge/models 與既有 models/ 關係

| Option | Description | Selected |
|--------|-------------|----------|
| 分離，edge 專屬量化產物 | models/ 不動；edge/models 放 INT8/GGUF；本 phase 空 dir + README | ✓ |
| symlink 共用 models/ | 不重複但量化產物與原型混雜難管理 | |

**User's choice:** 分離，edge 專屬量化產物

### Q2 — edge/runtime 與 server/ 程式碼關係

| Option | Description | Selected |
|--------|-------------|----------|
| 引用不複製 | launcher 指向既有 server/，避免雙份 | ✓ |
| 整包複製 server | 隔離但雙份維護，衝刺不宜 | |

**User's choice:** 引用不複製

### Q3 — 各子目錄 scaffold 完成度

| Option | Description | Selected |
|--------|-------------|----------|
| deploy/runtime 可跑，models 空殼 | adb 要真跑一次，deploy/runtime 須可執行；+ docs/DEPLOY_EDGE.md | ✓ |
| 全部純空骨架 + README | adb 無法在本 phase 真跑，與 EDGE-03 衝突 | |

**User's choice:** deploy/runtime 可跑，models 空殼

---

## ffmpeg 移除策略與 WAV 直讀

### Q1 — RIFF-sniff 後 PC 端 WebM/Opus 處理

| Option | Description | Selected |
|--------|-------------|----------|
| 保留 ffmpeg 當 fallback（雙路徑） | WAV 直讀、WebM 走 ffmpeg；PC 不壞，edge 不裝 ffmpeg | ✓ |
| 硬移除 ffmpeg（只吃 WAV） | 更純但破壞 PC 瀏覽器 MediaRecorder 路徑 | |

**User's choice:** 保留 ffmpeg 當 fallback（雙路徑）

### Q2 — WAV 直讀實作

| Option | Description | Selected |
|--------|-------------|----------|
| soundfile（已在用） | asr_sensevoice.py 已用；一致、無新依賴 | ✓ |
| 標準庫 wave + numpy | 無額外依賴但需自處理 header/位寬，與現有用法不一致 | |

**User's choice:** soundfile（已在用）

### Q3 — WAV 但 16k mono 不符時的行為

| Option | Description | Selected |
|--------|-------------|----------|
| 不符退 ffmpeg（PC）/ 報明確錯（edge） | 邊緣 ALSA 由我們控制擷取；fast path 只接已符合的；不靜默偽成功 | ✓ |
| 直讀後自己 resample | 更寬容但多一層複雜度，且掩蓋擷取端問題 | |

**User's choice:** 不符退 ffmpeg（PC）/ 報明確錯（edge）

---

## Claude's Discretion

- `LLM_N_CTX` / RIFF-sniff / launcher 的具體程式碼結構、命名、測試骨架。
- adb 部署腳本的具體 flag 與檔案佈局。
- board spike 執行細節（誰按 adb / NB 分攤）。

## Deferred Ideas

- 前端 loopback 對話在裝置上跑通 → Phase 8/9。
- Yocto native 啟動路徑抽象（dual-host launcher）→ 待 board spike go。
- 邊緣量化模型實際產出（INT8 tflite / GGUF）→ Phase 8 / Phase 10。
- espeak-ng-data GPL 殘留清除、app.py 全域單例重構 → 非 Day-0 零風險項，未排入本 phase。
