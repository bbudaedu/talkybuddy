# -*- coding: utf-8 -*-
"""test_games_spell.py — 遊戲 D「背單字」（Spell Along）。

一個詞三步：念單字 → 逐字母拼 → 例句。判定主力是拼音那一步
（2026-07-31 實測：ASR 對字母序列比對整個單字準得多）。

與另外三個遊戲共用的原則（見 server/games.py）：判定是規則式純函式、
斷網與連網逐字相同、狀態不可變。
"""

from __future__ import annotations

from server import games, scaffold, spelling


# ---------------------------------------------------------------------------
# 選詞：到期優先，跨分類
# ---------------------------------------------------------------------------

def test_state_has_the_two_new_fields_with_safe_defaults():
    """新欄位一律帶預設值，既有三個遊戲不受影響。"""
    st = games.GameState(game="i_spy")
    assert st.retries == 0
    assert st.student_id == ""


def test_due_words_from_falls_back_to_pool_order_without_a_student():
    pool = ["蘋果", "香蕉", "狗"]
    assert games._due_words_from(pool, None, 2) == ("蘋果", "香蕉")


def test_due_words_from_puts_due_words_first(tmp_db):
    """上次拼錯的詞排到最前面——這是間隔重複在教學迴圈裡的出口。"""
    spelling.record_word_result("STU-SPELL", "狗", False)  # 錯 → 立刻到期
    pool = ["蘋果", "香蕉", "狗"]
    assert games._due_words_from(pool, "STU-SPELL", 3)[0] == "狗"


def test_due_words_from_handles_an_empty_pool():
    assert games._due_words_from([], "STU-SPELL", 3) == ()
    assert games._due_words_from(None, None, 3) == ()


def test_due_first_still_behaves_exactly_as_before():
    """重構不得改動既有三個遊戲的取詞行為。"""
    hints = games._due_first("animal", None)
    assert hints == tuple(games._words_in_cat("animal")[:games._MAX_HINTS])
