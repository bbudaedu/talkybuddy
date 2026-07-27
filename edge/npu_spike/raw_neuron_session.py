# -*- coding: utf-8 -*-
"""edge/npu_spike/raw_neuron_session.py — D-02 Day-1 停損檢查點的執行載體。

(1) 本檔是唯一能在 Day 1 回答「到底有沒有任何一個算子被排到
NeuronExecutionProvider 上」這個問題的執行載體。通過條件是**至少 1 個節點**
落在 NeuronExecutionProvider——判定邏輯見 `format_probe_verdict`，其資料來源
是 `server.npu_placement.summarize_placement` 的 `accelerated` 欄位，與
session 是否建立成功、推論是否跑完全解耦。

(2) 為什麼必須繞過 sherpa-onnx：RESEARCH.md Pitfall N1 已證實 sherpa-onnx 的
Python API 把 `provider` 參數硬限制在 `cpu | cuda | coreml`，provider 白名單
寫死在其 C++ 核心的編譯期，Python 層沒有任何逃生門可以把
`NeuronExecutionProvider` 交給底層 session。因此本檔直接用 raw
`onnxruntime.InferenceSession`，不透過 sherpa-onnx 的便利包裝。

(3) 本檔刻意餵零值假輸入（`build_zero_feeds`）、不做 fbank 前處理、不做解碼，
因此**輸出的逐字稿沒有意義**——任何人不得拿本檔的推論結果宣稱 ASR 品質。
本檔的價值僅在於讓 ORT 完成 graph partition 並吐出節點放置日誌。

(4) `DEFAULT_NEURON_OPTIONS` 的鍵名出自 RESEARCH.md Assumptions Log A2 的
單一論壇來源，未經官方文件確認；錯誤的鍵「很可能被靜默忽略而非報錯」。因此
`main()` 實作「先帶 options、若零加速則改用空 options 重試」的兩段式流程，
避免把 A2 假設錯誤誤報成「NPU 不可用」。

比照 `edge/npu_spike/inspect_model.py` 與 `server/npu_placement.py`：純函式在
上、`main()` 在下、`main()` 內對 `onnxruntime` 採 lazy import，讓本檔在無
onnxruntime／onnx 的 dev 機上仍可被 pytest 完整驗證（見
`tests/test_raw_neuron_session.py`）。
"""

from __future__ import annotations

PROBE_VERDICT_PREFIX = "DAY1_NPU_PROBE:"

# A2（未驗證假設，見 10-RESEARCH.md Assumptions Log）：鍵名出自單一論壇貼文，
# 非官方文件保證。main() 的兩段式重試流程（見模組 docstring (4)）就是為了在
# 這組鍵被靜默忽略時，仍能拿到正確的「NPU 到底有沒有動」答案。
DEFAULT_NEURON_OPTIONS: dict[str, str] = {
    "NEURON_FLAG_USE_FP16": "1",
    "NEURON_FLAG_MIN_GROUP_SIZE": "1",
}

# dtype 字串（ORT session.get_inputs()/get_outputs() 的 .type 慣例，例如
# "tensor(float)"）與 raw ONNX TensorProto.elem_type 整數（describe_graph_io
# 直接透傳的欄位，例如 1）兩種鍵並存，讓呼叫端可以不經轉換直接把
# describe_graph_io 的 spec dict 餵給 build_zero_feeds。
_DTYPE_TO_NUMPY = {
    "tensor(float)": "float32",
    "tensor(int64)": "int64",
    "tensor(int32)": "int32",
    1: "float32",  # onnx.TensorProto.FLOAT
    6: "int32",  # onnx.TensorProto.INT32
    7: "int64",  # onnx.TensorProto.INT64
}


