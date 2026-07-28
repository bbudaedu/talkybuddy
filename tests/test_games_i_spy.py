# -*- coding: utf-8 -*-
"""test_games_i_spy.py — 遊戲 A「火眼金睛」（I Spy）。

課綱對應：附錄四溝通功能 `Naming common toys and household objects`、
`Talking about location`；句型是 `I see a ___.`（scaffold.VOCAB 的 animal
分類本來就是這個句型）。

設計原則（三個遊戲共用，見 server/games.py）：
1. **離線必須完整可玩**——斷網橋段是決賽主軸，遊戲斷線就掛等於自打嘴巴。
   所以判定、計分、鼓勵語全部是規則式純函式，不碰雲端。
2. 雲端只做加值（追問、場景敘述），**沒有雲端時遊戲照樣完整**。
3. 狀態是不可變的：每回合回傳新的 state，不原地改。這樣測試可以直接
   比對兩個 state，也不會有跨回合的隱藏耦合。
"""

from __future__ import annotations

import pytest

from server import games, scaffold


# ---------------------------------------------------------------------------
# 開場
# ---------------------------------------------------------------------------

def test_start_picks_a_topic_that_has_enough_words():
    """場景必須有足夠的詞可找，否則孩子找兩個就沒東西了。"""
    st = games.start_i_spy(topic="animal")
    assert st.game == "i_spy"
    assert st.topic == "animal"
    assert st.target_count >= 3
    assert st.found == ()
    assert not st.done


def test_start_rejects_a_topic_with_too_few_words():
    """分類詞數不足時要退回有詞的分類，不能開一個玩不下去的局。"""
    st = games.start_i_spy(topic="不存在的分類")
    assert st.topic in games.I_SPY_TOPICS


def test_opening_line_names_the_scene_in_both_languages():
    st = games.start_i_spy(topic="animal")
    line = games.i_spy_prompt(st)
    assert "動物" in line.zh
    assert "I see" in line.en


@pytest.mark.parametrize("topic", games.I_SPY_TOPICS)
def test_every_playable_topic_has_at_least_five_words(topic):
    """五題是一局的長度。少於五個詞的分類不該出現在可玩清單裡。"""
    words = [k for k, v in scaffold.VOCAB.items() if v["cat"] == topic]
    assert len(words) >= 5, f"{topic} 只有 {len(words)} 個詞"


# ---------------------------------------------------------------------------
# 判定（離線純規則）
# ---------------------------------------------------------------------------

def test_correct_word_in_scene_is_accepted():
    st = games.start_i_spy(topic="animal")
    turn = games.judge_i_spy(st, "I see a dog.")
    assert turn.correct is True
    assert turn.word == "狗"
    assert "狗" in turn.state.found


def test_chinese_input_also_counts():
    """孩子講中文也要接得住——這是雙語鷹架，不是純英文測驗。"""
    st = games.start_i_spy(topic="animal")
    turn = games.judge_i_spy(st, "我看到一隻貓")
    assert turn.correct is True
    assert turn.word == "貓"
    # 中文作答時要把英文目標句給出來讓孩子跟讀
    assert turn.target_en == scaffold.VOCAB["貓"]["sent"]


def test_word_from_another_scene_is_rejected_with_a_hint():
    """說了不在這個場景的詞：不算對，但要給方向，不能只說「錯」。"""
    st = games.start_i_spy(topic="animal")
    turn = games.judge_i_spy(st, "I see an apple.")
    assert turn.correct is False
    assert turn.word == "蘋果"
    assert "動物" in turn.reply_zh          # 提醒現在的場景是什麼
    assert turn.state.found == ()


def test_repeating_a_found_word_does_not_count_twice():
    st = games.start_i_spy(topic="animal")
    st = games.judge_i_spy(st, "I see a dog.").state
    turn = games.judge_i_spy(st, "I see a dog.")
    assert turn.correct is False
    assert "說過" in turn.reply_zh
    assert turn.state.found == ("狗",)


def test_unrecognised_input_asks_again_without_scolding():
    st = games.start_i_spy(topic="animal")
    turn = games.judge_i_spy(st, "嗯嗯嗯")
    assert turn.correct is False
    assert turn.word is None
    assert turn.reply_zh.strip()
    assert not turn.state.done


def test_state_is_immutable_between_turns():
    """回合之間不得原地改 state——否則跨回合的耦合會很難查。"""
    st = games.start_i_spy(topic="animal")
    before = st.found
    games.judge_i_spy(st, "I see a dog.")
    assert st.found == before, "judge 改到了傳進去的 state"


# ---------------------------------------------------------------------------
# 一局的結束
# ---------------------------------------------------------------------------

def test_game_finishes_after_target_count_words():
    st = games.start_i_spy(topic="animal", target_count=3)
    words = ["I see a dog.", "I see a cat.", "I see a bird."]
    for w in words:
        turn = games.judge_i_spy(st, w)
        assert turn.correct
        st = turn.state
    assert st.done
    assert len(st.found) == 3
    assert "恭喜" in turn.reply_zh or "完成" in turn.reply_zh


def test_finished_game_stops_judging():
    st = games.start_i_spy(topic="animal", target_count=1)
    st = games.judge_i_spy(st, "I see a dog.").state
    assert st.done
    turn = games.judge_i_spy(st, "I see a cat.")
    assert turn.correct is False
    assert turn.state.found == ("狗",)


# ---------------------------------------------------------------------------
# 間隔重複：到期的詞優先進場景
# ---------------------------------------------------------------------------

def test_due_words_are_offered_as_hints_first(tmp_db):
    """這孩子上次沒說對、又到了複習時間的詞，要優先被提示。

    這是把 word_reviews 接進遊戲的唯一可見效果——提示順序真的變了。
    """
    from server import srs

    srs.record_interactions(
        [{"seq": 1, "ts": "2026-07-20T09:00:00+08:00", "student_text": "rabbit",
          "ai_response_text": "好", "asr_confidence": 0.2}],
        student_id="alice",
    )
    st = games.start_i_spy(topic="animal", student_id="alice")
    assert st.hints and st.hints[0] == "兔子", st.hints


def test_hints_fall_back_to_the_scene_when_nothing_is_due(tmp_db):
    st = games.start_i_spy(topic="animal", student_id="nobody")
    assert st.hints
    for h in st.hints:
        assert scaffold.VOCAB[h]["cat"] == "animal"


def test_start_never_raises_without_a_database():
    """DB 不可用時遊戲照樣開得起來——排程是加值不是主幹。"""
    st = games.start_i_spy(topic="animal", student_id="whoever")
    assert st.topic == "animal"


# ---------------------------------------------------------------------------
# 契約：任何輸入都不得拋
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [None, "", "   ", 123, "🐶🐱", "a" * 5000])
def test_judge_never_raises_on_garbage(bad):
    st = games.start_i_spy(topic="animal")
    turn = games.judge_i_spy(st, bad)
    assert isinstance(turn.reply_zh, str) and turn.reply_zh.strip()
    assert turn.correct is False
