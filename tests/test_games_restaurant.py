# -*- coding: utf-8 -*-
"""test_games_restaurant.py — 遊戲 C「點餐時間」（Restaurant）。

課綱對應：附錄四溝通功能 `Ordering food & drinks`（明列）、
`Asking about prices`；附錄三主題 `Eating out`。句型 `I want a ___.`

這是三個遊戲裡最貼近真實情境的一個：孩子在餐廳真的會用到。
腳本固定三階段（招呼 → 點餐 → 結帳），離線完整跑得完；雲端只是讓店員
能應對意外的回答。
"""

from __future__ import annotations

import pytest

from server import games, scaffold


# ---------------------------------------------------------------------------
# 腳本階段
# ---------------------------------------------------------------------------

def test_start_opens_at_the_greeting_step():
    st = games.start_restaurant()
    assert st.game == "restaurant"
    assert st.step == "greet"
    assert st.order == ()
    assert not st.done


def test_greeting_line_uses_the_official_communicative_function():
    """開場白就是課綱 Ordering food & drinks 的標準句型。"""
    st = games.start_restaurant()
    line = games.restaurant_prompt(st)
    assert "What would you like" in line.en
    assert line.zh.strip()


def test_ordering_a_food_word_is_accepted():
    st = games.start_restaurant()
    turn = games.judge_restaurant(st, "I want a hamburger.")
    assert turn.correct is True
    assert turn.word == "漢堡"
    assert "漢堡" in turn.state.order
    assert turn.state.step == "more"


def test_article_comes_from_the_word_bank_not_guessed():
    """`np` 欄位已經寫好正確冠詞，回覆要用它，不要自己拼。

    麵包是不可數 → some bread，不是 a bread。這種錯誤直接教錯孩子。
    """
    st = games.start_restaurant()
    turn = games.judge_restaurant(st, "我要麵包")
    assert turn.correct
    assert turn.target_en == "I want some bread."
    assert "a bread" not in (turn.reply_en or "")


def test_non_food_word_is_redirected_kindly():
    st = games.start_restaurant()
    turn = games.judge_restaurant(st, "I want a dog.")
    assert turn.correct is False
    assert turn.word == "狗"
    assert "吃" in turn.reply_zh or "食物" in turn.reply_zh
    assert turn.state.order == ()


def test_unrecognised_input_asks_again():
    st = games.start_restaurant()
    turn = games.judge_restaurant(st, "嗯……")
    assert turn.correct is False
    assert turn.word is None
    assert turn.reply_zh.strip()


# ---------------------------------------------------------------------------
# 多樣餐點與結帳
# ---------------------------------------------------------------------------

def test_can_order_several_items():
    st = games.start_restaurant()
    for text in ("I want a hamburger.", "I want some juice."):
        turn = games.judge_restaurant(st, text)
        assert turn.correct, text
        st = turn.state
    assert st.order == ("漢堡", "果汁")


def test_duplicate_order_is_not_counted_twice():
    st = games.start_restaurant()
    st = games.judge_restaurant(st, "I want a hamburger.").state
    turn = games.judge_restaurant(st, "I want a hamburger.")
    assert turn.state.order == ("漢堡",)
    assert "點過" in turn.reply_zh or "已經" in turn.reply_zh


def test_saying_no_more_moves_to_checkout():
    st = games.start_restaurant()
    st = games.judge_restaurant(st, "I want a hamburger.").state
    turn = games.judge_restaurant(st, "No, thank you.")
    assert turn.state.done
    assert turn.state.step == "done"
    assert "漢堡" in turn.reply_zh


@pytest.mark.parametrize("no_more", ["No, thank you.", "不用了", "沒有了", "that's all"])
def test_various_ways_to_say_no_more(no_more):
    st = games.start_restaurant()
    st = games.judge_restaurant(st, "I want a hamburger.").state
    turn = games.judge_restaurant(st, no_more)
    assert turn.state.done, no_more


def test_checkout_lists_everything_ordered():
    st = games.start_restaurant()
    for text in ("I want a hamburger.", "I want some juice.", "I want a cookie."):
        st = games.judge_restaurant(st, text).state
    turn = games.judge_restaurant(st, "不用了")
    for zh in ("漢堡", "果汁", "餅乾"):
        assert zh in turn.reply_zh


def test_ending_without_ordering_anything_is_handled():
    """一樣都沒點就說不用了——不能出現「您點了：」後面空白。"""
    st = games.start_restaurant()
    turn = games.judge_restaurant(st, "不用了")
    assert turn.state.done
    assert turn.reply_zh.strip()
    assert "您點了：\n" not in turn.reply_zh


def test_order_limit_forces_checkout():
    """點太多會讓結帳句變得很長，也超出一局的長度。"""
    st = games.start_restaurant(max_items=2)
    st = games.judge_restaurant(st, "I want a hamburger.").state
    turn = games.judge_restaurant(st, "I want some juice.")
    assert turn.state.done
    assert len(turn.state.order) == 2


# ---------------------------------------------------------------------------
# 間隔重複
# ---------------------------------------------------------------------------

def test_menu_suggests_due_words_first(tmp_db):
    from server import srs

    srs.record_interactions(
        [{"seq": 1, "ts": "2026-07-20T09:00:00+08:00", "student_text": "banana",
          "ai_response_text": "好", "asr_confidence": 0.2}],
        student_id="alice",
    )
    st = games.start_restaurant(student_id="alice")
    assert st.hints and st.hints[0] == "香蕉", st.hints


def test_menu_falls_back_to_food_words(tmp_db):
    st = games.start_restaurant(student_id="nobody")
    assert st.hints
    for h in st.hints:
        assert scaffold.VOCAB[h]["cat"] == "food"


# ---------------------------------------------------------------------------
# 契約
# ---------------------------------------------------------------------------

def test_finished_game_stops_taking_orders():
    st = games.start_restaurant()
    st = games.judge_restaurant(st, "不用了").state
    turn = games.judge_restaurant(st, "I want a hamburger.")
    assert turn.correct is False
    assert turn.state.order == ()


def test_state_is_immutable_between_turns():
    st = games.start_restaurant()
    before = st.order
    games.judge_restaurant(st, "I want a hamburger.")
    assert st.order == before


@pytest.mark.parametrize("bad", [None, "", "   ", 123, "🍔", "a" * 5000])
def test_judge_never_raises_on_garbage(bad):
    st = games.start_restaurant()
    turn = games.judge_restaurant(st, bad)
    assert isinstance(turn.reply_zh, str) and turn.reply_zh.strip()
