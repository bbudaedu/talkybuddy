# NPU 重開真機執行手冊（2026-07-27）

對應 `ADR-npu-path.md` §7。

> **狀態：Step 1–3 已於 2026-07-27 執行完畢。** 結果見 §6 與 ADR §7。
> Step 4（FP32 SenseVoice）進行中。

## 執行時踩到的三個坑（重跑前必讀）

首次執行**沒有量到 NPU，而是量到三個 probe 自身的缺陷**——三個之中任一個都足以把「NPU 正常運作」誤報成 FAIL。已於 commit `46b39c5` 全數修好，此處保留紀錄以免重蹈：

1. **IR version 不相容**：`make_toy_model.py` 原本讓 `onnx` 1.22 寫出預設的 IR version 13，而裝置端 ORT 1.20.2 上限是 10 → `Unsupported model IR version: 13`。模型連載入都沒成功，探針量到的是版本不相容而不是 NPU。已釘為 7（與 SenseVoice 系列一致）。
2. **Parser 少認一種日誌格式**：ORT 只在圖被切分到多個 EP 時才印逐節點的 `Provider: [X]: [...]`；**整張圖都落在同一個 EP 時走的是彙總捷徑** `All nodes placed on [X]. Number of nodes: N`——也就是「完全加速」這個最好的結果，反而是舊 parser 唯一看不懂的格式，被記成 0/0。
3. **重試覆蓋成功**：第 2 點的誤判觸發了「零加速就換空 options 重試」，而空 options 拿掉 `NEURON_FLAG_USE_FP16` 後必然失敗（MDLA 不吃 FP32），失敗結果又被無條件寫回，把第一輪的成功抹掉。

**連帶結論**：ADR §5 的假設 A2（provider options 鍵名未驗證）已可結案——`NEURON_FLAG_USE_FP16` 不只鍵名有效，而且是 **MDLA 能吃下 FP32 圖的必要條件**。

---

**執行前這些步驟一步都沒跑過**——以下為原始腳本內容，仍是重跑時的正確流程。

判讀規則只有一條，與 Day-1 完全相同、不放寬：**per-op placement 的 NPU ops > 0 才算通過。** provider 出現在清單裡、session 建得起來、`mtk-mdla` 被列舉，都不算。

---

## 0. 前置：恢復裝置連線

```bash
ssh genio-520-evk 'echo OK'
```

2026-07-27 當下回 `Could not resolve hostname genio-520-evk`（Tailscale 未連線）。此步驟不通則後面全部無法執行。

## 1. 推送探針與 toy 模型（小檔，直接推）

```bash
rsync -av --partial --append-verify \
  edge/npu_spike/make_toy_model.py \
  edge/npu_spike/raw_neuron_session.py \
  edge/npu_spike/inspect_model.py \
  edge/npu_spike/__init__.py \
  genio-520-evk:/root/talkybuddy/edge/npu_spike/

rsync -av --partial --append-verify \
  edge/npu_spike/toy_conv.onnx edge/npu_spike/toy_matmul.onnx \
  genio-520-evk:/root/talkybuddy/edge/npu_spike/

rsync -av --partial --append-verify \
  server/npu_placement.py genio-520-evk:/root/talkybuddy/server/
```

`--partial --append-verify` 是 10-04 的教訓：大檔傳輸曾在中途斷線，留下 193,228,800 bytes 的殘檔卻沒被察覺。

## 2. 校驗閘門（不可跳過）

```bash
ssh genio-520-evk 'cd /root/talkybuddy && sha256sum \
  edge/npu_spike/toy_conv.onnx \
  edge/npu_spike/toy_matmul.onnx \
  edge/npu_spike/raw_neuron_session.py \
  server/npu_placement.py'
```

`toy_conv.onnx` 必須是 `f164351faaadece91167e9a334cc128c4f3b1d13fb9e0dea677b80d0ef25fd56`（1,135 bytes）；
`toy_matmul.onnx` 必須是 `10e81d9f9023c5eb570ec5af4053fadc7389960d9916e9bfd2158e433c470a1a`（33,197 bytes）。
對不上就重推，不要繼續。

## 3. Step 1 — toy 二分診斷（先跑這個，成本最低）

```bash
ssh genio-520-evk 'cd /root/talkybuddy && python3 -m edge.npu_spike.raw_neuron_session \
  --model edge/npu_spike/toy_conv.onnx' 2>&1 | tee edge/npu_spike/REOPEN-TOY-CONV-RAW.txt
echo "__EXIT__=$?"

ssh genio-520-evk 'cd /root/talkybuddy && python3 -m edge.npu_spike.raw_neuron_session \
  --model edge/npu_spike/toy_matmul.onnx' 2>&1 | tee edge/npu_spike/REOPEN-TOY-MATMUL-RAW.txt
echo "__EXIT__=$?"
```

用**系統 `python3`**，不是 venv——`inspect_model.py` docstring 已載明 `provision_device.sh` 建 venv 時沒加 `--system-site-packages`，venv 內看不到帶 NeuronEP 的 onnxruntime。

### 判讀

