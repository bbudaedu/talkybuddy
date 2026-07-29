# -*- coding: utf-8 -*-
"""LLM 輸出的簡轉繁：`guardrails.to_traditional()`。

2026-07-29 真機實測，edge LLM（qwen2.5-1.5B）回過「看到**一只**兔子」——簡體用字
直接進字幕。OpenCC s2twp 原本**只套在 ASR 路徑**（`asr_sensevoice.py`），
`llm.py` / `cloud_llm.py` 的輸出沒有經過任何繁化。

發音沒有差別（「只」與「隻」同音），所以 TTS 聽不出來；但**字幕會露簡體字**，
台灣市場的兒童產品被評審看到不好。

降級策略比照 `asr_sensevoice.py`：opencc 缺失或轉換失敗 → 回原文，不 throw。
繁化失敗只是字醜，讓對話中斷才是真的壞掉。
"""

import pytest

from server import guardrails


def test_simplified_characters_become_traditional():
    assert guardrails.to_traditional("看到一只兔子") == "看到一隻兔子"


def test_traditional_text_is_left_alone():
    text = "看到一隻兔子"
    assert guardrails.to_traditional(text) == text


def test_english_is_untouched():
    """帶讀的目標英文句不能被動到。"""
    text = "很棒！跟我說一遍：I see a rabbit."
    assert guardrails.to_traditional(text) == text


def test_mixed_reply_converts_only_the_chinese():
    out = guardrails.to_traditional("很好！看到一只狗。跟我說一遍：I see a dog.")
    assert "一隻狗" in out
    assert "I see a dog." in out


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_input_is_safe(value):
    """空值不得炸——這條路徑在回覆送出前，炸了等於對話中斷。"""
    assert guardrails.to_traditional(value) == (value or "")


def test_falls_back_to_the_original_when_opencc_is_missing(monkeypatch):
    """opencc 不可用時回原文，絕不 throw（比照 asr_sensevoice 的降級）。"""
    import builtins

    real_import = builtins.__import__

    def _no_opencc(name, *a, **kw):
        if name == "opencc":
            raise ImportError("模擬 opencc 缺失")
        return real_import(name, *a, **kw)

    guardrails._converter.cache_clear()
    monkeypatch.setattr(builtins, "__import__", _no_opencc)
    try:
        assert guardrails.to_traditional("看到一只兔子") == "看到一只兔子"
    finally:
        guardrails._converter.cache_clear()
