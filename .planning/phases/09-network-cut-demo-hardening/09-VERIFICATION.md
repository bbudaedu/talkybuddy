---
phase: 09-network-cut-demo-hardening
verified: 2026-07-25T21:30:00Z
status: human_needed
score: 5/6 must-haves verified
behavior_unverified: 0
overrides_applied: 0
human_verification:
  - test: "現場（或至少裝置旁）執行 edge/NETWORK_CUT_REHEARSAL.md 的 ≥3 次真機斷網演練（至少 1 次型態 B 講話中途切換），把 §5 結果表回填為真實量測數字"
    expected: "每列 M1（降級決策延遲）< 2.0s（型態 B 理論上界 3.0s）、無多秒靜默；每列附 `dump_recent_turns.py` 輸出作為證據；ROADMAP Phase 9 success criterion #4 才算達成"
    why_human: "NETCUT-03 依定義是 Genio 520 真機人工演練，無法由 agent 代跑、代量或推估（09-04-PLAN.md 明文禁止填入未實測數字，SUMMARY 亦誠實記錄此項 pending）"
  - test: "在瀏覽器實際點擊一次 airplaneSwitch，肉眼確認 modeBadge 播放一次縮放 pulse 動效；靜置 ≥15 秒（≥3 拍 /api/status 輪詢）確認徽章不會自行閃動"
    expected: "主動切換時徽章明顯閃一下吸引目光；被動 5 秒輪詢期間徽章視覺上完全靜止"
    why_human: "這是瀏覽器渲染的視覺行為，grep/靜態程式碼檢查只能證明『pulse 觸發程式碼只存在於 click handler、不存在於 applyMode()/refreshStatus() 函式體內』，不能證明動效在真實瀏覽器中確實如預期呈現（09-03-SUMMARY.md 亦自陳此步驟未在無頭執行環境中實測）"
---

# Phase 9: Network-Cut Demo Hardening Verification Report