def build_neuron_providers(
    options: dict[str, str] | None = None, cpu_fallback: bool = True
) -> list:
    """組出 `onnxruntime.InferenceSession(providers=...)` 用的 provider 清單。

    `options=None`（未提供）時套用 `DEFAULT_NEURON_OPTIONS`；`options={}`
    （顯式給空 dict）是 A2 假設錯誤時的重試形態，必須原樣保留、不得被
    default 覆蓋——這是一等公民而非特例分支。

    `cpu_fallback=False` 時只回傳 `[("NeuronExecutionProvider", options)]`。
    MediaTek 官方 NeuronEP 指南明載 **"Omit fallback providers; use only
    ("NeuronExecutionProvider", options)"**：帶著 CPU fallback 會讓 ORT 在兩個
    EP 之間切分圖並插入 memcpy 節點，官方記載的
    `Execution type 'XnnpackExecutionProvider' doesn't support memcpy` 就出自
    這條路徑。SenseVoice 的 `unordered_map::at` 也崩在同一個 partition 階段，
    因此這個開關是重測該模型的必要條件。

    預設仍為 `True`，以免既有呼叫端的語意被靜默改變。
    """
    resolved_options = DEFAULT_NEURON_OPTIONS if options is None else options
    providers: list = [("NeuronExecutionProvider", resolved_options)]
    if cpu_fallback:
        providers.append("CPUExecutionProvider")
    return providers


def build_zero_feeds(io_specs: list[dict]) -> dict[str, object]:
    """由 `describe_graph_io` 的 spec 串列產生零值假輸入 dict。

    只做「盡量跑得起來以取得訊號」——spike 腳本的價值不在正確推論。動態軸
    （`dynamic_dims` 內列出的 index，或形狀本身是非正整數／符號字串）一律
    以 1 代入，絕不拋例外。dtype 依 `_DTYPE_TO_NUMPY` 對應建立
    （`tensor(float)`/onnx elem_type 1 -> float32、
    `tensor(int64)`/elem_type 7 -> int64、
    `tensor(int32)`/elem_type 6 -> int32），未知型別一律退回 float32
    （SenseVoice 的 language/textnorm 這類純量輸入是整數，餵 float 會被 ORT
    直接拒絕，因此 int64/int32 對應不可省略）。
    """
    import numpy as np

    feeds: dict[str, object] = {}
    for spec in io_specs or []:
        name = spec.get("name")
        if not name:
            continue

        shape = spec.get("shape") or []
        dynamic_dims = set(spec.get("dynamic_dims") or [])
        resolved_shape: list[int] = []
        for idx, dim in enumerate(shape):
            if idx in dynamic_dims:
                resolved_shape.append(1)
                continue
            try:
                dim_int = int(dim)
            except (TypeError, ValueError):
                dim_int = 1
            resolved_shape.append(dim_int if dim_int > 0 else 1)

        numpy_dtype = _DTYPE_TO_NUMPY.get(spec.get("dtype"), "float32")
        feeds[name] = np.zeros(tuple(resolved_shape), dtype=numpy_dtype)

    return feeds


def choose_better_summary(first: object, second: object) -> dict:
    """在兩段式重試的兩輪結果中挑出較好的一輪——重試絕不能讓失敗覆蓋成功。

    排序準則：有加速優於沒加速；同樣有加速時取 `ops_accelerated` 較大者；
    完全平手則保留第一輪（重試是保險機制，不是預設優先）。非 dict／缺欄位
    的輸入視為「沒有加速的空結果」，不拋例外。

    存在理由（2026-07-27 Genio 520 真機）：第一輪帶 `NEURON_FLAG_USE_FP16`
    其實整圖成功放上 NeuronEP，卻因 parser 少認一種日誌格式被誤判為 0/0，
    於是觸發空-options 重試；空 options 因 MDLA 不支援 FP32 而編譯失敗，
    該失敗結果被無條件寫回，把成功抹掉。parser 已修，但覆蓋這個缺陷獨立
    存在，必須各自修。
    """

    def _score(summary: object) -> tuple[int, int]:
        if not isinstance(summary, dict):
            return (0, 0)
        accelerated = 1 if summary.get("accelerated") else 0
        try:
            ops = int(summary.get("ops_accelerated") or 0)
        except (TypeError, ValueError):
            ops = 0
        return (accelerated, ops)

    first_score = _score(first)
    second_score = _score(second)

    winner = second if second_score > first_score else first
    if isinstance(winner, dict):
        return winner
    # 兩輪都不是可用的 dict：回一個明確「沒加速」的結果，絕不讓缺資料被
    # 讀成通過（比照 format_probe_verdict 對缺資料的處置）。
    return {
        "ops_accelerated": 0,
        "ops_total": 0,
        "providers": {},
        "accelerated": False,
    }


