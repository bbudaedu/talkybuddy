# -*- coding: utf-8 -*-
"""test_consent_gate.py — B4-5 家長同意 gate（consent）行為測試。

涵蓋 research/b_axis/B4_隱私與Guardrails.md §2.3／§4.1.5：
- app /api/network_mode：未同意切 cloud → 強制 edge-only、不同步/不產雲端診斷。
- diagnose.generate_diagnosis：未同意 → 即使有 ANTHROPIC_API_KEY 也不呼真 API
  （防禦縱深；背景 _refresh_directive 亦涵蓋此真正資料出境 chokepoint）。
"""

from __future__ import annotations

from starlette.testclient import TestClient

from server import app as app_mod
from server import config, diagnose, store


# ---------------------------------------------------------------------------
# 1. app 層 gate：/api/network_mode cloud 分支
# ---------------------------------------------------------------------------

def test_switch_cloud_denied_without_consent(monkeypatch):
    """未同意 → 切 cloud 被擋回 edge-only：不同步、不產診斷、旗標明示。"""
    monkeypatch.setattr(config, "CONSENT_GRANTED", False)
    monkeypatch.setattr(app_mod.pipeline, "network_mode", "edge")
    monkeypatch.setattr(app_mod.pipeline, "_directive", None)
    client = TestClient(app_mod.app)

    resp = client.post("/api/network_mode", json={"mode": "cloud"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["network_mode"] == "edge"          # 強制降級 edge-only
    assert body["consent_required"] is True         # 明示被擋原因
    assert body["synced"] == 0
    assert body["new_diagnosis"] is None
    # 副作用：pipeline 未進雲端模式、未寫入任何雲端診斷、未動 directive 快取
    assert app_mod.pipeline.network_mode == "edge"
    assert store.list_diagnoses() == []
    assert app_mod.pipeline._directive is None


def test_switch_cloud_allowed_with_consent(monkeypatch):
    """已同意（預設）→ 切 cloud 正常啟用，產出診斷並推進 directive。"""
    monkeypatch.setattr(config, "CONSENT_GRANTED", True)
    monkeypatch.setattr(app_mod.pipeline, "network_mode", "edge")
    monkeypatch.setattr(app_mod.pipeline, "_directive", None)
    client = TestClient(app_mod.app)

    resp = client.post("/api/network_mode", json={"mode": "cloud"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["network_mode"] == "cloud"
    assert body.get("consent_required") in (None, False)
    assert app_mod.pipeline.network_mode == "cloud"
    assert app_mod.pipeline._directive is not None


# ---------------------------------------------------------------------------
# 2. diagnose 層 gate：真正的雲端資料出境 chokepoint
# ---------------------------------------------------------------------------

def test_generate_diagnosis_skips_cloud_without_consent(monkeypatch):
    """未同意 → 即使有 API key 也絕不呼 _call_anthropic_api，改走本地規則式。"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(config, "CONSENT_GRANTED", False)

    def _must_not_call(*a, **k):
        raise AssertionError("未同意時不得呼叫雲端 API")

    monkeypatch.setattr(diagnose, "_call_anthropic_api", _must_not_call)

    result = diagnose.generate_diagnosis([], None)

    assert result is not None
    assert result.get("companion_directive")  # 本地規則式仍產出完整診斷


def test_generate_diagnosis_attempts_cloud_with_consent(monkeypatch):
    """已同意 + 有 API key + provider=anthropic → 會嘗試雲端（失敗再靜默 fallback 本地）。"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(config, "CONSENT_GRANTED", True)
    monkeypatch.setattr(config, "LLM_CLOUD_PROVIDER", "anthropic", raising=False)
    called = {"n": 0}

    def _fake_cloud(interactions, prev, api_key):
        called["n"] += 1
        raise RuntimeError("模擬雲端失敗 → fallback 本地")

    monkeypatch.setattr(diagnose, "_call_anthropic_api", _fake_cloud)

    result = diagnose.generate_diagnosis([], None)

    assert called["n"] == 1        # 有同意 → 有嘗試上雲
    assert result is not None      # 失敗後仍 fallback 出診斷
