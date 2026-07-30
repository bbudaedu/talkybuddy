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


# ---------------------------------------------------------------------------
# 三步狀態機
# ---------------------------------------------------------------------------

def _game_on(word_zh="蘋果", **kw):
    """開一局並強制第一個詞，讓測試不依賴選詞順序。"""
    st = games.start_spell_along(**kw)
    return games.replace(st, hints=(word_zh, "狗", "書"), secret=word_zh)


def test_saying_the_word_advances_to_spelling():
    turn = games.judge_spell_along(_game_on(), "apple")
    assert turn.correct
    assert turn.state.step == "spell"
    assert turn.target_en == "A, P, P, L, E,"


def test_spelling_correctly_advances_to_the_sentence():
    st = games.replace(_game_on(), step="spell")
    turn = games.judge_spell_along(st, "A, P, P, L, E.")
    assert turn.correct
    assert turn.state.step == "sentence"
    assert turn.target_en == "I want to eat an apple."


def test_one_wrong_letter_still_passes():
    """寬鬆鼓勵制：80% 命中就過。"""
    st = games.replace(_game_on(), step="spell")
    turn = games.judge_spell_along(st, "A, P, P, O, E.")
    assert turn.correct
    assert turn.state.step == "sentence"


def test_a_bad_attempt_repeats_the_same_step_instead_of_advancing():
    st = games.replace(_game_on(), step="spell")
    turn = games.judge_spell_along(st, "我不會")
    assert not turn.correct
    assert turn.state.step == "spell", "沒過卻前進了"
    assert turn.state.retries == 1
    assert turn.target_en == "A, P, P, L, E,", "重來時要再念一次同樣的內容"


def test_the_child_is_never_stuck_on_one_step():
    """第 MAX_RETRIES+1 次一律往下走。卡在同一個詞出不去是最糟的失敗模式。"""
    st = games.replace(_game_on(), step="spell")
    for _ in range(spelling.MAX_RETRIES):
        st = games.judge_spell_along(st, "我不會").state
        assert st.step == "spell"
    turn = games.judge_spell_along(st, "我不會")
    assert turn.state.step == "sentence", "重試用完仍卡在原地"
    assert not turn.correct, "往下走不代表判定成功"


def test_retries_reset_when_a_new_step_begins():
    """重試上限是「同一步」的上限，不是整個詞的上限——否則第一步用掉配額，
    後面兩步一次機會都沒有。"""
    st = games.judge_spell_along(games.replace(_game_on()), "我不會").state
    assert st.retries == 1
    turn = games.judge_spell_along(st, "apple")
    assert turn.state.step == "spell"
    assert turn.state.retries == 0


def test_finishing_the_sentence_moves_to_the_next_word():
    st = games.replace(_game_on(), step="sentence")
    turn = games.judge_spell_along(st, "I want to eat an apple.")
    assert turn.state.found == ("蘋果",)
    assert turn.state.secret == "狗"
    assert turn.state.step == "say_word"
    assert turn.state.retries == 0
    assert not turn.done


def test_the_last_word_ends_the_round():
    st = games.replace(_game_on(), step="sentence", target_count=1)
    turn = games.judge_spell_along(st, "I want to eat an apple.")
    assert turn.done
    assert turn.state.done
    assert "背了 1 個" in turn.reply_zh


def test_judging_a_finished_round_does_not_crash():
    st = games.replace(_game_on(), done=True, secret="")
    turn = games.judge_spell_along(st, "apple")
    assert not turn.correct
    assert turn.reply_zh


def test_judging_never_raises_on_garbage():
    for junk in (None, "", 12345, "。" * 600):
        assert games.judge_spell_along(_game_on(), junk).reply_zh


# ---------------------------------------------------------------------------
# 學習狀況記錄
# ---------------------------------------------------------------------------

def test_a_clean_spelling_is_recorded_as_learned(tmp_db):
    from server import store

    st = games.replace(_game_on(student_id="STU-R"), step="spell")
    games.judge_spell_along(st, "A, P, P, L, E.")
    row = store.get_word_review("STU-R", "蘋果")
    assert row is not None and row["reps"] == 1


def test_a_spelling_that_needed_retries_is_not_counted_as_learned(tmp_db):
    """重試才過的不算學會——記下來的必須是真的會了。"""
    from server import store

    st = games.replace(_game_on(student_id="STU-R"), step="spell")
    st = games.judge_spell_along(st, "我不會").state      # retries → 1
    games.judge_spell_along(st, "A, P, P, L, E.")         # 這次過了
    assert store.get_word_review("STU-R", "蘋果")["interval_days"] == 0


def test_recording_happens_at_the_spelling_step_not_the_sentence_step(tmp_db):
    """拼音是判定主力，例句那一步的 ASR 太糊，不參與對錯判定。"""
    from server import store

    st = games.replace(_game_on(student_id="STU-R"), step="sentence")
    games.judge_spell_along(st, "完全不相干的話")
    assert store.get_word_review("STU-R", "蘋果") is None


def test_a_round_without_a_student_still_plays(tmp_db):
    """沒有 student_id 就不寫紀錄，但遊戲照樣玩得完。"""
    st = games.replace(_game_on(), step="spell")
    assert games.judge_spell_along(st, "A, P, P, L, E.").state.step == "sentence"