**Phase Goal:** 現場主持人可隨時手動切斷裝置的雲端連線，孩子與說說學伴的對話完全不受影響地持續離線進行，且不會出現多秒靜默 hang——這是決賽創意與可行性評分最高槓桿的記憶點。
**Verified:** 2026-07-25T21:30:00Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth (ROADMAP Success Criterion / NETCUT-ID) | Status | Evidence |
|---|---|---|---|
| 1 | NETCUT-01 — 主持人手動 kill-switch 對**進行中**的 `/ws/talk` 連線真正生效（不重整頁面、不重連 WS），下一回合改走 edge；loopback WS 不受影響 | ✓ VERIFIED | `server/app.py:424,483` 兩處 `conn_pipe.network_mode = pipeline.network_mode` 再同步已存在於程式碼（連同 line 376 的連線期初值共 3 處）；行為性回歸測試 `tests/test_e2e.py::test_network_mode_switch_affects_live_ws_session` 單獨執行 PASS（本次驗證親自跑過，非僅讀 SUMMARY），斷言切換後 cloud stub 未被再次呼叫、edge stub 被呼叫、第二回合 interaction row `network_mode=="edge"`、WS 全程未斷線重連 |
| 2 | NETCUT-01 — `POST /api/network_mode` 需有效 JWT，缺/壞 token → 401，且不改變全域模式；不限角色 | ✓ VERIFIED | `server/app.py:229` `identity_from_header(authorization)` 在 400 驗證前呼叫，程式碼現場確認；`tests/test_e2e.py::test_post_network_mode_requires_token` / `test_post_network_mode_invalid_token_returns_401` 於本次全套件執行中 PASS；`grep 'claims\["role"\]'` 於 `api_network_mode` 函式內無命中，未加角色限制 |
| 3 | NETCUT-02 — 雲端 LLM/TTS 內層逾時 <= 2.0s（1.5s），`LLM_TIMEOUT_S` 維持 >= 6.0（8.0）不被一併砍短，避免 edge 引擎（真機最壞 4170ms）被餓死；兩者可用環境變數覆寫 | ✓ VERIFIED | `server/cloud_llm.py:30` `_TIMEOUT_S = float(os.environ.get("CLOUD_LLM_TIMEOUT_S","1.5"))`；`server/config.py:113` `CLOUD_TTS_TIMEOUT_S` 同款、預設 1.5；`server/pipeline.py:36` `LLM_TIMEOUT_S: float = 8.0` 未變；`tests/test_pipeline_timeout_isolation.py` 的常數契約 + 行為隔離測試（正例/反例）於本次執行 PASS |
| 4 | NETCUT-02 — 背景 `_refresh_directive` 診斷刷新在 `network_mode=="edge"` 時不觸發雲端呼叫（關掉唯一繞過 kill-switch 的側通道），本地規則式刷新仍運作 | ✓ VERIFIED | `server/diagnose.py:628` `if allow_cloud and cfg and guardrails.consent_granted()`；`server/pipeline.py:341,347` `_refresh_directive` 依 `self.network_mode` 傳入 `allow_cloud`；`tests/test_diagnose_network_gate.py` 全部 4 條於本次執行 PASS，含 edge 模式下 `vp._directive is not None` 的本地刷新未被誤殺斷言 |
| 5 | NETCUT-02 — `/api/status`／教師儀表板 5 秒輪詢在離線視窗零出境呼叫（NETCUT-02「背景輪詢暫停」的適用範圍） | ✓ VERIFIED | `tests/test_pipeline_timeout_isolation.py::test_api_status_makes_no_outbound_call` 以 `urlopen` 間諜（依 hostname 排除 loopback 健康檢查）證明連續兩拍輪詢零真正出境呼叫，本次執行 PASS |
| 6 | ROADMAP Phase 9 success criterion #3 — UI 提供明確可見的 online/offline 狀態切換（badge），舞台距離可辨識，一次性動效只由主動切換觸發、5 秒輪詢絕不觸發 | ⚠️ 程式碼已就位，視覺行為未經瀏覽器實測 | `web/index.html` 現場確認：`.badge{padding:4px 12px}`、`.badge .dot{width:12px;height:12px}`、`@keyframes badgePulse`/`.badge.pulse` 存在；`applyMode()`（line 694-706）與 `refreshStatus()`（line 687-692）函式體內確認零 pulse 相關字串；pulse 觸發碼確實位於 click handler 的 `applyMode(target)` 之後（line 717-723）。但徽章實際在瀏覽器中是否如預期閃動、5 秒輪詢期間是否確實靜止，屬視覺渲染行為，09-03-SUMMARY.md 自陳此步驟未在無頭執行環境中人工檢視過——**路由至人工驗證** |
| 7 | ROADMAP Phase 9 success criterion #4 / NETCUT-03 — ≥3 次實體斷網重複演練（含講話中途斷網），每次恢復時間（M1）<1–2 秒 | 未達成（明確 pending，非缺陷） | `edge/NETWORK_CUT_REHEARSAL.md` §5 結果表除標明 `<!-- 範例 -->` 的範例列外**完全空白**；09-04-SUMMARY.md 誠實記錄「NOT completed — requires a human physically at the Genio 520 device」。此為 `human_verify_mode: end-of-phase` 下的預期延遲項，依任務指示**不計為 gap**，路由至人工驗證 |

