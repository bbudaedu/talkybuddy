# -*- coding: utf-8 -*-
"""test_store_materials.py — 教材上傳持久化（server/store.py 新增部分）。

tmp_db fixture（見 tests/conftest.py，autouse）已經把 DB 導向乾淨的 tmp 檔案，
本檔不需要自己處理隔離。
"""

from __future__ import annotations

from server import store


def test_add_material_returns_incrementing_seq():
    seq1 = store.add_material({"title": "動物園教材", "text": "...", "topic": "動物",
                                "entries": [], "accepted_count": 0,
                                "rejected_count": 0, "source": "rule"})
    seq2 = store.add_material({"title": "餐廳教材", "text": "...", "topic": "食物",
                                "entries": [], "accepted_count": 0,
                                "rejected_count": 0, "source": "rule"})
    assert seq2 == seq1 + 1


def test_list_materials_returns_oldest_first_with_full_payload():
    store.add_material({"title": "第一份", "text": "t1", "topic": "動物",
                         "entries": [{"zh": "無尾熊", "en": "koala", "cat": "animal",
                                      "np": "a koala", "sent": "I see a koala."}],
                         "accepted_count": 1, "rejected_count": 0, "source": "cloud"})
    store.add_material({"title": "第二份", "text": "t2", "topic": "食物",
                         "entries": [], "accepted_count": 0,
                         "rejected_count": 0, "source": "rule"})

    rows = store.list_materials()

    assert len(rows) == 2
    assert rows[0]["title"] == "第一份"  # 舊→新
    assert rows[1]["title"] == "第二份"
    assert rows[0]["entries"][0]["zh"] == "無尾熊"
    assert "seq" in rows[0] and "ts" in rows[0]


def test_list_materials_empty_when_none_uploaded():
    assert store.list_materials() == []
