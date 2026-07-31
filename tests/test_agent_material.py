# -*- coding: utf-8 -*-
"""test_agent_material.py — 教材提煉 agent（server/agents/material.py）測試集。

嚴格 TDD：規則式路徑先於雲端路徑測試。
"""

from __future__ import annotations


def test_rule_based_extract_finds_existing_vocab_by_chinese_key():
    """教材文字含既有詞庫的中文鍵 → 命中並回傳該詞條。"""
    from server.agents.material import _rule_based_extract

    result = _rule_based_extract("今天我們去動物園看獅子和大象。")

    assert result["source"] == "rule"
    assert result["rejected_count"] == 0
    hit_zh = {e["zh"] for e in result["entries"]}
    assert "獅子" in hit_zh
    assert "大象" in hit_zh
    assert result["accepted_count"] == len(result["entries"])


def test_rule_based_extract_finds_existing_vocab_by_english_word():
    """教材文字含既有詞庫的英文詞（不分大小寫）→ 也能命中。"""
    from server.agents.material import _rule_based_extract

    result = _rule_based_extract("Today we saw a Lion at the zoo.")

    hit_en = {e["en"] for e in result["entries"]}
    assert "lion" in hit_en


def test_rule_based_extract_never_invents_new_words():
    """規則式路徑絕不能回傳不在既有 VOCAB 裡的詞——就算文字裡有課綱外的字。"""
    from server.agents.material import _rule_based_extract
    from server.scaffold import VOCAB

    result = _rule_based_extract("我們今天學了 quokka 這個新單字，牠是一種可愛的動物。")

    for entry in result["entries"]:
        assert entry["zh"] in VOCAB, f"{entry} 不應是自創詞"
        assert VOCAB[entry["zh"]]["en"] == entry["en"]


def test_rule_based_extract_handles_no_match_without_raising():
    """教材文字完全沒有課綱詞彙 → 空 entries，不拋例外，仍是合法 schema。"""
    from server.agents.material import _rule_based_extract

    result = _rule_based_extract("這是一段完全沒有相關詞彙的中文。")

    assert result["entries"] == []
    assert result["accepted_count"] == 0
    assert result["rejected_count"] == 0
    assert isinstance(result["topic"], str) and result["topic"].strip()


def test_rule_based_extract_handles_empty_and_none_input():
    """空字串／None 輸入不拋例外。"""
    from server.agents.material import _rule_based_extract

    for bad_input in ("", None):
        result = _rule_based_extract(bad_input)
        assert result["source"] == "rule"
        assert result["entries"] == []


def test_rule_based_extract_caps_at_max_entries():
    """就算教材文字命中很多既有詞，也不超過上限（避免單次教材塞爆詞庫）。"""
    from server.agents.material import _rule_based_extract
    from server.scaffold import VOCAB

    # 把所有詞庫的中文鍵串成一段長文字，確保命中數遠超過上限
    all_keys_text = "、".join(VOCAB.keys())
    result = _rule_based_extract(all_keys_text)

    assert len(result["entries"]) <= 8
