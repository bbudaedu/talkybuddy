# -*- coding: utf-8 -*-
"""edge/npu_spike/fix_shape.py — make_dynamic_shape_fixed 固定 argv 包裝。

MediaTek 官方文件明載 Genio NPU 不支援動態 shape（`onnx_dev.html`），因此把
SenseVoice `model.int8.onnx` 的動態時間軸固定成靜態整數，是
`NeuronExecutionProvider` 可排程這張圖的**硬性前置條件**，不是效能優化選項。

Caveat（10-05 需承接，此檔不處理）：固定 shape 後，餵資料端必須自行把每段
音訊 pad/truncate 成該固定長度——這是相對 sherpa-onnx 原本 streaming-friendly
CPU 路徑的一個真實行為改變。這個 caller-side 義務由 `server/asr_npu.py`
（10-05）承擔；本檔只負責產出固定後的模型檔本身。

比照 `edge/npu_spike/inspect_model.py` 與 `edge/runtime/measure_peak_rss.py`：
純函式在上、`main()` 在下；所有子行程呼叫一律固定 argv 串列，不使用 shell
模式或字串插值。
"""

from __future__ import annotations

import subprocess
import sys


def build_fix_shape_argv(
    model_in: str,
    model_out: str,
    dim_param: str | None = None,
    dim_value: int | None = None,
    input_name: str | None = None,
    input_shape: list[int] | None = None,
) -> list[str]:
    """組出 `onnxruntime.tools.make_dynamic_shape_fixed` 的固定 argv 串列。

    兩種合法呼叫形式擇一：
      - `dim_param` + `dim_value`：已知動態軸的符號名稱（例如 "T"）。
      - `input_name` + `input_shape`：整個 input 要以固定 shape 取代（未命名
        動態軸的情況）。
    兩組都給、或都沒給，屬程式員參數組裝錯誤，直接 `ValueError` 早炸——這與
    「執行期外部呼叫失敗要安全降級」是不同情境，不應該吞掉。
    """
    has_dim_form = dim_param is not None and dim_value is not None
    has_input_form = input_name is not None and input_shape is not None

    if has_dim_form and has_input_form:
        raise ValueError(
            "dim_param/dim_value 與 input_name/input_shape 不可同時提供，"
            "請擇一形式呼叫 build_fix_shape_argv"
        )
    if not has_dim_form and not has_input_form:
        raise ValueError(
            "須提供 dim_param+dim_value 或 input_name+input_shape 其中一組"
        )

    argv = [sys.executable, "-m", "onnxruntime.tools.make_dynamic_shape_fixed"]
    if has_dim_form:
        argv += ["--dim_param", str(dim_param), "--dim_value", str(dim_value)]
    else:
        shape_str = ",".join(str(d) for d in input_shape)
        argv += ["--input_name", str(input_name), "--input_shape", shape_str]
    argv += [model_in, model_out]
    return argv


def run_fix_shape(argv: list[str]) -> tuple[int, str]:
    """固定 argv 呼叫 `make_dynamic_shape_fixed`，回傳 `(returncode, 合併輸出)`。

    `OSError`／`subprocess.SubprocessError` 一律回傳 `(-1, 錯誤訊息)`，不拋
    例外——真機上這支腳本失敗時，價值在於把錯誤原文帶回來，不是讓呼叫端崩潰。
    """
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, str(exc)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def _default_model_out(model_in: str) -> str:
    """`model.int8.onnx` -> `model.int8.fixed.onnx`；此檔名會被 10-05 的
    `NPU_ASR_MODEL_PATH` 預設值引用，兩處必須一致。"""
    if model_in.endswith(".onnx"):
        return model_in[: -len(".onnx")] + ".fixed.onnx"
    return model_in + ".fixed"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="固定 SenseVoice ONNX 動態 shape（NeuronExecutionProvider 前置條件）"
    )
    parser.add_argument("--model-in", required=True, help="來源 model.int8.onnx 路徑")
    parser.add_argument(
        "--model-out",
        default=None,
        help="輸出路徑；預設在 --model-in 同目錄下插入 .fixed（例如 model.int8.fixed.onnx）",
    )
    parser.add_argument("--dim-param", default=None, help="動態軸的符號名稱，例如 T")
    parser.add_argument("--dim-value", type=int, default=None, help="固定後的整數長度")
    parser.add_argument("--input-name", default=None, help="要整個固定 shape 的 input 名稱")
    parser.add_argument(
        "--input-shape", default=None, help="逗號分隔的固定 shape，例如 1,200,80"
    )
    args = parser.parse_args()

    model_out = args.model_out or _default_model_out(args.model_in)
    input_shape = None
    if args.input_shape:
        input_shape = [int(x) for x in args.input_shape.split(",")]

    try:
        argv = build_fix_shape_argv(
            args.model_in,
            model_out,
            dim_param=args.dim_param,
            dim_value=args.dim_value,
            input_name=args.input_name,
            input_shape=input_shape,
        )
    except ValueError as exc:
        print(f"參數組裝錯誤：{exc}")
        print("FIX_SHAPE: FAILED")
        return

    print("執行 argv:", argv)
    returncode, output = run_fix_shape(argv)
    print(output)
    print(f"returncode = {returncode}")
    print("FIX_SHAPE: OK" if returncode == 0 else "FIX_SHAPE: FAILED")


if __name__ == "__main__":
    main()
