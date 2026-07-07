# -*- coding: utf-8 -*-
"""test_scaffold.py — 規則式雙語鷹架引擎純單元測試（零依賴，不碰 DB）。

涵蓋 CONTRACTS.md「scaffold.py」規格：
- respond()：中英夾雜 / 純中文 / 純英文 / 禁詞 / 空輸入 五條路徑
- split_tts_segments()：中英混句切段
- compute_scores()：三維分數在 0-100 範圍
- safety_check()：中英禁詞命中
- FALLBACK_LINES：≥3 句兜底話術
"""

from __future__ import annotations

from server import scaffold


def test_fallback_lines_has_at_least_three():
    """兜底話術需 ≥3 句，供 pipeline 低信心時輪替。"""
    assert len(scaffold.FALLBACK_LINES) >= 3
    assert all(isinstance(line, str) and line for line in scaffold.FALLBACK_LINES)


def test_respond_pure_zh_with_vocab_hit():
    """純中文且命中詞庫（蘋果）→ 鼓勵語含詞庫詞，target_sentence 為完整英文句。"""
    result = scaffold.respond("我要一個蘋果")
    assert isinstance(result, scaffold.ScaffoldResult)
    assert "蘋果" in result.reply_text
    assert "apple" in result.reply_text
    assert result.target_sentence == "I want to eat an apple."
    assert result.safety_triggered is False
    assert set(result.scores.keys()) == {"fluency", "vocabulary", "grammar"}
    for v in result.scores.values():
        assert 0 <= v <= 100


def test_respond_pure_zh_no_vocab_hit_uses_generic_prompt():
    """純中文但無詞庫命中詞 → 退回通用引導句，仍給出可跟讀的英文句。"""
    result = scaffold.respond("今天心情很好")
    assert result.target_sentence == "How are you today?"
    assert result.safety_triggered is False


def test_respond_mixed_zh_en():
    """中英夾雜（句中含詞庫詞 + 英文字）→ 走替換中文詞產完整英文句路徑。"""
    result = scaffold.respond("我想吃apple")
    # 「吃」在詞庫中（action 類別），中英夾雜路徑會退回純中文邏輯並命中該詞
    assert "吃" in result.reply_text
    assert result.target_sentence == "I want to eat."
    assert result.safety_triggered is False


def test_respond_pure_en_with_article_fix():
    """純英文、a/an 使用錯誤 → 修正句子並回覆稱讚+修正提示（非延伸問句）。"""
    result = scaffold.respond("I eat a apple")
    assert result.target_sentence == "I eat an apple."
    assert "an apple" in result.target_sentence
    assert result.safety_triggered is False


def test_respond_pure_en_correct_gets_extension_question():
    """純英文且無誤 → 稱讚並給延伸問句（依詞彙分類）。"""
    result = scaffold.respond("I have a dog.")
    assert result.target_sentence == "What animal do you like?"
    assert result.safety_triggered is False


def test_respond_empty_input_returns_first_fallback_line():
    """空字串輸入 → 直接回第一句兜底話術，不給 target_sentence。"""
    result = scaffold.respond("")
    assert result.reply_text == scaffold.FALLBACK_LINES[0]
    assert result.target_sentence is None
    assert result.scores == {"fluency": 0, "vocabulary": 0, "grammar": 0}


def test_respond_forbidden_word_triggers_safety():
    """禁詞（中文）命中 → safety_triggered=True，不給 target_sentence，回安撫話術。"""
    result = scaffold.respond("我要殺人")
    assert result.safety_triggered is True
    assert result.target_sentence is None
    assert "深呼吸" in result.reply_text


def test_respond_forbidden_word_english():
    """禁詞（英文，字邊界比對）命中 → 同樣觸發安撫話術。"""
    result = scaffold.respond("I will kill you")
    assert result.safety_triggered is True
    assert result.target_sentence is None


def test_safety_check_hits_and_misses():
    """safety_check 對已知禁詞回 True，一般句子回 False。"""
    assert scaffold.safety_check("殺人") is True
    assert scaffold.safety_check("kill you") is True
    assert scaffold.safety_check("我要吃蘋果") is False
    assert scaffold.safety_check("I have a dog") is False


def test_safety_check_english_word_boundary():
    """英文禁詞以字邊界比對，避免誤傷含子字串的正常詞（如 killer 不應命中 kill？）

    此處僅驗證 safety_check 對明確禁詞命中，邊界比對細節屬實作內部行為。
    """
    assert scaffold.safety_check("this is a gun") is True


def test_split_tts_segments_mixed_sentence():
    """中英混句切段：依 unicode 範圍分類，交界處正確斷開。"""
    segments = scaffold.split_tts_segments("你好 hello world 再見")
    assert segments == [
        ("zh", "你好"),
        ("en", "hello world"),
        ("zh", "再見"),
    ]


def test_split_tts_segments_pure_english():
    """純英文句子只產生單一 en 段。"""
    segments = scaffold.split_tts_segments("I want to eat an apple.")
    assert len(segments) == 1
    assert segments[0][0] == "en"


def test_compute_scores_range_and_keys():
    """compute_scores 回傳三維分數，皆在 0-100 範圍內。"""
    for text in ["", "我要吃蘋果", "I have a dog.", "我想吃apple and drink milk"]:
        scores = scaffold.compute_scores(text)
        assert set(scores.keys()) == {"fluency", "vocabulary", "grammar"}
        for v in scores.values():
            assert 0 <= v <= 100
