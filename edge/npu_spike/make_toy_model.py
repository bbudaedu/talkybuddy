# -*- coding: utf-8 -*-
"""edge/npu_spike/make_toy_model.py — Day-1 停損缺的那一刀二分診斷。

2026-07-26 的 `DAY1_NPU_PROBE: FAIL 0/0 ops` 只證明了一件事：SenseVoice
`model.int8.fixed.onnx` 在真機 ORT 1.20.2 NeuronEP 上 session 初始化就以
`unordered_map::at` 失敗。它**沒有**區分兩個完全不同的世界：

  (A) 這台機器的 NeuronEP 根本建不起任何 session（環境問題，NPU 無望）。
  (B) NeuronEP 沒問題，是 SenseVoice 那組動態量化算子不被支援（模型問題，
      換模型或換量化格式就有救）。

本檔產生一個小到不能再小的 **FP32、全靜態 shape** ONNX graph，交給既有的
`edge/npu_spike/raw_neuron_session.py` 跑同一套 per-op placement 探針。
判讀方式只有兩種結局，沒有灰帶：

  - toy 也 FAIL -> 世界 (A)。NeuronEP 在此環境不可用，停損成立。
  - toy PASS    -> 世界 (B)。問題在 SenseVoice 模型；`model.int8.fixed.onnx`
    含 281 個 `DynamicQuantizeLinear` 與 281 個 `MatMulInteger`，兩者都需要
    runtime 計算 scale，是 NPU delegate 的典型不支援算子。下一步改測 FP32
    `model.onnx`（同一支 probe，只換 `--model`）。

為什麼 toy graph 必須是 FP32 且全靜態 shape：MediaTek 官方文件明載 Genio NPU
不支援動態 shape（見 `fix_shape.py` docstring），而混進任何量化算子就等於把
「量化格式」這個待測變因又帶回診斷裡，失去隔離意義。

比照 `edge/npu_spike/inspect_model.py`、`fix_shape.py`、`raw_neuron_session.py`
的既有結構：純函式（`build_toy_spec`、`format_toy_summary`）在上、無 I/O
副作用、可在無 `onnx`／`onnxruntime` 的 dev 機以 pytest 完整驗證；`main()`
在下，是唯一 lazy import `onnx` 並寫檔的地方。
"""

from __future__ import annotations

TOY_SUMMARY_PREFIX = "TOY_MODEL:"

TOY_VARIANTS = ("conv", "matmul")

# 真機 Yocto 的 ORT 1.20.2 最高只吃 IR version 10；`onnx` 1.22 預設寫出 13，
# 模型會在 `Model::Model` 就以 `Unsupported model IR version` 載入失敗，連
# graph partition 都到不了（2026-07-27 首次真機執行實際踩到）。釘在 7 是為了
# 與 SenseVoice 系列模型（實測 ir_version=7）完全對齊，讓 toy 與正式模型之間
# 不多出 IR version 這個變因——toy 的全部價值就在於「只剩一個變因」。
TOY_IR_VERSION = 7

# 兩個 variant 的形狀刻意選小：診斷要的是「有沒有 placement」，不是效能數字。
# graph 越小，FAIL 時「大概是哪個算子不支援」的模糊空間就越小。
_TOY_SPECS: dict[str, dict] = {
    # 最基本的 CNN 算子組合。任何宣稱支援 CNN 的 NPU delegate 都應該吃得下。
    "conv": {
        "op_types": ["Conv", "Relu"],
        "input": {"name": "x", "shape": [1, 3, 32, 32], "dtype": "float32"},
        "output": {"name": "y", "shape": [1, 8, 32, 32], "dtype": "float32"},
        "initializers": [
            {"name": "W", "shape": [8, 3, 3, 3], "dtype": "float32"},
            {"name": "B", "shape": [8], "dtype": "float32"},
        ],
    },
    # 最基本的 GEMM 算子組合。SenseVoice 的骨幹是 transformer，真正該被
    # 加速的是 MatMul；這個 variant 直接測那條路。
    "matmul": {
        "op_types": ["MatMul", "Add"],
        "input": {"name": "x", "shape": [1, 64, 128], "dtype": "float32"},
        "output": {"name": "y", "shape": [1, 64, 64], "dtype": "float32"},
        "initializers": [
            {"name": "W", "shape": [128, 64], "dtype": "float32"},
            {"name": "B", "shape": [64], "dtype": "float32"},
        ],
    },
}


def build_toy_spec(variant: str = "conv") -> dict:
    """回傳 toy graph 的純資料描述（不需要 `onnx`，故可被單元測試完整驗證）。

    未知的 `variant` 直接 `ValueError` 早炸——這與「執行期外部呼叫失敗要安全
    降級」是不同情境，屬程式員參數錯誤，比照
    `fix_shape.build_fix_shape_argv` 的處置。

    回傳的 dict 為深拷貝，呼叫端就地修改不會污染 `_TOY_SPECS`。
    """
    if variant not in _TOY_SPECS:
        raise ValueError(
            f"未知的 toy variant {variant!r}；可用值：{', '.join(TOY_VARIANTS)}"
        )

    import copy

    spec = copy.deepcopy(_TOY_SPECS[variant])
    spec["variant"] = variant
    spec["ir_version"] = TOY_IR_VERSION
    return spec


