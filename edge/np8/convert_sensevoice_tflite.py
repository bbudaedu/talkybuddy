# -*- coding: utf-8 -*-
"""SenseVoice ONNX → TFLite（MediaTek NP8 mtk_converter 路徑）。

## 為什麼是這條路

`edge/npu_spike/ADR-npu-path.md` §8 記錄 ORT-NeuronEP 路徑已用真機證據否證
（SenseVoice 兩版皆在 session 初始化崩潰 `unordered_map::at`，六個假設全排除、
原因未定位）。MediaTek 官方亦把 ORT 的 NPU EP 標為「in active development」，
而 IoT AI Hub 的 analytical model zoo 全部走 TFLite + NeuronSDK。

`edge/npu_spike/TFLITE-CONVERSION-BLOCKED.md` 記錄 onnx2tf 兩條 backend 都失敗
（flatbuffer_direct OOM >7.6GB、tf_converter 撞 tf_keras 相容性 bug）。
本檔改用 **mtk_converter 8.13.0**（NP8 public 層，`.planning/research/STACK.md`
原訂 A-plan 的正規工具），實測 **16.8 秒**完成 9082 節點的轉換、無 OOM。

## 前置

mtk_converter 只出到 cp311，專案主環境是 3.12，因此需要獨立環境：

    uv python install 3.11
    uv venv --python 3.11 .venv-np8
    unzip edge/np8/mtk_converter-8.13.0_packages.zip -d edge/np8/_extract \
        'mtk_converter-8.13.0-cp311*'
    uv pip install --python .venv-np8/bin/python \
        edge/np8/_extract/mtk_converter-8.13.0-cp311-*.whl
    uv pip install --python .venv-np8/bin/python "onnx==1.13.1"   # 需 <1.14.0

## 三個必要的前處理（缺一轉不過）

1. **固定 shape** — NPU 不支援動態 shape（MediaTek 官方明載）。
   由 `edge/npu_spike/fix_shape.py` 產出 `model.fixed.onnx`（x=[1,200,560]）。
2. **釘住 `x_length`** — 它是圖輸入（執行期變數），使依賴它的 `Range` 無法被
   常數摺疊，而 mtk_converter 明確不支援匯出 `Range` 到 TFLite。固定 shape 部署
   下 `x_length` 恆為 200（`fix_shape.py` docstring 已載明餵資料端須自行
   pad/truncate），因此把它改成 int32 常數 `[200]` 與既有設計一致。
   **注意 dtype 是 INT32 不是 INT64**，寫錯會在載入時報 Add 的型別衝突。
3. **常數摺疊，且必須用 `ORT_ENABLE_BASIC`** — `ORT_ENABLE_EXTENDED` 會引入
   `com.microsoft` 域的 `SkipLayerNormalization` / `FusedMatMul`，mtk_converter
   只吃預設域，會直接拒絕。折疊後還要清掉未使用的 opset 宣告（ORT 會宣告一堆
   沒用到的域，mtk_converter 的檢查看的是宣告不是實際使用）。

摺疊效果：節點 9082 → 3807，`Range` 3 → 0。

## 已知限制

- 輸出為 **float TFLite（約 937MB）**，裝置總記憶體只有 3.7GB 而 Phase 8 實測
  三引擎峰值已達 2723MB，**這個大小無法與既有引擎共存**。實際部署需要量化版，
  而量化需要真實的校準資料（fbank 特徵），尚未進行。
- ORT 折疊時會警告 `'logits' source:{1,204,25055} target:{1,200,25055}`。這是
  encoder 內部 padding 造成的宣告 shape 與實際 shape 不一致，ORT 以 lenient
  merge 處理。**尚未驗證這是否影響輸出正確性**，不可當作無害略過。
"""

from __future__ import annotations

import argparse
import os
import sys
import time

MODEL_DIR = "models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
FIXED_LEN = 200


