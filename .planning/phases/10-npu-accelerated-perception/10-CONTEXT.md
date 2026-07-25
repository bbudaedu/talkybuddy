# Phase 10: NPU-Accelerated Perception - Context

**Gathered:** 2026-07-25
**Status:** Ready for planning

<domain>
## Phase Boundary

在 MediaTek Genio 520 上，讓 ASR（SenseVoice）經 NPU（MDLA 5.3）delegate 加速，含 per-op 放置 logging（防「淪為音箱」的靜默 CPU fallback），並以真實繁中決賽腳本音訊做中文 INT8 品質閘；1–2 天內定案 ORT-NeuronEP vs TFLite 路徑，排除 NDA-gated 路徑：

1. **NPU 路徑定案（NPU-01）**：ORT-NeuronEP（sherpa-onnx 既有跑在 ONNX Runtime，Genio 520/720 官方文件宣稱 NeuronExecutionProvider 預設開啟）vs TFLite 轉檔（onnx2tf → NP8 Converter → Neuron Stable Delegate）。研究（`.planning/research/PITFALLS.md` Pitfall 3）建議先試 ORT+NeuronEP，因轉換風險較低。
2. **ASR NPU 加速 + per-op 放置 logging（NPU-02）**：SenseVoice 經 NPU delegate 加速；算子不支援時退 CPU，且必須有明確 log/HUD 顯示「NPU: ON, X/Y ops accelerated」，不得只憑「跑起來了」就宣稱 NPU 加速（`.planning/research/PITFALLS.md` Pitfall 1 — 淪為音箱風險 #1）。
3. **中文 INT8 品質閘（NPU-03）**：以真實繁中決賽腳本音訊 + 母語聽測驗證 ASR/TTS 品質，FP32 vs INT8 A/B，防退化到「音箱」等級。

**Requirements:** NPU-01, NPU-02, NPU-03（`.planning/REQUIREMENTS.md` M2）

**不在本 phase**：NPU 加速 LLM 生成（GAI Toolkit / ncc-tflite DLA 路徑，NDA-gated，明確排除）、NPU TTS 加速（P2，ASR 優先，時間有餘才做，REQUIREMENTS Out of Scope 已列）、雲端教師閉環（Phase 11）、Nova Sonic staging（Phase 12）。

</domain>

<decisions>
## Implementation Decisions

### NDA / 帳號狀態（NPU-01 前提）
- **D-01（鎖定）：** 使用者**已持有 NeuroPilot Public 帳號**（可下載 NP8 Converter），不受帳號註冊阻擋。可直接開始 ORT-NeuronEP vs TFLite 真機比較 spike，不需先做「不需帳號的部分」的迂迴安排。

### 時間預算與 stop-loss（跨 NPU-01/02/03）
- **D-02（鎖定）：** **照 ROADMAP 原訂 1–2 天硬性 time-box**，非壓縮版半天快篩。中途設檢查點（建議 Day 1 結束）：若尚未看到「至少一個算子/子圖真的跑在 NPU 上」的可運作證據（per-op placement log 顯示 NPU 執行比例 > 0），立即收斂回 Phase 8 CPU-only 基線，記為 not-attempted 或部分達成，把剩餘時間讓給 Phase 11/12，不得為了硬湊 NPU 加速故事而超支到決賽剩餘天數的風險區。
- 理由：決賽約剩 5 天，Phase 9 仍卡在人工真機驗證、Phase 11/12 尚未開始；NPU 屬加值軌道非決賽存亡關鍵（Phase 8 CPU-only 基線已交付，可退可守），不宜排擠風險更低、更確定能交付的 Phase 11/12 時間。

