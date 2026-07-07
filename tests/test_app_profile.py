# -*- coding: utf-8 -*-
"""test_app_profile.py — B2：切 cloud 時 build_profile → save_profile 接線。"""

from __future__ import annotations

from starlette.testclient import TestClient

from server import app as app_mod
from server import store


def test_switch_cloud_builds_and_saves_profile():
    """POST /api/network_mode cloud → 依互動建 profile 並存進 store。"""
    store.add_interaction({
        "student_text": "I see a cat and a dog",
        "asr_confidence": 0.9,
        "ai_response_text": "很好！",
        "scores": {"fluency": 60, "vocabulary": 62, "grammar": 58},
    })
    client = TestClient(app_mod.app)

    resp = client.post("/api/network_mode", json={"mode": "cloud"})

    assert resp.status_code == 200
    prof = store.get_profile()
    assert prof is not None
    assert prof["generated_by"] == "rule"
    assert prof["interaction_count"] >= 1
    assert prof["interests"][0]["topic"] == "animal"


def test_switch_edge_does_not_build_profile():
    """切回 edge → 不建 profile（保持異步、只在 cloud 時機更新）。"""
    client = TestClient(app_mod.app)

    client.post("/api/network_mode", json={"mode": "edge"})

    assert store.get_profile() is None