**Score:** 5/6 automatable must-haves verified (truth #7 是 phase 設計上明確排除於自動化驗證之外的真機演練項，不計入本欄分母；若含入則為 5/7）

### Required Artifacts

| Artifact | Expected | Status | Details |
|---|---|---|---|
| `server/app.py` | NETCUT-01 re-sync fix + JWT gate | ✓ VERIFIED | 3 處 `conn_pipe.network_mode = pipeline.network_mode`、`identity_from_header` 閘門皆現場確認存在且置於 400 檢查之前 |
| `server/cloud_llm.py` | 雲端 LLM 逾時縮短、env 可覆寫 | ✓ VERIFIED | `_TIMEOUT_S` 現場確認為 `float(os.environ.get("CLOUD_LLM_TIMEOUT_S","1.5"))` |
| `server/config.py` | 雲端 TTS 逾時縮短 | ✓ VERIFIED | `CLOUD_TTS_TIMEOUT_S` 現場確認預設 1.5 |
| `server/pipeline.py` | `LLM_TIMEOUT_S` 維持寬鬆 + `_refresh_directive` 側通道閘門 | ✓ VERIFIED | 值仍為 8.0；`allow_cloud` 傳參現場確認 |
| `server/diagnose.py` | `generate_diagnosis(allow_cloud=True)` 第三道出境閘門 | ✓ VERIFIED | 簽名與條件式現場確認 |
| `web/index.html` | badge 視覺強化 + pulse 動效 + toast 文案 | ✓ VERIFIED（程式碼層）/ ⚠️ 視覺渲染未人工確認 | CSS/HTML/JS 全部現場 grep 確認存在且位置正確；瀏覽器實測留待人工 |
| `edge/NETWORK_CUT_REHEARSAL.md` | 彩排腳本、M1/M2 操作定義、結果表 | ✓ VERIFIED（文件本身）/ 結果表待填 | 六節齊全，§5 結果表為空（預期，pending human-verify） |
| `edge/runtime/dump_recent_turns.py` | 彩排量測客觀證據工具 | ✓ VERIFIED | 存在、可 import、`tests/test_dump_recent_turns.py` 5 條測試本次執行全數 PASS |
| `tests/*`（09-01~09-04 新增測試） | 對應行為的自動化守門 | ✓ VERIFIED | 全套件本次執行 `347 passed`，與 SUMMARY 宣稱一致 |

### Key Link Verification

| From | To | Via | Status | Details |
|---|---|---|---|---|
| `POST /api/network_mode` | 全域 `pipeline.network_mode` | line 236 唯一寫入者 | ✓ WIRED | `grep 'pipeline.network_mode = '` 全專案僅 3 處寫入，皆在 `server/app.py`（初始化 1 處 + 端點內 2 處），無第二寫入來源 |
| 全域 `pipeline.network_mode` | `conn_pipe.network_mode`（每回合） | 兩個 dispatch 點的再同步讀取 | ✓ WIRED | 現場確認、且行為測試親自執行通過 |
| `conn_pipe.network_mode` | `VoicePipeline._process_text` engines 清單閘門 | `server/pipeline.py:260-266` 既有邏輯，本 phase 未改動閘門本身，僅改動它讀到的值 | ✓ WIRED | 行為測試（`test_network_mode_switch_affects_live_ws_session`）間接證明此鏈路整體生效 |
| `cloud_llm._TIMEOUT_S` / `config.CLOUD_TTS_TIMEOUT_S` | 各自 `urlopen(timeout=...)` | `server/cloud_llm.py:95`、`server/cloud_tts.py:99`（透過函式內 import config） | ✓ WIRED | 消費點程式碼未被本 phase 改動，僅常數值改變，維持既有連結 |
| `airplaneSwitch` click 成功回呼 | `modeBadge.classList.add("pulse")` | `applyMode(target)` 之後 | ✓ WIRED | 位置順序現場確認正確（避免被 `applyMode()` 的 `className` 整段覆寫洗掉） |
| `VoicePipeline._refresh_directive` | `diagnose.generate_diagnosis(allow_cloud=...)` | `server/pipeline.py:341,347` | ✓ WIRED | 現場確認 + 行為測試通過 |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|---|---|---|---|
| 活體 WS 切換即時生效（NETCUT-01 核心行為） | `.venv/bin/python -m pytest tests/test_e2e.py::test_network_mode_switch_affects_live_ws_session -x -q` | `1 passed` | ✓ PASS |
| NETCUT-01/02/03 相關新測試（4 檔）獨立執行 | `.venv/bin/python -m pytest tests/test_e2e.py tests/test_pipeline_timeout_isolation.py tests/test_diagnose_network_gate.py tests/test_dump_recent_turns.py -q` | `27 passed` | ✓ PASS |
| 全套件回歸（一次性執行，未重複跑整包對每個 must-have 各跑一次） | `.venv/bin/python -m pytest tests/ -q` | `347 passed, 3 warnings` | ✓ PASS（與 09-04-SUMMARY 宣稱的 347 一致） |
| 內嵌 `<script>` 語法（`web/index.html`） | `node --check` on extracted inline JS | 無錯誤 | ✓ PASS |
| 彩排真機演練（NETCUT-03） | N/A（不可自動化） | — | ? SKIP — 路由至人工驗證 |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|---|---|---|---|---|
| NETCUT-01 | 09-01 | 主持人手動 kill-switch 為主要斷網機制；切斷雲端 uplink 時裝置持續離線對話 | ✓ SATISFIED | 再同步修復 + JWT 閘門，皆現場確認且有行為測試守護 |
| NETCUT-02 | 09-02, 09-03 | 縮短/race 雲端 timeout 並暫停背景輪詢，提供可見 online/offline UI/badge | ✓ SATISFIED（後端）/ ⚠️ 待人工確認（前端視覺） | 逾時常數、側通道閘門、輪詢零出境皆現場確認；badge 視覺渲染待人工在瀏覽器確認 |
| NETCUT-03 | 09-04 | 實體斷網彩排腳本（重複實機演練，非只自動偵測） | 腳本/工具 SATISFIED；真機數據 PENDING（人工） | `edge/NETWORK_CUT_REHEARSAL.md` 與 `dump_recent_turns.py` 皆交付且測試守護；§5 結果表未填（依 `human_verify_mode: end-of-phase` 設計上延後，非缺陷） |

**Orphaned requirements check:** REQUIREMENTS.md §Phase 9 對照表僅列 NETCUT-01/02/03，三者皆已在 09-01~09-04 的 PLAN frontmatter `requirements:` 欄位中被聲明覆蓋，無孤兒需求。

### Anti-Patterns Found

掃描本 phase 修改過的全部檔案（`server/app.py`、`server/pipeline.py`、`server/cloud_llm.py`、`server/config.py`、`server/diagnose.py`、`web/index.html`、`edge/runtime/dump_recent_turns.py`、`edge/NETWORK_CUT_REHEARSAL.md`），`TBD|FIXME|XXX|TODO|HACK|PLACEHOLDER|not yet implemented|not available|coming soon` 一律零命中。無 debt marker，無 stub 樣式（`return null`/空物件等）落在本 phase 改動的程式路徑上。

### Deferred Items

無。ROADMAP 未把 NETCUT-03 的真機演練排到其他 phase；它就是本 phase success criterion #4 本身，只是依 `human_verify_mode: end-of-phase` 設計上路由至人工驗證階段，而非缺陷或延後至他期。

## Human Verification Required

### 1. NETCUT-03 真機斷網彩排（≥3 次，含 ≥1 次型態 B）

**Test:** 依 `edge/NETWORK_CUT_REHEARSAL.md` §0–§4 在 Genio 520（`root@192.168.31.78`）上執行至少 3 次演練（至少 1 次講話中途切換），每次以 `dump_recent_turns.py` 取得客觀證據並回填 §5 結果表。
**Expected:** 每列 M1（降級決策延遲）< 2.0s（型態 B 理論上界 3.0s），無多秒靜默；§6 判定為 GO。
**Why human:** NETCUT-03 依定義是真機人工演練，不可由 agent 代跑/代量/推估（09-04-PLAN.md 與 SUMMARY 皆明文自陳此邊界）。

### 2. modeBadge pulse 動效與 5 秒輪詢靜默的瀏覽器實測

**Test:** 開學生頁，點一下 `airplaneSwitch`，觀察徽章是否播放一次縮放動效；接著靜置 ≥15 秒（≥3 拍 `/api/status` 輪詢），觀察徽章是否保持靜止。
**Expected:** 主動切換時徽章明顯閃一下；被動輪詢期間徽章不閃動。
**Why human:** 這是瀏覽器渲染層的視覺行為；靜態程式碼檢查只能證明「pulse 觸發程式碼的位置與範圍正確」，無法證明動效實際在瀏覽器中呈現符合預期，且 09-03-SUMMARY.md 已自陳此步驟未在本次無頭執行環境中完成。

## Gaps Summary

無 gaps。所有可自動化驗證的 must-have（NETCUT-01、NETCUT-02 的後端行為、彩排腳本/工具本身）皆現場確認存在、正確接線，且有行為測試守護並於本次驗證中親自執行通過（非僅信任 SUMMARY 宣稱）。剩餘兩項未達 `passed` 狀態的原因純粹是「需要人眼/真機才能確認」，而非程式碼缺陷：(1) NETCUT-03 的真機演練數字依設計延後至 `human_verify_mode: end-of-phase`；(2) badge pulse 動效的瀏覽器視覺呈現未經人工檢視。兩者皆已誠實記錄在對應 SUMMARY 中，非本次驗證新發現的缺口。

---

*Verified: 2026-07-25T21:30:00Z*
*Verifier: Claude (gsd-verifier)*
