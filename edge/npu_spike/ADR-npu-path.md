# NPU Path Decision — SenseVoice ASR on Genio 520 MDLA 5.3 (Phase 10-04)

**決策日期：** 2026-07-26（原判定）／2026-07-27（重開）  
**狀態：** REOPENED — 見 §7  
**需求：** NPU-01

## 1. 決策脈絡

Phase 8 已交付可在 Genio 520 離線運作的 CPU-only ASR → LLM → TTS 基線；本 Phase 10 是不應危及主 demo 的加值軌道。D-02 鎖定「照 ROADMAP 原訂 1–2 天硬性 time-box」，並規定若 Day 1 結束仍未看到「至少一個算子/子圖真的跑在 NPU 上」的 per-op placement 證據，立即收斂回 Phase 8 CPU-only 基線。決賽剩餘時間應優先讓給 Phase 11/12；本 ADR 因此只依真機 Day-1 證據做出可執行判定。  
**依據：** `10-CONTEXT.md` D-02；`DAY1-EVIDENCE.md` §4。

## 2. 候選路徑與評估

| 路徑 | NDA 狀態 | 需重建的工作量 | 本輪是否採用 | 依據 |
|---|---|---|---|---|
| ORT + `NeuronExecutionProvider` | NDA-free | 在 sherpa-onnx 之外重建 fbank 前處理、CTC 解碼，以及固定 shape 的 pad/truncate | Day-1 raw session 已試；無可運作 placement，停止 | `10-RESEARCH.md` Summary、Pitfall N1；`DAY1-EVIDENCE.md` §4 |
| TFLite（`onnx2tf` → NP8 Converter → Neuron Stable Delegate） | NDA-free；D-01 確認已持有 NeuroPilot Public 帳號，帳號不是阻擋因素 | 包含 ORT 路徑的全部前處理／後處理重建，另加 `onnx2tf` 轉換及獨立的算子覆蓋失敗模式 | 不採用 | `10-RESEARCH.md` Summary、A5：在 Day-1 ORT FAIL 後，這不是更便宜的 fallback |
| Neuron SDK All-in-One Bundle（`ncc-tflite` → `.dla` → `neuronrt`）／GAI Toolkit | NDA-gated；前者無 Python API、後者 Android-only | 額外 SDK 取得與 C/C++／Android 整合 | 明確排除 | `REQUIREMENTS.md` Out of Scope；`10-CONTEXT.md` Deferred Ideas；`STACK.md` NDA-gated 對照 |

## 3. Day-1 實測證據

完整、逐位元保存的真機 verbose output 見 [`DAY1-EVIDENCE.md`](DAY1-EVIDENCE.md) 與 [`DAY1-RAW-OUTPUT.txt`](DAY1-RAW-OUTPUT.txt)。固定模型與探針檔案皆已在 Genio 520 端以 SHA-256 校驗。該證據檔的原文 marker 如下：

> `DAY1_NPU_PROBE: FAIL 0/0 ops on NeuronExecutionProvider`

探針 exit code 為 `1`。NeuronEP 與 `mtk-mdla` 均被真機 runtime 列舉；第一次帶 provider options、第二次空 options 重試都在 ORT session 初始化以 `unordered_map::at` 終止，未形成可提供 per-op placement 的 session。  
**依據：** `DAY1-EVIDENCE.md` §2–§4；`DAY1-RAW-OUTPUT.txt`。

## 4. 決策

停止 ORT-NeuronEP SenseVoice 實作，並不轉向 TFLite。D-02 的客觀 PASS 條件是 per-op placement log 的 NPU 執行比例大於零；本次結果為 `0/0`，未達條件，故不能將 NPU 視為可用能力。TFLite 與 raw ORT 共用 fbank／後處理重建成本，且另有轉檔與覆蓋風險，不能在 Day-1 FAIL 後視為低成本補救。若未來在不同 MediaTek runtime、模型或已能完成 session 初始化的環境取得至少一個非瑣碎算子的 NPU placement，可重新開一個 time-boxed spike；本 ADR 不主張將既有 Day-1 結果外推為所有模型永遠不可用。  
**依據：** `DAY1-EVIDENCE.md` §4；`10-RESEARCH.md` Summary、Open Question 2、A5；`10-CONTEXT.md` D-02。

