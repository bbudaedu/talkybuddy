# -*- coding: utf-8 -*-
"""edge/npu_spike/make_op_probe_models.py — 找出讓 NeuronEP 崩潰的算子。

一次性診斷工具。2026-07-27 真機實測顯示：`toy_conv`（Conv+Relu）PASS 1/1，
`toy_matmul`（MatMul+Add）乾淨地退回 CPU，但 SenseVoice **FP32 與 int8 兩版
都**在 session 初始化以 `unordered_map::at` 崩潰。兩版同一個錯誤，代表崩潰
與量化無關——原本「dynamic-quant 算子是主因」的假設已被 FP32 結果推翻。

`unordered_map::at` 是查表查不到鍵時的標準例外。對照組很關鍵：`MatMul`
NeuronEP **認得**（所以能優雅地拒收、丟回 CPU），而崩潰代表某個算子連
map 裡的鍵都沒有，`.at()` 直接拋。因此本檔對 SenseVoice 圖裡出現、而
toy 尚未覆蓋的算子逐一產生**單算子 FP32 模型**，讓真機一次跑完，把
「NeuronEP 崩潰 / 乾淨拒收 / 接受」三種結果分開。

比照 `make_toy_model.py`：純函式在上、`main()` 在下、lazy import `onnx`，
且同樣把 `ir_version` 釘在 7（真機 ORT 1.20.2 上限為 10）。
"""

from __future__ import annotations

IR_VERSION = 7
OPSET = 13

# SenseVoice FP32 圖實際出現、且 toy_conv/toy_matmul 未覆蓋的算子。以「NPU
# delegate 最可能沒實作」由高到低排序：形狀/控制流類算子最可疑，逐點數學
# 類次之。每筆為 (名稱, 建圖用的 op 描述)。
OP_PROBES: tuple[str, ...] = (
    "Range",
    "Tile",
    "ConstantOfShape",
    "Where",
    "Expand",
    "Equal",
    "Sin",
    "Cos",
    "Softmax",
    "ReduceMean",
    "Transpose",
    "Gather",
    # 以下不是「算子」而是**圖形態**探針。前 12 個單算子全部乾淨退回 CPU、
    # 無一崩潰，代表 `unordered_map::at` 不是任一算子單獨造成的。SenseVoice
    # 與所有 toy 之間還有一個未受控差異：它有 4 個輸入，其中 3 個是 int64
    # 純量（`x_length`/`language`/`text_norm`），而每個 toy 都只有單一
    # float32 輸入。`.at()` 崩在 dtype→neuron-type 查表是很合理的解釋。
    "Int64Input",
    "MultiInput",
)


def probe_op_names() -> tuple[str, ...]:
    """回傳本檔會產生模型的算子名稱清單（純函式，不需要 onnx）。"""
    return OP_PROBES


