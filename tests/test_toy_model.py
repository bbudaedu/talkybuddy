# -*- coding: utf-8 -*-
"""edge/npu_spike/make_toy_model.py 的純函式單元測試（Phase 10 重開後的二分診斷）。

比照 tests/test_raw_neuron_session.py 風格：只測不需要 `onnx` 的純函式
（`build_toy_spec`、`format_toy_summary`），dev 機不保證安裝 `onnx`／
`onnxruntime`，本檔在兩者皆缺的環境仍須全綠。

**這支工具存在的理由**：2026-07-26 的 Day-1 停損只證明「SenseVoice
`model.int8.fixed.onnx` 在 NeuronEP 上 session 初始化失敗」，沒有區分
「模型算子不支援」與「這台機器的 NeuronEP 根本建不起 session」。toy model
是那一刀二分診斷——若一個 5-node FP32 graph 都拿不到 placement，問題在環境；
若拿得到，問題就在 SenseVoice 的 `MatMulInteger`/`DynamicQuantizeLinear`
（281 個各）這組動態量化算子上。
"""

from __future__ import annotations

import pytest

from edge.npu_spike.make_toy_model import (
    TOY_IR_VERSION,
    TOY_SUMMARY_PREFIX,
    TOY_VARIANTS,
    build_toy_spec,
    format_toy_summary,
)


# ---------------------------------------------------------------------------
# build_toy_spec
# ---------------------------------------------------------------------------


def test_toy_variants_contains_conv_and_matmul():
    assert "conv" in TOY_VARIANTS
    assert "matmul" in TOY_VARIANTS


def test_build_toy_spec_defaults_to_conv():
    spec = build_toy_spec()
    assert spec["variant"] == "conv"


@pytest.mark.parametrize("variant", ["conv", "matmul"])
def test_build_toy_spec_shapes_are_fully_static(variant):
    """NPU 不支援動態 shape——toy model 若帶符號軸就失去診斷價值。"""
    spec = build_toy_spec(variant)
    for tensor in (spec["input"], spec["output"]):
        assert tensor["shape"], "shape 不可為空"
        for dim in tensor["shape"]:
            assert isinstance(dim, int), f"{tensor['name']} 有非整數維度 {dim!r}"
            assert dim > 0, f"{tensor['name']} 有非正維度 {dim}"


@pytest.mark.parametrize("variant", ["conv", "matmul"])
def test_build_toy_spec_is_fp32_only(variant):
    """toy model 必須全 FP32：混進量化算子就無法把量化因素隔離掉。"""
    spec = build_toy_spec(variant)
    assert spec["input"]["dtype"] == "float32"
    assert spec["output"]["dtype"] == "float32"
    for op_type in spec["op_types"]:
        assert "Quantize" not in op_type
        assert "Integer" not in op_type


def test_build_toy_spec_conv_uses_conv_and_relu():
    spec = build_toy_spec("conv")
    assert spec["op_types"] == ["Conv", "Relu"]
    assert len(spec["initializers"]) == 2  # W + B


def test_build_toy_spec_matmul_uses_matmul_and_add():
    spec = build_toy_spec("matmul")
    assert spec["op_types"] == ["MatMul", "Add"]
    assert len(spec["initializers"]) == 2  # W + B


def test_build_toy_spec_node_count_stays_tiny():
    """graph 越小，失敗時越沒有『大概是哪個算子』的模糊空間。"""
    for variant in TOY_VARIANTS:
        spec = build_toy_spec(variant)
        assert len(spec["op_types"]) <= 3, f"{variant} 的 toy graph 過大"


@pytest.mark.parametrize("variant", ["conv", "matmul"])
def test_build_toy_spec_pins_ir_version_to_device_limit(variant):
    """真機 ORT 1.20.2 的 IR version 上限是 10；onnx 1.22 預設產出 13 會直接載入失敗。

    2026-07-27 首次真機執行就是踩到這個坑：`Unsupported model IR version: 13,
    max supported IR version: 10`——診斷根本沒跑到 NPU 就死了。釘在 7 是為了
    與 SenseVoice 模型（實測 ir_version=7）完全一致，讓 toy 與正式模型之間
    不存在 IR version 這個額外變因。
    """
    assert TOY_IR_VERSION == 7
    spec = build_toy_spec(variant)
    assert spec["ir_version"] == TOY_IR_VERSION
    assert spec["ir_version"] <= 10


def test_build_toy_spec_rejects_unknown_variant():
    """參數組裝錯誤要早炸，比照 fix_shape.build_fix_shape_argv 的慣例。"""
    with pytest.raises(ValueError):
        build_toy_spec("no-such-variant")


# ---------------------------------------------------------------------------
# format_toy_summary
# ---------------------------------------------------------------------------


def test_format_toy_summary_last_line_is_grep_marker():
    spec = build_toy_spec("conv")
    lines = format_toy_summary(spec).splitlines()
    assert lines[-1].startswith(TOY_SUMMARY_PREFIX)
    assert "conv" in lines[-1]
    assert "Conv" in lines[-1]


def test_format_toy_summary_survives_garbage_input():
    """診斷工具絕不因輸入異常而拋例外——比照 format_probe_verdict 的契約。"""
    for junk in (None, {}, [], "nope", 42):
        text = format_toy_summary(junk)
        assert isinstance(text, str)
        assert text.splitlines()[-1].startswith(TOY_SUMMARY_PREFIX)
