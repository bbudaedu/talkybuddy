# NeuronEP 算子分流結果（2026-07-27，裝置 `root@192.168.31.78`）

目的：找出讓 SenseVoice 在 session 初始化崩潰（`unordered_map::at`）的算子。
方法：`edge/npu_spike/make_op_probe_models.py` 產生單算子／單一圖形態的最小 FP32
模型（`ir_version=7`、全靜態 shape），逐一餵給 `raw_neuron_session` probe。

## 結果

| Probe | 判定 | 是否崩潰 | 放置 |
|---|---|---|---|
| `toy_conv`（Conv+Relu） | **PASS 1/1** | 否 | **NeuronExecutionProvider** |
| `toy_matmul`（MatMul+Add） | FAIL 0/2 | 否（乾淨拒收） | CPUExecutionProvider |
| Range | FAIL 0/1 | 否 | CPU |
| Tile | FAIL 0/1 | 否 | CPU |
| ConstantOfShape | FAIL 0/1 | 否 | CPU |
| Where | FAIL 0/1 | 否 | CPU |
| Expand | FAIL 0/1 | 否 | CPU |
| Equal | FAIL 0/2 | 否 | CPU |
| Sin | FAIL 0/1 | 否 | CPU |
| Cos | FAIL 0/1 | 否 | CPU |
| Softmax | FAIL 0/1 | 否 | CPU |
| ReduceMean | FAIL 0/1 | 否 | CPU |
| Transpose | FAIL 0/1 | 否 | CPU |
| Gather | FAIL 0/1 | 否 | CPU |
| Int64Input（Conv+Cast+Add，含 int64 輸入） | FAIL 0/3 | 否 | CPU |
| MultiInput（Conv + 3× int64 純量輸入，仿 SenseVoice 簽章） | FAIL 0/7 | 否 | CPU |
| **SenseVoice `model.fixed.onnx`（FP32）** | FAIL 0/0 | **是** | 無（session 初始化即崩潰） |
| **SenseVoice `model.int8.fixed.onnx`** | FAIL 0/0 | **是** | 無（同上） |

## 結論一：崩潰原因未定位，但已排除多項假設

14 個探針**無一**重現 `unordered_map::at`。以下假設皆已被實測推翻：

- ~~dynamic-quant 算子（`DynamicQuantizeLinear`/`MatMulInteger`）是主因~~ — **推翻**。FP32 版零量化算子，仍以完全相同的錯誤在同一階段崩潰。
- ~~形狀／控制流類算子（Range/Tile/ConstantOfShape/Where/Expand）沒有 map 項目~~ — **推翻**。全部乾淨退回 CPU，未崩潰。
- ~~int64 圖輸入的 dtype 對應查表失敗~~ — **推翻**。`Int64Input` 與 `MultiInput` 皆未崩潰。

剩餘未受控差異為**規模**（9082 節點／895MB）與**未逐一測試的常見算子**（Reshape、Concat、Shape、Unsqueeze、Slice、Split、Pad 等）。

## 結論二（更具決定性）：NeuronEP 在此 build 是「整張圖全收或全不收」

三個資料點一致：

- `Conv + Relu` → **整張圖上 NeuronEP**
- `Conv + Cast + Add` → 整張圖退 CPU（Conv 本身明明被接受）
- `MatMul + Add` → 整張圖退 CPU

只要圖中出現一個 MDLA 不支援的算子，**整張圖**就被拒收，不做逐算子切分、不留部分加速。

## 對 NPU-02 的直接後果

MDLA 明確拒收 `BatchMatMul`（`toy_matmul` 的 target report：EDMA/GPU/MDLA 三者皆
unsupported）。SenseVoice 的骨幹是 transformer，FP32 圖含 **421 個 `MatMul`**、
僅 70 個 `Conv`。在「全收或全不收」的語意下，即使崩潰被修好，SenseVoice 仍會
因 BatchMatMul 而被整張拒收。

**因此：ASR（SenseVoice）經 ORT-NeuronEP 加速，在此硬體／此 ORT build 上不可達。**
這個結論不依賴崩潰原因是否查明——兩條路都通向同一個結果。

## 未被此結論否定的事

- **NPU 本身可用且已證明**：`toy_conv` 取得整圖 NeuronEP 放置並建立 apusys session。
- **SC3（可觀察的 CPU fallback）已滿足**：拒收時 ORT 把圖放上 CPU、session 照常成立，
  `format_placement_line` 印出 `NPU: OFF, X/Y ops accelerated`，降級可被觀察，非靜默偽成功。
