# -*- coding: utf-8 -*-
"""measure_peak_rss.py — 跨行程 VmHWM 峰值記憶體加總工具（ELOOP-04）。

08-PATTERNS.md Pitfall 4：ELOOP-04 的 4GB 記憶體驗收閘門必須是「uvicorn +
llama-server 兩個獨立行程」的 VmHWM（/proc/<pid>/status 記錄的峰值常駐集，
非目前 RSS）**加總**，只量單一 PID 會系統性低估真機記憶體壓力。

本模組把「解析單一 PID 的 VmHWM」「跨多個 PID 加總」「MB 換算」「4GB 門檻
比較」拆成純函式（無 I/O 副作用、可注入 proc_root 供單元測試指向假
/proc 目錄），main() 才是唯一有真副作用（pgrep 子行程 + 讀真 /proc）的
進入點，供裝置上人工執行取數（08-05-PLAN.md checkpoint）。

子行程一律以固定 argv 串列呼叫 subprocess.run，絕不啟用 shell 模式或用字串
插值組指令（比照 edge/runtime/audio_io.py 既有慣例，避免命令注入）。
"""

from __future__ import annotations

import subprocess
import sys

_DEFAULT_LIMIT_MB = 4096

# 跨行程 VmHWM 加總鎖定的兩個目標行程（ELOOP-04）：uvicorn 應用行程、
# llama-server 推論行程。pgrep -f 走固定 argv，不做 shell 字串插值。
_UVICORN_PGREP_ARGV = ["pgrep", "-f", "uvicorn server.app:app"]
_LLAMA_SERVER_PGREP_ARGV = ["pgrep", "-f", "llama-server"]


def read_peak_rss_kb(pid: int, proc_root: str = "/proc") -> int | None:
    """讀 {proc_root}/{pid}/status 的 VmHWM 行，回傳 kB（int）。

    找不到檔案、找不到 VmHWM 行、或該行格式無法解析，一律回傳 None
    （不拋例外——量測工具本身不該因單一行程消失而整批失敗）。
    """
    status_path = f"{proc_root}/{pid}/status"
    try:
        with open(status_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    # 格式："VmHWM:\t   51200 kB\n" -> split() = ["VmHWM:", "51200", "kB"]
                    parts = line.split()
                    return int(parts[1])
    except (FileNotFoundError, OSError, IndexError, ValueError):
        return None
    return None


def sum_peak_rss(pids: list[int], proc_root: str = "/proc") -> int:
    """對多個 pid 各自取 VmHWM 並加總（kB），略過回 None 的 pid。"""
    total = 0
    for pid in pids:
        peak = read_peak_rss_kb(pid, proc_root=proc_root)
        if peak is not None:
            total += peak
    return total


def kb_to_mb(kb: int) -> float:
    """kB 換算 MB（純計算，無副作用）。"""
    return kb / 1024.0


def within_threshold(total_kb: int, limit_mb: int = _DEFAULT_LIMIT_MB) -> bool:
    """total_kb（換算 MB 後）是否落在 limit_mb 門檻內（<=，含邊界）。"""
    return kb_to_mb(total_kb) <= limit_mb


def _pgrep_first_pid(argv: list[str]) -> int | None:
    """以固定 argv 呼叫 pgrep（絕不啟用 shell 模式 / 字串插值），回傳第一個 PID。

    pgrep 找不到行程時 exit code 為 1（非例外情境）；呼叫失敗或找不到
    可解析的 PID 一律回傳 None，不拋例外。
    """
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    first_line = result.stdout.strip().splitlines()
    if not first_line:
        return None
    try:
        return int(first_line[0])
    except ValueError:
        return None


def main() -> None:
    """裝置上人工執行取數的進入點：找出 uvicorn + llama-server PID，各自印
    VmHWM，加總後與 4096MB 門檻比較，印 PASS/FAIL。"""
    uvicorn_pid = _pgrep_first_pid(_UVICORN_PGREP_ARGV)
    llama_server_pid = _pgrep_first_pid(_LLAMA_SERVER_PGREP_ARGV)

    pids: list[int] = []
    if uvicorn_pid is not None:
        peak = read_peak_rss_kb(uvicorn_pid)
        print(f"uvicorn PID={uvicorn_pid} VmHWM={peak} kB")
        pids.append(uvicorn_pid)
    else:
        print("uvicorn PID: 找不到（pgrep -f 'uvicorn server.app:app' 無結果）")

    if llama_server_pid is not None:
        peak = read_peak_rss_kb(llama_server_pid)
        print(f"llama-server PID={llama_server_pid} VmHWM={peak} kB")
        pids.append(llama_server_pid)
    else:
        print("llama-server PID: 找不到（pgrep -f 'llama-server' 無結果）")

    total_kb = sum_peak_rss(pids)
    total_mb = kb_to_mb(total_kb)
    ok = within_threshold(total_kb, limit_mb=_DEFAULT_LIMIT_MB)
    status = "PASS" if ok else "FAIL"
    print(
        f"跨行程 VmHWM 加總 = {total_kb} kB ({total_mb:.1f} MB) "
        f"vs {_DEFAULT_LIMIT_MB} MB 門檻 -> {status}"
    )
    if not pids:
        print("WARNING: 未取得任何行程 PID，加總為 0，數字不具意義（未實測）", file=sys.stderr)


if __name__ == "__main__":
    main()
