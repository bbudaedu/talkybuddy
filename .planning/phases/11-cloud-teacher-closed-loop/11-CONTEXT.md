# Phase 11: Cloud Teacher Closed-Loop - Context

**Gathered:** 2026-07-27
**Status:** Ready for planning

<domain>
## Phase Boundary

孩子完成一次邊緣對話後，衍生文字與分數在裝置重新連網時**機會式**同步上雲，經雲端 LLM 產出四維診斷，顯示在**既有**教師儀表板上，讓「邊緣對話 → 教師洞察」的敘事閉環成立；同時收斂 G1 consent 缺口。

**Requirements:** TCLOUD-01、TCLOUD-02（`.planning/REQUIREMENTS.md` M2 §44-45）

**本 phase 已完成、不重做的部分（2026-07-27 對 codebase 查證）：**

| ROADMAP SC | 狀態 | 證據 |
|---|---|---|
| SC3 — `diagnose.py` 經 direct `boto3 bedrock-runtime.converse()` | ✅ 已實作 | `server/bedrock_converse.py`；`server/diagnose.py:625-661` 走 `bedrock_converse.converse_text` / `resolve_config(role="diag")` |
| SC4 — 教師儀表板顯示診斷 | 🟡 元件齊全，端到端未驗 | `web/teacher.html`、`/api/diagnoses`、`server/app.py:293-298` 持久化帶 `student_id` |
| SC2 — `/api/sync` 端點 | 🟡 端點在，機會式觸發缺 | `server/app.py:375-397` |
| SC1 — `push_pending()` 隱私閘門 | ❌ **未做，本 phase 主要工作** | `server/sync_client.py:17-25` 零閘門 |

**不在本 phase**：強化 `deidentify()` 的語意層（擋諧音／拼音規避／間接個資）——其 docstring 明載「正式版需語意層（B4 之後做）」，屬獨立工作；NPU 加速（Phase 10）；Nova Sonic staging（Phase 12）。

</domain>

<decisions>
## Implementation Decisions

### 去識別化套用點（TCLOUD-01）
- **D-01（鎖定）：** `deidentify()` **只在上傳瞬間套用**。本地 SQLite 保留原文，`push_pending()` 組 payload 時才轉換。
  - 理由：符合 PRIV-02「文字上雲前先去識別化」的原文；本地教學邏輯（scaffold 詞庫比對、SRS、發音評分）對原文的依賴不受影響，不必回測 Phase 8 離線迴圈。
  - **已知限制須誠實記錄，不得掩蓋**：`guardrails.deidentify()` 只遮中文個資詞（住址／身分證／密碼類）、3+ 位連續數字、詞庫外的 Title-case 英文專名。**它不遮中文人名**。因此上雲文字仍可能含中文姓名。這是既有實作的邊界，不是本 phase 引入的退步，但 planner／executor 不得把「已呼叫 deidentify」表述為「已完成去識別化」。

### 同意未授權時的資料命運（TCLOUD-01 / G1）
- **D-02（鎖定）：** consent 未授權時，**不上傳且不標記已同步**，紀錄留在 pending 佇列。日後取得同意後自動補傳。
  - 理由：語意最直觀，且「家長先不同意、後來同意」不會遺失資料；與 Phase 8「離線也能完整對話」不衝突。
  - 推論：`push_pending()` 在 consent 未授權時必須**在打網路之前**就返回，不得先送出再判斷。

### 機會式上傳的觸發時機（TCLOUD-01）
- **D-03（鎖定）：** **兩層觸發**——(a) `network_mode` 由 edge 轉 cloud 的瞬間立即觸發一次；(b) 每回合結束時若為 online 且有 pending 就補一次。
  - 理由：(a) 是決賽橋段「插回網路 → 馬上補傳」的演出節奏；(b) 是兜底，避免轉換事件漏接（server 重啟、手動改 flag）導致 pending 永久卡住。
  - **不得新增定時輪詢**：Phase 9 已硬化「離線視窗背景輪詢暫停」，再加一個輪詢與該方向相反。

### 上傳欄位白名單（TCLOUD-01）
- **D-04（鎖定）：** 以**白名單常數**強制「只上傳衍生文字／分數」——明訂允許出裝置的欄位，其餘一律剝除。
  - 理由：`store.add_interaction()`（`server/store.py:187-202`）把 body 整包 `json.dumps` 進 payload，**沒有 schema**。黑名單在此情況下不可靠：日後任何人新增一個欄位（例如音檔路徑）就預設外洩。白名單是「音檔絕不出裝置」唯一可稽核的寫法。
  - 推論：白名單需搭配測試——新增一個未列入白名單的欄位，必須被剝除。