## 5. 已知風險與未驗證假設

- **A1（Yocto ORT 含 NeuronEP）：已證實。** 真機 `onnxruntime 1.20.2` 可列舉並建立 `NeuronExecutionProvider`，且 runtime 列舉 `mtk-mdla`。**依據：** `DAY1-EVIDENCE.md` §3。
- **A2（provider options 鍵名）：仍未知。** 探針實際先試帶 provider options、再以空 options 重試；兩者都在 session 初始化失敗，沒有一組被證實可用。**依據：** `DAY1-RAW-OUTPUT.txt` 的重試訊息與兩次初始化錯誤。
- **A3（SenseVoice 有動態 shape）：已證實。** 原圖輸入 `x=[N,T,560]`，已固定為 `[1,200,560]` 後再部署。**依據：** `DAY1-EVIDENCE.md` §2。
- **A4（verbose placement log 可用於此 ORT build）：部分證偽。** verbose log 確實輸出 runtime 與圖最佳化資訊，但 session 在 partition/placement table 出現前失敗，因此本模型無可解析 placement table。**依據：** `DAY1-EVIDENCE.md` §3。
- **A5（TFLite 不是較便宜 fallback）：仍為研究推論，未做真機 TFLite conversion。** D-02 time-box 下不為驗證此推論另開轉檔工作。**依據：** `10-RESEARCH.md` Summary、A5；`10-CONTEXT.md` D-02。

## 6. 停損與後續範圍

NPU_PATH_DECISION: STOP-LOSS-CPU-BASELINE

`DAY1_NPU_PROBE:` 為 FAIL，且 NPU placement 為零；此標記直接依 D-02 客觀條件決定，非裁量結果。

- **10-05 與 10-06 不執行。** 不建立 `server/asr_npu.py` 或 NPU runtime wiring；TFLite 不是較便宜的 fallback，因其共用全部前處理／後處理重建工作且額外需要 `onnx2tf` 轉換與其算子風險。
- **10-07 降級為 NPU-03 not-attempted 的誠實紀錄。** 沒有可比較的 NPU ASR 路徑，因此不做 FP32/INT8 A/B 聽測。
- **NPU-01：Complete。** 已完成 ORT-NeuronEP vs TFLite 的書面決策，並排除 NDA-gated 路徑。
- **NPU-02：Not attempted。** 真機 Day-1 無可運作的 NPU placement；證據見 `DAY1-EVIDENCE.md`。
- **NPU-03：Not attempted。** 依賴 NPU-02，故不進行 A/B 品質閘。
- **Phase 8 CPU-only 基線不受影響，無需任何回退工作。** 本次 NPU 交付新增於 `edge/npu_spike/`；本工作樹相對 HEAD 的既有 `server/` 變更僅為其他 session 的 `server/diagnose.py`，非本 NPU work，且未修改現有 CPU ASR／pipeline 路徑。**依據：** 本次 `git diff --name-only HEAD -- server` 與 NPU 檔案清單。

**簽核：** 2026-07-26 — D-02 stop-loss 已依真機 `DAY1_NPU_PROBE` FAIL 生效。

---

## 7. 重開（2026-07-27）

**授權：** 使用者於 2026-07-27 明確指示重開 NPU 路徑，time-box 改為「不設限，跑通為止」，並與 Phase 11 平行推進。此指示取代 §1 的 D-02 硬性 time-box。

**重開的技術理由——§4 的停損混淆了兩個變因。**

Day-1 probe 只測過一顆模型：`model.int8.fixed.onnx`。對該模型做算子普查後（`onnx.load` + `Counter(op_type)`，2026-07-27 於開發機執行）：

| 算子 | 出現次數 |
|---|---|
| `DynamicQuantizeLinear` | 281 |
| `MatMulInteger` | 281 |

