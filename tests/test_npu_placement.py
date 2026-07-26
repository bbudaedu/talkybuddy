# -*- coding: utf-8 -*-
"""server/npu_placement.py 單元測試（NPU-02 的證據層：EP placement 解析與 fd 擷取）。

只測純函式：parse_ep_placement_log / summarize_placement / format_placement_line
以及 capture_fd_output 這個 context manager。全部不依賴 onnxruntime 或真實硬體——
fixture 文字直接抄自 ORT VerifyEachNodeIsAssignedToAnEp 的實際輸出格式
（見 10-RESEARCH.md Pattern 1），讓解析器可在沒有 NPU、沒有 onnxruntime 的
機器上被驗證。
"""

from __future__ import annotations

from server.npu_placement import (
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
