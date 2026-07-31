# -*- coding: utf-8 -*-
"""test_app_material.py — POST /api/material 端點。"""

from __future__ import annotations

from starlette.testclient import TestClient

from server import app as app_mod, auth, scaffold, store


def _tok(sub, role):
    return auth.issue_token(sub, role)


def test_requires_token():
    client = TestClient(app_mod.app)
    resp = client.post("/api/material", json={"title": "t", "text": "動物園"})
    assert resp.status_code == 401


def test_student_role_forbidden():
    client = TestClient(app_mod.app)
    h = {"Authorization": f"Bearer {_tok('STUDENT-AMING-004', 'student')}"}
    resp = client.post("/api/material", json={"title": "t", "text": "動物園"},
                       headers=h)
    assert resp.status_code == 403


def test_tutor_can_upload_material_offline_rule_path():
    """network_mode 預設是 edge/測試環境沒有雲端設定，走規則式，仍要回合法 schema。"""
    snapshot = {zh: dict(v) for zh, v in scaffold.VOCAB.items()}
    try:
        client = TestClient(app_mod.app)
        h = {"Authorization": f"Bearer {_tok('TUTOR-001', 'tutor')}"}
        app_mod.pipeline.network_mode = "edge"

        resp = client.post("/api/material",
                           json={"title": "動物園教材", "text": "今天去看了獅子和大象。"},
                           headers=h)

        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "rule"
        assert "topic" in body and "accepted_count" in body and "rejected_count" in body
    finally:
        scaffold.VOCAB.clear()
        scaffold.VOCAB.update(snapshot)


def test_uploaded_material_is_persisted():
    client = TestClient(app_mod.app)
    h = {"Authorization": f"Bearer {_tok('TUTOR-001', 'tutor')}"}
    app_mod.pipeline.network_mode = "edge"

    client.post("/api/material", json={"title": "動物園教材", "text": "獅子"},
               headers=h)

    rows = store.list_materials()
    assert len(rows) == 1
    assert rows[0]["title"] == "動物園教材"


def test_lifespan_replay_merges_stored_materials_into_vocab():
    """啟動時 replay：DB 裡已有的教材詞條要重新合併回 scaffold.VOCAB。"""
    snapshot = {zh: dict(v) for zh, v in scaffold.VOCAB.items()}
    try:
        store.add_material({
            "title": "舊教材", "text": "...", "topic": "動物",
            "entries": [{"zh": "無尾熊", "en": "koala", "cat": "animal",
                        "np": "a koala", "sent": "I see a koala."}],
            "accepted_count": 1, "rejected_count": 0, "source": "cloud",
        })
        assert "無尾熊" not in scaffold.VOCAB  # replay 前確認還沒合併

        app_mod._replay_materials()

        assert "無尾熊" in scaffold.VOCAB
        assert scaffold.VOCAB["無尾熊"]["en"] == "koala"
    finally:
        scaffold.VOCAB.clear()
        scaffold.VOCAB.update(snapshot)