這是 **dynamic quantization（QOperator 形態）**，不是靜態 QDQ。`DynamicQuantizeLinear` 必須在 runtime 依實際張量值計算 scale／zero-point，`MatMulInteger` 則是整數 GEMM；兩者都是 NPU delegate 的典型不支援算子。ORT session 初始化時拋出的 `unordered_map::at`，與「EP 在 op-type → Neuron-op 對照表上做 `.at()` 查表、撞到未註冊 op type」的行為特徵一致（`.at()` 查無鍵即拋，不像 `find()` 會回 end iterator）。

因此 §4 的結論「NPU 不可用」與「這顆 int8 模型的算子不被支援」在 Day-1 證據下**無法區分**。停損缺的是一刀二分診斷，而該診斷成本極低（一個 5-node graph，數分鐘）。

**同一份 FP32 模型的算子普查（`model.onnx`，937,617,178 bytes，2026-07-27 下載）：**

```text
quant ops: (none)
   421  MatMul        ← 標準 FP32 GEMM，非 MatMulInteger
```

零量化算子。而 NPU-03 的驗收條件本來就是 FP32 vs INT8 A/B，FP32 路徑本就在 Phase 10 範圍內，Day-1 未測即停損屬覆蓋不足。

**重開後的執行順序（便宜的診斷先做）：**

1. **toy model 二分診斷** — `edge/npu_spike/make_toy_model.py` 產出 2-node、全靜態 shape、純 FP32 的 `toy_conv.onnx` / `toy_matmul.onnx`，交給既有 `raw_neuron_session.py` 跑同一套 per-op placement 探針。
   - toy 也 FAIL → 環境問題，NeuronEP 在此 Yocto ORT build 不可用，停損**成立**且這次有根據。
   - toy PASS → 模型問題，進入第 2 步。
2. **FP32 SenseVoice** — `model.fixed.onnx`（已於開發機備妥，見下表），同一支 probe 只換 `--model`。
3. 若 FP32 取得 placement > 0 → 恢復執行 10-05（`server/asr_npu.py` wiring）與 10-06。
4. 若 FP32 記憶體吃不下（895 MB 模型 vs Phase 8 實測 ≈2723 MB 峰值／4 GB 上限），再評估靜態 QDQ 重新量化，而非退回 dynamic-quant int8。

**已備妥的真機輸入（開發機 SHA-256，真機端須重新校驗後才可採信）：**

| 檔案 | bytes | SHA-256 |
|---|---|---|
| `models/.../model.fixed.onnx`（FP32，`x=[1,200,560]` 全靜態） | 937,617,173 | `5844137db3105aae5730273f9fb928c0580dd09577e639cb5b0dd6b27edb17bc` |
| `edge/npu_spike/toy_conv.onnx` | 1,135 | `f164351faaadece91167e9a334cc128c4f3b1d13fb9e0dea677b80d0ef25fd56` |
| `edge/npu_spike/toy_matmul.onnx` | 33,197 | `10e81d9f9023c5eb570ec5af4053fadc7389960d9916e9bfd2158e433c470a1a` |

**§6 的 gate 狀態變更：**

NPU_PATH_DECISION: REOPENED-PENDING-BISECT

10-05／10-06 的 gate 從「不執行」改為「待 toy 二分診斷結果」。**PASS 條件維持不變且不放寬**：per-op placement 中 NPU ops > 0；provider 存在、session 建得起來、`mtk-mdla` 被列舉，一律不構成通過。§4 對「provider presence 不等於加速」的判準在重開後完全沿用。

---

## 8. 重開後的真機實測結果（2026-07-27，裝置 `root@192.168.31.78`）

判準全程未放寬：per-op placement 的 NPU ops > 0。

### 8.1 NPU 可用——已證明

