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


# ---------------------------------------------------------------------------
# 開局
# ---------------------------------------------------------------------------

def test_start_picks_three_words_and_stands_on_the_first_step():
    st = games.start_spell_along()
    assert st.game == "spell_along"
    assert len(st.hints) == games.SPELL_TARGET_COUNT
    assert st.secret == st.hints[0]
    assert st.step == "say_word"
    assert st.retries == 0
    assert st.found == ()
    assert not st.done


def test_start_without_a_topic_draws_from_the_whole_vocabulary():
    """背單字要跨分類練到期詞，不綁單一場景。"""
    st = games.start_spell_along(target_count=5)
    assert all(w in scaffold.VOCAB for w in st.hints)
    assert st.topic == ""


def test_start_with_a_topic_stays_inside_that_category():
    st = games.start_spell_along(topic="animal")
    assert st.topic == "animal"
    assert all(scaffold.VOCAB[w]["cat"] == "animal" for w in st.hints)


def test_start_remembers_the_student_so_judging_can_record():
    st = games.start_spell_along(student_id="STU-9")
    assert st.student_id == "STU-9"


def test_start_survives_a_nonsense_target_count():
    assert games.start_spell_along(target_count="很多").target_count >= 1
    assert games.start_spell_along(target_count=0).target_count >= 1


# ---------------------------------------------------------------------------
# 開場白
# ---------------------------------------------------------------------------

def test_opening_line_names_the_first_word_in_both_languages():
    st = games.start_spell_along(topic="animal")
    line = games.spell_along_prompt(st)
    assert st.secret in line.zh
    assert line.en == scaffold.VOCAB[st.secret]["en"]


def test_step_content_is_what_the_child_repeats():
    """每一步要孩子跟著念的英文內容。拼音那一步必須是實測選出來的格式。"""
    assert games._spell_step_en("say_word", "蘋果") == "apple"
    assert games._spell_step_en("spell", "蘋果") == "A, P, P, L, E,"
    assert games._spell_step_en("sentence", "蘋果") == "I want to eat an apple."
    assert games._spell_step_en("spell", "不存在的詞") == ""


# ---------------------------------------------------------------------------
# 註冊進遊戲目錄
# ---------------------------------------------------------------------------

def test_the_game_is_in_the_public_catalog():
    """前端拿 GAMES 畫按鈕、game_intent 拿它認名字，沒註冊等於開局沒反應。"""
    entry = next(g for g in games.GAMES if g["kind"] == "spell_along")
    assert entry["zh"] == "背單字"
    assert "spell_along" in games.GAME_KINDS


def test_the_game_name_is_easy_for_asr_and_for_a_child():
    """2026-07-29 真機實測「火眼金睛」被聽成「佛火眼鏡」——冷僻用字對
    ASR 和對孩子都難。名字刻意選常用字。"""
    entry = next(g for g in games.GAMES if g["kind"] == "spell_along")
    assert len(entry["zh"]) <= 4


def test_generic_start_dispatches_through_the_shared_contract():
    st = games.start("spell_along")
    assert st.game == "spell_along"
    assert games.prompt(st).en
