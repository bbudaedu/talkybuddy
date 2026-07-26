# -*- coding: utf-8 -*-
"""server/npu_placement.py — NPU-02 的證據層：EP placement 解析與 fd 級日誌擷取。

本模組是本 phase（10-npu-accelerated-perception）唯一的「證據層」，把
ONNX Runtime 自己在 session 初始化時印出的 verbose 節點放置日誌，變成
可計數、可斷言、可上 HUD 的結構化資料。`available() == True`、session
建得起來、推論跑得完，全部都不構成 NPU 加速的證據；唯一的 ground truth
是 ORT 在啟用 verbose logging（`ort.set_default_logger_severity(0)`）時
印出的 `VerifyEachNodeIsAssignedToAnEp` 節點清單（見
`.planning/research/PITFALLS.md` Pitfall 1、`10-RESEARCH.md` Pattern 1）。

`PLACEMENT_MARKER` 的日誌格式來自社群觀察（`10-RESEARCH.md` Assumptions
Log A4），非 ONNX Runtime 官方文件保證的穩定 API——因此
`parse_ep_placement_log` 採「盡量解析、解析不到就回空 dict／略過殘缺行」
的容錯策略，而非嚴格驗證；格式若在未來 ORT 版本漂移，最多是少解析幾行，
不會拋例外中斷呼叫端。

`summarize_placement` 的 `accelerated` 欄位，是「是否真的有節點被排到
NPU（`accel_provider`，預設 `NeuronExecutionProvider`）」的唯一判準。
呼叫端（10-03 spike、10-05 正式引擎、10-06 `/api/status`）不得改用
「session 是否建立成功」或「推論是否跑完」來推斷 NPU 是否真的加速——
那正是 Pitfall 1「靜默偽成功」的根源。

本模組刻意**不 import onnxruntime**，讓它能在沒有硬體、沒有安裝
onnxruntime 的機器上被 pytest 完整驗證。

`capture_fd_output`：ONNX Runtime 的 verbose 日誌由其 C++ 層直接寫到
process 的 fd 2（stderr 的檔案描述符本身），而不是透過 Python 的
`sys.stderr` 物件——這代表 Python 標準庫的 `contextlib.redirect_stderr`
攔不到這些輸出（它只是替換 `sys.stderr` 這個物件參照，C 層根本不經過
它）。`capture_fd_output` 用 `os.dup2` 在 fd 層級把輸出導向暫存檔，
是唯一能可靠擷取這份 ground truth 的方式。呼叫端有義務在使用完
`ort.set_default_logger_severity(0)` 開啟 verbose 模式後，把 severity
調回原值（正式引擎路徑在 10-05 落實），本模組本身不做這件事。
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from dataclasses import dataclass

PLACEMENT_MARKER = "VerifyEachNodeIsAssignedToAnEp"

# T-10-04（DoS，medium，mitigate）：verbose 日誌對大 graph 可能極長，
# 無上限的擷取緩衝等於把 session 初始化變成記憶體風險；超過此上限即截斷
# 並設 truncated 旗標，而非無限成長。
MAX_CAPTURE_BYTES = 2_000_000

_PROVIDER_LINE_RE = re.compile(r"Provider:\s*\[(\w+)\]:\s*(.*)")
_NODE_PAIR_RE = re.compile(r"\w+\s*\(([^()]+)\)")


def parse_ep_placement_log(log_text: str) -> dict[str, list[str]]:
    """把 ORT 的 VerifyEachNodeIsAssignedToAnEp verbose 日誌解析成結構化資料。

    回傳 {provider_name: [node_name, ...]}。日誌格式屬社群觀察、非官方
    API 保證（見模組 docstring），因此本函式：
    - 若整段文字完全不含 PLACEMENT_MARKER，回空 dict（不嘗試硬解析）。
    - 逐行掃描符合 `Provider: [<name>]: [...]` 的行；方括號內容跨行
      折斷時，累積後續行直到方括號閉合、或遇到下一個 marker 行、空白行、
      或不含逗號與括號的行為止。
    - 結構殘缺（例如缺冒號、缺方括號）的行直接略過，不猜測、不拋例外。
    - 任何非預期例外，回傳目前已解析出的部分結果（部分結果優於整批失敗，
      比照 edge/runtime/dump_recent_turns.py 慣例）。
    """
    result: dict[str, list[str]] = {}
    try:
        if not log_text or PLACEMENT_MARKER not in log_text:
            return result

        lines = log_text.splitlines()
        n = len(lines)
        i = 0
        while i < n:
            line = lines[i]
            match = _PROVIDER_LINE_RE.search(line)
            if not match:
                i += 1
                continue

            provider = match.group(1)
            content = match.group(2)
            open_count = content.count("[")
            close_count = content.count("]")

            j = i + 1
            while open_count > close_count and j < n:
                next_line = lines[j]
                stripped = next_line.strip()
                if not stripped:
                    break
                if PLACEMENT_MARKER in next_line:
                    break
                if "," not in next_line and "(" not in next_line:
                    break
                content += " " + next_line
                open_count += next_line.count("[")
                close_count += next_line.count("]")
                j += 1

            nodes = [node.strip() for node in _NODE_PAIR_RE.findall(content)]
            if nodes:
                result.setdefault(provider, []).extend(nodes)

            i = j if j > i + 1 else i + 1

        return result
    except Exception:
        return result


def summarize_placement(
    placement: dict[str, list[str]],
    accel_provider: str = "NeuronExecutionProvider",
) -> dict:
    """把 parse_ep_placement_log 的輸出摘要成「幾個算子加速了」的計數。

    `accel_provider` 預設 `NeuronExecutionProvider`，保留參數化以便未來
    換 provider 名稱。`accelerated` 僅由 `accel_provider` 的節點數 > 0
    決定，與 session 是否建立成功完全解耦——這是 NPU-02「不得靜默偽
    成功」的機器可讀形式：有 session、有輸出、但 accelerated 仍可能是
    False（例如只有 CPUExecutionProvider）。
    """
    providers = {name: len(nodes) for name, nodes in placement.items()}
    ops_total = sum(providers.values())
    ops_accelerated = providers.get(accel_provider, 0)
    return {
        "ops_accelerated": ops_accelerated,
        "ops_total": ops_total,
        "providers": providers,
        "accelerated": ops_accelerated > 0,
    }


def format_placement_line(summary: dict) -> str:
    """把 summarize_placement 的輸出格式化成固定樣板的一行結論，供 HUD 顯示。

    格式：`"NPU: ON, X/Y ops accelerated"` 或 `"NPU: OFF, X/Y ops accelerated"`。
    """
    prefix = "NPU: ON" if summary.get("accelerated") else "NPU: OFF"
    ops_accelerated = summary.get("ops_accelerated", 0)
    ops_total = summary.get("ops_total", 0)
    return f"{prefix}, {ops_accelerated}/{ops_total} ops accelerated"


@dataclass
class CapturedOutput:
    """capture_fd_output 的擷取結果。text 為擷取到的文字，truncated 標示是否
    超過 MAX_CAPTURE_BYTES 而被截斷。"""

    text: str = ""
    truncated: bool = False


@contextlib.contextmanager
def capture_fd_output(fd: int = 2):
    """以 os.dup2 在 fd 層級攔截寫入，擷取 ONNX Runtime C++ 層直接寫到
    該 fd（預設 2 = stderr）的 verbose 日誌。

    Python 的 `contextlib.redirect_stderr` 只替換 `sys.stderr` 這個物件
    參照，C 層寫入完全繞過它——這是本函式存在的唯一理由，不是為了通用
    日誌收集。用法：

        with capture_fd_output() as buf:
            ...（觸發會寫到 fd 2 的 C 層輸出，例如建立啟用 verbose
                logging 的 onnxruntime.InferenceSession）...
        # buf.text 為擷取到的文字，可直接餵給 parse_ep_placement_log。

    呼叫端有義務在使用完 `ort.set_default_logger_severity(0)` 開啟
    verbose 模式後，把 severity 調回原值（正式引擎路徑在 10-05 落實）；
    本函式本身只負責擷取，不負責管理 ORT 的 logger severity。

    還原順序：無論正常結束或拋出例外，都在 `finally` 內先把 fd 還原
    （`os.dup2` 導回原 fd 並關閉暫存的已保存 fd），再讀出暫存檔內容——
    還原必須排在讀取之前，避免讀檔期間的任何輸出又被吞掉。
    """
    saved_fd = os.dup(fd)
    tmp_file = tempfile.TemporaryFile()
    os.dup2(tmp_file.fileno(), fd)
    result = CapturedOutput()
    try:
        yield result
    finally:
        os.dup2(saved_fd, fd)
        os.close(saved_fd)

        tmp_file.flush()
        tmp_file.seek(0)
        raw = tmp_file.read(MAX_CAPTURE_BYTES + 1)
        tmp_file.close()

        if len(raw) > MAX_CAPTURE_BYTES:
            result.truncated = True
            raw = raw[:MAX_CAPTURE_BYTES]

        result.text = raw.decode("utf-8", errors="replace")
