# -*- coding: utf-8 -*-
"""test_profile_store.py — B2：student_profile 表的 get_profile / save_profile 存取。"""

from __future__ import annotations

from server import config, store


def test_get_profile_returns_none_when_absent():
    """尚未存過 profile → get_profile 回 None（不炸）。"""
    assert store.get_profile("STUDENT-X") is None


def test_save_and_get_profile_roundtrip():
    """save_profile 後 get_profile 取回同一 payload。"""
    prof = {
        "student_id": "STUDENT-X",
        "updated_at": "2026-07-07T21:30:00+08:00",
        "interests": [{"topic": "animal", "label": "動物", "hits": 3}],
        "generated_by": "rule",
    }
    store.save_profile(prof)
    got = store.get_profile("STUDENT-X")
    assert got is not None
    assert got["student_id"] == "STUDENT-X"
    assert got["interests"][0]["topic"] == "animal"


def test_save_profile_replaces_existing():
    """同 student_id 再存 → 覆寫（單列 / 學生）。"""
    store.save_profile({"student_id": "STUDENT-X", "interaction_count": 1})
    store.save_profile({"student_id": "STUDENT-X", "interaction_count": 9})
    got = store.get_profile("STUDENT-X")
    assert got["interaction_count"] == 9


def test_get_profile_defaults_to_config_student_id():
    """省略 student_id → 用 config.STUDENT_ID。"""
    store.save_profile({"student_id": config.STUDENT_ID, "interaction_count": 5})
    got = store.get_profile()
    assert got is not None
    assert got["student_id"] == config.STUDENT_ID
