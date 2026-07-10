# -*- coding: utf-8 -*-
from __future__ import annotations

from starlette.testclient import TestClient

from server import app as app_mod, auth, store


def _tok(sub, role):
    return auth.issue_token(sub, role)


def test_interactions_requires_token():
    client = TestClient(app_mod.app)
    assert client.get("/api/interactions").status_code == 401


def test_student_sees_only_own():
    store.add_interaction({"student_text": "mine", "student_id": "STUDENT-AMING-004"})
    store.add_interaction({"student_text": "other", "student_id": "STU-OTHER"})
    client = TestClient(app_mod.app)
    h = {"Authorization": f"Bearer {_tok('STUDENT-AMING-004', 'student')}"}
    rows = client.get("/api/interactions", headers=h).json()
    assert len(rows) == 1  # 非空保護：fixture 各插一筆本人/他人，過濾後應剩本人 1 筆
    assert all(r["student_id"] == "STUDENT-AMING-004" for r in rows)


def test_tutor_can_query_student():
    store.add_interaction({"student_text": "mine", "student_id": "STUDENT-AMING-004"})
    client = TestClient(app_mod.app)
    h = {"Authorization": f"Bearer {_tok('TUTOR-001', 'tutor')}"}
    rows = client.get("/api/interactions?student=STUDENT-AMING-004", headers=h).json()
    assert len(rows) == 1


def test_tutor_without_student_query_400():
    client = TestClient(app_mod.app)
    h = {"Authorization": f"Bearer {_tok('TUTOR-001', 'tutor')}"}
    assert client.get("/api/interactions", headers=h).status_code == 400