| toy 結果 | 意義 | 下一步 |
|---|---|---|
| 兩個都 `DAY1_NPU_PROBE: FAIL` | 環境問題：NeuronEP 在此 Yocto ORT 1.20.2 build 上建不起任何 session | 停損成立且這次有根據。把原文貼回 ADR §7，結案，全力轉 Phase 11 |
| 任一個 `PASS X/Y ops` 且 X > 0 | NeuronEP 可用，Day-1 的失敗是 SenseVoice int8 算子造成 | 進入 Step 2 |
| session 建起來但 `0/N ops` | EP 可用但這組算子全被丟回 CPU | 記錄下來，仍進 Step 2（SenseVoice 算子集不同） |

**若兩個 toy 都 FAIL，Step 2 就不必跑**——895 MB 的傳輸沒有意義。

## 4. Step 2 — FP32 SenseVoice（只在 toy 通過時執行）

```bash
rsync -av --partial --append-verify --progress \
  models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/model.fixed.onnx \
  genio-520-evk:/root/talkybuddy/models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/

ssh genio-520-evk 'cd /root/talkybuddy && sha256sum \
  models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/model.fixed.onnx'
```

必須是 `5844137db3105aae5730273f9fb928c0580dd09577e639cb5b0dd6b27edb17bc`（937,617,173 bytes）。

```bash
ssh genio-520-evk 'cd /root/talkybuddy && python3 -m edge.npu_spike.raw_neuron_session \
  --model models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/model.fixed.onnx' \
  2>&1 | tee edge/npu_spike/REOPEN-FP32-RAW.txt
echo "__EXIT__=$?"
```

### 記憶體警告

Phase 8 實測三引擎峰值 ≈2723 MB／4 GB 上限（33.5% 餘裕）。FP32 SenseVoice 光模型就 895 MB，比 int8 版多 ~656 MB。**這支 probe 是單獨跑的，不會撞到那個預算**；但若 FP32 真的取得 placement，10-05 的 wiring 必須重新量峰值 RSS，不能沿用 Phase 8 數字。若超標，走 ADR §7 第 4 步的靜態 QDQ 重新量化，而不是退回 dynamic-quant int8。

## 5. 收尾（無論結果）

1. 三份 raw 輸出（`REOPEN-TOY-CONV-RAW.txt`、`REOPEN-TOY-MATMUL-RAW.txt`、必要時 `REOPEN-FP32-RAW.txt`）逐位元保存，附 bytes + SHA-256。
2. 把 `DAY1_NPU_PROBE:` 判定行與 exit code 貼進 `ADR-npu-path.md` §7。
3. 依結果更新 §7 的 `NPU_PATH_DECISION:` marker：
   - 取得 NPU ops > 0 → `NPU_PATH_DECISION: NPU-VIABLE-FP32`，10-05/10-06 解除 gate
   - 全數 FAIL → `NPU_PATH_DECISION: STOP-LOSS-CPU-BASELINE-CONFIRMED`，這次的停損有二分診斷背書
4. **不得**把 provider presence、session 建立成功或 `mtk-mdla` 列舉寫成任何形式的「NPU 加速」宣稱。

---

## 6. Step 1–3 實際結果（2026-07-27，裝置 `root@192.168.31.78`）

判準未放寬，仍是 per-op placement NPU ops > 0。

| Probe | 判定 | exit | 原文關鍵行 |
|---|---|---|---|
| `toy_conv.onnx`（Conv+Relu，FP32 靜態） | **PASS 1/1** | 0 | `All nodes placed on [NeuronExecutionProvider]. Number of nodes: 1`；`[apusys][info]apusysSession: Seesion(0xaaab1193a150)` |
| `toy_matmul.onnx`（MatMul+Add，FP32 靜態） | FAIL 0/2（退 CPU） | 1 | `All nodes placed on [CPUExecutionProvider]. Number of nodes: 2`；`MDLA: Cannot support Float32 input/output`、`BatchMatMulLayer{BATCH_MATMUL}` 於 EDMA/GPU/MDLA 三個 target 皆 unsupported |

### 二分診斷結論

**世界 (B)：環境可用，Day-1 的失敗是模型問題。** `toy_conv` 取得整圖 NPU 放置並建立 apusys session，證明這顆 Genio 520 的 NeuronEP 能編譯並執行子圖。因此 2026-07-26 對 `model.int8.fixed.onnx` 的 `unordered_map::at` 不能再被解讀為「NPU 不可用」。

### 一個 ROADMAP 成功條件已就地滿足

`toy_matmul` 的結果同時是 **SC3（算子不支援時自動退 CPU，且 fallback 可在 log/HUD 被觀察到，不得靜默偽成功）** 的實證：NeuronEP 拒收 BatchMatMul 後，ORT 把節點放上 CPU、session 照常成立，而 `format_placement_line` 印出 `NPU: OFF, 0/2 ops accelerated`——降級是**可觀察**的，不是靜默的。

### 對 SenseVoice 的預期（尚未驗證，勿當結論）

MDLA 接受 Conv 但拒收 BatchMatMul。SenseVoice 的骨幹是 transformer：FP32 版有 421 個 `MatMul`、僅 70 個 `Conv`（共 9082 節點）。若 BatchMatMul 的拒收在 SenseVoice 上同樣成立，**可預期只有零星 Conv 子圖上得了 NPU**，未必構成有意義的加速。Step 4 的實測數字才算數。
