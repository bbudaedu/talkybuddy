# Phase 11: Cloud Teacher Closed-Loop - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-27
**Phase:** 11-Cloud Teacher Closed-Loop
**Areas discussed:** 去識別化的可見度、未取得同意時的資料命運、機會式上傳的觸發時機、上傳欄位白名單（使用者全選 4 項）

---

## 去識別化套用點

| Option | Description | Selected |
|--------|-------------|----------|
| 只在上傳瞬間（建議） | 本地 DB 保留原文，`push_pending()` 組 payload 時才套用。符合 PRIV-02 原文，不影響本地 scaffold/SRS/發音評分對原文的依賴。改動最小 | ✓ |
| 寫入本地 DB 時就套用 | 裝置上也不留原文，隱私最強。但本地教學邏輯可能需要原文，需回測 Phase 8 離線迴圈 | |
| 雙欄位兩份都存 | payload 同存原文與去識別化版，上傳只送後者。可稽核性最好，但 payload 膨脹且多一個「傳錯欄位」的失效面 | |

**User's choice:** 只在上傳瞬間
**Notes:** 討論前先查證了 `guardrails.deidentify()` 的實際粒度——它只遮中文個資詞、3+ 位數字、詞庫外的 Title-case 英文專名，**不遮中文人名**，docstring 自承「正式版需語意層（B4 之後做）」。強化語意層判定為 scope creep，本 phase 只確保閘門被呼叫；此限制已寫進 CONTEXT.md D-01 要求誠實記錄，不得表述為「已完成去識別化」。

---

## 同意未授權時的資料命運

| Option | Description | Selected |
|--------|-------------|----------|
| 留在佇列等補傳（建議） | 不上傳、不標記已同步。日後取得同意後自動補傳。語意最直觀，「家長先不同意、後來同意」不遺失資料 | ✓ |
| 不上傳但標記已同步 | 本地保留、永不上雲。語意更嚴格，但家長事後同意也要不回來 | |
| 直接刪除本地紀錄 | 最激進的隱私立場。但會連帶破壞本地離線教學迴圈（SRS、診斷歷史），與 Phase 8「離線也能完整對話」相衝突 | |

**User's choice:** 留在佇列等補傳
**Notes:** 此選擇使 `mark_all_synced()` 的部分失敗缺陷從 nice-to-have 升格為前置條件——若部分失敗仍全部標記已同步，「補傳」的承諾在實作上不成立。已寫入 CONTEXT.md `<specifics>`。

---

## 機會式上傳的觸發時機

| Option | Description | Selected |
|--------|-------------|----------|
| 轉換瞬間 + 回合兜底（建議） | `network_mode` edge→cloud 轉換時立即觸發一次，另在每回合結束若 online 且有 pending 就補一次。兩層保險 | ✓ |
| 只在轉換瞬間觸發 | 最乾淨，決賽故事線最明確。但轉換事件漏接（server 重啟、手動改 flag）時 pending 會一直卡著 | |
| 只在每回合結束檢查 | 實作最簡單，但「插回網路 → 自動補傳」得等孩子再講一句才發生，演出節奏很弱 | |
| 定時輪詢 | 背景定時器。但 Phase 9 已硬化「離線視窗背景輪詢暫停」，再加輪詢與該方向相反 | |

**User's choice:** 轉換瞬間 + 回合兜底
**Notes:** 決賽敘事要求：插回網路後**不必等孩子再講話**，儀表板就出現新診斷。CONTEXT.md D-03 明訂不得新增定時輪詢，以免抵銷 Phase 9 的硬化成果。

---

## 上傳欄位白名單

| Option | Description | Selected |
|--------|-------------|----------|
| 白名單常數（建議） | 明訂允許出裝置的欄位，其餘一律剝除。日後新增欄位預設不外洩 | ✓ |
| 黑名單剝除 | 剝除已知敏感欄位（如 audio_path）。改動小，但日後任何人新增欄位就默認外洩 | |
| 維持現狀整包傳 | 只對文字欄位套 deidentify。最小改動，但 TCLOUD-01「只上傳衍生文字/分數」淪為口頭宣稱，無法稽核 | |

**User's choice:** 白名單常數
**Notes:** 決定關鍵在於查證出 `store.add_interaction()`（`server/store.py:187-202`）把 body 整包 `json.dumps` 進 payload，**沒有 schema**。無 schema 的情況下黑名單不可靠，白名單是「音檔絕不出裝置」唯一可稽核的寫法。

---

## 教師儀表板的學生身分顯示（討論中追加）

**使用者原話：** 「老師儀錶板要有學生完整姓名」

無選項表——這是直接指示，非多選題。

**查證：** `web/teacher.html:196` 硬編「阿明」、`:200`/`:260` 硬編 `STUDENT-AMING-004`；`server/store.py` 只有 `student_id`，`student_profile` 表為自由 payload、無姓名欄位。**目前的姓名是 mock。**

**範圍判定：** 屬 ROADMAP SC4「顯示真實（非 mock）診斷資料」的一部分，不是新增能力，因此不列為 deferred。

**Notes:** 與 D-01（deidentify 只在上傳瞬間）表面上有張力，實際不衝突——身分欄位（`student_id`、姓名）不經 deidentify，對話內容才經。逐字稿裡的名字被遮成 `[名字]`、同時頁面標題顯示完整姓名，是預期行為。另決定姓名不進上傳白名單：它存在 server 端（即儀表板讀取的同一 DB），裝置端只送 `student_id` 供綁定。

---

## Claude's Discretion

- 白名單的具體欄位清單（須為顯式常數、有測試覆蓋、預設拒絕）
- 兩層觸發的實作掛載位置（`pipeline` hook vs `app.py` 端點 vs `sync_client` 自身）
- `mark_all_synced()` 部分失敗的具體修法（依 seq 標記 vs 依回應明細標記）

## Deferred Ideas

- 強化 `deidentify()` 語意層（中文人名、諧音／拼音規避、間接個資）— `guardrails.py:107` 自承「B4 之後做」
- 跨裝置同步壓測 — `.planning/codebase/CONCERNS.md:267`；STATE.md 已列 v2 out-of-scope
- consent 變更的稽核軌跡 — `.planning/codebase/CONCERNS.md:90-96`，屬觀測性強化
