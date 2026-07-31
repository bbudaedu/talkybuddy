# -*- coding: utf-8 -*-
"""今日主題要給「一組」句子，不是只給一句。

2026-07-31 十輪模擬對話抓到的問題：孩子第 1 輪就把 `I see a dog.` 唸對了，
之後每輪都唸對，玩偶卻十輪都在教同一句，孩子自己抗議「你怎麼一直叫我唸
一樣的啦！」。

教練 prompt 本來就寫著「孩子跟上就換下一句或延伸一點」——但 `build_lesson`
一場只給**一句**，模型根本不知道還有哪些句子可以換。而 `scaffold.VOCAB` 的
animal 類其實有 29 句。

這不是模型不聽話，是我們沒給它材料。
"""
from __future__ import annotations

from server.lesson import pick_target_sentence, topic_sentences
from server.scaffold import build_live_system_prompt


def test_topic_sentences_returns_several():
    out = topic_sentences("animal", limit=5)
    assert len(out) == 5
    assert all(isinstance(s, str) and s for s in out)
    assert len(set(out)) == 5, "不可以有重複"


def test_first_sentence_matches_the_picked_target():
    """今日目標句要排第一，教練才知道從哪開始。"""
    out = topic_sentences("animal", limit=4)
    assert out[0] == pick_target_sentence("animal")


def test_unknown_topic_degrades_safely():
    assert topic_sentences("不存在的主題", limit=4) == []


def test_live_prompt_lists_the_progression():
    out = build_live_system_prompt(
        "I see a dog.", None, "animal",
        more_sentences=["I see a cat.", "I see a bird."],
    )
    assert "I see a cat." in out
    assert "I see a bird." in out


def test_live_prompt_unchanged_without_more_sentences():
    """不給就跟以前一樣——/ws/live 零迴歸。"""
    base = build_live_system_prompt("I see a dog.", None, "animal")
    assert "I see a cat." not in base
