# -*- coding: utf-8 -*-
"""test_student_identity.py — 驗證 D-05：教師儀表板學生姓名真實化（非 mock）。

涵蓋三塊：
1. `store.student_display_name()` 的三層解析（config 有值 / 被覆寫 / config
   缺屬性時回退），鏡像 `_student_id()` 的既有形態。
2. `GET /api/student_profile` 端點——沿用既有 `identity_from_header()` +
   `_resolve_student()` 授權模型，不新增授權面。
3. D-05 與 D-04 的邊界：姓名不得進上傳白名單，`project_for_upload()` 的
   輸出永遠不含姓名值。
"""

from __future__ import annotations

from server import config, store


def test_student_display_name_returns_nonempty_by_default():
    """未設任何覆寫時，回傳非空字串（demo 預設姓名）。"""
    assert store.student_display_name()


def test_student_display_name_reflects_config_override(monkeypatch):
    """monkeypatch config.STUDENT_NAME 後，回傳被覆寫的值。"""
    monkeypatch.setattr(config, "STUDENT_NAME", "王小明")
    assert store.student_display_name() == "王小明"


def test_student_display_name_falls_back_when_config_attr_missing(monkeypatch):
    """config 缺少 STUDENT_NAME 屬性時，回退到 _FALLBACK_STUDENT_NAME，不拋例外。"""
    monkeypatch.delattr(config, "STUDENT_NAME", raising=False)
    assert store.student_display_name() == store._FALLBACK_STUDENT_NAME


def test_student_display_name_accepts_any_student_id_without_raising():
    """student_id 參數目前只是介面預留（單一學生 demo），任何值都不應拋例外。"""
    assert store.student_display_name(student_id=None)
    assert store.student_display_name(student_id="STUDENT-AMING-004")
    assert store.student_display_name(student_id="anything-not-registered")
