# SenseVoice → TFLite 轉換受阻紀錄（2026-07-27）

目標：把 SenseVoice ONNX 轉成 TFLite，交給已驗證可用的 Neuron Stable Delegate
（見 `TFLITE-PATH-EVIDENCE.md`，mobilenet 實測 12.9×），並用 `PRE_OPERATION_CHECK`
取得逐算子支援度與實際延遲。**此目標未達成。**

## 兩條 onnx2tf backend 都失敗

| backend | 模型 | 結果 |
|---|---|---|
| `flatbuffer_direct`（預設） | `model.fixed.onnx`（FP32, 895MB） | **OOM**（SIGKILL, exit 137） |
| `flatbuffer_direct` | `model.int8.fixed.onnx`（239MB） | **OOM**（SIGKILL, exit 137） |
| `tf_converter` | `model.int8.fixed.onnx` | **套件 bug**：`Gather.py:375` → `tf_keras.backend.is_keras_tensor()` 拋 `ValueError: Unexpectedly found an instance of type <class 'list'>. Expected a symbolic tensor instance.` |

OOM 的確切死點（以 `PYTHONUNBUFFERED=1 stdbuf -oL` 取得，緩衝輸出在 SIGKILL 時會遺失）：

```text
Automatic generation of each OP name complete!
Model loaded
flatbuffer_direct fast path started
<killed>
```

## 記憶體事實（實測，非推測）

- 主機 15GB，清掉一個卡了 20 小時、佔 2.1GB 的殘留 pytest 後仍有 7.6GB 可用。
- 已確認**無 cgroup 限制**：測試程式順利配置到 7.4GB。
- 因此 onnx2tf 對這張 9082 節點的圖，實際需求 **> 7.6GB**——與模型檔僅 239MB 不成比例，
  原因是它逐節點建構 TF graph。

## 另一道獨立的牆：就算轉成功，FP32 也放不進裝置

裝置總記憶體 3.7GB；Phase 8 實測三引擎峰值 ≈2723MB。FP32 TFLite 約 900MB，加起來超標。
**可行目標只有 int8 TFLite（約 240MB）**——而那正是 `tf_converter` 撞到套件 bug 的那條。

## 未嘗試（供後續參考，非本輪結論）

- 降版 `tensorflow` / `tf_keras` 到 onnx2tf 相容組合（版本轉盤，耗時不可控）。
- 在記憶體更大的機器上轉檔。
- 即使轉換成功，仍有 ADR 早已指出的成本：需在 sherpa-onnx 之外重建 fbank 前處理與 CTC 解碼。

## 平台側的重要脈絡

MediaTek 自家的 **Whisper on NPU 在 IoT AI Hub 標為「Q3 estimated」**
（`litert_gai_g520.html`），亦即**原廠本身也尚未在這顆 NPU 上交付 ASR**。
analytical model zoo 則清一色是視覺模型，無任何音訊／語音模型。

這不是替我們的未達成找理由，而是校準期待：ASR-on-NPU 在此平台目前沒有已知的成功路徑。
