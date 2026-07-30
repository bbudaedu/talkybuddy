# -*- coding: utf-8 -*-
"""test_app_status_bedrock.py — /api/status 需揭露雲端後端身分，而且不得說謊。

決賽現場操作員（與評審）靠 /api/status 判斷系統狀態。既有欄位只有
`cloud_llm`（布林），看不出對話大腦究竟走原生 Bedrock 還是 Anthropic relay
——而「大腦 100% 在 Bedrock」正是本輪的合規宣稱，看不見就無法當場佐證。

`cloud_provider` 欄位：`"bedrock"` | `"relay"` | `"none"`。

⚠️ 2026-07-30 修正語意：本欄位原本只讀環境變數，也就是「設定上會走哪條」。
那讓它**無法**擔任它被賦予的佐證角色——裝置實測 `cloud_provider="relay"`，
但 relay 指的 `127.0.0.1:8317` 上根本沒有行程在聽，雲端大腦從頭到尾沒被呼叫過。
現在 `cloud_provider` 回**最近一次成功呼叫實際走的後端**（沒有成功紀錄 → `"none"`），
設定讀數移到新欄位 `cloud_provider_configured`。
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from server.app import app, cloud_llm_engine

_ALL_ENV = [
    "TALKYBUDDY_CLOUD_PROVIDER", "BEDROCK_REGION", "BEDROCK_MODEL_ID",
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ALL_ENV:
        monkeypatch.delenv(name, raising=False)
    # cloud_llm_engine 是 app 的模組級單例，_last 會跨測試殘留。
    monkeypatch.setattr(cloud_llm_engine, "_last", None, raising=False)
    return monkeypatch


def _status() -> dict:
    with TestClient(app) as client:
        resp = client.get("/api/status")
        assert resp.status_code == 200
        return resp.json()


# ---------------------------------------------------------------------------
# cloud_provider_configured：設定讀數（原 cloud_provider 的語意）
# ---------------------------------------------------------------------------

def test_configured_none_without_any_backend(_clean_env):
    assert _status()["cloud_provider_configured"] == "none"


def test_configured_relay_with_anthropic_key(_clean_env):
    _clean_env.setenv("ANTHROPIC_API_KEY", "sk-x")
    assert _status()["cloud_provider_configured"] == "relay"


def test_configured_bedrock_when_selected(_clean_env):
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    assert _status()["cloud_provider_configured"] == "bedrock"


def test_configured_bedrock_wins_over_relay(_clean_env):
    """兩者都設定時顯示 bedrock，與 CloudLLM.generate 的實際優先序一致。"""
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    _clean_env.setenv("ANTHROPIC_API_KEY", "sk-x")
    assert _status()["cloud_provider_configured"] == "bedrock"


# ---------------------------------------------------------------------------
# cloud_provider：證據讀數（設定齊全但沒跑過／跑失敗都不得宣稱）
# ---------------------------------------------------------------------------

def test_cloud_provider_none_when_configured_but_never_called(_clean_env):
    """設定齊全但這次啟動還沒成功生成過 → 不得宣稱 bedrock。

    這正是裝置上發生的形狀：設定看起來完美，實際一次都沒通。
    """
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    body = _status()
    assert body["cloud_provider_configured"] == "bedrock"
    assert body["cloud_provider"] == "none"
    assert body["cloud_llm"] is False


def test_cloud_provider_none_after_failed_call(_clean_env):
    """設定 relay 但呼叫失敗（隧道沒建 → Connection refused）→ 仍是 none。"""
    _clean_env.setenv("ANTHROPIC_API_KEY", "sk-x")
    cloud_llm_engine._record(False, "relay", "URLError: Connection refused", 26)
    body = _status()
    assert body["cloud_provider_configured"] == "relay"
    assert body["cloud_provider"] == "none"
    assert body["cloud_llm"] is False
    assert "降級" in body["cloud_llm_detail"]


def test_cloud_provider_reports_verified_backend(_clean_env):
    """有成功紀錄 → 回實際走的後端，且 cloud_llm 為 true。"""
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    cloud_llm_engine._record(True, "bedrock", "ok", 812)
    body = _status()
    assert body["cloud_provider"] == "bedrock"
    assert body["cloud_llm"] is True
    assert "bedrock" in body["cloud_llm_detail"]


def test_existing_status_fields_unchanged(_clean_env):
    """既有欄位一個都不能少（web/index.html 與 teacher.html 在讀）。"""
    body = _status()
    for key in (
        "asr", "llm", "tts", "cloud_tts", "cloud_llm", "cloud_provider",
        "network_mode", "pending", "live_s2s",
    ):
        assert key in body, f"既有欄位 {key} 消失"
