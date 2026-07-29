# -*- coding: utf-8 -*-
"""`server/game_intent.py`：純規則的遊戲意圖偵測。

裝置沒有螢幕，開局只能靠聲音。這層刻意不經 LLM——開局是最需要確定性的動作，
斷網與連網必須一模一樣，而 edge LLM 一輪要 4–5 秒且輸出不可測。

**最重要的是誤觸邊界**：孩子在自由對話裡講到「點餐」不該把他丟進遊戲，
因為餐廳本來就可能是當天的對話主題。
"""

import pytest

from server import game_intent, games


# ---------------------------------------------------------------------------
# detect_start：意圖詞 + 遊戲名 同時命中才開局
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,kind", [
    ("我要玩火眼金睛", "i_spy"),
    ("我想玩猜猜我是誰", "guess_who"),
    ("來玩點餐時間", "restaurant"),
    ("開始玩火眼金睛好不好", "i_spy"),
    ("我們來玩 I Spy", "i_spy"),
])
def test_detect_start_recognises_each_game(text, kind):
    assert game_intent.detect_start(text) == kind


@pytest.mark.parametrize("text", [
    "點餐時間到了",          # 遊戲名但沒有意圖詞：可能只是在聊餐廳
    "火眼金睛",              # 光講名字不夠
    "我要玩",                # 光有意圖詞，沒說玩哪個
    "我想吃蘋果",
    "",
])
def test_detect_start_does_not_misfire(text):
    assert game_intent.detect_start(text) is None


def test_detect_start_covers_every_registered_game():
    """`games.GAMES` 新增一個遊戲時，這裡必須跟著能叫得出來。

    清單只有一份（games.GAMES），所以「新增遊戲但叫不出來」在這條會亮。
    """
    for g in games.GAMES:
        assert game_intent.detect_start(f"我要玩{g['zh']}") == g["kind"]


def test_detect_start_tolerates_none():
    assert game_intent.detect_start(None) is None


# ---------------------------------------------------------------------------
# detect_stop
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["不玩了", "我不想玩了", "結束遊戲", "停止"])
def test_detect_stop_recognises_quitting(text):
    assert game_intent.detect_stop(text) is True


@pytest.mark.parametrize("text", ["I see a dog.", "我看到一隻狗", "", None])
def test_detect_stop_ignores_ordinary_speech(text):
    assert game_intent.detect_stop(text) is False


# ---------------------------------------------------------------------------
# detect_yes_no：主動邀請的確認
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["好", "好啊", "要", "嗯", "yes", "OK"])
def test_detect_yes(text):
    assert game_intent.detect_yes_no(text) is True


@pytest.mark.parametrize("text", ["不要", "不用了", "no", "不想"])
def test_detect_no(text):
    assert game_intent.detect_yes_no(text) is False


@pytest.mark.parametrize("text", ["我看到一隻狗", "apple", "", None])
def test_detect_yes_no_returns_none_when_unrelated(text):
    """聽不出是不是在回答 → None，交給呼叫端當「沒回應」處理（不糾纏）。"""
    assert game_intent.detect_yes_no(text) is None


@pytest.mark.parametrize("text", [
    "我心情真的很好",        # 含「好」但這是在講心情，不是在答應
    "今天天氣很好",
    "我想要一顆蘋果",        # 含「要」
    "這個顏色很好看",
    "老師說可以帶點心來",    # 含「可以」
])
def test_detect_yes_no_ignores_sentences_that_merely_contain_a_yes_word(text):
    """只有**答句形狀**的短句才算回答。

    2026-07-29 實測：子字串比對讓「我心情真的很好」被當成答應而擅自開局，
    孩子明明只是在講心情。回答邀請的話一定很短，長句一律當作沒回答。
    """
    assert game_intent.detect_yes_no(text) is None


def test_no_beats_yes_when_both_appear():
    """「不要」含「要」——否定必須優先，否則孩子說不要卻被開局。"""
    assert game_intent.detect_yes_no("不要") is False


# ---------------------------------------------------------------------------
# 沒指定玩哪一個：「我要玩小遊戲」
#
# 2026-07-29 真機實測：「我要玩火眼金睛」被 SenseVoice 聽成「我要玩佛火眼鏡」
# ——**意圖詞「我要玩」完全正確，壞的是四字成語遊戲名**。
# 成語用字冷僻，對 ASR 難、對國小孩子講也難，所以主要觸發語改成「小遊戲」。
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "我要玩小遊戲",
    "我想玩小遊戲",
    "我要玩遊戲",
    "來玩小遊戲",
    "我們來玩遊戲好不好",
])
def test_asking_for_a_game_without_naming_one(text):
    assert game_intent.detect_start(text) == game_intent.ANY_GAME


def test_naming_a_game_still_wins_over_the_generic_phrase():
    """講得出名字就照名字開，不要被「遊戲」兩個字蓋過去。"""
    assert game_intent.detect_start("我要玩點餐時間這個遊戲") == "restaurant"


@pytest.mark.parametrize("text", [
    "遊戲時間到了",      # 沒有意圖詞
    "我不想玩遊戲",      # 否定
    "我要玩",            # 沒說玩什麼
])
def test_generic_phrase_still_needs_a_real_intent(text):
    assert game_intent.detect_start(text) is None
