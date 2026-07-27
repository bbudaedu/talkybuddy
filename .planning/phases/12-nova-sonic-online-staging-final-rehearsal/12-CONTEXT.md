# Phase 12: Nova Sonic Online Staging & Final Rehearsal - Context

**Gathered:** 2026-07-27
**Status:** Ready for planning

<domain>
## Phase Boundary

在決賽現場，Nova Sonic 連網 S2S 已完成 staging，可作為斷網橋段前「連網」半場的可靠演出；並完成含斷網橋段的完整端到端彩排與備援影片。

**Requirements:** NOVA-01（`.planning/REQUIREMENTS.md` §49）

**這個 phase 不是寫功能。** Nova Sonic 本身於 Phase 4 已交付（`server/nova_sonic.py`，`amazon.nova-2-sonic-v1:0`，`available()` 以 AWS 憑證 + `aws_sdk_bedrock_runtime`/`smithy_aws_core` 可 import 為閘門）。本 phase 的交付物是**演出編排、彩排證據、備援影片、書面 cutline**。

**決賽日期：2026-08-01**（使用者於 2026-07-27 更正；先前專案內多處誤記為 07-30）。

</domain>

<decisions>
## Implementation Decisions

### 決賽劇本順序（SC1、SC2）
- **D-01（鎖定）：** 完整順序為 **雲端半場（Nova Sonic 即時對話）→ 主持人實體斷網 → 裝置持續離線對話 → 插回網路 → 教師儀表板出現新診斷**。
  - 理由：這條順序把 Phase 4/12（Nova Sonic）、Phase 8（離線迴路）、Phase 9（斷網硬化）、Phase 11（雲端教師閉環）四個 phase 的成果串成**單一連續敘事**，而不是四個各自獨立的展示。斷網瞬間的對比效果也最強——觀眾剛看完雲端即時對話，才會對「拔掉網路還能講」有感。
  - 推論：Phase 11 的「插回網路後**不必等孩子再說話**就出現新診斷」（D-03a 轉換瞬間觸發）正是這條劇本的收尾動作，兩者必須在同一次彩排中一起驗證。

### 現場斷網手法（SC1、SC4）
- **D-02（鎖定）：** **實體拔除乙太網路線**作為主要斷網手法。
  - 理由：觀眾看得懂，說服力遠高於畫面上按一個鈕。Genio 520 只有乙太網路可用（藍芽與 USB WiFi 皆已實測不可行，見記憶 `project-genio520-hardware`），拔線在情境上也最自然。
  - **必須在彩排中量測的風險**：插回網路後 DHCP 重新取得位址的時間。Phase 9 的 M2（可聽見回覆恢復時間）是針對 kill-switch 量的，**實體拔線的恢復時間是不同的數字，不可沿用**。若 DHCP 太慢，需準備靜態 IP 作為備案。
  - Phase 9 的 kill-switch 不廢除，保留為**彩排與救場工具**（現場若拔線恢復不順，可用它快速回到已知狀態）。

### 備援影片範圍（SC3）
- **D-03（鎖定）：** 60–90 秒影片**只涵蓋最脆弱的斷網橋段**：斷網 → 離線對話 → 恢復。其餘環節現場實跑。
  - 理由：錄製成本最低，且精準對準最可能出包的環節（依賴實體硬體、實體網路、真機延遲三者同時成立）。錄完整一輪需要先能完整跑過一次，是循環依賴；且 60–90 秒也裝不下全場。
  - 推論：影片必須是**真機實拍畫面**（含 badge 狀態變化），不可用簡報動畫或螢幕模擬——否則失去「這是真的」的證據價值。

### Claude's Discretion
- 彩排腳本的檔案位置與格式：比照既有 `edge/NETWORK_CUT_REHEARSAL.md` 與 `docs/TCLOUD_VERIFY.md` 的體例，由 planner/executor 決定要新增檔案或擴充既有檔案。
- 影片的錄製方式（手機外拍 vs 螢幕錄影 + 外拍剪接）與工具，由執行時依現場可得資源決定；但**必須是真機實拍**（D-03）。
- cutline 的具體判準文字，由 planner 依 ROADMAP SC4 原文起草；但必須是**可執行的判準**（明確的時間點與觀察條件），不是「視情況而定」。

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 需求與範圍
- `.planning/REQUIREMENTS.md` §NOVA-01 — 需求原文與「最低優先、落後先砍」定位
- `.planning/ROADMAP.md` §Phase 12 — Goal 與四條 success criteria（含 SC4 書面 cutline）

