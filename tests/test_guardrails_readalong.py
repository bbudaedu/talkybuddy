# -*- coding: utf-8 -*-
"""帶讀護欄 `guardrails.ensure_readalong()`：確保回覆恰好含一句合規帶讀。

背景（`edge/PR7_MERGE_VALIDATION_2026-07-29.md` §三）：原本 edge 與雲端各自用
`if target and target not in text` 判斷，實測漏掉兩種情況：

1. `<>` 包裹逃過比對 —— LLM 回「我們來嘗試說一遍：<What animal do you like?>」，
   子字串比對為真所以不補正，但帶讀格式已經跑掉。
2. 中文句號造成重複帶讀 —— LLM 回「…I want to eat an apple。」（中文句號），
   子字串比對為假而再補一次，孩子會聽到同一句被唸兩遍。

兩種都是「用子字串比對代替格式檢查」的後果，故改為正規化後檢查帶讀格式。
"""

import pytest

from server import guardrails


TARGET = "I like apples."


def test_compliant_readalong_is_left_untouched():
    """已是合規格式 → 一字不動（不得畫蛇添足）。"""
    text = f"很棒！跟我說一遍：{TARGET}"
    assert guardrails.ensure_readalong(text, TARGET) == text


def test_missing_target_gets_readalong_appended():
    """完全沒有目標句 → 補上合規帶讀（原有行為，回歸保護）。"""
    out = guardrails.ensure_readalong("你今天好棒！", TARGET)
    assert f"跟我說一遍：{TARGET}" in out


def test_angle_bracket_wrapped_target_is_normalised():
    """漏洞一：`<>` 包裹 —— 要修成合規格式，且目標句只出現一次。"""
    text = "很好！我們來嘗試說一遍：<I like apples.>"
    out = guardrails.ensure_readalong(text, TARGET)

    assert f"跟我說一遍：{TARGET}" in out
    assert out.count("I like apples") == 1
    assert "<" not in out and ">" not in out


def test_chinese_full_stop_does_not_duplicate_readalong():
    """漏洞二：中文句號 —— 已經帶讀過就不得再補一次。"""
    text = "很棒！跟我說一遍：I like apples。"
    out = guardrails.ensure_readalong(text, TARGET)

    assert out.count("I like apples") == 1


def test_trailing_whitespace_variant_does_not_duplicate():
    """空白差異不該被當成不同句子而重複帶讀。"""
    text = "很棒！跟我說一遍： I like  apples."
    out = guardrails.ensure_readalong(text, TARGET)

    assert out.count("I like") == 1


def test_case_difference_does_not_duplicate():
    """大小寫差異同理 —— 比對前正規化。"""
    text = "很棒！跟我說一遍：i like apples."
    out = guardrails.ensure_readalong(text, TARGET)

    assert out.lower().count("i like apples") == 1


@pytest.mark.parametrize("target", [None, "", "   "])
def test_empty_target_returns_text_unchanged(target):
    """沒有目標句可帶讀 → 原樣返回，不得憑空造句。"""
    text = "你今天好棒！"
    assert guardrails.ensure_readalong(text, target) == text


def test_result_always_contains_exactly_one_readalong_marker():
    """不論輸入形態，輸出最多一個「跟我說一遍：」標記。"""
    for text in (
        "很棒！跟我說一遍：I like apples.",
        "很好！我們來嘗試說一遍：<I like apples.>",
        "很棒！跟我說一遍：I like apples。",
        "你今天好棒！",
    ):
        out = guardrails.ensure_readalong(text, TARGET)
        assert out.count("跟我說一遍：") == 1, text


def test_chinese_only_readalong_clause_is_removed():
    """真機實測：LLM 回「跟我說一遍：我看到一隻兔子。」——要孩子跟讀中文。

    2026-07-29 裝置上的實際輸出：
        太棒了！我聽見了！跟我說一遍：我看到一隻兔子。 跟我說一遍：I see a rabbit.

    帶讀的對象**必須是英文句**，所以帶讀標記後面不含任何英文字母時，那一句一定
    是錯的，可以安全刪掉。這條規則很窄，不會誤刪正常內容。
    """
    text = "太棒了！我聽見了！跟我說一遍：我看到一隻兔子。"
    out = guardrails.ensure_readalong(text, "I see a rabbit.")

    assert out.count("跟我說一遍：") == 1
    assert "我看到一隻兔子" not in out
    assert "跟我說一遍：I see a rabbit." in out
    assert "太棒了！我聽見了！" in out, "稱讚語不得被一起刪掉"


def test_chinese_clause_removed_even_when_a_correct_one_exists():
    """LLM 同時吐出中文帶讀與正確英文帶讀 → 只留英文那句。"""
    text = "跟我說一遍：我看到一隻兔子。跟我說一遍：I see a rabbit."
    out = guardrails.ensure_readalong(text, "I see a rabbit.")

    assert out.count("跟我說一遍：") == 1
    assert "我看到一隻兔子" not in out


def test_a_normal_compliant_reply_is_still_untouched():
    """加了新規則之後，正常回覆仍必須一字不動（回歸保護）。"""
    text = "很棒！跟我說一遍：I like apples."
    assert guardrails.ensure_readalong(text, "I like apples.") == text