def format_probe_verdict(summary: dict) -> str:
    """把 `summarize_placement` 的輸出格式化成 Day-1 停損檢查點的判定字串。

    格式：`"DAY1_NPU_PROBE: PASS X/Y ops on NeuronExecutionProvider"` /
    `"DAY1_NPU_PROBE: FAIL X/Y ops on NeuronExecutionProvider"`。缺資料
    （非 dict、空 dict、或缺必要欄位）一律回傳以 `DAY1_NPU_PROBE: FAIL` 起頭
    的字串且不拋例外——絕不因為「沒量到」就當作通過，這是 T-10-07
    （Repudiation）的機器可讀鎖定形式。
    """
    if not isinstance(summary, dict) or not summary:
        return f"{PROBE_VERDICT_PREFIX} FAIL (no summary data)"

    ops_accelerated = summary.get("ops_accelerated")
    ops_total = summary.get("ops_total")
    accelerated = summary.get("accelerated")
    if ops_accelerated is None or ops_total is None or accelerated is None:
        return f"{PROBE_VERDICT_PREFIX} FAIL (incomplete summary data)"

    verdict = "PASS" if accelerated else "FAIL"
    return (
        f"{PROBE_VERDICT_PREFIX} {verdict} {ops_accelerated}/{ops_total} "
        "ops on NeuronExecutionProvider"
    )


