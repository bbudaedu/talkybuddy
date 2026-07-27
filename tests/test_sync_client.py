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
