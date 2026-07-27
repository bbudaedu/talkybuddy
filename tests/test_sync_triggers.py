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
from starlette.testclient import TestClient

from server import app as app_mod, auth, config, diagnose, store, sync_client
from server.pipeline import VoicePipeline

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _tok(sub="TUTOR-001", role="tutor"):
    return auth.issue_token(sub, role)


class _StubASR:
    def available(self) -> bool:
        return False

    def transcribe(self, wav_path):
        return ("", 0.0)


class _StubLLM:
    def available(self) -> bool:
        return False

    def generate(self, *args, **kwargs):
        return None


class _StubTTS:
    def available(self) -> bool:
        return False

    def synth(self, segments):
        return b""


def _make_pipeline() -> VoicePipeline:
    return VoicePipeline(_StubASR(), _StubLLM(), _StubTTS())

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


# --- Task 2: D-03(a) — network_mode edge→cloud 轉換瞬間觸發 ---


def test_network_mode_cloud_syncs_pending_and_reports_count():
    store.add_interaction(
        {"student_text": "t1", "device_id": "D1", "client_ts": "2026-07-10T10:00:00"}
    )
    store.add_interaction(
        {"student_text": "t2", "device_id": "D1", "client_ts": "2026-07-10T10:01:00"}
    )
    client = TestClient(app_mod.app)
    h = {"Authorization": f"Bearer {_tok()}"}

    res = client.post("/api/network_mode", json={"mode": "cloud"}, headers=h)

    assert res.status_code == 200
    body = res.json()
    assert body["synced"] == 2
    assert store.pending_count() == 0


def test_network_mode_edge_never_syncs():
    store.add_interaction(
        {"student_text": "t1", "device_id": "D1", "client_ts": "2026-07-10T10:00:00"}
    )
    client = TestClient(app_mod.app)
    h = {"Authorization": f"Bearer {_tok()}"}

    res = client.post("/api/network_mode", json={"mode": "edge"}, headers=h)

    assert res.status_code == 200
    assert store.pending_count() == 1


def test_network_mode_cloud_without_consent_blocks_sync(monkeypatch):
    store.add_interaction(
        {"student_text": "t1", "device_id": "D1", "client_ts": "2026-07-10T10:00:00"}
    )
    monkeypatch.setattr(config, "CONSENT_GRANTED", False)
    client = TestClient(app_mod.app)
    h = {"Authorization": f"Bearer {_tok()}"}

    res = client.post("/api/network_mode", json={"mode": "cloud"}, headers=h)

    assert res.status_code == 200
    body = res.json()
    assert body["consent_required"] is True
    assert body["network_mode"] == "edge"
    assert store.pending_count() == 1


def test_network_mode_requires_token():
    client = TestClient(app_mod.app)
    res = client.post("/api/network_mode", json={"mode": "cloud"})
    assert res.status_code == 401


def test_network_mode_cloud_syncs_even_when_diagnosis_raises(monkeypatch):
    store.add_interaction(
        {"student_text": "t1", "device_id": "D1", "client_ts": "2026-07-10T10:00:00"}
    )

    def boom(*args, **kwargs):
        raise RuntimeError("diagnosis backend unavailable")

    monkeypatch.setattr(diagnose, "generate_diagnosis", boom)
    client = TestClient(app_mod.app)
    h = {"Authorization": f"Bearer {_tok()}"}

    res = client.post("/api/network_mode", json={"mode": "cloud"}, headers=h)

    assert res.status_code == 200
    body = res.json()
    assert body["synced"] == 1
    assert body["new_diagnosis"] is None
    assert store.pending_count() == 0


# --- Task 3: D-03(b) — 回合結束的兜底觸發（直接測 _opportunistic_sync，較穩定）---


async def test_pipeline_opportunistic_sync_cloud_syncs_offline_pending():
    store.add_interaction(
        {"student_text": "t1", "device_id": "D1", "client_ts": "2026-07-10T10:00:00"}
    )
    vp = _make_pipeline()
    vp.network_mode = "cloud"

    await vp._opportunistic_sync()

    assert store.pending_count() == 0


async def test_pipeline_opportunistic_sync_edge_leaves_pending_untouched():
    store.add_interaction(
        {"student_text": "t1", "device_id": "D1", "client_ts": "2026-07-10T10:00:00"}
    )
    vp = _make_pipeline()
    vp.network_mode = "edge"

    await vp._opportunistic_sync()

    assert store.pending_count() == 1


async def test_pipeline_process_text_no_pending_skips_background_task(monkeypatch):
    """cloud 模式但 pending_count() 為 0 → 不建立背景任務（不做無謂工作）。"""
    created = []
    real_create_task = __import__("asyncio").create_task

    def spy_create_task(coro, *args, **kwargs):
        created.append(coro)
        return real_create_task(coro, *args, **kwargs)

    monkeypatch.setattr("asyncio.create_task", spy_create_task)
    vp = _make_pipeline()
    vp.network_mode = "cloud"

    async def _emit(_payload):
        return None

    await vp.run_turn_text("hello there friend", _emit)

    assert store.pending_count() == 0
    assert not any(
        getattr(c, "__qualname__", "").endswith("_opportunistic_sync") for c in created
    )


async def test_pipeline_opportunistic_sync_exception_does_not_raise(monkeypatch):
    """背景任務拋例外 → 不影響呼叫端（不 raise），且有 _log.exception 記錄。"""
    store.add_interaction(
        {"student_text": "t1", "device_id": "D1", "client_ts": "2026-07-10T10:00:00"}
    )
    logged = []

    def fake_opportunistic_sync():
        raise RuntimeError("boom")

    monkeypatch.setattr(sync_client, "opportunistic_sync", fake_opportunistic_sync)
    monkeypatch.setattr(
        "server.pipeline._log.exception", lambda *a, **k: logged.append(a)
    )
    vp = _make_pipeline()
    vp.network_mode = "cloud"

    await vp._opportunistic_sync()  # 不應拋出

    assert logged, "應呼叫 _log.exception 記錄失敗"


async def test_pipeline_opportunistic_sync_reentrancy_guard():
    """_sync_pushing 為 True 時，第二次呼叫直接返回，不做任何同步。"""
    store.add_interaction(
        {"student_text": "t1", "device_id": "D1", "client_ts": "2026-07-10T10:00:00"}
    )
    vp = _make_pipeline()
    vp.network_mode = "cloud"
    vp._sync_pushing = True

    await vp._opportunistic_sync()

    assert store.pending_count() == 1  # 被旗標擋下，未同步
    assert vp._sync_pushing is True  # 提早 return，未進 finally 復位
