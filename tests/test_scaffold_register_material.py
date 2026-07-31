# -*- coding: utf-8 -*-
"""test_scaffold_register_material.py — 教材詞條驗證與合併入口。"""

from __future__ import annotations

import pytest

from server import scaffold


@pytest.fixture(autouse=True)
def _restore_vocab():
    """register_material_vocab 會原地 mutate 全域 VOCAB，測試後還原快照，
    避免污染其他測試檔案（tmp_db 只處理 SQLite，管不到這個全域 dict）。"""
    snapshot = {zh: dict(v) for zh, v in scaffold.VOCAB.items()}
    yield
    scaffold.VOCAB.clear()
    scaffold.VOCAB.update(snapshot)


def test_valid_entry_is_merged_in_place():
    """合法詞條原地寫入 VOCAB（同一個 dict 物件，不是重新賦值）。"""
    vocab_ref_before = scaffold.VOCAB
    entries = [{"en": "koala", "zh": "無尾熊", "cat": "animal",
                "np": "a koala", "sent": "I see a koala."}]

    accepted, rejected = scaffold.register_material_vocab(entries)

    assert scaffold.VOCAB is vocab_ref_before, "應原地 mutate，不得重新賦值 VOCAB"
    assert rejected == 0
    assert len(accepted) == 1
    assert accepted[0]["zh"] == "無尾熊"
    assert "無尾熊" in scaffold.VOCAB
    assert scaffold.VOCAB["無尾熊"] == {
        "en": "koala", "cat": "animal", "np": "a koala", "sent": "I see a koala."
    }


def test_duplicate_english_word_is_rejected():
    """en 與既有 VOCAB 重複（不分大小寫）→ 拒絕，不覆蓋既有詞條。"""
    original = dict(scaffold.VOCAB["獅子"])
    entries = [{"en": "Lion", "zh": "新獅子詞", "cat": "animal",
                "np": "a lion", "sent": "I want a lion."}]

    accepted, rejected = scaffold.register_material_vocab(entries)

    assert accepted == []
    assert rejected == 1
    assert "新獅子詞" not in scaffold.VOCAB
    assert scaffold.VOCAB["獅子"] == original


def test_duplicate_sentence_is_rejected():
    """sent 與既有 VOCAB 重複 → 拒絕（homework 靠 sent 去重出題）。"""
    entries = [{"en": "koala", "zh": "無尾熊", "cat": "animal",
                "np": "a lion", "sent": "I see a lion."}]  # sent 撞既有「獅子」

    accepted, rejected = scaffold.register_material_vocab(entries)

    assert accepted == []
    assert rejected == 1
    assert "無尾熊" not in scaffold.VOCAB


def test_invalid_category_is_rejected():
    """cat 不在既有 6 類 → 拒絕（games.py 的分類假設不能被打破）。"""
    entries = [{"en": "robot", "zh": "機器人", "cat": "toy",
                "np": "a robot", "sent": "I see a robot."}]

    accepted, rejected = scaffold.register_material_vocab(entries)

    assert accepted == []
    assert rejected == 1
    assert "機器人" not in scaffold.VOCAB


def test_wrong_article_is_rejected():
    """np 冠詞不符合 a/an 規則 → 拒絕（koala 開頭是子音，應該用 a 不是 an）。"""
    entries = [{"en": "koala", "zh": "無尾熊", "cat": "animal",
                "np": "an koala", "sent": "I see an koala."}]

    accepted, rejected = scaffold.register_material_vocab(entries)

    assert accepted == []
    assert rejected == 1
    assert "無尾熊" not in scaffold.VOCAB


def test_non_a_an_article_is_not_strictly_checked():
    """np 開頭是 some/my/the 等非 a/an 時不做嚴格檢查（沒有明確規則可比對）。"""
    entries = [{"en": "juice", "zh": "果汁", "cat": "food",
                "np": "some juice", "sent": "I want to drink some juice."}]

    accepted, rejected = scaffold.register_material_vocab(entries)

    assert rejected == 0
    assert len(accepted) == 1
    assert "果汁" in scaffold.VOCAB


def test_one_bad_entry_does_not_block_the_rest_of_the_batch():
    """一批詞條裡有不合法的，只丟該條，其他合法詞條照常合併。"""
    entries = [
        {"en": "koala", "zh": "無尾熊", "cat": "animal",
         "np": "a koala", "sent": "I see a koala."},
        {"en": "robot", "zh": "機器人", "cat": "toy",  # 不合法分類
         "np": "a robot", "sent": "I see a robot."},
    ]

    accepted, rejected = scaffold.register_material_vocab(entries)

    assert len(accepted) == 1
    assert rejected == 1
    assert "無尾熊" in scaffold.VOCAB
    assert "機器人" not in scaffold.VOCAB


def test_missing_or_empty_field_is_rejected():
    """欄位缺漏或空字串 → 拒絕，不拋例外。"""
    entries = [
        {"en": "", "zh": "無尾熊", "cat": "animal", "np": "a koala", "sent": "I see a koala."},
        {"en": "koala", "cat": "animal", "np": "a koala", "sent": "I see a koala."},  # 缺 zh
    ]

    accepted, rejected = scaffold.register_material_vocab(entries)

    assert accepted == []
    assert rejected == 2


def test_non_dict_entry_is_rejected_without_raising():
    """輸入不是 dict（None、字串、list…）不拋例外，直接算拒絕。"""
    entries = [None, "not a dict", 42, {"en": "koala", "zh": "無尾熊",
               "cat": "animal", "np": "a koala", "sent": "I see a koala."}]

    accepted, rejected = scaffold.register_material_vocab(entries)

    assert len(accepted) == 1
    assert rejected == 3
