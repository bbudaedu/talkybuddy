# NPU 重開真機執行手冊（2026-07-27）

對應 `ADR-npu-path.md` §7。**執行前這些步驟一步都沒跑過**——本檔是待執行的腳本，不是結果紀錄。

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