def format_toy_summary(spec: dict) -> str:
    """把 `build_toy_spec` 的結果格式化，最後一行固定為人眼／grep 兩用 marker。

    任何格式異常的輸入（非 dict、空 dict、缺欄位）一律回傳以
    `TOY_MODEL:` 起頭的最後一行且**不拋例外**——比照
    `raw_neuron_session.format_probe_verdict` 的契約，診斷工具不能因為
    輸入怪就死在真機上。
    """
    if not isinstance(spec, dict) or not spec:
        return f"{TOY_SUMMARY_PREFIX} (no spec data)"

    variant = spec.get("variant", "(unknown)")
    op_types = spec.get("op_types") or []
    input_spec = spec.get("input") or {}
    output_spec = spec.get("output") or {}

    def _describe(tensor: object, fallback: str) -> str:
        if not isinstance(tensor, dict) or not tensor:
            return fallback
        name = tensor.get("name", "?")
        shape = tensor.get("shape") or []
        dtype = tensor.get("dtype", "?")
        return f"{name}: {dtype}{list(shape)}"

    lines = [
        f"variant = {variant}",
        f"input  {_describe(input_spec, '(none)')}",
        f"output {_describe(output_spec, '(none)')}",
        f"nodes = {len(op_types)}",
        f"{TOY_SUMMARY_PREFIX} {variant} ops={','.join(str(o) for o in op_types)}",
    ]
    return "\n".join(lines)


def build_toy_model(spec: dict):
    """由 spec 組出真正的 `onnx.ModelProto`。唯一需要 `onnx` 的建構函式。

    權重以固定 seed 產生（`numpy.random.default_rng(0)`），讓同一個 variant
    在開發機與真機產出逐位元相同的模型檔——真機端得以用 SHA-256 比對確認
    傳輸完整，比照 `DAY1-EVIDENCE.md` 對 SenseVoice 模型的處置。
    """
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    rng = np.random.default_rng(0)
    variant = spec["variant"]

    initializers = []
    for init in spec["initializers"]:
        array = rng.standard_normal(tuple(init["shape"])).astype(np.float32) * 0.1
        initializers.append(numpy_helper.from_array(array, name=init["name"]))

    input_spec = spec["input"]
    output_spec = spec["output"]

    if variant == "conv":
        nodes = [
            helper.make_node(
                "Conv",
                inputs=[input_spec["name"], "W", "B"],
                outputs=["conv_out"],
                kernel_shape=[3, 3],
                pads=[1, 1, 1, 1],
                strides=[1, 1],
            ),
            helper.make_node("Relu", inputs=["conv_out"], outputs=[output_spec["name"]]),
        ]
    elif variant == "matmul":
        nodes = [
            helper.make_node(
                "MatMul", inputs=[input_spec["name"], "W"], outputs=["mm_out"]
            ),
            helper.make_node(
                "Add", inputs=["mm_out", "B"], outputs=[output_spec["name"]]
            ),
        ]
    else:  # pragma: no cover -- build_toy_spec 已擋掉未知 variant
        raise ValueError(f"未知的 toy variant {variant!r}")

    graph = helper.make_graph(
        nodes,
        f"toy_{variant}",
        inputs=[
            helper.make_tensor_value_info(
                input_spec["name"], TensorProto.FLOAT, input_spec["shape"]
            )
        ],
        outputs=[
            helper.make_tensor_value_info(
                output_spec["name"], TensorProto.FLOAT, output_spec["shape"]
            )
        ],
        initializer=initializers,
    )

    # opset 13：與 SenseVoice `model.int8.fixed.onnx` 相同（實測 opset_import
    # 為 ('', 13)），避免 opset 版本差異變成另一個未受控變因。
    model = helper.make_model(
        graph, opset_imports=[helper.make_opsetid("", 13)], producer_name="talkybuddy-npu-spike"
    )
    # 必須在 check_model 之前覆寫：`make_model` 會套用**本機 onnx 版本**的預設
    # IR version（onnx 1.22 為 13），而真機 ORT 1.20.2 上限是 10。不覆寫的話
    # 模型在裝置端連載入都失敗，探針量到的就不是 NPU 而是版本不相容。
    model.ir_version = spec.get("ir_version", TOY_IR_VERSION)
    onnx.checker.check_model(model)
    return model


def main() -> None:
    """開發機執行的進入點：產出 toy 模型檔並印出 SHA-256 供真機比對。"""
    import argparse
    import hashlib
    import sys

    parser = argparse.ArgumentParser(
        description="產生 FP32 靜態 shape toy ONNX，供 NeuronEP 二分診斷使用"
    )
    parser.add_argument(
        "--variant",
        default="conv",
        choices=list(TOY_VARIANTS),
        help="toy graph 形態；conv 測 CNN 算子、matmul 測 transformer 骨幹的 GEMM 路徑",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="輸出路徑；預設為 edge/npu_spike/toy_<variant>.onnx",
    )
    args = parser.parse_args()

    try:
        spec = build_toy_spec(args.variant)
    except ValueError as exc:
        print(f"參數錯誤：{exc}")
        sys.exit(2)

    print(format_toy_summary(spec))

    out_path = args.out or f"edge/npu_spike/toy_{args.variant}.onnx"

    try:
        import onnx  # noqa: PLC0415 -- lazy import

        model = build_toy_model(spec)
        onnx.save(model, out_path)
    except Exception as exc:  # noqa: BLE001 -- 印出原文即可，不需 traceback
        print(f"建立/寫出 toy 模型失敗：{exc}")
        sys.exit(2)

    with open(out_path, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()

    import os

    print(f"written = {out_path}")
    print(f"bytes   = {os.path.getsize(out_path)}")
    print(f"sha256  = {digest}")


if __name__ == "__main__":
    main()
