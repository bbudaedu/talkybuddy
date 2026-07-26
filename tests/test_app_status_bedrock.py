# -*- coding: utf-8 -*-
"""test_app_status_bedrock.py — /api/status 需揭露雲端後端身分。

決賽現場操作員（與評審）靠 /api/status 判斷系統狀態。既有欄位只有
`cloud_llm`（布林），看不出對話大腦究竟走原生 Bedrock 還是 Anthropic relay
——而「大腦 100% 在 Bedrock」正是本輪的合規宣稱，看不見就無法當場佐證。

新增 `cloud_provider` 欄位：`"bedrock"` | `"relay"` | `"none"`。
既有欄位一律不動（教師儀表板與 web/index.html 都在讀）。
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from server.app import app

_ALL_ENV = [
    "TALKYBUDDY_CLOUD_PROVIDER", "BEDROCK_REGION", "BEDROCK_MODEL_ID",
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ALL_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _status() -> dict:
    with TestClient(app) as client:
        resp = client.get("/api/status")
        assert resp.status_code == 200
        return resp.json()


def test_cloud_provider_none_without_any_backend(_clean_env):
    assert _status()["cloud_provider"] == "none"


def test_cloud_provider_relay_with_anthropic_key(_clean_env):
    _clean_env.setenv("ANTHROPIC_API_KEY", "sk-x")
    assert _status()["cloud_provider"] == "relay"


def test_cloud_provider_bedrock_when_selected(_clean_env):
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    assert _status()["cloud_provider"] == "bedrock"


def test_cloud_provider_bedrock_wins_over_relay(_clean_env):
    """兩者都設定時顯示 bedrock，與 CloudLLM.generate 的實際優先序一致。"""
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    _clean_env.setenv("ANTHROPIC_API_KEY", "sk-x")
    assert _status()["cloud_provider"] == "bedrock"


def test_existing_status_fields_unchanged(_clean_env):
    """既有欄位一個都不能少（web/index.html 與 teacher.html 在讀）。"""
    body = _status()
    for key in (
        "asr", "llm", "tts", "cloud_tts", "cloud_llm",
        "network_mode", "pending", "live_s2s",
    ):
        assert key in body, f"既有欄位 {key} 消失"
