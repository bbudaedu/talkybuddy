# -*- coding: utf-8 -*-
from __future__ import annotations

from server import store, sync_client


def test_push_pending_sends_unsynced_and_marks():
    store.add_interaction({"student_text": "t1", "device_id": "D1", "client_ts": "2026-07-10T10:00:00"})
    sent = {}

    def fake_post(url, json, headers):
        sent["url"] = url
        sent["count"] = len(json["interactions"])
        return {"accepted": sent["count"], "skipped": 0}

    res = sync_client.push_pending("http://cloud", "tok", fake_post)
    assert sent["count"] == 1
    assert res["accepted"] == 1
    assert store.pending_count() == 0  # 已 mark_all_synced