def main() -> None:
    """真機人工執行的進入點。唯一觸及真實 I/O 與 session 建立的地方。

    執行序（見模組 docstring (4) 與 10-RESEARCH.md Pattern 1）：
    1. lazy import `onnxruntime`；失敗則印明確訊息並以 exit code 2 結束
       （區別於「跑得動但沒加速」的 exit code 1）。
    2. 在建立 session **之前**開啟 ORT verbose logger severity（0），
       並記下原本的 severity，於 `finally` 內還原為 2
       （10-02 模組 docstring 已載明呼叫端有此義務，見 T-10-06/T-10-08）。
    3. 用 `capture_fd_output(2)` 包住 `ort.InferenceSession(...)` 建立；
       建立本身以 try/except 包住，失敗時把例外訊息與已擷取到的
       `buf.text` 都印出來，再進入第 4 步的重試而非直接結束。
    4. 把 `buf.text` 交給 `parse_ep_placement_log` -> `summarize_placement`；
       若第一輪（帶 options）`accelerated` 為 False，自動改用空 options
       重試一次（A2 保險）。最多重試一次。
    5. 印出 provider 逐項計數、`format_placement_line`（HUD 字串），最後印
       `format_probe_verdict` 作為**整個腳本的最後一行**。
    6. 依判定設定 exit code：PASS -> 0、FAIL -> 1、環境不可用 -> 2。
    """
    import argparse
    import sys

    from server.npu_placement import (
        capture_fd_output,
        format_placement_line,
        parse_ep_placement_log,
        summarize_placement,
    )

    parser = argparse.ArgumentParser(
        description="D-02 Day-1 停損檢查點：raw NeuronExecutionProvider 節點放置探針"
    )
    default_model = None
    try:
        from server.config import SENSEVOICE_DIR  # noqa: PLC0415 -- lazy import

        default_model = str(SENSEVOICE_DIR / "model.int8.fixed.onnx")
    except Exception as exc:  # noqa: BLE001 -- 診斷工具：印出即可，不中止
        print(f"無法從 server.config 推導預設模型路徑：{exc}；請顯式提供 --model")

    parser.add_argument(
        "--model",
        default=default_model,
        help="model.int8.fixed.onnx 路徑（10-01 fix_shape.py 產出）",
    )
    parser.add_argument(
        "--no-provider-options",
        action="store_true",
        help="強制走空 options（供人工複測 A2 假設用），跳過第一輪帶 options 的嘗試",
    )
    parser.add_argument(
        "--run-inference",
        action="store_true",
        help="預設關閉；開啟才會真的呼叫一次 session.run（Day-1 只需要 graph partition 結果）",
    )
    parser.add_argument(
        "--no-cpu-fallback",
        action="store_true",
        help=(
            "只用 NeuronExecutionProvider、不掛 CPU fallback。"
            "MediaTek 官方 NeuronEP 指南要求如此（見 build_neuron_providers docstring）"
        ),
    )
    args = parser.parse_args()

    if not args.model:
        print("未提供 --model 且無法推導預設路徑，無法繼續")
        sys.exit(2)

    try:
        import onnxruntime as ort  # noqa: PLC0415 -- lazy import，無 import 期副作用
    except ImportError as exc:
        print(f"無法 import onnxruntime：{exc}")
        sys.exit(2)

    # onnxruntime 的 Python API 只提供 setter、無公開 getter 可讀回目前
    # severity，因此「原本的值」採 ORT 的預設 severity（2 = WARNING）——
    # 這與 10-02 `server/npu_placement.py` 模組 docstring 對呼叫端的義務
    # 一致（開 verbose 之前記下、finally 內還原）。
    original_severity = 2
    ort.set_default_logger_severity(0)

    io_specs: list[dict] = []
    try:
        import onnx  # noqa: PLC0415 -- lazy import

        from edge.npu_spike.inspect_model import describe_graph_io

        onnx_model = onnx.load(args.model)
        io_specs = describe_graph_io(onnx_model.graph)
    except Exception as exc:  # noqa: BLE001 -- 拿不到 spec 就用空 feeds，仍嘗試量 NPU 放置
        print(f"讀取 graph IO 失敗（將以空 feeds 繼續）：{exc}")

    try:
        summary = _probe_once(
            model_path=args.model,
            options=None if not args.no_provider_options else {},
            io_specs=io_specs,
            ort=ort,
            run_inference=args.run_inference,
            capture_fd_output=capture_fd_output,
            parse_ep_placement_log=parse_ep_placement_log,
            summarize_placement=summarize_placement,
        )

        if not summary.get("accelerated") and not args.no_provider_options:
            print("第一輪（帶 provider_options）未偵測到加速，改以空 options 重試一次...")
            retry_summary = _probe_once(
                model_path=args.model,
                options={},
                io_specs=io_specs,
                ort=ort,
                run_inference=args.run_inference,
                capture_fd_output=capture_fd_output,
                parse_ep_placement_log=parse_ep_placement_log,
                summarize_placement=summarize_placement,
                cpu_fallback=not args.no_cpu_fallback,
            )
            # 取兩輪較好者，不無條件覆蓋——見 choose_better_summary docstring。
            summary = choose_better_summary(summary, retry_summary)

        for provider_name, count in summary.get("providers", {}).items():
            print(f"  {provider_name}: {count} ops")
        print(format_placement_line(summary))
        verdict_line = format_probe_verdict(summary)
        print(verdict_line)

        exit_code = 0 if summary.get("accelerated") else 1
    finally:
        ort.set_default_logger_severity(
            original_severity if original_severity is not None else 2
        )

    sys.exit(exit_code)


def _probe_once(
    *,
    model_path: str,
    options: dict[str, str] | None,
    io_specs: list[dict],
    ort,
    run_inference: bool,
    capture_fd_output,
    parse_ep_placement_log,
    summarize_placement,
    cpu_fallback: bool = True,
) -> dict:
    """建立一次 session、擷取放置日誌並摘要——`main()` 兩段式重試流程的單輪實作。"""
    providers = build_neuron_providers(options, cpu_fallback=cpu_fallback)

    with capture_fd_output(2) as buf:
        session = None
        try:
            session = ort.InferenceSession(model_path, providers=providers)
        except Exception as exc:  # noqa: BLE001 -- 印出例外與已擷取輸出，交給呼叫端判定/重試
            print(f"InferenceSession 建立失敗：{exc}")

    if buf.text:
        print(buf.text)

    placement = parse_ep_placement_log(buf.text)
    summary = summarize_placement(placement)

    if session is not None and run_inference:
        try:
            feeds = build_zero_feeds(io_specs)
            output_names = [o.name for o in session.get_outputs()]
            session.run(output_names, feeds)
        except Exception as exc:  # noqa: BLE001 -- Day-1 不需要推論成功，僅記錄
            print(f"session.run 失敗（Day-1 不需要推論成功，僅供參考）：{exc}")

    return summary


if __name__ == "__main__":
    main()
