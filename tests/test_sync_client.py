# -*- coding: utf-8 -*-
from __future__ import annotations

from server import config, store, sync_client


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


# --- project_for_upload（Task 2：D-01 + D-04 白名單投影）---


def test_project_for_upload_strips_latency_ms():
    """latency_ms 是裝置遙測、無教學價值，不在白名單內，必須被剝除。"""
    out = sync_client.project_for_upload({"student_text": "hi", "latency_ms": {"asr": 100}})
    assert "latency_ms" not in out


def test_project_for_upload_strips_unlisted_audio_path_field():
    """模擬日後有人新增音檔欄位：未列名欄位一律不送，這是 D-04 的驗收核心。"""
    out = sync_client.project_for_upload({"student_text": "hi", "audio_path": "/tmp/x.wav"})
    assert "audio_path" not in out


def test_project_for_upload_strips_seq_and_synced():
    """seq／synced 是本地狀態，接收端無意義，必須被剝除。"""
    out = sync_client.project_for_upload({"student_text": "hi", "seq": 3, "synced": True})
    assert "seq" not in out
    assert "synced" not in out


def test_project_for_upload_deidentifies_text_fields():
    out = sync_client.project_for_upload({"student_text": "我家電話 0912345678"})
    assert "0912345678" not in out["student_text"]
    assert "[數字]" in out["student_text"]


def test_project_for_upload_keeps_score_values_unmasked():
    """數值欄位不經 deidentify，否則會被 [數字] 破壞。"""
    out = sync_client.project_for_upload({"scores": {"pronunciation": 62.5}})
    assert out["scores"]["pronunciation"] == 62.5


def test_project_for_upload_maps_ts_to_client_ts_when_missing():
    out = sync_client.project_for_upload({"ts": "2026-07-27T10:00:00"})
    assert out["client_ts"] == "2026-07-27T10:00:00"
    assert "ts" not in out


def test_project_for_upload_keeps_existing_client_ts():
    out = sync_client.project_for_upload(
        {"ts": "2026-07-27T09:00:00", "client_ts": "2026-07-27T10:00:00"}
    )
    assert out["client_ts"] == "2026-07-27T10:00:00"


def test_project_for_upload_output_keys_are_subset_of_upload_fields():
    out = sync_client.project_for_upload(
        {
            "student_text": "hi",
            "scores": {"a": 1},
            "student_id": "S1",
            "latency_ms": {},
            "seq": 1,
            "synced": True,
        }
    )
    assert set(out) <= sync_client.UPLOAD_FIELDS


# --- push_pending()（Task 3：D-02 consent 閘門 + 部分失敗安全標記）---


def test_push_pending_blocks_network_when_consent_not_granted(monkeypatch):
    """consent 未授權時，http_post 完全未被呼叫，pending 全數留在佇列。"""
    store.add_interaction(
        {"student_text": "t1", "device_id": "D1", "client_ts": "2026-07-10T10:00:00"}
    )
    monkeypatch.setattr(config, "CONSENT_GRANTED", False)
    called = []

    def fake_post(url, json, headers):
        called.append(url)
        return {"accepted": 1, "skipped": 0}

    res = sync_client.push_pending("http://cloud", "tok", fake_post)
    assert called == []
    assert store.pending_count() == 1
    assert res.get("consent_required") is True


def test_push_pending_marks_synced_when_all_accepted(monkeypatch):
    store.add_interaction(
        {"student_text": "t1", "device_id": "D1", "client_ts": "2026-07-10T10:00:00"}
    )
    monkeypatch.setattr(config, "CONSENT_GRANTED", True)

    def fake_post(url, json, headers):
        return {"accepted": 1, "skipped": 0}

    sync_client.push_pending("http://cloud", "tok", fake_post)
    assert store.pending_count() == 0


def test_push_pending_partial_failure_leaves_all_pending(monkeypatch):
    """雲端只回收一部分（部分失敗）時，一筆都不標記，全數留在佇列等補傳（D-02 前置）。"""
    for i in range(3):
        store.add_interaction(
            {"student_text": f"t{i}", "device_id": "D1", "client_ts": f"2026-07-10T10:0{i}:00"}
        )
    monkeypatch.setattr(config, "CONSENT_GRANTED", True)

    def fake_post(url, json, headers):
        return {"accepted": 1, "skipped": 0}

    sync_client.push_pending("http://cloud", "tok", fake_post)
    assert store.pending_count() == 3


def test_push_pending_marks_synced_when_accepted_plus_skipped_covers_all(monkeypatch):
    """雲端回應 accepted+skipped 等於送出總數（全數已處理）時才標記已同步。"""
    for i in range(3):
        store.add_interaction(
            {"student_text": f"t{i}", "device_id": "D1", "client_ts": f"2026-07-10T10:0{i}:00"}
        )
    monkeypatch.setattr(config, "CONSENT_GRANTED", True)

    def fake_post(url, json, headers):
        return {"accepted": 1, "skipped": 2}

    sync_client.push_pending("http://cloud", "tok", fake_post)
    assert store.pending_count() == 0


def test_push_pending_no_pending_skips_network():
    called = []

    def fake_post(url, json, headers):
        called.append(url)
        return {"accepted": 0, "skipped": 0}

    res = sync_client.push_pending("http://cloud", "tok", fake_post)
    assert called == []
    assert res == {"accepted": 0, "skipped": 0}


def test_push_pending_sent_payload_keys_are_whitelisted(monkeypatch):
    store.add_interaction(
        {
            "student_text": "hi",
            "device_id": "D1",
            "client_ts": "2026-07-10T10:00:00",
            "latency_ms": {"asr": 100},
            "audio_path": "/tmp/x.wav",
        }
    )
    monkeypatch.setattr(config, "CONSENT_GRANTED", True)
    captured = {}

    def fake_post(url, json, headers):
        captured["interactions"] = json["interactions"]
        return {"accepted": 1, "skipped": 0}

    sync_client.push_pending("http://cloud", "tok", fake_post)
    for item in captured["interactions"]:
        assert set(item) <= sync_client.UPLOAD_FIELDS


def test_push_pending_does_not_mutate_local_sqlite_text(monkeypatch):
    """D-01：deidentify 只在上傳瞬間套用，本地 SQLite 原文不變。"""
    store.add_interaction(
        {
            "student_text": "我家電話 0912345678",
            "device_id": "D1",
            "client_ts": "2026-07-10T10:00:00",
        }
    )
    monkeypatch.setattr(config, "CONSENT_GRANTED", True)

    def fake_post(url, json, headers):
        return {"accepted": 1, "skipped": 0}

    sync_client.push_pending("http://cloud", "tok", fake_post)
    rows = store.list_interactions(limit=10)
    assert rows[0]["student_text"] == "我家電話 0912345678"
