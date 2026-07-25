# Phase 10: NPU-Accelerated Perception - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-25
**Phase:** 10-npu-accelerated-perception
**Areas discussed:** NDA / 帳號狀態, 時間預算與 stop-loss

---

## NDA / 帳號狀態

### Q1: NPU-01 需要下載 MediaTek「NeuroPilot Public」tier 的 NP8 Converter（免 NDA 但需帳號註冊），這一步只有使用者能做。目前狀態是？

| Option | Description | Selected |
|--------|-------------|----------|
| 已有帳號/已下載 | 可直接開始 spike，立即著手 ORT-NeuronEP vs TFLite 比較與真機驗證。 | ✓ |
| 還沒有，現在去辦 | 使用者先去 neuropilot.mediatek.com 註冊，agent 先做不需帳號的部分。 | |
| 不確定/不想辦，先跳過 Phase 10 | 直接套用 stop-loss，記為 not-attempted，轉去做 Phase 11。 | |

**User's choice:** 已有帳號/已下載
**Notes:** `ask_user_questions` 連續兩次回傳 `cancelled: true`，改用純文字詢問後於 2026-07-25 取得使用者實際回覆「1.有帳號 2.原預算」。

## 時間預算與 stop-loss

### Q2: 距決賽約剩 5 天，Phase 9 還卡在人工真機驗證、Phase 11/12 都還沒動。NPU spike 要投入多少時間？

| Option | Description | Selected |
|--------|-------------|----------|
| 照原訂 1–2 天硬性 time-box | 嚴格執行 ROADMAP 已寫好的 stop-loss：中途檢查點沒看到可運作的 NPU 加速就立刻收斂回 Phase 8 CPU-only 基線。 | ✓ |
| 壓縮到半天快篩 | 只做最小可行性判斷，多數時間留給 Phase 11/12。 | |

**User's choice:** 照原訂 1–2 天硬性 time-box（Recommended）
**Notes:** 與上題同批回覆取得。

---

## Claude's Discretion

- ORT-NeuronEP 與 TFLite 兩路徑的實際試驗順序、時數切分，交由 planner/executor 依 `PITFALLS.md` Pitfall 3 建議（先試 ORT+NeuronEP）決定，但需在 Day 1 結束前有明確可行/不可行證據。
- per-op 放置 logging 的具體形式，交由 executor 依現有程式碼慣例決定。
- 中文 INT8 品質閘的具體驗收方式，交由 executor 依現場可取得資源決定；時間不足時優先完成 NPU-01/02，NPU-03 可部分達成或誠實記錄延後。

## Deferred Ideas

- TFLite + Neuron Stable Delegate 完整轉換路徑 — 僅在 ORT+NeuronEP 算子覆蓋不足時才啟動。
- NPU TTS 加速 — REQUIREMENTS 既定 Out of Scope（P2）。
- Neuron SDK All-in-One Bundle / ncc-tflite DLA offline 路徑 — NDA-gated，明確排除。
- GAI Toolkit（NPU 加速 LLM 生成）— NDA-gated 且 Android-only，明確排除。
