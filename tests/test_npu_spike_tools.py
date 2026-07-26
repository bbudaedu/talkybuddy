# -*- coding: utf-8 -*-
"""edge/npu_spike/ 診斷工具的純函式單元測試（NPU-01）。

比照 tests/test_measure_peak_rss.py 風格：只測純函式，不觸真 onnxruntime／onnx
（dev 機不保證安裝這兩個套件）；main() 走真 I/O，不在本檔涵蓋範圍——那部分
由 10-01-PLAN.md 的 <verify><human-check> 在 Genio 520 真機上執行。
"""

from __future__ import annotations

import pytest

from edge.npu_spike.fix_shape import build_fix_shape_argv, run_fix_shape
from edge.npu_spike.inspect_model import (
    describe_graph_io,
    format_metadata_map,
    format_provider_report,
    probe_runtime,
)


class _FakeOrt:
    """模擬 onnxruntime 模組介面（__version__ + get_available_providers()）。"""

    def __init__(self, version: str, providers: list[str]):
        self.__version__ = version
        self._providers = providers

    def get_available_providers(self):
        return self._providers


def test_probe_runtime_with_neuron_present():
    ort = _FakeOrt("1.20.2", ["CPUExecutionProvider", "NeuronExecutionProvider"])
    info = probe_runtime(ort)
    assert info == {
        "version": "1.20.2",
        "providers": ["CPUExecutionProvider", "NeuronExecutionProvider"],
        "has_neuron": True,
    }


def test_probe_runtime_without_neuron():
    ort = _FakeOrt("1.20.2", ["CPUExecutionProvider"])
    info = probe_runtime(ort)
    assert info["has_neuron"] is False


def test_probe_runtime_none_input_returns_empty_result():
    info = probe_runtime(None)
    assert info == {"version": None, "providers": [], "has_neuron": False}


def test_format_provider_report_last_line_present_when_neuron_available():
    report = format_provider_report(
        {"version": "1.20.2", "providers": ["NeuronExecutionProvider"], "has_neuron": True}
    )
    assert report.splitlines()[-1] == "NEURON_EP: PRESENT"


def test_format_provider_report_last_line_absent_when_no_neuron():
    report = format_provider_report(
        {"version": "1.20.2", "providers": ["CPUExecutionProvider"], "has_neuron": False}
    )
    assert report.splitlines()[-1] == "NEURON_EP: ABSENT"


class _Dim:
    def __init__(self, dim_param: str = "", dim_value: int = 0):
        self.dim_param = dim_param
        self.dim_value = dim_value


class _Shape:
    def __init__(self, dims):
        self.dim = dims


class _TensorType:
    def __init__(self, dims, elem_type: int = 1):
        self.shape = _Shape(dims)
        self.elem_type = elem_type


class _ValueType:
    def __init__(self, tensor_type):
        self.tensor_type = tensor_type


class _ValueInfo:
    def __init__(self, name: str, dims, elem_type: int = 1):
        self.name = name
        self.type = _ValueType(_TensorType(dims, elem_type))


class _Graph:
    def __init__(self, inputs=None, outputs=None):
        self.input = inputs or []
        self.output = outputs or []


def test_describe_graph_io_marks_dim_param_axis_as_dynamic():
    stub_input = _ValueInfo(
        "speech", [_Dim(dim_value=1), _Dim(dim_param="T"), _Dim(dim_value=80)]
    )
    graph = _Graph(inputs=[stub_input])
    result = describe_graph_io(graph)
    assert len(result) == 1
    assert result[0]["name"] == "speech"
    assert result[0]["dynamic_dims"] == [1]


def test_describe_graph_io_no_input_returns_empty_list():
    graph = _Graph(inputs=[], outputs=[])
    assert describe_graph_io(graph) == []


def test_describe_graph_io_none_graph_returns_empty_list_without_raising():
    assert describe_graph_io(None) == []


def test_format_metadata_map_empty_dict_fixed_string():
    assert format_metadata_map({}) == "(no custom metadata)"


def test_format_metadata_map_sorted_key_value_lines():
    result = format_metadata_map({"blank_id": "0", "lfr_window_size": "7"})
    assert result == "blank_id = 0\nlfr_window_size = 7"


def test_build_fix_shape_argv_dim_param_form():
    argv = build_fix_shape_argv("a.onnx", "b.onnx", dim_param="T", dim_value=200)
    assert argv[argv.index("--dim_param") + 1] == "T"
    assert argv[argv.index("--dim_value") + 1] == "200"
    assert argv[-2:] == ["a.onnx", "b.onnx"]


def test_build_fix_shape_argv_input_shape_form():
    argv = build_fix_shape_argv(
        "a.onnx", "b.onnx", input_name="x", input_shape=[1, 200, 80]
    )
    assert argv[argv.index("--input_name") + 1] == "x"
    assert argv[argv.index("--input_shape") + 1] == "1,200,80"
    assert argv[-2:] == ["a.onnx", "b.onnx"]


def test_build_fix_shape_argv_raises_when_both_forms_given():
    with pytest.raises(ValueError):
        build_fix_shape_argv(
            "a.onnx",
            "b.onnx",
            dim_param="T",
            dim_value=200,
            input_name="x",
            input_shape=[1, 200, 80],
        )


def test_build_fix_shape_argv_raises_when_neither_form_given():
    with pytest.raises(ValueError):
        build_fix_shape_argv("a.onnx", "b.onnx")


def test_run_fix_shape_oserror_returns_minus_one(monkeypatch):
    import edge.npu_spike.fix_shape as fix_shape_module

    def _raise(*args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(fix_shape_module.subprocess, "run", _raise)
    returncode, output = run_fix_shape(["fake", "argv"])
    assert returncode == -1
    assert "boom" in output