### Claude's Discretion
- 白名單的具體欄位清單，由 planner／executor 依 `store` 既有 payload 實際欄位與 `/api/sync` 契約決定；但必須是顯式常數、有測試覆蓋，且預設拒絕。
- 兩層觸發的實作位置（`pipeline` hook vs `app.py` 端點 vs `sync_client` 自身），依現有程式碼慣例決定，不視為新決策。
- `mark_all_synced()` 的部分失敗修法（見 `<specifics>`）的具體形式（改為依 seq 標記 vs 依回應明細標記）由 executor 決定。

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 需求與範圍
- `.planning/REQUIREMENTS.md` §TCLOUD-01、§TCLOUD-02 — 本 phase 的需求原文與 Out of Scope
- `.planning/ROADMAP.md` §Phase 11 — Goal 與四條 success criteria（含「教師儀表板 5 秒輪詢維持不變」）

### 隱私硬限制（跨切面，不可協商）
- `.planning/REQUIREMENTS.md` §PRIV-01／PRIV-02 — 家長同意閘門與上雲前去識別化
- `server/guardrails.py:54-58`（`consent_granted`）、`:102-125`（`deidentify`）— 兩個閘門的實際契約與**自承限制**
- `.planning/STATE.md` §Known-Gaps Backlog G1 — consent 缺口的登錄原文與已修復的 cloud-TTS 部分（`server/pipeline.py:357-363` 是同一 pattern 的參考實作）

### 前置 phase 交接
- `.planning/phases/09-network-cut-demo-hardening/09-CONTEXT.md` — `network_mode` kill-switch 語意；D-03 觸發點沿用此機制
- `server/app.py:260-298` — 既有 `/api/sync_now` 流程（mark_all_synced → generate_diagnosis → add_diagnosis），本 phase 的觸發邏輯必須與之對齊而非另起一套
- `server/diagnose.py:625-661` — TCLOUD-02 已完成的 direct converse 實作，**不要重寫**

### 既有缺陷（本 phase 一併收斂，非新增範圍）
- `server/sync_client.py:23-24` — `mark_all_synced()` 全有全無的部分失敗缺陷，見 `<specifics>`

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `server/guardrails.consent_granted()` / `deidentify()` — 兩個閘門都已存在且有測試，本 phase 是「接上去」而非「造新的」。
- `server/pipeline.py:357-363` — cloud-TTS 分支的 consent 守門是同一 pattern 的**已驗證參考實作**（G1 於 2026-07-20 out-of-band 修復時建立），`push_pending()` 應比照。
- `server/bedrock_converse.py` — TCLOUD-02 的 direct converse 已完成，含 `resolve_config(role="diag")`。
- `server/store.mark_all_synced()` / `interaction_exists()` — 去重與同步標記機制已在。

### Established Patterns
- 閘門 pattern：在雲端呼叫點之前先 `guardrails.consent_granted()`，未授權則走本地路徑或直接返回（見 `pipeline.py` LLM 與 cloud-TTS 兩處分支）。
- `network_mode` 全域狀態 + 每回合再同步（Phase 09-01 建立）——D-03 的觸發點應掛在同一條狀態轉換上，不要另建網路偵測。
- 測試慣例：純函式在上、`main()` 在下、外部相依以參數注入（`push_pending(base_url, token, http_post)` 已是此形態，測試不必打真網路）。

### Integration Points
- `server/sync_client.py::push_pending()` — SC1 的主改點（deidentify + consent + 白名單）。
- `network_mode` edge→cloud 轉換處 — D-03(a) 的掛載點。
- 回合結束 hook — D-03(b) 的掛載點。
- `server/app.py:375` `/api/sync` — 接收端；白名單是**發送端**強制，接收端不必改契約。

</code_context>

<specifics>
## Specific Ideas

- **`mark_all_synced()` 的部分失敗缺陷（ROADMAP 未列，本 phase 一併收斂）**：`server/sync_client.py:23-24` 目前只要 `accepted` 或 `skipped` 任一大於 0，就把**全部** pending 標記已同步。若雲端只收了一部分，未被接受的紀錄會被靜默標記為已同步而永久遺失。這與 D-02「留在佇列等補傳」的決策直接衝突——D-02 承諾的補傳語意，在這個缺陷下不成立。因此它不是 nice-to-have，是 D-02 的前置條件。

- 決賽敘事的節奏要求：主持人拔網路 → 孩子繼續離線對話 → 插回網路 → **不必等孩子再講話**，儀表板就出現新診斷。這是 D-03 選「轉換瞬間觸發」而非「只在回合結束檢查」的原因。

</specifics>

<deferred>
## Deferred Ideas

- **強化 `deidentify()` 語意層**（擋中文人名、諧音／拼音規避、間接個資）— `guardrails.py:107` docstring 自承「正式版需語意層（B4 之後做）」。本 phase 只確保閘門被呼叫，不改其粒度。
- **跨裝置同步壓測** — `.planning/codebase/CONCERNS.md:267` 登錄「PC 原型為單裝置，Genio 520 多裝置情境未測」；STATE.md Deferred Items 已列為 v2 out-of-scope。
- **consent 變更的稽核軌跡** — `.planning/codebase/CONCERNS.md:90-96` 建議「consent 設定變更時結構化記錄」。屬觀測性強化，不阻擋本 phase。

</deferred>

---

*Phase: 11-Cloud Teacher Closed-Loop*
*Context gathered: 2026-07-27*
