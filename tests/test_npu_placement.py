# -*- coding: utf-8 -*-
"""server/npu_placement.py 單元測試（NPU-02 的證據層：EP placement 解析與 fd 擷取）。

只測純函式：parse_ep_placement_log / summarize_placement / format_placement_line
以及 capture_fd_output 這個 context manager。全部不依賴 onnxruntime 或真實硬體——
fixture 文字直接抄自 ORT VerifyEachNodeIsAssignedToAnEp 的實際輸出格式
（見 10-RESEARCH.md Pattern 1），讓解析器可在沒有 NPU、沒有 onnxruntime 的
機器上被驗證。
"""

from __future__ import annotations

import os

import pytest

from server.npu_placement import (
    MAX_CAPTURE_BYTES,
    capture_fd_output,
    format_placement_line,
    parse_ep_placement_log,
    summarize_placement,
)

# Test 1: 正常路徑 — 兩個 provider，各自單行完整輸出。
FIXTURE_TWO_PROVIDERS = """\
[V:onnxruntime:, session_state.cc:1601 VerifyEachNodeIsAssignedToAnEp]
Provider: [NeuronExecutionProvider]: [Conv (Conv_12), MatMul (MatMul_45)]
[V:onnxruntime:, session_state.cc:1601 VerifyEachNodeIsAssignedToAnEp]
Provider: [CPUExecutionProvider]: [Gather (Gather_3)]
"""

# Test 2: 跨行 — provider 節點清單被 ORT 換行折斷。
FIXTURE_CONTINUATION = """\
[V:onnxruntime:, session_state.cc:1601 VerifyEachNodeIsAssignedToAnEp]
Provider: [NeuronExecutionProvider]: [Conv (Conv_12),
MatMul (MatMul_45), Add (Add_7)]
"""

# Test 3: 無 marker — 完全不含 VerifyEachNodeIsAssignedToAnEp 的一般文字。
FIXTURE_NO_MARKER = """\
Some unrelated server log line
Another line without any EP placement info at all
"""

# Test 4: 格式漂移 — marker 存在，但其中一個 provider 行的方括號結構殘缺。
FIXTURE_FORMAT_DRIFT = """\
[V:onnxruntime:, session_state.cc:1601 VerifyEachNodeIsAssignedToAnEp]
Provider: [NeuronExecutionProvider] malformed line missing the colon and brackets
[V:onnxruntime:, session_state.cc:1602 VerifyEachNodeIsAssignedToAnEp]
Provider: [CPUExecutionProvider]: [Gather (Gather_3)]
"""


def test_parse_ep_placement_log_normal_path():
    result = parse_ep_placement_log(FIXTURE_TWO_PROVIDERS)
    assert result == {
        "NeuronExecutionProvider": ["Conv_12", "MatMul_45"],
        "CPUExecutionProvider": ["Gather_3"],
    }


def test_parse_ep_placement_log_continuation_lines():
    result = parse_ep_placement_log(FIXTURE_CONTINUATION)
    assert result == {
        "NeuronExecutionProvider": ["Conv_12", "MatMul_45", "Add_7"],
    }


def test_parse_ep_placement_log_no_marker_returns_empty_dict():
    result = parse_ep_placement_log(FIXTURE_NO_MARKER)
    assert result == {}
    assert parse_ep_placement_log("") == {}


def test_parse_ep_placement_log_format_drift_skips_malformed_line():
    result = parse_ep_placement_log(FIXTURE_FORMAT_DRIFT)
    # 殘缺行被略過，不得出現在結果中；其餘正常行仍正確解析。
    assert "NeuronExecutionProvider" not in result
    assert result == {"CPUExecutionProvider": ["Gather_3"]}


# --------------------------------------------------------------------------
# Genio 520 真機實測格式（2026-07-27）：ORT 在「整張圖都落在同一個 EP」時
# 走的是彙總行，不逐節點列出。原本的 parser 只認 `Provider: [X]: [...]`
# 逐節點格式，於是把一次成功的 NPU 放置誤判成 0/0 FAIL，並觸發不必要的
# 空-options 重試——那次重試因 MDLA 不支援 FP32 而失敗，反過來覆蓋掉第一
# 輪的成功。整段誤判的起點就是這裡少解析一種格式。
# --------------------------------------------------------------------------

_REAL_DEVICE_ALL_NODES_LOG = (
    "2026-07-27 02:37:28.428231561 [V:onnxruntime:, session_state.cc:1148 "
    "VerifyEachNodeIsAssignedToAnEp] Node placements\n"
    "2026-07-27 02:37:28.428236561 [V:onnxruntime:, session_state.cc:1151 "
    "VerifyEachNodeIsAssignedToAnEp]  All nodes placed on "
    "[NeuronExecutionProvider]. Number of nodes: 1\n"
)


def test_parse_ep_placement_log_all_nodes_summary_form():
    """真機彙總行必須被解析成該 provider 的節點，否則成功會被誤判成 FAIL。"""
    placement = parse_ep_placement_log(_REAL_DEVICE_ALL_NODES_LOG)
    assert "NeuronExecutionProvider" in placement
    assert len(placement["NeuronExecutionProvider"]) == 1


