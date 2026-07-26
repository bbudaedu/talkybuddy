# -*- coding: utf-8 -*-
"""edge/npu_spike/inspect_model.py — NPU-01 Day-1 的第一個動作。

RESEARCH.md Assumption A1/A3 與 Open Question 1 全部指向：在寫任何 NPU 引擎
程式碼之前，必須先在真機上回答兩個問題——(1) 這顆已燒錄的 Yocto image 帶的
onnxruntime 有沒有 `NeuronExecutionProvider`？(2) SenseVoice `model.int8.onnx`
的 graph 輸入長什麼樣、動態軸在哪、custom metadata 帶了哪些特徵前處理參數？
本檔就是回答這兩個問題的診斷工具，不建立任何推論 session、不碰 `server/`
既有程式碼。

比照 `edge/runtime/measure_peak_rss.py` 的結構：純函式（`probe_runtime`、
`format_provider_report`、`describe_graph_io`、`format_metadata_map`）在上、
無 I/O 副作用、可在無 NPU 硬體的 dev 機以 pytest 驗證；`main()` 才是唯一
觸及真實 I/O（import onnxruntime/onnx、讀模型檔）的進入點。

已知陷阱：`edge/runtime/provision_device.sh` 建立裝置端 venv 時**未加**
`--system-site-packages`，因此就算 Yocto 系統層本身帶了
`NeuronExecutionProvider` 版本的 onnxruntime，在 venv 內的 python 也看不到
——量到的到底是 venv 還是系統 python3 會直接決定這次診斷有沒有意義。因此
`main()` 開頭一定先印出目前直譯器的 `sys.executable`／`sys.version`，讓人
一眼看出這次量的是哪一個環境。
"""

from __future__ import annotations

import sys


def probe_runtime(ort_module) -> dict:
    """探測 onnxruntime 模組的版本與可用 provider 清單。

    傳入具 `__version__` 與 `get_available_providers()` 的物件（真實
    onnxruntime 或測試 stub）；傳入 `None`（例如 import 失敗）回傳空結果，
    不拋例外。
    """
    if ort_module is None:
        return {"version": None, "providers": [], "has_neuron": False}

    try:
        version = getattr(ort_module, "__version__", None)
    except Exception:
        version = None

    try:
        providers = list(ort_module.get_available_providers())
    except Exception:
        providers = []

    has_neuron = "NeuronExecutionProvider" in providers
    return {"version": version, "providers": providers, "has_neuron": has_neuron}


def format_provider_report(info: dict) -> str:
    """把 `probe_runtime` 的結果轉成人類可讀報表，末行固定供人眼與 grep 兩用。"""
    if not isinstance(info, dict):
        info = {}
    version = info.get("version")
    providers = info.get("providers") or []
    has_neuron = bool(info.get("has_neuron"))

    lines = [
        f"onnxruntime version: {version}",
        f"available providers: {', '.join(providers) if providers else '(none)'}",
        "NEURON_EP: PRESENT" if has_neuron else "NEURON_EP: ABSENT",
    ]
    return "\n".join(lines)


def _describe_tensor(tensor) -> dict | None:
    """描述單一 `onnx.ValueInfoProto`（或等價 stub）的 name/shape/dtype/動態軸。"""
    try:
        name = getattr(tensor, "name", None)
        tensor_type = tensor.type.tensor_type
        dims = list(tensor_type.shape.dim)
    except Exception:
        return None

    shape: list = []
    dynamic_dims: list[int] = []
    for idx, dim in enumerate(dims):
        dim_param = getattr(dim, "dim_param", "") or ""
        dim_value = getattr(dim, "dim_value", 0)
        if dim_param:
            shape.append(dim_param)
            dynamic_dims.append(idx)
        else:
            shape.append(dim_value)
            if dim_value == 0:
                dynamic_dims.append(idx)

    dtype = getattr(tensor_type, "elem_type", None)
    return {"name": name, "shape": shape, "dtype": dtype, "dynamic_dims": dynamic_dims}


def describe_graph_io(graph) -> list[dict]:
    """吃 `onnx.ModelProto.graph`（或等價 stub），逐一描述每個 input/output。

    無 input 時（且無 output）回空 list；任何格式異常一律安全跳過，不拋例外
    ——診斷工具的價值在於「盡量多印」，不在於「第一個錯就停」。
    """
    result: list[dict] = []
    if graph is None:
        return result

    try:
        tensors = list(getattr(graph, "input", []) or []) + list(
            getattr(graph, "output", []) or []
        )
    except Exception:
        return result

    for tensor in tensors:
        entry = _describe_tensor(tensor)
        if entry is not None:
            result.append(entry)
    return result


def format_metadata_map(meta: dict) -> str:
    """把 ONNX custom metadata dict 依 key 排序輸出；空 dict 回固定字串。"""
    if not isinstance(meta, dict) or not meta:
        return "(no custom metadata)"
    lines = [f"{key} = {meta[key]}" for key in sorted(meta.keys())]
    return "\n".join(lines)


def main() -> None:
    """裝置上人工執行的診斷進入點。每一步各自 try/except，任一步失敗只印
    該步錯誤並繼續下一步，不中止整支腳本。"""
    print(f"sys.executable = {sys.executable}")
    print(f"sys.version = {sys.version}")

    ort = None
    try:
        import onnxruntime as ort  # noqa: PLC0415 -- lazy import，避免 import 期副作用
    except ImportError as exc:
        print(
            f"無法 import onnxruntime：{exc}；"
            "請改用系統 python3 重跑，或以 --system-site-packages 重建 venv"
        )

    try:
        info = probe_runtime(ort)
        print(format_provider_report(info))
    except Exception as exc:
        print(f"probe_runtime/format_provider_report 失敗：{exc}")

    import argparse

    parser = argparse.ArgumentParser(description="SenseVoice ONNX 簽章/metadata 診斷工具")
    parser.add_argument(
        "--model",
        default=None,
        help="model.int8.onnx 路徑；未指定則試著從 server.config.SENSEVOICE_DIR 推導",
    )
    args = parser.parse_args()

    model_path = args.model
    if model_path is None:
        try:
            from server.config import SENSEVOICE_DIR  # noqa: PLC0415 -- lazy import

            model_path = str(SENSEVOICE_DIR / "model.int8.onnx")
        except Exception as exc:
            print(f"無法從 server.config 取得預設模型路徑：{exc}；請顯式提供 --model")

    if model_path is None:
        print("未提供 --model 且無法推導預設路徑，略過模型檢查")
        return

    print(f"model_path = {model_path}")

    graph = None
    try:
        import onnx  # noqa: PLC0415 -- lazy import

        model = onnx.load(model_path)
        graph = model.graph
        print("=== Graph IO ===")
        for entry in describe_graph_io(graph):
            print(entry)
    except Exception as exc:
        print(f"onnx.load / describe_graph_io 失敗：{exc}")

    try:
        if ort is None:
            raise RuntimeError("onnxruntime 未載入，無法開 CPU session 讀 metadata")
        # 刻意用 CPUExecutionProvider——這一步只是讀 metadata，不是測 NPU。
        session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        meta = session.get_modelmeta().custom_metadata_map
        print("=== Custom Metadata ===")
        print(format_metadata_map(dict(meta)))
    except Exception as exc:
        print(f"讀取 custom metadata 失敗：{exc}")


if __name__ == "__main__":
    main()
