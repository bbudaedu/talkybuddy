#!/usr/bin/env python3
"""Piper/VITS vocoder 子圖抽取 → 固定形狀 → mtk_converter TFLite。

**為什麼要切子圖**：完整的 Piper VITS 圖（2,755 節點）含 12 個 `NonZero`、
30 個 `ScatterND`、2 個 `RandomNormalLike`，且輸入輸出全為動態形狀——
整張圖不可能上 MDLA。但架構上 VITS 分四段：

    enc_p（text encoder, 865 節點, transformer）
    dp  （duration predictor, 1,455 節點, 含隨機取樣）
    flow（196 節點）
    dec （vocoder, 67 節點）  ← 只有這段是純 CNN

`dec` 的算子組成為 24 Add / 20 Conv / 16 LeakyRelu / 3 ConvTranspose / 3 Div / 1 Tanh，
**零 MatMul、零 BatchMatMul、零動態形狀算子**——正是 MDLA 吃得下的形態。
且邊界乾淨：單一輸入 `/Mul_7_output_0`、單一輸出 `/dec/Tanh_output_0`。

**實測結果（2026-07-28，Genio 520）**：整圖被 Neuron Stable Delegate 完整接收，
零算子拒收，`Inference (avg)` 30.4ms vs CPU-6T 243.7ms = **8.0×**。
原始輸出見 `edge/npu_spike/VOCODER-NPU-PREOPCHECK-RAW.txt`。

**兩個關鍵陷阱（都是撞到才發現）**：

1. `onnx.utils.extract_model` 會**原樣保留母圖的 `value_info`**，其中 68 個中間張量
   仍帶 `unk__NNNN` 動態維度。即使把 graph input/output 釘成靜態，這些殘留會讓
   mtk_converter 在 `_create_tensor_from_onnx_value_info` 拋 `AssertionError`。
   **必須 `del graph.value_info[:]` 清空後再跑 shape inference**，逼它全部重算。
2. 清 output shape 時不能只 `del dims[:]`——會變成 rank-0，shape inference 會以
   「Inferred shape and existing shape differ in rank: (3) vs (0)」失敗。
   要逐維 `Clear()` 後填入正確 dim_value。

用法：

    # 階段 1（主環境 .venv，onnx>=1.14）
    .venv/bin/python edge/np8/extract_piper_vocoder.py --stage prepare \\
        --src models/zh_CN-huayan-medium.onnx --out /tmp/dec_T200.onnx --frames 200

    # 階段 2（.venv-np8，Python 3.11 + mtk_converter 8.13.0）
    .venv-np8/bin/python edge/np8/extract_piper_vocoder.py --stage convert \\
        --src /tmp/dec_T200.onnx --out edge/np8/dec_T200.tflite
"""

from __future__ import annotations

import argparse

# Piper medium 的聲碼器上採樣率：1 個 mel frame → 256 個音訊樣本，取樣率 22050Hz。
HOP_LENGTH = 256
SAMPLE_RATE = 22050

# `dec` 子圖的邊界張量。若換 Piper 模型版本需重新以節點名稱前綴分群確認。
VOCODER_INPUT = "/Mul_7_output_0"
VOCODER_OUTPUT = "/dec/Tanh_output_0"


def prepare(src: str, dst: str, frames: int) -> None:
    """抽出 vocoder 子圖並釘成靜態 shape。需在主環境執行。"""
    import onnx
    from onnx import shape_inference
    from onnx.utils import extract_model

    tmp = dst + ".raw.onnx"
    extract_model(src, tmp, [VOCODER_INPUT], [VOCODER_OUTPUT])
    model = onnx.load(tmp)

    # 釘 graph input：[batch, 192, T] → [1, 192, frames]
    in_dims = model.graph.input[0].type.tensor_type.shape.dim
    in_dims[0].Clear()
    in_dims[0].dim_value = 1
    in_dims[2].Clear()
    in_dims[2].dim_value = frames

    # 釘 graph output：[batch, 1, T*hop] → [1, 1, frames*256]
    # 陷阱 2：逐維 Clear 而非整段刪除，否則 shape inference 會撞 rank 不符。
    out_dims = model.graph.output[0].type.tensor_type.shape.dim
    out_dims[0].Clear()
    out_dims[0].dim_value = 1
    out_dims[2].Clear()
    out_dims[2].dim_value = frames * HOP_LENGTH

    # 陷阱 1：清空繼承自母圖、仍帶 unk__ 維度的 value_info，逼全部重推。
    del model.graph.value_info[:]

    inferred = shape_inference.infer_shapes(model, strict_mode=True)
    onnx.checker.check_model(inferred)

    residual = [
        vi.name
        for vi in inferred.graph.value_info
        if any(d.dim_param for d in vi.type.tensor_type.shape.dim)
    ]
    if residual:
        raise SystemExit(f"仍有動態維度未消除，mtk_converter 會失敗：{residual[:5]}")

    onnx.save(inferred, dst)
    secs = frames * HOP_LENGTH / SAMPLE_RATE
    print(f"[prepare] {dst}  輸入 [1,192,{frames}] → 輸出 [1,1,{frames * HOP_LENGTH}]"
          f"（{secs:.2f} 秒音訊 @{SAMPLE_RATE}Hz）")


def convert(src: str, dst: str) -> None:
    """mtk_converter ONNX → TFLite。需在 `.venv-np8`（Python 3.11）內執行。"""
    import mtk_converter

    converter = mtk_converter.OnnxConverter.from_model_proto_file(src)
    # 量化需真實 fbank/mel 校準資料，尚未進行；FP32 版已足以驗證算子相容性。
    converter.quantize = False
    converter.convert_to_tflite(output_file=dst)
    print(f"[convert] {dst}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["prepare", "convert"], required=True)
    parser.add_argument("--src", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--frames", type=int, default=200,
                        help="釘住的 mel frame 數；200 ≈ 2.32 秒音訊")
    args = parser.parse_args()

    if args.stage == "prepare":
        prepare(args.src, args.out, args.frames)
    else:
        convert(args.src, args.out)


if __name__ == "__main__":
    main()
