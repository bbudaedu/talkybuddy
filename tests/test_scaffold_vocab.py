# -*- coding: utf-8 -*-
"""test_scaffold_vocab.py — 詞庫的結構規則（加詞時的守門）。

詞庫從 44 個示範詞擴到 136 個之後，用眼睛看不出重複與冠詞錯誤了。
這些規則每一條都對應一個會**靜默**出錯的地方：

- en 重複 → `profile._EN_INFO` 是 en → 詞條的反查表，後面的會蓋掉前面的，
  於是有個詞的興趣分類與掌握度統計永遠算在別人頭上
- sent 重複 → `homework._pick_vocab_entries` 對 sent 去重，重複的題目
  會被丟掉，作業因此少一題（而且少的是哪一題不固定）
- 冠詞錯 → 直接教錯。孩子跟讀的就是 sent

三條全部是「加詞的人不會想到、但一定會踩」的類型，所以用測試擋，
不靠 code review。
"""

from __future__ import annotations

import re
from collections import Counter

import pytest

from server import curriculum_data as cd
from server import scaffold

VOCAB = scaffold.VOCAB
_CATS = {"food", "school", "animal", "family", "action", "color"}


def test_every_entry_has_the_full_schema():
    for zh, entry in VOCAB.items():
        assert set(entry) == {"en", "cat", "np", "sent"}, f"{zh} 欄位不齊：{entry}"
        for key, val in entry.items():
            assert isinstance(val, str) and val.strip(), f"{zh}.{key} 空值"
        assert entry["cat"] in _CATS, f"{zh} 分類非法：{entry['cat']}"


def test_english_words_are_unique():
    """en 重複會讓 profile._EN_INFO 的反查表後蓋前。"""
    dupes = [en for en, n in Counter(v["en"] for v in VOCAB.values()).items() if n > 1]
    assert dupes == [], f"英文詞重複：{dupes}"


def test_target_sentences_are_unique():
    """sent 重複會被 homework 的去重靜默丟掉一題。"""
    dupes = [s for s, n in Counter(v["sent"] for v in VOCAB.values()).items() if n > 1]
    assert dupes == [], f"目標句重複：{dupes}"


def test_chinese_keys_do_not_shadow_each_other_wrongly():
    """短詞是長詞的子字串沒關係（tokenizer 長詞優先），但要確定順序真的對。

    「書包」必須先於「書」被匹配，否則孩子說「書包」會被判成「書」。
    """
    assert scaffold._find_zh_vocab("我的書包很重") == ["書包"]
    assert scaffold._find_zh_vocab("我在看書") == ["書"]
    assert scaffold._find_zh_vocab("小狗跟狗") == ["小狗", "狗"]


@pytest.mark.parametrize("zh,entry", sorted(VOCAB.items()))
def test_article_matches_the_noun(zh, entry):
    """np 的冠詞要對：a 接子音、an 接母音；不可數用 some。"""
    np = entry["np"]
    if np.startswith("an "):
        head = np[3:].split()[0].lower()
        assert head[0] in "aeiou", f"{zh}：an {head} 的開頭不是母音字母"
    elif np.startswith("a "):
        head = np[2:].split()[0].lower()
        # 母音字母開頭卻用 a 的，只有發音是子音的例外（uniform / one…）才合法
        if head[0] in "aeiou":
            assert head.startswith(scaffold._A_EXCEPTIONS), f"{zh}：a {head} 應為 an"


def test_sentence_contains_its_own_word():
    """目標句一定要用到這個詞，不然練的是別的東西。"""
    for zh, entry in VOCAB.items():
        head = entry["np"].split()[-1].lower()
        sent = entry["sent"].lower()
        assert entry["en"].lower() in sent or head in sent, \
            f"{zh}：目標句沒有用到 {entry['en']}／{head} → {entry['sent']}"


def test_sentences_are_well_formed():
    for zh, entry in VOCAB.items():
        sent = entry["sent"]
        assert sent[0].isupper(), f"{zh}：目標句要大寫開頭 → {sent}"
        assert sent[-1] in ".!?", f"{zh}：目標句要有句尾標點 → {sent}"
        assert len(sent.split()) <= 8, f"{zh}：國小目標句太長（>8 詞）→ {sent}"


# ---------------------------------------------------------------------------
# 對課綱的依據（與 test_curriculum_data.py 的覆蓋率測試互補）
# ---------------------------------------------------------------------------

def test_every_word_is_in_the_official_vocabulary_list():
    """每個 en 都要在教育部參考字彙表（2,000 字）內。"""
    official = set(w.lower() for w in cd.basic_vocab() + cd.extra_vocab())
    for zh, entry in VOCAB.items():
        en = entry["en"].lower()
        assert cd.is_basic(en) or en in official, \
            f"{zh}／{entry['en']} 不在教育部參考字彙表內"


def test_target_sentences_only_use_official_words():
    """例句用字也要是課綱字彙——目標句是孩子要跟讀的，不能夾帶課綱外的詞。"""
    official = set()
    for entry in cd.basic_vocab() + cd.extra_vocab():
        official.add(entry.strip().lower())
        official.add(entry.split("(")[0].strip().lower())
        if "(" in entry:
            for alt in entry[entry.find("(") + 1:entry.rfind(")")].split(","):
                official.add(alt.strip().lower())
    # 唯一的放行：官方表把 a 與 an 併成 "a/an" 一筆，拆不出來。
    # 其他功能詞（I / to / my / this / is / the / up / down / with…）
    # 本來就各自在表裡，不需要放行——放寬會讓這條測試失去意義。
    official |= {"a", "an"}

    def known(word: str) -> bool:
        w = word.lower()
        cands = [w, re.sub(r"ies$", "y", w), re.sub(r"es$", "", w),
                 re.sub(r"s$", "", w), re.sub(r"ing$", "", w), re.sub(r"ed$", "", w)]
        return any(c in official for c in cands)

    unknown = {
        (w, entry["sent"])
        for entry in VOCAB.values()
        for w in re.findall(r"[A-Za-z']+", entry["sent"])
        if not known(w)
    }
    assert unknown == set(), f"例句用到課綱外的字：{sorted(unknown)}"


def test_vocab_is_big_enough_to_defend():
    """44 個示範詞撐不住「教材依據是什麼」。訂一條下限，避免有人手滑刪回去。"""
    assert len(VOCAB) >= 120, f"詞庫只剩 {len(VOCAB)} 個詞"
    per_cat = Counter(v["cat"] for v in VOCAB.values())
    for cat in _CATS:
        assert per_cat[cat] >= 8, f"分類 {cat} 只有 {per_cat[cat]} 個詞，出題會重複"


def test_uncountable_nouns_are_excluded_from_plural_fixing():
    """"two rice" 不該被改成 "two rices"——教錯比不教更糟。"""
    for word in ("bread", "rice", "water", "milk", "soup", "cheese", "juice"):
        assert word not in scaffold._EN_NOUNS, f"{word} 是不可數名詞，不該進複數修正表"
    for word in ("dog", "book", "apple", "pencil"):
        assert word in scaffold._EN_NOUNS, f"{word} 是可數名詞，應該進複數修正表"