def test_summarize_placement_accelerated_from_all_nodes_summary_form():
    """端到端：真機彙總行 → accelerated 為 True、ops 計數正確。"""
    summary = summarize_placement(parse_ep_placement_log(_REAL_DEVICE_ALL_NODES_LOG))
    assert summary["accelerated"] is True
    assert summary["ops_accelerated"] == 1
    assert summary["ops_total"] == 1
    assert format_placement_line(summary) == "NPU: ON, 1/1 ops accelerated"


def test_parse_ep_placement_log_all_nodes_summary_multi_node_count():
    """節點數必須照實反映，不能永遠回 1。"""
    log = (
        "[V:onnxruntime:, session_state.cc:1151 VerifyEachNodeIsAssignedToAnEp]"
        "  All nodes placed on [NeuronExecutionProvider]. Number of nodes: 337\n"
    )
    assert len(parse_ep_placement_log(log)["NeuronExecutionProvider"]) == 337


def test_parse_ep_placement_log_all_nodes_summary_cpu_is_not_accelerated():
    """同一格式落在 CPU 上時，accelerated 必須是 False——格式支援不得放寬判準。"""
    log = (
        "[V:onnxruntime:, session_state.cc:1151 VerifyEachNodeIsAssignedToAnEp]"
        "  All nodes placed on [CPUExecutionProvider]. Number of nodes: 12\n"
    )
    summary = summarize_placement(parse_ep_placement_log(log))
    assert summary["accelerated"] is False
    assert summary["ops_accelerated"] == 0
    assert summary["ops_total"] == 12


def test_parse_ep_placement_log_both_forms_in_one_log():
    """逐節點格式與彙總格式並存時，兩者都要算進去。"""
    log = FIXTURE_TWO_PROVIDERS + _REAL_DEVICE_ALL_NODES_LOG
    placement = parse_ep_placement_log(log)
    assert len(placement.get("NeuronExecutionProvider", [])) >= 1


def test_summarize_placement_normal_path():
    placement = parse_ep_placement_log(FIXTURE_TWO_PROVIDERS)
    summary = summarize_placement(placement)
    assert summary["ops_accelerated"] == 2
    assert summary["ops_total"] == 3
    assert summary["accelerated"] is True
    assert summary["providers"] == {
        "NeuronExecutionProvider": 2,
        "CPUExecutionProvider": 1,
    }


def test_summarize_placement_empty_input_no_zero_division():
    summary = summarize_placement({})
    assert summary["ops_accelerated"] == 0
    assert summary["ops_total"] == 0
    assert summary["accelerated"] is False


def test_summarize_placement_cpu_only_is_not_accelerated():
    summary = summarize_placement({"CPUExecutionProvider": ["a", "b"]})
    assert summary["accelerated"] is False


def test_format_placement_line_on_and_off():
    on_summary = summarize_placement(parse_ep_placement_log(FIXTURE_TWO_PROVIDERS))
    off_summary = summarize_placement({})
    assert format_placement_line(on_summary) == "NPU: ON, 2/3 ops accelerated"
    assert format_placement_line(off_summary) == "NPU: OFF, 0/0 ops accelerated"


# --- Task 2: capture_fd_output — 攔截 ONNX Runtime C++ 層寫到 fd 2 的日誌。
# Python 的 contextlib.redirect_stderr 只換掉 sys.stderr 物件，攔不到 C 層
# 直接寫 fd 的輸出，這正是必須自己做 fd 級擷取的唯一理由。


def test_capture_fd_output_captures_raw_fd_write():
    with capture_fd_output(fd=2) as buf:
        os.write(2, b"hello-from-c-layer\n")
    assert "hello-from-c-layer" in buf.text


def test_capture_fd_output_restores_fd_after_block():
    with capture_fd_output(fd=2) as buf:
        os.write(2, b"inside-block\n")
    text_after_block = buf.text
    # fd 2 已還原；此後寫入不應再進入 buf。
    os.write(2, b"after-block-should-not-be-captured\n")
    assert buf.text == text_after_block
    assert "after-block-should-not-be-captured" not in buf.text


def test_capture_fd_output_restores_fd_even_on_exception():
    with pytest.raises(RuntimeError):
        with capture_fd_output(fd=2):
            raise RuntimeError("boom")
    # fd 2 必須已還原，此後寫入不應拋例外（還原發生在 finally）。
    os.write(2, b"fd-still-usable-after-exception\n")


def test_capture_fd_output_truncates_over_limit():
    with capture_fd_output(fd=2) as buf:
        os.write(2, b"x" * (MAX_CAPTURE_BYTES + 100))
    assert buf.truncated is True
    assert len(buf.text) <= MAX_CAPTURE_BYTES


def test_capture_fd_output_no_truncation_under_limit():
    with capture_fd_output(fd=2) as buf:
        os.write(2, b"short-write\n")
    assert buf.truncated is False


def test_capture_fd_output_integrates_with_parser():
    with capture_fd_output(fd=2) as buf:
        os.write(2, FIXTURE_TWO_PROVIDERS.encode("utf-8"))
    result = parse_ep_placement_log(buf.text)
    assert result == {
        "NeuronExecutionProvider": ["Conv_12", "MatMul_45"],
        "CPUExecutionProvider": ["Gather_3"],
    }
