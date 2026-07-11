# -*- coding: utf-8 -*-
from __future__ import annotations

from starlette.testclient import TestClient

from server import app as app_mod, auth, store


def _device_tok():
    # device 帳號 sub 綁 STUDENT-AMING-004
    return auth.issue_token("STUDENT-AMING-004", "device")


def test_sync_accepts_and_dedups():
    client = TestClient(app_mod.app)
    h = {"Authorization": f"Bearer {_device_tok()}"}
    batch = {"interactions": [
        {"student_text": "doll turn 1", "device_id": "GENIO-520-X992", "client_ts": "2026-07-10T10:00:00"},
        {"student_text": "doll turn 2", "device_id": "GENIO-520-X992", "client_ts": "2026-07-10T10:01:00"},
    ]}
    r1 = client.post("/api/sync", json=batch, headers=h)
    assert r1.json() == {"accepted": 2, "skipped": 0}

    # 重送同批 → 全部跳過
    r2 = client.post("/api/sync", json=batch, headers=h)
    assert r2.json() == {"accepted": 0, "skipped": 2}

    rows = store.list_interactions(student_id="STUDENT-AMING-004")
    assert len(rows) == 2
