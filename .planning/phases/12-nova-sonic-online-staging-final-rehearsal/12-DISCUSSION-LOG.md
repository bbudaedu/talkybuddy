# Phase 12: Nova Sonic Online Staging & Final Rehearsal - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-27
**Phase:** 12-Nova Sonic Online Staging & Final Rehearsal
**Areas discussed:** 決賽劇本順序、現場斷網手法、備援影片範圍

---

## 決賽劇本順序

| Option | Description | Selected |
|--------|-------------|----------|
| 雲端半場 → 斷網 → 離線 → 恢復（建議） | Nova Sonic 即時對話先震撼 → 主持人斷網 → 裝置繼續離線對話 → 插回網路 → 教師儀表板出現診斷。四個 phase 的成果串成一條敘事 | ✓ |
| 離線先行、雲端收尾 | 先展示離線實力（不依賴網路，最低風險），再接網路展示 Nova Sonic 與雲端閉環 | |
| 只做斷網橋段，砍 Nova Sonic | 直接執行 ROADMAP 的 cutline——Nova Sonic 本來就是「落後先砍」項 | |

**User's choice:** 雲端半場 → 斷網 → 離線 → 恢復
**Notes:** 斷網瞬間的對比效果來自順序——觀眾剛看完雲端即時對話，才會對「拔掉網路還能講」有感。此選擇也讓 Phase 11 的「插回網路不必等孩子再說話就出診斷」成為劇本的自然收尾，兩者需在同一次彩排驗證。

---

## 現場斷網手法

| Option | Description | Selected |
|--------|-------------|----------|
| 實體拔網路線（建議） | 觀眾看得懂，說服力最高。Genio 520 只有乙太網路可用，拔線很自然。風險：插回後 DHCP 重取位址可能慢 | ✓ |
| Phase 9 的 kill-switch | 畫面上按一下，badge 即時變離線。最可控、恢復最快，但觀眾可能質疑「只是改了個設定」 | |
| 兩者都做 | 先拔線（物理證明），畫面 badge 同步變離線（視覺確認）。說服力最強，但彩排要驗證兩者真的同步 | |

**User's choice:** 實體拔網路線
**Notes:** 已在 CONTEXT.md 記錄一項必須彩排量測的風險——實體拔線的恢復時間與 Phase 9 kill-switch 的 M2 是**不同的數字**，不可沿用。若 DHCP 太慢需備靜態 IP。kill-switch 不廢除，保留為彩排與救場工具。

---

## 備援影片範圍

| Option | Description | Selected |
|--------|-------------|----------|
| 只錄最脆弱的斷網橋段（建議） | 影片只涵蓋「斷網 → 離線對話 → 恢復」真機畫面，其餘現場實跑。錄製成本最低，且對準最可能出包的環節 | ✓ |
| 錄完整一輪演出 | 從頭到尾都錄，任何環節出包都能切。最安全，但要先能完整跑過一次才錄得成，且 60–90 秒裝不下 | |
| 錄兩段：離線 + 雲端閉環 | 分別錄，現場哪段出包切哪段。彈性最好，成本中等 | |

**User's choice:** 只錄最脆弱的斷網橋段
**Notes:** CONTEXT.md 追加一條推論：影片必須是真機實拍（含 badge 狀態變化），不可用簡報動畫或螢幕模擬，否則失去「這是真的」的證據價值。

---

## Claude's Discretion

- 彩排腳本的檔案位置與格式（擴充既有 `edge/NETWORK_CUT_REHEARSAL.md` vs 新增檔案）
- 影片錄製方式與工具（但必須真機實拍）
- cutline 判準的具體文字（但必須可執行，不得寫成「視情況而定」）

## Deferred Ideas

- 裝置端喚醒詞／VAD 斷句 — 全新工程量，決賽前不做
- NPU/GPU 硬體加速 — Phase 10 軌道，本 phase 不得假設任何加速已就位
- Phase 8 冷啟動的結構性修法 — 2026-07-27 已實測並放棄（`edge/PROMPT_ORDERING_FINDING.md`），改以主持人暖場處理
