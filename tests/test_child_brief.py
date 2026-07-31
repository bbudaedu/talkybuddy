# -*- coding: utf-8 -*-
"""build_child_brief — 把「這個孩子是誰」變成一段可注入的話。

釘住兩件最容易出事的事：

1. **第一次見面不可以假裝認識他。** 沒資料就回 None，呼叫端不注入。
2. **摘要是給模型讀的背景，不是要唸出來的稿子。** 所以一定要帶使用說明，
   否則玩偶會開場就把孩子的資料整段唸一遍——那在現場會非常尷尬，
   而且是隱私問題。
"""
from __future__ import annotations

from server.child_brief import build_child_brief


def test_no_data_returns_none():
    """第一次見到這個孩子，寧可不說也不要說錯。"""
    assert build_child_brief() is None
    assert build_child_brief({}, [], []) is None


def test_interaction_count_becomes_not_our_first_meeting():
    out = build_child_brief({"interaction_count": 7})
    assert out is not None
    assert "7" in out
    assert "不是第一次見面" in out


def test_interests_are_included():
    out = build_child_brief({"interaction_count": 1, "interests": [{"topic": "animal"}]})
    assert "animal" in out


def test_learning_vocab_is_included():
    out = build_child_brief({"interaction_count": 1,
                             "learning_vocab": [{"en": "apple"}, {"en": "cat"}]})
    assert "apple" in out and "cat" in out


def test_due_words_are_flagged_for_review():
    out = build_child_brief({"interaction_count": 1}, [{"word": "banana"}])
    assert "banana" in out
    assert "複習" in out


def test_emotional_state_is_carried_over():
    out = build_child_brief({"interaction_count": 1, "emotional_recent": "有點沮喪"})
    assert "有點沮喪" in out


def test_weakest_dimension_from_latest_diagnosis():
    out = build_child_brief({"interaction_count": 1}, None,
                            [{"weakest_dim": "發音"}, {"weakest_dim": "文法"}])
    assert "文法" in out, "要用最後一筆診斷"
    assert "發音" not in out


def test_brief_tells_the_model_not_to_recite_it():
    """沒有這句，玩偶會開場把孩子的資料整段唸出來——尷尬且是隱私問題。"""
    out = build_child_brief({"interaction_count": 3, "interests": [{"topic": "animal"}]})
    assert "不要一口氣講出來" in out


def test_lists_are_capped():
    """列太多會佔掉 prompt 篇幅，也讓模型抓不到重點。"""
    many = [{"en": f"word{i}"} for i in range(10)]
    out = build_child_brief({"interaction_count": 1, "learning_vocab": many})
    assert "word0" in out
    assert "word9" not in out


def test_plain_string_items_are_tolerated():
    """profile 的形狀在不同版本間會漂，純字串也要吃得下。"""
    out = build_child_brief({"interaction_count": 1, "learning_vocab": ["apple"]})
    assert "apple" in out


def test_bad_interaction_count_does_not_crash():
    assert build_child_brief({"interaction_count": "壞掉的值"}) is None


def test_interests_prefer_the_chinese_label():
    """玩偶會把它唸出來——「他喜歡聊 animal」在台灣國小的對話裡很突兀。"""
    out = build_child_brief({
        "interaction_count": 5,
        "interests": [{"topic": "animal", "label": "動物", "hits": 10}],
    })
    assert "動物" in out
    assert "animal" not in out
