# -*- coding: utf-8 -*-
"""test_material_integration.py — 教材詞合併後，既有 homework/games 零改動自動支援。

驗證 docs/superpowers/specs/2026-08-01-teacher-material-agent-design.md §3
的核心承諾：VOCAB 原地合併後，homework.py／games.py 完全不用改就看得到新詞。
"""

from __future__ import annotations


def test_homework_picks_up_newly_registered_word():
    """合併新詞到 animal 分類後，該分類產作業時新詞要有機會出現在候選池裡。"""
    from server import scaffold
    from server.agents.homework import _pick_vocab_entries

    snapshot = {zh: dict(v) for zh, v in scaffold.VOCAB.items()}
    try:
        accepted, rejected = scaffold.register_material_vocab([
            {"en": "koala", "zh": "無尾熊", "cat": "animal",
             "np": "a koala", "sent": "I see a koala."},
        ])
        assert rejected == 0 and len(accepted) == 1

        # animal 分類挑滿詞庫所有候選（n 設大一點確保新詞排得進來）
        entries = _pick_vocab_entries("pronunciation", n=100)
        zh_keys = {e["zh_key"] for e in entries}

        assert "無尾熊" in zh_keys, (
            "新合併的教材詞應出現在 homework 的候選池裡，"
            "若沒出現代表 homework._pick_vocab_entries 沒有即時讀 VOCAB"
        )
    finally:
        scaffold.VOCAB.clear()
        scaffold.VOCAB.update(snapshot)


def test_games_module_sees_new_word_via_shared_vocab_object():
    """games.py 讀的 scaffold.VOCAB 跟合併時操作的是同一個物件。"""
    from server import scaffold, games

    snapshot = {zh: dict(v) for zh, v in scaffold.VOCAB.items()}
    try:
        scaffold.register_material_vocab([
            {"en": "koala", "zh": "無尾熊", "cat": "animal",
             "np": "a koala", "sent": "I see a koala."},
        ])

        assert games.scaffold.VOCAB is scaffold.VOCAB, (
            "games.py 應該跟 scaffold.py 共用同一個 VOCAB 物件參照"
        )
        assert "無尾熊" in games.scaffold.VOCAB
    finally:
        scaffold.VOCAB.clear()
        scaffold.VOCAB.update(snapshot)