| Probe | 節點數 | 判定 | 證據 |
|---|---|---|---|
| `toy_conv`（Conv+Relu） | 2 | **PASS 1/1** | `All nodes placed on [NeuronExecutionProvider]`；`[apusys][info]apusysSession` |
| `ConvStackPlusUnsupported`（4×Conv+4×Relu+Softmax） | 9 | **PASS 1/1** | 同上，整圖融合為 1 個 fused node |
| `BigConvStack`（120×(Conv+Relu)） | 240 | **PASS 1/1** | 同上 |

「Number of nodes: 1」是 EP 把接管的子圖替換成單一 fused node 的標準表示法，不是「只加速一個算子」。

### 8.2 §7 的重開理由已被自己的實測推翻

§7 主張失敗主因是 int8 的 `DynamicQuantizeLinear`/`MatMulInteger`。**這是錯的。** FP32 版（零量化算子、已固定 shape、checksum 相符）在同一階段以完全相同的 `unordered_map::at` 崩潰。量化不是原因。

### 8.3 崩潰原因仍未定位；以下假設皆已由實測排除

| 假設 | 狀態 | 反證 |
|---|---|---|
| dynamic-quant 算子 | **排除** | FP32 版同樣崩潰 |
| 形狀／控制流算子無 map 項目（Range/Tile/ConstantOfShape/Where/Expand/Equal/Sin/Cos/Softmax/ReduceMean/Transpose/Gather） | **排除** | 12 個單算子探針無一崩潰，全部乾淨退回 CPU |
| int64 圖輸入的 dtype 查表失敗 | **排除** | `Int64Input`、`MultiInput`（仿 SenseVoice 4 輸入簽章）皆未崩潰 |
| CPU fallback 造成跨 EP partition 失敗 | **排除** | 依官方指南加 `--no-cpu-fallback` 重測，仍崩潰 |
| 圖規模／編譯器上限 | **排除** | 240 節點 Conv 堆疊 PASS |

**誠實記錄：崩潰原因未知。** 不以任何未經證實的推測填補此空白。

### 8.4 官方文件澄清的事（MediaTek Genio Community，NeuronEP 指南）

- `NEURON_FLAG_USE_FP16` **是強制的**——原文「This flag is mandatory, as the NPU does not support FP32 execution」。這與真機 target report 的 `MDLA: Cannot support Float32 input/output` 完全一致。**ADR §5 假設 A2 據此結案：鍵名有效且必要。**
- `NEURON_FLAG_MIN_GROUP_SIZE` 為子圖 offload 的最小節點數。
- 動態 shape 必須先固定（本輪已做）。
- INT8 須為 **QDQ**，非 dynamic quant——與 int8 版失敗一致，但**無法**解釋 FP32 版為何同樣失敗。
- 建議省略 fallback providers（本輪已依此重測）。

### 8.5 對 NPU-02 的現況判定

`toy` 證明 NPU 可用，但 **SenseVoice ASR 經 ORT-NeuronEP 加速尚未達成**，且有兩道各自獨立的阻擋：

1. session 初始化崩潰（原因未定位，使該模型連 partition 都到不了）；
2. 即使崩潰解除，MDLA 對 `BatchMatMul` 的拒收有明確 target report 佐證，而 SenseVoice 的 transformer 骨幹含 421 個 `MatMul`。

**使用者於 2026-07-27 決定改走「換一個 Conv 為主的感知模型上 NPU」**，不再在 SenseVoice 上加碼。§8.1 的三個 PASS 使該路徑具備實證基礎。

### 8.6 已滿足的 ROADMAP 成功條件

- **SC3（算子不支援時自動退 CPU，且 fallback 可被觀察，不得靜默偽成功）：已滿足。** `toy_matmul` 被拒收時 ORT 把圖放上 CPU、session 照常成立，`format_placement_line` 印出 `NPU: OFF, 0/2 ops accelerated`，降級可觀察。

NPU_PATH_DECISION: REOPENED-NPU-PROVEN-ASR-PENDING

**未達成、誠實登錄：** NPU-02（SenseVoice on NPU）與 NPU-03（FP32 vs INT8 繁中品質閘，依賴 NPU-02）。