### 既有彩排資產（擴充，不重造）
- `edge/NETWORK_CUT_REHEARSAL.md` — Phase 9 斷網彩排腳本：M1（降級決策延遲）／M2（可聽見回覆恢復時間）操作定義、兩種演練型態、結果表。**D-02 的實體拔線恢復時間需新增為第三種型態**
- `docs/TCLOUD_VERIFY.md` — Phase 11 雲端教師閉環彩排腳本（五步驟，含 `source == "rule"` 不算通過的判準）
- `edge/EDGE_TURN_LOOP_VALIDATION.md` — Phase 8 真機延遲基線：`{'asr': 405, 'llm': 4170, 'tts_first': 1209, 'round_total': 5852}`（冷啟動）／穩態 2.96–2.99s

### 實作與設定
- `server/nova_sonic.py:30-41` — `available()` 閘門：AWS 憑證 + SDK import 皆須成立
- `server/config.py:100-102` — `NOVA_SONIC_MODEL_ID`（`amazon.nova-2-sonic-v1:0`）、`NOVA_SONIC_VOICE`（`tiffany`）、`LIVE_S2S_ENABLED`
- `edge/BOARD_BRINGUP_DECISION.md` — Genio 520 硬體與 OS 決策紀錄

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **Nova Sonic 全套已交付**（Phase 4）：`NovaSonicSession`（start/send_audio/end_user_turn/events）、`/ws/live` 端點、瀏覽器端 AEC。本 phase 不改這些程式碼。
- **Phase 9 kill-switch** 保留為彩排與救場工具（見 D-02）。
- **既有兩份彩排腳本**已建立體例（量測定義 → 演練型態 → 結果表 → 判準），第三份應沿用同一結構而非另創格式。

### Established Patterns
- 彩排腳本的判準必須**機器可讀或明確可觀察**（Phase 11 的 `source == "rule"` 不算通過、Phase 9 的 M1/M2 秒數門檻），不得寫成主觀描述。
- 真機驗證一律由使用者親自執行（見記憶 `user-context`），Claude 負責產出腳本與判準、不代跑、不代為核准。

### Integration Points
- 劇本第 4 步「插回網路」直接觸發 Phase 11 的 `api_network_mode` → `opportunistic_sync()` → `generate_diagnosis()` 鏈路。
- 劇本第 5 步「儀表板出現診斷」的驗收條件即 `docs/TCLOUD_VERIFY.md` 的第 4/5 步。

</code_context>

<specifics>
## Specific Ideas

- **主持人必須先暖場一輪。** Phase 8 記錄冷啟動首句 5.85s（NO-GO，門檻 3–4s），穩態才是 2.96–2.99s。彩排腳本必須明文要求：在觀眾看到第一句對話之前，主持人先跑一次拋棄式暖身回合。這是已接受的營運性補救，不是可選項。
- **裝置端沒有喚醒詞。** `edge/runtime/local_client.py` 是固定 4 秒錄音 + Enter 觸發，喚醒詞只存在於瀏覽器端。劇本中裝置側的每一次發話都由主持人手動觸發，不可設計成「喊說說學伴」的橋段。
- 斷網橋段的說服力來自三件事同時成立：實體拔線（看得見）＋ badge 變離線（畫面確認）＋ 對話不中斷（聽得見）。彩排要逐項確認，缺一項就削弱敘事。

</specifics>

<deferred>
## Deferred Ideas

- **裝置端喚醒詞／VAD 斷句** — 全新工程量（見記憶 `project-demo-strategy`），決賽前不做，以主持人手動觸發代替。
- **NPU/GPU 硬體加速** — Phase 10 軌道，與本 phase 無依賴關係；本 phase 的劇本與彩排不得假設任何加速已就位。
- **Phase 8 冷啟動 5.85s 的結構性修法**（把回覆格式指令移入 system prompt）— 2026-07-27 已實測並**放棄**，見 `edge/PROMPT_ORDERING_FINDING.md`：快取面省 1.2s，但中文稱讚合規率從 5/5 掉到 0/5。彩排改以「主持人先暖場」處理。

</deferred>

---

*Phase: 12-Nova Sonic Online Staging & Final Rehearsal*
*Context gathered: 2026-07-27*
