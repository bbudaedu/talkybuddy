# -*- coding: utf-8 -*-
"""D-03 兩層機會式觸發的測試：

(a) network_mode 由 edge 轉 cloud 的瞬間立即補傳一次（掛在
    ``app.py::api_network_mode`` 的 cloud 分支）；
(b) 每回合結束時若為 cloud 且仍有 pending 就補一次（掛在
    ``pipeline.VoicePipeline._process_text`` 回合尾，見
    ``VoicePipeline._opportunistic_sync``）。

兩層都收斂到 ``sync_client.opportunistic_sync()`` 這個唯一入口，本檔同時
覆蓋該入口本身的五個 behavior（Task 1）。
"""

from __future__ import annotations

import pytest

from server import config, store, sync_client

# --- Task 1: sync_client.opportunistic_sync() 統一入口 ---


def test_opportunistic_sync_no_pending_returns_zero_and_noop():
    called = []

    def fake_post(url, json, headers):
        called.append(url)
        return {"accepted": 0, "skipped": 0}

    res = sync_client.opportunistic_sync(base_url="http://cloud", token="tok", http_post=fake_post)
    assert res == {"synced": 0}
    assert called == []


def test_opportunistic_sync_consent_not_granted_leaves_pending(monkeypatch):
    store.add_interaction(
        {"student_text": "t1", "device_id": "D1", "client_ts": "2026-07-10T10:00:00"}
    )
    store.add_interaction(
        {"student_text": "t2", "device_id": "D1", "client_ts": "2026-07-10T10:01:00"}
    )
    monkeypatch.setattr(config, "CONSENT_GRANTED", False)

    res = sync_client.opportunistic_sync()

    assert res.get("consent_required") is True
    assert store.pending_count() == 2


def test_opportunistic_sync_local_path_marks_all_pending_synced(monkeypatch):
    for i in range(3):
        store.add_interaction(
            {"student_text": f"t{i}", "device_id": "D1", "client_ts": f"2026-07-10T10:0{i}:00"}
        )
    monkeypatch.setattr(config, "CONSENT_GRANTED", True)

    res = sync_client.opportunistic_sync()

    assert res == {"synced": 3}
    assert store.pending_count() == 0


def test_opportunistic_sync_remote_path_delegates_to_push_pending(monkeypatch):
    for i in range(2):
        store.add_interaction(
            {"student_text": f"t{i}", "device_id": "D1", "client_ts": f"2026-07-10T10:0{i}:00"}
        )
    monkeypatch.setattr(config, "CONSENT_GRANTED", True)
    captured = {}

    def fake_post(url, json, headers):
        captured["interactions"] = json["interactions"]
        return {"accepted": 2, "skipped": 0}

    res = sync_client.opportunistic_sync(base_url="http://cloud", token="tok", http_post=fake_post)

    assert res == {"synced": 2}
    assert "interactions" in captured
    for item in captured["interactions"]:
        assert set(item) <= sync_client.UPLOAD_FIELDS
    assert store.pending_count() == 0


def test_opportunistic_sync_never_raises_on_garbage_transport(monkeypatch):
    store.add_interaction(
        {"student_text": "t1", "device_id": "D1", "client_ts": "2026-07-10T10:00:00"}
    )
    monkeypatch.setattr(config, "CONSENT_GRANTED", True)

    def bad_post(*args, **kwargs):
        raise RuntimeError("boom")

    res = sync_client.opportunistic_sync(base_url="http://cloud", token="tok", http_post=bad_post)

    assert res == {"synced": 0, "error": True}
