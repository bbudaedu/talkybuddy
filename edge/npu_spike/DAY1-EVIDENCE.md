# Genio 520 Day-1 NeuronEP Probe Evidence

**驗證日期：** 2026-07-26 07:13–07:14 UTC  
**裝置：** `genio-520-evk`（Genio 520，aarch64）  
**執行環境：** 真機系統 `python3`、`onnxruntime 1.20.2`  
**判定：** FAIL — 觸發 D-02 stop-loss

## 1. 目的與有效判定條件

本次是 Phase 10 D-02 Day-1 真機檢查點。唯一有效的 PASS 條件是 ONNX Runtime verbose placement log 顯示至少一個算子被排入 `NeuronExecutionProvider`；provider 出現在可用清單、NeuronEP 建立成功，或 session 開始初始化都不構成 PASS。

## 2. 已校驗輸入與探針

固定 shape 模型由開發機官方 `onnxruntime.tools.make_dynamic_shape_fixed` 產生，部署到真機後重新校驗：

```text
239233683 bytes models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/model.int8.fixed.onnx
d9c5d2cef743268156768786bae155a5da777cc1791dac2e08cb896765948049  models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/model.int8.fixed.onnx
```

固定輸入簽章：`x=[1,200,560]`、`x_length=[1]`、`language=[1]`、`text_norm=[1]`。真機 probe 檔案也已逐一以 SHA-256 與開發機來源比對相符：

```text
7243b50335ccdbd881f045659bd4454533c30cace2ad7bad0cdbc8934f15fa00  edge/npu_spike/__init__.py
c2a3d1df59932d23b6745b435974a9692f03d5cc2d5be9e2e9b94949c9202b6d  edge/npu_spike/raw_neuron_session.py
d1b21a7d6ee8ffc4c4b3ed85e25bab317d1cb6e5d5d05498d82e9e0f676e3d24  server/npu_placement.py
```

執行命令：

```bash
cd /root/talkybuddy && python3 -m edge.npu_spike.raw_neuron_session \
  --model models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/model.int8.fixed.onnx
```

## 3. 原始輸出保存

完整真機 stdout/stderr（包含 ORT verbose log）逐位元保存於同目錄的 [`DAY1-RAW-OUTPUT.txt`](DAY1-RAW-OUTPUT.txt)：

```text
355823 bytes
SHA-256: 94dc698082498a2e1d62eaa22e941bcd67586b1cafb15df799336ef1f8034d64
```

原始輸出中的 Neuron runtime 與兩次 provider options 嘗試均實際列舉 MDLA：

```text
2026-07-26 07:13:46.431482712 [I:onnxruntime:Default, neuron_execution_provider.cc:119 NeuronExecutionProvider] Neuron Execution Provider Version (ORT): 1.20.2.1
INFO: Num devices: 2
INFO: Got device name: mtk-gpu
INFO: Got device name: mtk-mdla
INFO: NeuronApi version: 8.2.16
2026-07-26 07:13:46.539802575 [I:onnxruntime:Default, neuron_execution_provider.cc:124 NeuronExecutionProvider] Create Neuron Execution Provider successfully.
...
2026-07-26 07:14:04.926147988 [I:onnxruntime:Default, neuron_execution_provider.cc:119 NeuronExecutionProvider] Neuron Execution Provider Version (ORT): 1.20.2.1
2026-07-26 07:14:04.926202142 [I:onnxruntime:Default, neuron_execution_provider.cc:124 NeuronExecutionProvider] Create Neuron Execution Provider successfully.
```

但兩次均在 session 初始化期間終止，沒有產出 per-op placement table：

```text
2026-07-26 07:14:04.391553343 [E:onnxruntime:, inference_session.cc:2117 operator()] Exception during initialization: unordered_map::at
2026-07-26 07:14:27.073861081 [E:onnxruntime:, inference_session.cc:2117 operator()] Exception during initialization: unordered_map::at

NPU: OFF, 0/0 ops accelerated
DAY1_NPU_PROBE: FAIL 0/0 ops on NeuronExecutionProvider

__DAY1_PROBE_EXIT_CODE__=1
```

## 4. D-02 判定與停損

`DAY1_NPU_PROBE: FAIL 0/0 ops on NeuronExecutionProvider`

真機確實有 NeuronEP、Neuron runtime 與 `mtk-mdla`，但在此已校驗的 SenseVoice fixed-shape 模型上，兩種 provider options 都無法完成 ORT session 初始化，故實際 NPU placement 是 **0/X**（probe 輸出為 `0/0`，原因是初始化失敗而未能產生可 partition 的 session）。這不滿足 D-02 的「至少一個算子落到 NPU」條件。

**停止條件已觸發：** 不將 NPU 宣稱為可用能力；不實作或驗證 `server/asr_npu.py` 路徑；收斂並維持 Phase 8 CPU-only offline baseline。後續書面決策見 [`ADR-npu-path.md`](ADR-npu-path.md)。

## 5. 可達性恢復後的重驗

**重驗時間：** 2026-07-26 07:55–07:56 UTC  
**裝置可達性：** ICMP `2/2` 回覆、`0% packet loss`；SSH 成功連至 `genio-520-evk`。

在執行前，真機再次完成不可跳過的完整性閘門：

```text
MODEL_STAT_BYTES=239233683
MODEL_SHA256=d9c5d2cef743268156768786bae155a5da777cc1791dac2e08cb896765948049
PROBE_EXISTS=yes
PLACEMENT_EXISTS=yes
```

模型與 probe 檔案均通過後，才以第 2 節的相同命令執行 raw NeuronEP probe。完整 stdout/stderr 與本機捕獲的 exit code 保存於 [`DAY1-RAW-OUTPUT-20260726T0755Z.txt`](DAY1-RAW-OUTPUT-20260726T0755Z.txt)：

```text
355808 bytes
SHA-256: fbeaffb4185e5e1fe32c490a3f9f4c0cebbc50073f385cade540ce1bf0529647
NPU: OFF, 0/0 ops accelerated
DAY1_NPU_PROBE: FAIL 0/0 ops on NeuronExecutionProvider
__DAY1_PROBE_EXIT_CODE__=1
```

兩輪仍均在 session 初始化報 `Exception during initialization: unordered_map::at`。這是同一個已校驗模型的可重現真機 FAIL，不是網路中斷，也不改變第 4 節的 D-02 stop-loss 結論。

