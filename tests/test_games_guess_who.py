# -*- coding: utf-8 -*-
"""test_games_guess_who.py — 遊戲 B「猜猜我是誰」（20 Questions）。

課綱對應：附錄四 `Asking about abilities`／`Asking about ownership`／
`Comparing things, people, etc.`；句型是 Yes/No 問句 `Is it ___?`。

**這個遊戲是雲端價值的展示台。** 離線版只能回答「屬性表答得出來」的問題；
雲端版任何問題都答得出來。斷網那一刻的落差是看得見的——這正是斷網橋段
要讓評審感受到的東西。

所以離線版的**能力邊界必須誠實**：答不出來的問題要明說「我只能回答這幾種」，
不能瞎猜 Yes/No。瞎猜會讓孩子學到錯的東西，比承認做不到更糟。

屬性全部**從既有資料推導**，不新造資料集：
- `cat` → 是不是動物／食物／學校用品
- `np` 開頭 → 冠詞是 a 還是 an（母音開頭）
- `en` 首字母 → 「是不是 D 開頭？」
"""

from __future__ import annotations

import pytest

from server import games, scaffold


# ---------------------------------------------------------------------------
# 開局
# ---------------------------------------------------------------------------

def test_start_picks_a_secret_from_the_word_bank():
    st = games.start_guess_who(topic="animal")
    assert st.game == "guess_who"
    assert st.secret in scaffold.VOCAB
    assert scaffold.VOCAB[st.secret]["cat"] == "animal"
    assert not st.done


def test_secret_is_deterministic_for_the_same_seed():
    """同一個種子要選到同一個謎底——現場可重現，測試也才驗得動。"""
    a = games.start_guess_who(topic="animal", seed="2026-07-30|alice")
    b = games.start_guess_who(topic="animal", seed="2026-07-30|alice")
    assert a.secret == b.secret


def test_different_seeds_pick_different_secrets():
    seen = {games.start_guess_who(topic="animal", seed=f"s{i}").secret
            for i in range(20)}
    assert len(seen) > 1, "種子沒有影響謎底，等於固定答案"


def test_opening_line_teaches_the_question_pattern():
    """孩子不會問問句就玩不下去，開場白必須示範句型。"""
    st = games.start_guess_who(topic="animal")
    line = games.guess_who_prompt(st)
    assert "Is it" in line.en
    assert "問" in line.zh


def test_due_word_is_preferred_as_the_secret(tmp_db):
    """謎底優先挑這孩子答錯過、又到期的詞——遊戲即複習。"""
    from server import srs

    srs.record_interactions(
        [{"seq": 1, "ts": "2026-07-20T09:00:00+08:00", "student_text": "rabbit",
          "ai_response_text": "好", "asr_confidence": 0.2}],
        student_id="alice",
    )
    st = games.start_guess_who(topic="animal", student_id="alice")
    assert st.secret == "兔子"


# ---------------------------------------------------------------------------
# 離線能回答的問題（屬性由既有資料推導）
# ---------------------------------------------------------------------------

def test_category_question_is_answered():
    st = games.start_guess_who(topic="animal", seed="fixed")
    turn = games.judge_guess_who(st, "Is it an animal?")
    assert turn.answer == "yes"
    assert "Yes" in turn.reply_en


def test_wrong_category_question_gets_no():
    st = games.start_guess_who(topic="animal", seed="fixed")
    turn = games.judge_guess_who(st, "Is it food?")
    assert turn.answer == "no"


def test_first_letter_question_is_answered():
    st = games.start_guess_who(topic="animal", seed="fixed")
    en = scaffold.VOCAB[st.secret]["en"]
    turn = games.judge_guess_who(st, f"Does it start with {en[0]}?")
    assert turn.answer == "yes"
    other = "z" if en[0].lower() != "z" else "b"
    assert games.judge_guess_who(st, f"Does it start with {other}?").answer == "no"


def test_chinese_questions_work_too():
    """雙語鷹架：孩子問中文也要答得出來。"""
    st = games.start_guess_who(topic="animal", seed="fixed")
    assert games.judge_guess_who(st, "它是動物嗎？").answer == "yes"
    assert games.judge_guess_who(st, "可以吃嗎？").answer == "no"


# ---------------------------------------------------------------------------
# 離線答不出來的問題：**誠實說做不到，不瞎猜**
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("q", [
    "Is it bigger than a car?",
    "Does it live in Africa?",
    "它住在哪裡？",
])
def test_unsupported_question_says_so_instead_of_guessing(q):
    """離線答不出來時回 unknown，並告訴孩子可以問什麼。

    瞎猜 Yes/No 會讓孩子學到錯的東西，比承認做不到更糟。
    這也是雲端版的價值所在——同樣的問題，雲端答得出來。
    """
    st = games.start_guess_who(topic="animal", seed="fixed")
    turn = games.judge_guess_who(st, q)
    assert turn.answer == "unknown"
    assert turn.correct is False
    assert "Is it" in turn.reply_zh or "動物" in turn.reply_zh


def test_supported_question_kinds_are_advertised():
    """能回答的問題種類要能被列出來——前端要拿它做提示按鈕。"""
    kinds = games.GUESS_WHO_SUPPORTED
    assert len(kinds) >= 3
    for k in kinds:
        assert k.get("zh") and k.get("en")


# ---------------------------------------------------------------------------
# 猜答案
# ---------------------------------------------------------------------------

def test_correct_guess_wins():
    st = games.start_guess_who(topic="animal", seed="fixed")
    en = scaffold.VOCAB[st.secret]["en"]
    turn = games.judge_guess_who(st, f"Is it a {en}?")
    assert turn.correct is True
    assert turn.state.done
    assert turn.target_en == scaffold.VOCAB[st.secret]["sent"]
    assert "答對" in turn.reply_zh or "猜對" in turn.reply_zh


def test_wrong_guess_is_a_no_not_a_failure():
    st = games.start_guess_who(topic="animal", seed="fixed")
    wrong = next(k for k, v in scaffold.VOCAB.items()
                 if v["cat"] == "animal" and k != st.secret)
    turn = games.judge_guess_who(st, f"Is it a {scaffold.VOCAB[wrong]['en']}?")
    assert turn.correct is False
    assert turn.answer == "no"
    assert not turn.state.done


def test_question_budget_runs_out_and_reveals_the_answer():
    """問完額度要公布答案，不能讓孩子一直問下去。"""
    st = games.start_guess_who(topic="animal", seed="fixed", max_questions=3)
    for _ in range(3):
        turn = games.judge_guess_who(st, "Is it an animal?")
        st = turn.state
    assert st.done
    assert scaffold.VOCAB[st.secret]["en"] in turn.reply_en


def test_finished_game_stops_answering():
    st = games.start_guess_who(topic="animal", seed="fixed", max_questions=1)
    st = games.judge_guess_who(st, "Is it an animal?").state
    assert st.done
    turn = games.judge_guess_who(st, "Is it a dog?")
    assert turn.answer == "unknown"


# ---------------------------------------------------------------------------
# 契約
# ---------------------------------------------------------------------------

def test_state_is_immutable_between_turns():
    st = games.start_guess_who(topic="animal", seed="fixed")
    before = st.asked
    games.judge_guess_who(st, "Is it an animal?")
    assert st.asked == before


@pytest.mark.parametrize("bad", [None, "", "   ", 123, "🐶", "a" * 5000])
def test_judge_never_raises_on_garbage(bad):
    st = games.start_guess_who(topic="animal", seed="fixed")
    turn = games.judge_guess_who(st, bad)
    assert isinstance(turn.reply_zh, str) and turn.reply_zh.strip()