### Claude's Discretion
- ORT-NeuronEP 與 TFLite 兩路徑的實際試驗順序、每條路徑分配的時數切分（在 1–2 天總預算內），由 planner/executor 依 `PITFALLS.md` Pitfall 3 建議（先試 ORT+NeuronEP，因轉換風險較低）決定，但需在 Day 1 結束前有明確可行/不可行的證據。
- per-op 放置 logging 的具體形式（console log / debug HUD / 檔案輸出）由 executor 依現有程式碼慣例（`server/app.py` 既有 status 端點模式）決定，不視為新決策。
- 中文 INT8 品質閘的具體驗收方式（母語聽測人選、腳本音檔來源）由 executor 依現場可取得資源決定；若 1–2 天 time-box 內來不及做完整 NPU-03 品質閘，優先完成 NPU-01 定案 + NPU-02 可運作 spike，NPU-03 可視情況部分達成或延後（但不得跳過誠實記錄）。

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 研究基礎（milestone 層級，已涵蓋 Phase 10 範圍）
- `.planning/research/STACK.md` — Genio 520 NPU 技術棧完整比較：MDLA 5.3、TFLite+Neuron Stable Delegate（NDA-free）路徑、onnx2tf、NP8 Converter、tflite-runtime/ai-edge-litert、**onnxruntime + Genio ONNX Runtime NPU EP**（列為 B-plan 但需一日比較 spike）；NDA-gated 對照表（GAI Toolkit、Neuron SDK All-in-One Bundle/ncc-tflite DLA 皆排除）。
- `.planning/research/PITFALLS.md` — 三個關鍵 pitfall：
  - **Pitfall 1**（淪為音箱風險）：TFLite delegate 靜默 per-op CPU fallback，`available()==True` 不等於「真的跑在 NPU」；需 `benchmark_model --use_delegate=stable_delegate` 或等效工具印出 per-op 放置，加 runtime log/HUD「NPU: X/Y accelerated」。
  - **Pitfall 2**（INT8 品質退化）：量化準確度損失依語言分布而定，公版 calibration 資料集罕見對應繁中/兒童語音；需 FP32 vs INT8 A/B 用決賽腳本實測。
  - **Pitfall 3**（誤設 TFLite 為唯一路徑）：sherpa-onnx 已跑在 ONNX Runtime，Genio 520/720 官方文件宣稱 ORT NeuronExecutionProvider 內建預設開啟；應先花 1–2 天試 ORT+NeuronEP，只有在算子不支援時才轉 TFLite 全套轉換。

### Milestone / 需求脈絡
- `.planning/REQUIREMENTS.md` §NPU-01~03 全文與 Out of Scope（NPU TTS、GAI Toolkit/ncc-tflite DLA 皆排除）。
- `.planning/ROADMAP.md` §Phase 10 — Goal、success criteria、1–2 天 time-box 與 stop-loss 條款。

### 前置 phase 交接
- `.planning/phases/08-cpu-only-offline-edge-turn-loop/08-CONTEXT.md` — CPU-only 離線迴路（llama.cpp + sherpa-onnx CPU）已交付，是本 phase 的 fallback 基線；stop-loss 觸發時直接退回此基線，不影響 Phase 8 交付狀態。
- `edge/BOARD_BRINGUP_DECISION.md` — Genio 520 硬體規格（6× Cortex-A55 + 2× Cortex-A78，無 i8mm，有 asimddp/dotprod；Yocto `Rity Demo Layer 25.1.1-release scarthgap`）。

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- 現有 sherpa-onnx ASR（SenseVoice-Small int8）已跑在 ONNX Runtime — 若 ORT+NeuronEP 路徑可行，理論上只需在既有 ONNX Runtime session 建立時指定 NeuronExecutionProvider，不需重寫 ASR 介面（`ASREngine`/backend factory 既有介面沿用）。
- 現有 CPU-only edge pipeline（Phase 8 交付）本身即是 stop-loss fallback，不需額外開發。

### Established Patterns
- `TALKYBUDDY_PIPELINE_PROFILE` feature flag 機制（既有）：可比照此模式做 NPU on/off 切換，若 NPU 加速上線可用類似機制而非新開一套設定系統。

### Integration Points
- ASR engine 初始化處（sherpa-onnx session 建立）是 ORT-NeuronEP provider 注入點；需 executor 實際定位程式碼行號（本輪 discuss 未逐行掃描，留給 phase-researcher/planner 於規劃前確認）。

</code_context>

<specifics>
## Specific Ideas

- 決賽故事線：「NPU 管感知（ASR 加速）、CPU 管生成（LLM）」是 PROJECT.md 既定分工，Phase 10 的成敗只影響「國產晶片加分」故事的真實性，不影響離線對話迴路本身能不能跑（Phase 8 已保底）。
- 使用者選擇嚴格 time-box 而非壓縮快篩，反映其判斷「若 spike 頭一天就有訊號，值得投入完整 1–2 天；若沒訊號，越早停損越好」，非「無論如何都要壓縮到半天」。

</specifics>

<deferred>
## Deferred Ideas

- **TFLite + Neuron Stable Delegate 完整轉換路徑**：僅在 ORT+NeuronEP 被證實算子覆蓋不足時才啟動；若 ORT+NeuronEP 可行，本輪不投入 onnx2tf/NP8 Converter 轉檔工作。
- **NPU TTS 加速**：REQUIREMENTS 既定 Out of Scope（P2），ASR 加速優先，時間有餘才考慮。
- **Neuron SDK All-in-One Bundle / ncc-tflite DLA offline 路徑**：NDA-gated 且無 Python API（僅 C/C++），明確排除。
- **GAI Toolkit（NPU 加速 LLM 生成）**：NDA-gated 且 Android-only，明確排除；CPU-only llama.cpp（Phase 8 已交付）維持為生成引擎。

</deferred>

---

*Phase: 10-npu-accelerated-perception*
*Context gathered: 2026-07-25*
