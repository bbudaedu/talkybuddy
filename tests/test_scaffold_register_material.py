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
    entries = [{"en": "lemonade", "zh": "檸檬水", "cat": "food",
                "np": "some lemonade", "sent": "I want to drink some lemonade."}]

    accepted, rejected = scaffold.register_material_vocab(entries)

    assert rejected == 0
    assert len(accepted) == 1
    assert "檸檬水" in scaffold.VOCAB


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


def test_existing_zh_with_different_en_is_rejected():
    """既有 zh 且 en 屬於他者 → 拒絕（會造成 en 重複）。

    "獅子" 已有 en="lion"，若嘗試改為 en="tiger"（屬於 "老虎"），
    會違反 en 唯一性不變式。
    """
    original = dict(scaffold.VOCAB["獅子"])
    entries = [{"zh": "獅子", "en": "tiger", "cat": "animal",
                "np": "a tiger", "sent": "I see a tiger."}]

    accepted, rejected = scaffold.register_material_vocab(entries)

    assert accepted == []
    assert rejected == 1
    assert scaffold.VOCAB["獅子"] == original  # 未被覆寫


def test_curriculum_entry_not_silently_overwritten():
    """教科書詞條不應被教師上傳的詞條無聲覆寫（兩個 en 不同）。

    確認既有教科書詞條的 zh 若遭重新提交（但 en 改變），
    會因 en 重複而被拒，教科書詞條保持不變。
    """
    original_apple = dict(scaffold.VOCAB["蘋果"])  # {"en": "apple", ...}
    original_banana = dict(scaffold.VOCAB["香蕉"])  # {"en": "banana", ...}

    # 嘗試用 "banana" 的 en 覆寫 "蘋果"（失敗）
    entries = [{"zh": "蘋果", "en": "banana", "cat": "food",
                "np": "a banana", "sent": "This is a test sentence."}]

    accepted, rejected = scaffold.register_material_vocab(entries)

    # 應拒絕，因 "banana" 已被 "香蕉" 佔用
    assert accepted == []
    assert rejected == 1

    # 驗證兩個詞條都保持原狀
    assert scaffold.VOCAB["蘋果"] == original_apple
    assert scaffold.VOCAB["香蕉"] == original_banana

    # 確認沒有重複 en
    en_list = [v["en"] for v in scaffold.VOCAB.values()]
    assert en_list.count("apple") == 1
    assert en_list.count("banana") == 1