def pin_x_length(src: str, dst: str, length: int = FIXED_LEN) -> None:
    """把 `x_length` 從圖輸入改成常數 initializer。需要 onnx（主環境即可）。

    dtype 必須沿用原圖宣告（SenseVoice 為 INT32）；寫成 INT64 會讓下游 `Add`
    的型別參數綁到兩種型別而在載入期失敗。
    """
    import numpy as np
    import onnx
    from onnx import TensorProto, numpy_helper

    model = onnx.load(src)
    graph = model.graph
    dtypes = {i.name: i.type.tensor_type.elem_type for i in graph.input}
    if "x_length" not in dtypes:
        raise SystemExit("圖中沒有 x_length，模型可能已處理過或非預期版本")

    np_dtype = {
        TensorProto.INT32: np.int32,
        TensorProto.INT64: np.int64,
    }[dtypes["x_length"]]

    kept = [i for i in graph.input if i.name != "x_length"]
    graph.initializer.append(
        numpy_helper.from_array(np.array([length], dtype=np_dtype), name="x_length")
    )
    del graph.input[:]
    graph.input.extend(kept)
    onnx.save(model, dst)


def fold_constants(src: str, dst: str) -> None:
    """以 ORT `ORT_ENABLE_BASIC` 常數摺疊，並清掉未使用的 opset 宣告。

    刻意不用 EXTENDED：它會引入 `com.microsoft` 融合算子，mtk_converter 不吃。
    """
    import onnx
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
    options.optimized_model_filepath = dst
    ort.InferenceSession(src, options, providers=["CPUExecutionProvider"])

    model = onnx.load(dst)
    default_only = [o for o in model.opset_import if o.domain == ""]
    if len(default_only) != len(model.opset_import):
        del model.opset_import[:]
        model.opset_import.extend(default_only)
        onnx.save(model, dst)


def convert(src: str, dst: str) -> None:
    """mtk_converter ONNX → TFLite。需在 `.venv-np8`（Python 3.11）內執行。"""
    import mtk_converter

    converter = mtk_converter.OnnxConverter.from_model_proto_file(src)
    converter.quantize = False  # 量化需校準資料，見模組 docstring「已知限制」
    converter.convert_to_tflite(output_file=dst)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", choices=["prepare", "convert"], required=True,
                        help="prepare 在主環境（onnx>=1.14 可）；convert 在 .venv-np8")
    parser.add_argument("--model-dir", default=MODEL_DIR)
    parser.add_argument("--out", default="edge/np8_out/sensevoice_float.tflite")
    args = parser.parse_args()

    fixed = os.path.join(args.model_dir, "model.fixed.onnx")
    pinned = os.path.join(args.model_dir, "model.pinned.onnx")
    folded = os.path.join(args.model_dir, "model.folded.onnx")

    start = time.time()
    if args.stage == "prepare":
        if not os.path.exists(fixed):
            raise SystemExit(f"缺少 {fixed}；先跑 edge/npu_spike/fix_shape.py")
        print(f"[1/2] 釘住 x_length={FIXED_LEN} -> {pinned}", flush=True)
        pin_x_length(fixed, pinned)
        print(f"[2/2] 常數摺疊（BASIC）-> {folded}", flush=True)
        fold_constants(pinned, folded)
        import collections
        import onnx
        counts = collections.Counter(
            n.op_type for n in onnx.load(folded, load_external_data=False).graph.node
        )
        print(f"nodes={sum(counts.values())} Range={counts.get('Range', 0)}")
        if counts.get("Range", 0):
            print("PREPARE_RESULT: FAIL（Range 未清空，轉換會被拒）")
            sys.exit(1)
        print(f"PREPARE_RESULT: OK ({time.time() - start:.1f}s)")
    else:
        if not os.path.exists(folded):
            raise SystemExit(f"缺少 {folded}；先跑 --stage prepare")
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        print(f"[1/1] mtk_converter -> {args.out}", flush=True)
        convert(folded, args.out)
        print(f"CONVERT_RESULT: OK ({time.time() - start:.1f}s, "
              f"{os.path.getsize(args.out)} bytes)")


if __name__ == "__main__":
    main()
