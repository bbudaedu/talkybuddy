# -*- coding: utf-8 -*-
"""edge/npu_spike/raw_neuron_session.py 的純函式單元測試（NPU-01/NPU-02 Day-1 探針）。

比照 tests/test_npu_spike_tools.py 與 tests/test_npu_placement.py 風格：只測
`build_neuron_providers`、`build_zero_feeds`、`format_probe_verdict` 三個純函式，
不 import `onnxruntime`／`onnx`（dev 機不保證安裝這兩個套件，see 10-03-PLAN.md
Task 2 acceptance criteria: 本檔在未安裝 onnxruntime/onnx 的環境仍須全綠）。
`numpy` 為既有測試依賴（見 tests/test_asr_backend.py），可直接 import。

`main()` 是唯一觸及真實 I/O 與 session 建立的進入點，不在本檔測試範圍——那部分
由 Genio 520 真機執行，結果貼進 edge/npu_spike/DAY1-EVIDENCE.md（Task 3）。
"""

from __future__ import annotations

import numpy as np
import pytest

from edge.npu_spike.raw_neuron_session import (
    DEFAULT_NEURON_OPTIONS,
    PROBE_VERDICT_PREFIX,
    build_neuron_providers,
    build_zero_feeds,
    choose_better_summary,
    format_probe_verdict,
)


# ---------------------------------------------------------------------------
# choose_better_summary — 兩段式重試不得讓失敗覆蓋成功
#
# 2026-07-27 Genio 520 真機實測踩到的第二個坑：第一輪（帶
# NEURON_FLAG_USE_FP16）其實整圖成功放上 NeuronEP，但當時 parser 少認一種
# 日誌格式而誤判 0/0，於是觸發空-options 重試；空 options 因 MDLA 不支援
# FP32 而編譯失敗，該失敗結果被無條件寫回 summary，把成功抹掉。parser 已
# 修好，但「重試覆蓋」這個缺陷獨立存在——即使第一輪只有部分加速、第二輪
# 全滅，也不該讓第二輪的結果取代第一輪。
# ---------------------------------------------------------------------------


def _summary(accelerated: bool, ops: int = 1, total: int = 1) -> dict:
    return {
        "ops_accelerated": ops if accelerated else 0,
        "ops_total": total,
        "providers": {},
        "accelerated": accelerated,
    }


def test_choose_better_summary_keeps_accelerated_first_round():
    first = _summary(True, ops=1, total=1)
    second = _summary(False, ops=0, total=0)
    assert choose_better_summary(first, second) is first


def test_choose_better_summary_takes_accelerated_second_round():
    first = _summary(False, ops=0, total=0)
    second = _summary(True, ops=3, total=5)
    assert choose_better_summary(first, second) is second


def test_choose_better_summary_prefers_more_accelerated_ops():
    """兩輪都有加速時取加速算子多的那輪。"""
    first = _summary(True, ops=2, total=10)
    second = _summary(True, ops=7, total=10)
    assert choose_better_summary(first, second) is second


def test_choose_better_summary_ties_keep_first():
    """平手保留第一輪——重試是保險，不是預設優先。"""
    first = _summary(True, ops=4, total=4)
    second = _summary(True, ops=4, total=4)
    assert choose_better_summary(first, second) is first


def test_choose_better_summary_both_failed_keeps_first():
    first = _summary(False, ops=0, total=9)
    second = _summary(False, ops=0, total=0)
    assert choose_better_summary(first, second) is first


@pytest.mark.parametrize("junk", [None, {}, "nope", 42, []])
def test_choose_better_summary_survives_garbage(junk):
    """診斷工具不得因輸入異常而拋例外。"""
    good = _summary(True)
    assert choose_better_summary(good, junk) is good
    assert choose_better_summary(junk, good) is good
    result = choose_better_summary(junk, junk)
    assert isinstance(result, dict)
    assert result.get("accelerated") is not True


# ---------------------------------------------------------------------------
# build_neuron_providers
# ---------------------------------------------------------------------------


def test_build_neuron_providers_default_options():
    providers = build_neuron_providers()
    assert len(providers) == 2
    assert isinstance(providers[0], tuple)
    assert providers[0][0] == "NeuronExecutionProvider"
    assert providers[0][1] == DEFAULT_NEURON_OPTIONS
    assert providers[1] == "CPUExecutionProvider"


def test_build_neuron_providers_empty_options_is_first_class_retry_shape():
    providers = build_neuron_providers({})
    assert providers[0][1] == {}


# ---------------------------------------------------------------------------
# build_zero_feeds
# ---------------------------------------------------------------------------


def test_build_zero_feeds_basic_float_spec():
    specs = [
        {"name": "speech", "shape": [1, 200, 80], "dtype": "tensor(float)", "dynamic_dims": []}
    ]
    feeds = build_zero_feeds(specs)
    assert set(feeds.keys()) == {"speech"}
    assert feeds["speech"].shape == (1, 200, 80)
    assert feeds["speech"].dtype == np.float32


def test_build_zero_feeds_dynamic_axis_defaults_to_one():
    specs = [
        {
            "name": "speech",
            "shape": [1, "T", 80],
            "dtype": "tensor(float)",
            "dynamic_dims": [1],
        }
    ]
    feeds = build_zero_feeds(specs)
    assert feeds["speech"].shape == (1, 1, 80)


def test_build_zero_feeds_non_positive_dim_without_dynamic_flag_defaults_to_one():
    specs = [
        {"name": "speech", "shape": [1, 0, 80], "dtype": "tensor(float)", "dynamic_dims": []}
    ]
    feeds = build_zero_feeds(specs)
    assert feeds["speech"].shape == (1, 1, 80)


def test_build_zero_feeds_int64_dtype():
    specs = [{"name": "language", "shape": [1], "dtype": "tensor(int64)", "dynamic_dims": []}]
    feeds = build_zero_feeds(specs)
    assert feeds["language"].dtype == np.int64


# ---------------------------------------------------------------------------
# format_probe_verdict
# ---------------------------------------------------------------------------


def test_format_probe_verdict_pass():
    summary = {"ops_accelerated": 3, "ops_total": 152, "accelerated": True}
    assert (
        format_probe_verdict(summary)
        == "DAY1_NPU_PROBE: PASS 3/152 ops on NeuronExecutionProvider"
    )


def test_format_probe_verdict_fail():
    summary = {"ops_accelerated": 0, "ops_total": 152, "accelerated": False}
    assert (
        format_probe_verdict(summary)
        == "DAY1_NPU_PROBE: FAIL 0/152 ops on NeuronExecutionProvider"
    )


def test_format_probe_verdict_empty_summary_fails_without_raising():
    result = format_probe_verdict({})
    assert result.startswith("DAY1_NPU_PROBE: FAIL")


def test_probe_verdict_prefix_constant_matches_marker():
    assert PROBE_VERDICT_PREFIX == "DAY1_NPU_PROBE:"
