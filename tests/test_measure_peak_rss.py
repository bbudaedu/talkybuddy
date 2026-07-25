# -*- coding: utf-8 -*-
"""edge/runtime/measure_peak_rss.py 單元測試（跨行程 VmHWM 加總工具）。

只用 tmp_path 造假 /proc/<pid>/status 檔驗證解析與加總邏輯，不需要真行程、
不呼叫 main()（main() 走真 pgrep + 真 /proc，屬裝置上人工執行取數範圍，
不在自動化測試涵蓋內——見 08-05-PLAN.md Task 1 acceptance_criteria）。
"""

from __future__ import annotations

from edge.runtime.measure_peak_rss import (
    kb_to_mb,
    read_peak_rss_kb,
    sum_peak_rss,
    within_threshold,
)


def _write_status(proc_root, pid: int, body: str) -> None:
    pid_dir = proc_root / str(pid)
    pid_dir.mkdir(parents=True, exist_ok=True)
    (pid_dir / "status").write_text(body, encoding="utf-8")


def test_read_peak_rss_kb_parses_vmhwm_line(tmp_path):
    _write_status(
        tmp_path,
        1234,
        "Name:\tllama-server\nVmHWM:\t 51200 kB\nVmRSS:\t 40000 kB\n",
    )
    assert read_peak_rss_kb(1234, proc_root=str(tmp_path)) == 51200


def test_read_peak_rss_kb_missing_pid_returns_none(tmp_path):
    assert read_peak_rss_kb(9999, proc_root=str(tmp_path)) is None


def test_read_peak_rss_kb_missing_vmhwm_line_returns_none(tmp_path):
    _write_status(tmp_path, 5555, "Name:\tuvicorn\nVmRSS:\t 12345 kB\n")
    assert read_peak_rss_kb(5555, proc_root=str(tmp_path)) is None


def test_sum_peak_rss_adds_two_processes(tmp_path):
    _write_status(tmp_path, 100, "VmHWM:\t 800000 kB\n")
    _write_status(tmp_path, 200, "VmHWM:\t 500000 kB\n")
    assert sum_peak_rss([100, 200], proc_root=str(tmp_path)) == 1300000


def test_sum_peak_rss_skips_none_values(tmp_path):
    _write_status(tmp_path, 100, "VmHWM:\t 800000 kB\n")
    # pid 200 有意不建 status 檔，模擬行程已消失/查不到。
    assert sum_peak_rss([100, 200], proc_root=str(tmp_path)) == 800000


def test_sum_peak_rss_all_missing_returns_zero(tmp_path):
    assert sum_peak_rss([100, 200], proc_root=str(tmp_path)) == 0


def test_kb_to_mb_conversion():
    assert kb_to_mb(1024) == 1.0
    assert kb_to_mb(0) == 0.0


def test_within_threshold_boundary():
    # 4096 MB = 4194304 kB 門檻；等於門檻視為仍在門檻內（<=）。
    limit_kb = 4096 * 1024
    assert within_threshold(limit_kb, limit_mb=4096) is True
    assert within_threshold(limit_kb + 1, limit_mb=4096) is False
    assert within_threshold(0, limit_mb=4096) is True


def test_within_threshold_default_limit_is_4096mb():
    under = 4000 * 1024
    over = 4200 * 1024
    assert within_threshold(under) is True
    assert within_threshold(over) is False
