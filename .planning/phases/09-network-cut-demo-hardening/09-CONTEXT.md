# Phase 9: Network-Cut Demo Hardening - Context

**Gathered:** 2026-07-25
**Status:** Ready for planning

<domain>
## Phase Boundary

現場主持人可隨時手動切斷裝置的雲端連線，孩子與說說學伴的對話完全不受影響地持續離線進行，且不會出現多秒靜默 hang：

1. **kill-switch 機制（NETCUT-01）**：沿用/改造既有 `/api/network_mode` 飛航模式開關作為主持人主控，切換時瀏覽器↔本機 server 的 loopback 不受影響，裝置持續離線對話。
2. **逾時縮短 + 無縫降級（NETCUT-02）**：雲端 LLM/TTS 逾時大幅縮短（現況 8s/6s），避免中途斷網時多秒靜默 hang；UI 提供明確可見的 online/offline 狀態切換。
3. **彩排腳本（NETCUT-03）**：≥3 次實體斷網重複演練（含講話中途斷網），每次恢復時間 <1–2 秒。

**Requirements:** NETCUT-01, NETCUT-02, NETCUT-03（`.planning/REQUIREMENTS.md` M2）

**不在本 phase**：NPU 加速（Phase 10）、雲端教師閉環（Phase 11）、Nova Sonic 連網 staging（Phase 12）、斷網視覺呈現的大改版（本輪沿用既有小徽章，未鎖定戲劇化改版）。

</domain>

<decisions>
## Implementation Decisions

### 斷網觸發方式（NETCUT-01）
- **D-01（鎖定）：** kill-switch = **純軟體 toggle**，沿用/改造既有 `POST /api/network_mode`（`server/app.py:206-259`）與 `web/index.html` 的 `airplaneSwitch`/`applyMode()`（lines 684-732）。不做「真實實體斷網（拔線/關 AP）」路徑——理由：舞台上最穩定、不依賴現場 Wi-Fi 硬體，不會發生「拔了線網路不回來」的風險。
- **D-02（鎖定）：** **不加自動網路偵測安全網**。既然 kill-switch 是純軟體 toggle，`pipeline.network_mode` 直接、確定地閘門雲端呼叫（見 `server/pipeline.py:260-266`），開關狀態與行為完全一致，不需要額外偵測「現場 Wi-Fi 真的斷線」的邏輯。若未來真的改用實體斷網，才需要回頭補這層。
- **D-03（鎖定）：** 中途斷網（主持人在雲端 LLM/TTS 請求進行到一半按下開關）**採「縮短逾時自然降級」，不做 asyncio 取消/重跑機制**。理由：不做取消機制，只把雲端 LLM/TTS 逾時從目前 `_TIMEOUT_S=8.0`（`server/cloud_llm.py:21`）、`CLOUD_TTS_TIMEOUT_S=6.0`（`server/config.py:112`）、`LLM_TIMEOUT_S=8.0`（`server/pipeline.py:29`）大幅縮短（目標對齊 ROADMAP 的 <1–2 秒恢復門檻），遇斷線很快就逾時降級到 edge，避開 asyncio 取消/重跑的競態風險，且該輪對話仍能完整完成（只是走 edge 回覆）。

### 主持人操作介面
- **D-04（鎖定）：** **直接沿用學生畫面上的既有飛航模式按鈕**（`web/index.html` 的 `airplaneSwitch`）當主持人主控，不另開主持人專用頁面/路由，不做鍵盤快捷鍵。現場由主持人親自操作或站在孩子旁邊代為點擊。
- **D-05（鎖定）：** **不加防誤觸機制**。維持現狀行為（單點即切，不彈確認框、不用長按）。現場主持人全程控場；誤點也只是再點一次切回來，不需額外實作。

### Claude's Discretion
- 縮短後的具體逾時數字（如 1s / 1.5s / 2s）由 planner/executor 依 ROADMAP D-05 沿用的「<1–2 秒恢復」門檻與真機/PC 實測結果決定，本輪只鎖定方向（大幅縮短、不做取消機制）。
- `/api/status` 5 秒輪詢（`web/index.html:998`）與教師儀表板 5 秒輪詢（`web/teacher.html:631`）皆為本機 loopback/既有機制，非雲端呼叫，NETCUT-02「背景輪詢於離線視窗暫停」若有適用對象由 executor 依實際程式碼確認後決定範圍（掃描本輪未發現額外背景雲端輪詢器）。
- 現有飛航模式的 toast 文案（「✈️ 飛航模式開啟，改用邊緣端運算」）與小徽章視覺是否需微調文字以呼應「斷網示範」語境，由 executor 依既有風格調整，不視為新決策。

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### kill-switch 既有實作（D-01 起點）
- `server/app.py:191-259` — 既有 `NetworkModeBody` + `POST /api/network_mode`：edge/cloud 切換、consent gate（B4-5）、cloud 分支的 mark_all_synced + generate_diagnosis 邏輯。
- `web/index.html:684-732` — 既有 `refreshStatus()`/`applyMode()`/`airplaneSwitch` click handler：飛航模式 UI 狀態機、toast 文案、`/api/network_mode` 呼叫。
- `server/pipeline.py:166` — `self.network_mode: str = "edge"`（per-connection 狀態）；`server/app.py:61` — 全域 `pipeline.network_mode = config.default_network_mode()`；`server/app.py:369` — 新連線承接目前模式。

