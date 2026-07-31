# -*- coding: utf-8 -*-
"""OpenCCProcessor 測試。

轉換器可注入，所以不需要真的裝 opencc 就能測邏輯；另有一個真實 opencc 的
測試，裝了才跑（板子與開發機都有裝，CI 沒有也不會紅）。
"""

from __future__ import annotations

import pytest
from pipecat.frames.frames import TextFrame, TTSTextFrame
from pipecat.utils.text.base_text_aggregator import AggregationType
from pipecat.processors.frame_processor import FrameDirection
from pipecat.tests.utils import run_test

from edge.runtime.pipecat_adapters.opencc_processor import OpenCCProcessor

# 不要用 pytest.importorskip：它在模組載入時就拋 Skipped，會把**整個檔案**跳過，
# 連不需要 opencc 的測試也一起消失（看起來像全部通過，其實一個都沒跑）。
try:
    import opencc as _opencc
except Exception:
    _opencc = None


class _FakeCC:
    """假轉換器：只做一組固定替換，足以驗證接線。"""

    def __init__(self, mapping: dict[str, str] | None = None, boom: bool = False):
        self._mapping = mapping or {"苹果": "蘋果", "说": "說"}
        self._boom = boom

    def convert(self, text: str) -> str:
        if self._boom:
            raise RuntimeError("opencc 壞了")
        for k, v in self._mapping.items():
            text = text.replace(k, v)
        return text


def _proc(cc=None, load_failed: bool = False) -> OpenCCProcessor:
    p = OpenCCProcessor()
    p._converter = cc
    p._load_failed = load_failed
    return p


def test_converts_simplified_to_traditional():
    """基本轉換要生效。"""
    assert _proc(_FakeCC()).convert("苹果很好吃") == "蘋果很好吃"


def test_empty_text_is_passed_through():
    """空字串不該進轉換器。"""
    assert _proc(_FakeCC()).convert("") == ""


def test_missing_opencc_degrades_to_original():
    """OpenCC 沒裝就出原文——簡體逐字稿不理想，但比整場對話掛掉好。"""
    assert _proc(cc=None, load_failed=True).convert("苹果") == "苹果"


def test_conversion_failure_degrades_to_original():
    """轉換爆炸也要回原文，不得拋出去炸穿 pipeline。"""
    assert _proc(_FakeCC(boom=True)).convert("苹果") == "苹果"


@pytest.mark.asyncio
async def test_tts_text_frame_is_converted_in_pipeline():
    """真實 pipeline 驅動下，TTSTextFrame 的內容要被轉成繁體。"""
    p = _proc(_FakeCC())
    down, _ = await run_test(
        p,
        frames_to_send=[TTSTextFrame("苹果", aggregated_by=AggregationType.SENTENCE)],
        expected_down_frames=None,
    )
    texts = [f.text for f in down if isinstance(f, TTSTextFrame)]
    assert texts == ["蘋果"]


@pytest.mark.asyncio
async def test_other_frames_pass_through_untouched():
    """只碰 TTSTextFrame。

    `LLMTextFrame`／`TextFrame` 是串流 token 片段，逐片段轉會破壞 s2twp 的
    詞彙轉換（如「軟件」→「軟體」需要完整詞），所以刻意不碰。
    """
    p = _proc(_FakeCC())
    down, _ = await run_test(
        p,
        frames_to_send=[TextFrame("苹果")],
        expected_down_frames=None,
    )
    texts = [f.text for f in down if isinstance(f, TextFrame) and not isinstance(f, TTSTextFrame)]
    assert texts == ["苹果"], "TextFrame 不該被轉換"


@pytest.mark.asyncio
async def test_missing_opencc_does_not_break_pipeline():
    """OpenCC 不可用時 pipeline 仍要正常流動。"""
    p = _proc(cc=None, load_failed=True)
    down, _ = await run_test(
        p, frames_to_send=[TTSTextFrame("苹果", aggregated_by=AggregationType.SENTENCE)], expected_down_frames=None
    )
    assert [f.text for f in down if isinstance(f, TTSTextFrame)] == ["苹果"]


@pytest.mark.skipif(_opencc is None, reason="未安裝 opencc")
def test_real_opencc_uses_taiwan_wording():
    """真實 opencc：s2twp 不只轉字，還要轉台灣用詞。"""
    p = OpenCCProcessor()
    if p._ensure_converter() is None:
        pytest.skip("opencc 不可用")
    assert p.convert("苹果") == "蘋果"
    # s2twp 的重點是台灣用詞：軟件→軟體、質量→品質
    assert p.convert("软件") == "軟體"
