# -*- coding: utf-8 -*-
"""學生端「今天主題」標籤必須跟目標句同一類。

2026-08-01 線上實測：畫面同時顯示「今天主題：動物」與
「He is eating an apple.」。原因是標籤拿了 `topic`（診斷輪替出來的，供
延伸問句與遊戲出題用），而帶讀句優先取老師指定的本週單元，兩者本來就
可能不同類。

修法刻意**不動 topic**——`tests/test_unit_alignment.py` 有一條相反方向的
契約（topic 要維持由診斷決定），合併兩個欄位會把那個刻意的設計改掉。
改成新增 `sentence_topic` 給畫面用。
"""
from server import lesson


def test_sentence_topic_matches_the_target_sentence():
    diagnoses = [{"level_state": {"topic": "animal", "target_form": "簡單句"},
                  "companion_directive": None}]
    lp = lesson.build_lesson(diagnoses, profile=None)
    actual = lesson.topic_of_sentence(lp.target_sentence)
    if actual is not None:
        assert lp.sentence_topic == actual, (
            f"標籤用的 sentence_topic={lp.sentence_topic!r} 與目標句 "
            f"{lp.target_sentence!r}（實際 {actual!r}）不一致"
        )


def test_topic_stays_from_the_diagnosis():
    """topic 不可以被句子帶著跑：延伸問句與遊戲出題靠它。"""
    diagnoses = [{"level_state": {"topic": "animal", "target_form": "簡單句"},
                  "companion_directive": None}]
    lp = lesson.build_lesson(diagnoses, profile=None)
    assert lp.topic == "animal"


def test_topic_of_sentence_unknown_returns_none():
    """句子不在題庫裡就回 None，讓呼叫端退回 topic，不要亂猜。"""
    assert lesson.topic_of_sentence("This sentence is not in the vocab.") is None


def test_build_lesson_survives_empty_diagnoses():
    lp = lesson.build_lesson([], profile=None)
    assert lp.target_sentence
    assert lp.sentence_topic
