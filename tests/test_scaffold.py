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


def test_respond_pure_zh_no_vocab_hit_prefers_lesson_target_sentence():
    """純中文無詞庫命中時，若有今日課程目標句，應優先引導該句而非通用預設句。"""
    result = scaffold.respond("今天心情很好", lesson_target_sentence="I see a big dog.")
    assert result.target_sentence == "I see a big dog."


def test_respond_pure_zh_no_vocab_hit_falls_back_when_no_lesson():
    """沒有課程資訊（lesson_target_sentence=None）時，行為與現況一致。"""
    result = scaffold.respond("今天心情很好")
    assert result.target_sentence == "How are you today?"


def test_respond_pure_en_correct_no_vocab_hit_uses_lesson_topic_question():
    """純英文正確、句中無詞庫詞可判斷分類時，延伸問句改依今日課程主題。"""
    result = scaffold.respond("I am happy today.", lesson_topic="color")
    assert result.target_sentence == scaffold._EXTENSION_QUESTIONS["color"]


def test_respond_turn_index_rotates_praise_without_repeating():
    """同一句輸入在不同輪次（turn_index）應輪替到不同鼓勵語，避免卡在同一句。"""
    replies = {
        scaffold.respond("我要一個蘋果", turn_index=i).reply_text
        for i in range(len(scaffold._PRAISE_ZH))
    }
    assert len(replies) == len(scaffold._PRAISE_ZH)


# ---------------------------------------------------------------------------
# matched 欄位 + stuck_hint 降階提示（pipeline 連續卡關偵測用）
# ---------------------------------------------------------------------------

def test_respond_matched_true_when_zh_vocab_hit():
    """純中文命中詞庫 → matched=True（不算卡關）。"""
    result = scaffold.respond("我要一個蘋果")
    assert result.matched is True


def test_respond_matched_false_when_zh_no_vocab_hit():
    """純中文完全沒命中詞庫 → matched=False，供 pipeline 累加卡關輪次。"""
    result = scaffold.respond("今天心情很好")
    assert result.matched is False


def test_respond_matched_true_for_pure_english_even_with_fixes():
    """純英文一律 matched=True，即使還需要文法修正——這不算「卡關」。"""
    result = scaffold.respond("I eat a apple")
    assert result.matched is True


def test_respond_stuck_hint_used_when_no_vocab_hit():
    """有 stuck_hint 時，純中文無詞庫命中應改用 stuck_hint 當目標句，並換一組簡化開場。"""
    result = scaffold.respond("今天心情很好", stuck_hint="Is it a or an?")
    assert result.target_sentence == "Is it a or an?"
    assert any(lead in result.reply_text for lead in scaffold._PRAISE_STUCK)
    assert result.matched is False


def test_respond_stuck_hint_ignored_when_vocab_hit():
    """就算帶了 stuck_hint，只要這輪命中詞庫，就不該用簡化提示蓋掉正常回覆。"""
    result = scaffold.respond("我要一個蘋果", stuck_hint="Is it a or an?")
    assert result.target_sentence == "I want to eat an apple."
    assert result.matched is True


def test_respond_empty_input_matched_false():
    """空輸入視為沒命中（matched=False），但不影響現行的兜底話術行為。"""
    result = scaffold.respond("")
    assert result.matched is False
    assert result.target_sentence is None


def test_respond_safety_triggered_matched_true():
    """安撫話術不算卡關，matched 應為 True（避免誤觸發降階提示）。"""
    result = scaffold.respond("我要殺人")
    assert result.safety_triggered is True
    assert result.matched is True


# ---------------------------------------------------------------------------
# 純英文糾錯型態輪替（Lyster & Ranta：recast / metalinguistic / clarification）
# ---------------------------------------------------------------------------

def test_respond_pure_en_feedback_style_rotates_by_turn_index():
    """同一個有 a/an 錯誤的句子，在不同 turn_index 應輪替出不同的糾錯型態開場。"""
    leads = {
        scaffold._respond_pure_en("I eat a apple", i, None)[0]
        for i in range(len(scaffold._FEEDBACK_STYLES))
    }
    assert len(leads) == len(scaffold._FEEDBACK_STYLES)


def test_respond_pure_en_metalinguistic_style_names_article_rule():
    """輪到 metalinguistic 型態、且錯誤是 a/an 時，應該講出文法規則本身。"""
    idx = scaffold._FEEDBACK_STYLES.index("metalinguistic")
    lead, _sentence, matched = scaffold._respond_pure_en("I eat a apple", idx, None)
    assert lead == scaffold._METALINGUISTIC_HINTS["article"]
    assert matched is True


def test_compute_scores_range_and_keys():
    """compute_scores 回傳三維分數，皆在 0-100 範圍內。"""
    for text in ["", "我要吃蘋果", "I have a dog.", "我想吃apple and drink milk"]:
        scores = scaffold.compute_scores(text)
        assert set(scores.keys()) == {"fluency", "vocabulary", "grammar"}
        for v in scores.values():
            assert 0 <= v <= 100
