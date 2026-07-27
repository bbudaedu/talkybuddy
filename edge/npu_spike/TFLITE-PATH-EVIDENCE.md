# TFLite + Neuron Stable Delegate 路徑實測（2026-07-27，`root@192.168.31.78`）

## 為什麼轉這條路

ORT-NeuronEP 路徑在 SenseVoice 上以 `unordered_map::at` 崩潰，六個假設全部被實測排除、
原因未定位（見 `ADR-npu-path.md` §8）。文件研究後發現關鍵背景：**MediaTek 自己把
ORT 的 NPU EP 標為「in active development」**（`How to Deploy ONNX Runtime on Genio
Platform` 官方回覆），而 IoT AI Hub 的 analytical model zoo **全部**使用
「TFLite Interpreter with NeuronSDK/Neuronrt-MDLA」，不是 ORT。

## 裝置端既有資產（皆已 SSH 確認，非推測）

| 項目 | 路徑 | 狀態 |
|---|---|---|
| Neuron Stable Delegate | `/usr/lib/libneuron_stable_delegate.so` | 存在，匯出 `TFL_TheStableDelegate@@VERS_1.0` |
| Delegate 設定檔 | `/usr/share/label_image/stable_delegate_settings.json` | 存在，含 `PRE_OPERATION_CHECK` 與 `allow_fp16_precision_for_fp32: true` |
| 支援該旗標的 benchmark_model | `/usr/sbin/benchmark_model` | 存在（`/root/benchmark_suite/` 下那兩個**不**支援） |
| Neuron runtime | `/usr/sbin/neuronrt` | 存在 |
| tflite_runtime | Python 3.12 site-packages | 2.16.1（但 `load_delegate` 走舊 ABI，**無法**載入 stable delegate） |
| `ncc-tflite` | — | **不在裝置上**（host 端 NDA-gated 編譯器；此路徑不需要它） |

## 實測結果

指令（官方 `How to Identify Unsupported Operators` 文件所載形式）：

```bash
/usr/sbin/benchmark_model \
  --stable_delegate_settings_file=/usr/share/label_image/stable_delegate_settings.json \
  --use_nnapi=false --use_xnnpack=false --use_gpu=false \
  --min_secs=5 --graph=mobilenet_v2_1.0_224_float.tflite
```

原文關鍵行（完整輸出見 `TFLITE-DELEGATE-MOBILENET-RAW.txt`）：

```text
INFO: Neuron stable delegate version: 1.4.3
INFO: STABLE_DELEGATE delegate created.
[apusys][info]apusysSession: Seesion(0xaaaaf982a750): thd(benchmark_model) version(5) log(0)
INFO: Explicitly applied STABLE_DELEGATE delegate, and the model graph will be completely executed by the delegate.
INFO: Inference timings in us: Init: 714949, First inference: 2967, Warmup (avg): 2648.59, Inference (avg): 2682.96
```

| 執行方式 | mobilenet_v2 1.0 224 float 單次推論 |
|---|---|
| CPU（XNNPACK，`tflite_runtime`） | 34.6 ms |
| **NPU（Neuron Stable Delegate）** | **2.68 ms** |
| 加速比 | **約 12.9×** |

初始化 715 ms（delegate 執行期編譯圖），此成本一次性、可在服務啟動時吸收。

## 這條路相對 ORT-NeuronEP 的三個關鍵優勢

1. **成熟**：MediaTek 官方 model zoo 走這條；ORT NPU EP 官方自承仍在開發中。
2. **逐算子 CPU fallback**：官方「Workaround for Unsupported Ops」載明「any unsupported
   operation will be delegated to the CPU at runtime」。ORT-NeuronEP 在 SenseVoice 上是
   **整個 session 崩潰**；TFLite 路徑則會照跑、只把不支援的算子退回 CPU，**部分加速可達成**。
3. **有可用診斷**：`PRE_OPERATION_CHECK` 會直接印出
   `ERROR: OP BATCH_MATMUL (v1) is not supported (Unsupported operation type.)` 這種逐算子原因，
   不再需要我方自行做二分搜尋。

## 尚未驗證（不得當成已完成）

- SenseVoice ONNX → TFLite 的轉換（`onnx2tf`）尚未進行。
- 轉換後有多少比例的算子落在 NPU、實際端到端 ASR 延遲改善多少，**完全未知**。
- 12.9× 是 mobilenet 的數字，**不可外推**到 SenseVoice。