def _build_single_op_model(op_type: str):
    """為單一算子組出最小的合法 FP32 ONNX 模型。需要 `onnx`。"""
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    initializers = []
    inputs = []
    outputs = []
    nodes = []

    def f32_in(name, shape):
        inputs.append(helper.make_tensor_value_info(name, TensorProto.FLOAT, shape))

    def f32_out(name, shape):
        outputs.append(helper.make_tensor_value_info(name, TensorProto.FLOAT, shape))

    def const(name, array):
        initializers.append(numpy_helper.from_array(array, name=name))

    if op_type == "Range":
        # Range 產生 int 序列後轉 float，是位置編碼的典型形狀算子。
        const("start", np.array(0, dtype=np.float32))
        const("limit", np.array(16, dtype=np.float32))
        const("delta", np.array(1, dtype=np.float32))
        f32_in("x", [1, 16])
        nodes.append(helper.make_node("Range", ["start", "limit", "delta"], ["r"]))
        nodes.append(helper.make_node("Add", ["x", "r"], ["y"]))
        f32_out("y", [1, 16])

    elif op_type == "Tile":
        f32_in("x", [1, 4])
        const("reps", np.array([1, 4], dtype=np.int64))
        nodes.append(helper.make_node("Tile", ["x", "reps"], ["y"]))
        f32_out("y", [1, 16])

    elif op_type == "ConstantOfShape":
        f32_in("x", [1, 8])
        const("shape", np.array([1, 8], dtype=np.int64))
        nodes.append(
            helper.make_node(
                "ConstantOfShape",
                ["shape"],
                ["c"],
                value=numpy_helper.from_array(np.array([1.0], dtype=np.float32)),
            )
        )
        nodes.append(helper.make_node("Add", ["x", "c"], ["y"]))
        f32_out("y", [1, 8])

    elif op_type == "Where":
        f32_in("x", [1, 8])
        const("cond", np.ones((1, 8), dtype=bool))
        const("other", np.zeros((1, 8), dtype=np.float32))
        nodes.append(helper.make_node("Where", ["cond", "x", "other"], ["y"]))
        f32_out("y", [1, 8])

    elif op_type == "Expand":
        f32_in("x", [1, 1])
        const("shape", np.array([1, 8], dtype=np.int64))
        nodes.append(helper.make_node("Expand", ["x", "shape"], ["y"]))
        f32_out("y", [1, 8])

    elif op_type == "Equal":
        # Equal 產生 bool，再 Cast 回 float 才能當圖輸出。
        f32_in("x", [1, 8])
        const("other", np.zeros((1, 8), dtype=np.float32))
        nodes.append(helper.make_node("Equal", ["x", "other"], ["e"]))
        nodes.append(helper.make_node("Cast", ["e"], ["y"], to=TensorProto.FLOAT))
        f32_out("y", [1, 8])

    elif op_type in ("Sin", "Cos"):
        f32_in("x", [1, 8])
        nodes.append(helper.make_node(op_type, ["x"], ["y"]))
        f32_out("y", [1, 8])

    elif op_type == "Softmax":
        f32_in("x", [1, 8])
        nodes.append(helper.make_node("Softmax", ["x"], ["y"], axis=-1))
        f32_out("y", [1, 8])

    elif op_type == "ReduceMean":
        f32_in("x", [1, 8])
        nodes.append(
            helper.make_node("ReduceMean", ["x"], ["y"], axes=[1], keepdims=1)
        )
        f32_out("y", [1, 1])

    elif op_type == "Transpose":
        f32_in("x", [1, 4, 8])
        nodes.append(helper.make_node("Transpose", ["x"], ["y"], perm=[0, 2, 1]))
        f32_out("y", [1, 8, 4])

    elif op_type == "Gather":
        f32_in("x", [8, 4])
        const("idx", np.array([0, 2, 4], dtype=np.int64))
        nodes.append(helper.make_node("Gather", ["x", "idx"], ["y"], axis=0))
        f32_out("y", [3, 4])

    elif op_type == "Int64Input":
        # 單一 int64 圖輸入 + Conv（Conv 已知會被 NPU 接受）。若這個崩潰，
        # 兇手就是 int64 輸入的 dtype 對應，而不是任何算子。
        f32_in("x", [1, 3, 8, 8])
        inputs.append(helper.make_tensor_value_info("n", TensorProto.INT64, [1]))
        const("W", (np.zeros((4, 3, 3, 3), dtype=np.float32) + 0.1))
        nodes.append(
            helper.make_node(
                "Conv", ["x", "W"], ["c"], kernel_shape=[3, 3], pads=[1, 1, 1, 1]
            )
        )
        nodes.append(helper.make_node("Cast", ["n"], ["nf"], to=TensorProto.FLOAT))
        nodes.append(helper.make_node("Add", ["c", "nf"], ["y"]))
        f32_out("y", [1, 4, 8, 8])

    elif op_type == "MultiInput":
        # 比照 SenseVoice 的輸入簽章形狀：1 個 float 主輸入 + 3 個 int64 純量。
        f32_in("x", [1, 3, 8, 8])
        for extra in ("x_length", "language", "text_norm"):
            inputs.append(
                helper.make_tensor_value_info(extra, TensorProto.INT64, [1])
            )
        const("W", (np.zeros((4, 3, 3, 3), dtype=np.float32) + 0.1))
        nodes.append(
            helper.make_node(
                "Conv", ["x", "W"], ["c"], kernel_shape=[3, 3], pads=[1, 1, 1, 1]
            )
        )
        prev = "c"
        for idx, extra in enumerate(("x_length", "language", "text_norm")):
            nodes.append(
                helper.make_node("Cast", [extra], [f"{extra}_f"], to=TensorProto.FLOAT)
            )
            out_name = "y" if idx == 2 else f"acc{idx}"
            nodes.append(helper.make_node("Add", [prev, f"{extra}_f"], [out_name]))
            prev = out_name
        f32_out("y", [1, 4, 8, 8])

    else:  # pragma: no cover -- probe_op_names 已界定範圍
        raise ValueError(f"未知的 op probe {op_type!r}")

    graph = helper.make_graph(
        nodes, f"probe_{op_type}", inputs=inputs, outputs=outputs, initializer=initializers
    )
    model = helper.make_model(
        graph,
        opset_imports=[helper.make_opsetid("", OPSET)],
        producer_name="talkybuddy-npu-op-probe",
    )
    model.ir_version = IR_VERSION  # 真機 ORT 1.20.2 上限為 10；務必在 check 前設定
    onnx.checker.check_model(model)
    return model


def main() -> None:
    import argparse
    import os
    import sys

    parser = argparse.ArgumentParser(
        description="為每個可疑算子產生單算子 FP32 ONNX，供真機一次跑完分流"
    )
    parser.add_argument("--out-dir", default="edge/npu_spike/op_probes")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    try:
        import onnx  # noqa: PLC0415 -- lazy import
    except ImportError as exc:
        print(f"無法 import onnx：{exc}")
        sys.exit(2)

    written = 0
    for op_type in probe_op_names():
        path = os.path.join(args.out_dir, f"op_{op_type}.onnx")
        try:
            onnx.save(_build_single_op_model(op_type), path)
        except Exception as exc:  # noqa: BLE001 -- 診斷工具：跳過失敗的、繼續產生其餘
            print(f"SKIP {op_type}: {exc}")
            continue
        written += 1
        print(f"ok {op_type} -> {path}")

    print(f"OP_PROBE_MODELS: {written}/{len(probe_op_names())} written")


if __name__ == "__main__":
    main()
