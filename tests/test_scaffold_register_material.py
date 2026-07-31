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


def test_existing_zh_with_novel_en_is_rejected():
    """既有 zh 且提議的 en 是全新的（不撞任何其他詞的 en）→ 仍須拒絕。

    這正是「教材只能新增、不能重新定義既有詞條」的核心不變式：
    en 唯一性檢查擋不住這個案例（"koala" 沒有跟任何既有詞撞名），
    必須靠獨立的 zh-已存在檢查才擋得下來。若這裡沒擋住，
    ``VOCAB["獅子"]`` 會被無聲換成 koala，"lion" 這個課綱詞就此消失，
    而且這個損失會持久化到 ``materials`` 表、每次重啟都經
    ``_replay_materials`` 重放一次。"""
    original = dict(scaffold.VOCAB["獅子"])
    assert "koala" not in {v["en"] for v in scaffold.VOCAB.values()}, \
        "測試前提：koala 不得已存在於 VOCAB，否則測試沒有測到 zh 專屬的檢查"

    entries = [{"zh": "獅子", "en": "koala", "cat": "animal",
                "np": "a koala", "sent": "I see a koala at the zoo."}]

    accepted, rejected = scaffold.register_material_vocab(entries)

    assert accepted == []
    assert rejected == 1
    assert scaffold.VOCAB["獅子"] == original, "課綱詞條「獅子」→lion 不得被覆寫"
    assert "koala" not in {v["en"] for v in scaffold.VOCAB.values()}


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


def test_merged_word_is_found_by_its_own_key_not_a_stale_substring_match():
    """合併後，新詞的中文鍵不得被既有詞的子字串搶先卡位。

    ``_find_zh_vocab``／``_substitute_zh`` 都依賴 ``_ZH_KEYS_BY_LEN``
    這份「依長度遞減排序」的索引來決定先比對哪個詞。這份索引在 import 當下
    算好一次，``register_material_vocab`` 若沒有跟著重建它，新合併的
    「無尾熊」會被舊索引裡排更前面的「熊」（VOCAB 既有的子字串）搶先卡位，
    ``_find_zh_vocab("我看到無尾熊")`` 會回傳 ``['熊']`` 而不是
    ``['無尾熊']``——教了「koala」的孩子会被回答成「bear」。
    """
    scaffold.register_material_vocab([
        {"en": "koala", "zh": "無尾熊", "cat": "animal",
         "np": "a koala", "sent": "I see a koala."},
    ])

    matches = scaffold._find_zh_vocab("我看到無尾熊")

    assert matches == ["無尾熊"], (
        f"應命中新合併的完整詞「無尾熊」，卻得到 {matches}——"
        "代表 _ZH_KEYS_BY_LEN 沒有跟著 register_material_vocab 重建"
    )


def test_respond_uses_the_newly_merged_entry_not_a_stale_substring_match():
    """端到端驗證同一個 bug：respond() 純中文路徑要用新詞的 sent，不是舊詞的。"""
    scaffold.register_material_vocab([
        {"en": "koala", "zh": "無尾熊", "cat": "animal",
         "np": "a koala", "sent": "I see a koala."},
    ])

    result = scaffold.respond("我喜歡無尾熊", 0)

    assert result.target_sentence == "I see a koala.", (
        f"應使用新合併詞條「無尾熊」的目標句，卻得到 {result.target_sentence!r}"
        "——代表命中的是舊詞「熊」的子字串殘留"
    )


def test_register_clears_guardrails_safe_en_words_cache():
    """新合併詞的英文（Title-case 時常見於教材原句）不得被
    ``guardrails.deidentify`` 誤判成人名遮罩掉。

    ``guardrails._safe_en_words`` 用 ``@lru_cache(maxsize=1)`` 快取白名單，
    伺服器第一次做雲端去識別化時就會暖機、之後永遠不再讀 VOCAB。
    ``register_material_vocab`` 必須在合併成功後清掉這個快取，
    新詞才能在合併後的下一次 ``deidentify`` 呼叫就被視為安全詞。
    """
    from server import guardrails

    # 先暖機快取（模擬伺服器已經處理過至少一輪雲端去識別化）
    warm_up = guardrails.deidentify("Hello there")
    assert "[名字]" not in warm_up or True  # 暖機用，不是這條測試的斷言重點
    guardrails._safe_en_words()  # 確保快取確實填入

    scaffold.register_material_vocab([
        {"en": "koala", "zh": "無尾熊", "cat": "animal",
         "np": "a koala", "sent": "I see a koala."},
    ])

    masked = guardrails.deidentify("I saw a Koala today")

    assert "[名字]" not in masked, (
        f"新合併詞「Koala」不應被誤判為人名遮罩，卻得到：{masked!r}——"
        "代表 guardrails._safe_en_words 的快取沒有被清掉"
    )


def test_entries_beyond_the_cap_are_rejected_and_counted():
    """單次呼叫超過 MATERIAL_MAX_ENTRIES 的詞條要被拒絕且誠實計入
    rejected_count，不是靜默丟棄不計數——避免單一雲端呼叫把 VOCAB
    一次撐大過多（且這個成長會持久化、每次重啟都 replay）。"""
    cap = scaffold.MATERIAL_MAX_ENTRIES
    entries = [
        {"en": f"newword{i}", "zh": f"新詞{i}", "cat": "animal",
         "np": f"a newword{i}", "sent": f"I see a newword{i} today, number {i}."}
        for i in range(cap + 3)
    ]

    accepted, rejected = scaffold.register_material_vocab(entries)

    assert len(accepted) == cap
    assert rejected == 3
    for e in entries[cap:]:
        assert e["zh"] not in scaffold.VOCAB