### 逾時縮短標的（D-03）
- `server/cloud_llm.py:20-21,86` — `_TIMEOUT_S = 8.0`，`urlopen(req, timeout=_TIMEOUT_S)`。
- `server/config.py:112` — `CLOUD_TTS_TIMEOUT_S: float = float(os.environ.get("CLOUD_TTS_TIMEOUT_S", "6.0"))`。
- `server/pipeline.py:29,255-282` — `LLM_TIMEOUT_S = 8.0`；cloud→edge→scaffold 降級鏈的 `asyncio.wait_for(..., timeout=LLM_TIMEOUT_S)` 序列式嘗試邏輯（非 race）。
- `server/cloud_tts.py:60,99` — `CLOUD_TTS_TIMEOUT_S` 實際用於 `urlopen` 的位置。

### Milestone / 需求脈絡
- `.planning/REQUIREMENTS.md` §NETCUT-01~03 全文與 Out of Scope。
- `.planning/ROADMAP.md` §Phase 9 — Goal 與 4 條 success criteria（含「恢復時間 <1–2 秒」「≥3 次實體斷網重複演練」的既鎖定數字）。
- `.planning/PROJECT.md` — 「決賽創意/可行性評分最高槓桿的記憶點」定位，張力：Phase 9 為決賽記憶點但非 Phase 8（存亡關鍵）本身。

### 前一 phase 交接（Phase 8）
- `.planning/phases/08-cpu-only-offline-edge-turn-loop/08-CONTEXT.md` — Genio 520 CPU-only 離線迴路已交付（真機 A GO 穩態 2.96–2.99s、冷啟動暖身後 5.85s 仍 NO-GO）；Phase 9 的 edge 對話路徑建立在此之上，斷網後降級到的「edge」engine 即 Phase 8 交付的 llama-server + ALSA 迴路。
- `.planning/STATE.md` §Pending Todos — 08-05 殘留缺口（暖身後冷啟動仍 5.85s NO-GO）非本 phase 阻擋項，現場暫以「主持人先暖場一輪」規避；若 Phase 9 彩排腳本涵蓋冷啟動情境，需注意此已知落差。

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `/api/network_mode` + `airplaneSwitch` UI：已是完整可運作的 edge/cloud 手動切換機制，D-01 直接沿用其骨幹，不必從零打造 kill-switch。
- `pipeline.network_mode` 閘門邏輯（`server/pipeline.py:260-266`）：cloud LLM 只在 `network_mode=="cloud"` 且 `guardrails.consent_granted()` 時進入 engines 清單，天生具備「開關=決定路徑」的確定性，支撐 D-02（不需自動偵測）的判斷基礎。

### Established Patterns
- 降級鏈設計（CloudLLM→EdgeLLM→scaffold；`server/pipeline.py:255-282`）：任一層逾時/例外/None 續試下一層，`asyncio.wait_for(timeout=LLM_TIMEOUT_S)` 已是現成的逾時保護點，D-03 的「縮短逾時」直接調整此常數與 `cloud_llm.py`/`config.py` 對應常數即可，不需重構降級鏈結構本身。
- Consent gate（`guardrails.consent_granted()`）：cloud 分支進入前已有硬性檢查，斷網示範時 kill-switch 切到 edge 不受此影響（edge 分支無 consent 檢查）。

### Integration Points
- kill-switch UI（`web/index.html` airplaneSwitch）→ `POST /api/network_mode` → `pipeline.network_mode` → `server/pipeline.py:_process_text()` 的 LLM engines 清單組裝。
- 縮短後的逾時常數 → `server/cloud_llm.py::_TIMEOUT_S`、`server/config.py::CLOUD_TTS_TIMEOUT_S`、`server/pipeline.py::LLM_TIMEOUT_S` 三處需同步調整，維持「與 pipeline 外層對齊、雙保險」的既有註解慣例（`server/cloud_llm.py:20`）。

</code_context>

<specifics>
## Specific Ideas

- 現場示範敘事：主持人在學生畫面上（或代替孩子）點擊既有的飛航模式開關，孩子與說說學伴的對話應「無縫」持續——縮短逾時是達成這個「無縫感」的核心技術手段，而非取消/重跑等複雜機制。
- 使用者明確拒絕了「真實實體斷網」與「主持人專用隱藏介面」兩個更複雜的方向，優先選擇舞台穩定性與最小實作量——與 08-05 收尾時「決賽剩 5 天，不宜為了硬湊數字反覆調整」的時間壓力判斷一致。

</specifics>

<deferred>
## Deferred Ideas

- **真實實體斷網（拔線/關 AP）作為 kill-switch**：本輪明確不選，若未來現場彩排發現純軟體 toggle 說服力不足（評審質疑「是不是其實還連著網路」），可回頭評估——需同時補上自動偵測邏輯（D-02 的前提會改變）。
- **主持人專用操作介面（獨立頁面/路由或鍵盤快捷鍵）**：本輪明確不選，優先沿用學生畫面既有按鈕。若彩排時發現小朋友頻繁誤觸影響節奏，可回頭補此項。
- **斷網視覺呈現的戲劇化改版**（大徽章/全螢幕狀態轉場）：使用者本輪未選擇討論此主題（僅選了觸發方式與操作介面），現況小徽章+文字予以沿用；若要加強觀眾/評審的「記憶點」效果，可在後續補強或於彩排後依實際觀感決定。
- **自動網路偵測安全網**：本輪因純軟體 toggle 而判定不需要，若觸發方式決策未來改變（見上），需重新評估此項。

</deferred>

---

*Phase: 9-network-cut-demo-hardening*
*Context gathered: 2026-07-25*
