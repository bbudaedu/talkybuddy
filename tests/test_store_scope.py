# -*- coding: utf-8 -*-
from __future__ import annotations

from server import store


def test_list_interactions_filters_by_student():
    store.add_interaction({"student_text": "hi A", "student_id": "STU-A"})
    store.add_interaction({"student_text": "hi B", "student_id": "STU-B"})

    only_a = store.list_interactions(student_id="STU-A")
    assert len(only_a) == 1
    assert only_a[0]["student_id"] == "STU-A"

    all_rows = store.list_interactions()  # 省略 → 全部
    assert len(all_rows) == 2
