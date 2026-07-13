# -*- coding: utf-8 -*-
"""test_anthropic_relay.py — 雲端腦端點/認證/model 由環境變數解析（純函式，不觸網）。

diagnose 與 cloud_llm 共用此模組。涵蓋：無憑證→None、x-api-key vs Bearer、
AUTH_TOKEN 優先、base_url 四型正規化、model override。
"""
from __future__ import annotations

import pytest

from server import anthropic_relay

_ALL_ENV = [
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_MODEL",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ALL_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_no_credential_returns_none(_clean_env):
    assert anthropic_relay.resolve_config() is None


def test_api_key_uses_official_endpoint_and_x_api_key(_clean_env):
    _clean_env.setenv("ANTHROPIC_API_KEY", "sk-official")
    cfg = anthropic_relay.resolve_config()
    assert cfg is not None
    assert cfg["url"] == anthropic_relay.DEFAULT_URL
    assert cfg["model"] == anthropic_relay.DEFAULT_MODEL
    assert cfg["headers"].get("x-api-key") == "sk-official"
    assert "Authorization" not in cfg["headers"]
    assert cfg["headers"].get("anthropic-version") == "2023-06-01"


def test_auth_token_uses_bearer(_clean_env):
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "sk-relay-token")
    cfg = anthropic_relay.resolve_config()
    assert cfg["headers"].get("Authorization") == "Bearer sk-relay-token"
    assert "x-api-key" not in cfg["headers"]


def test_auth_token_wins_over_api_key(_clean_env):
    _clean_env.setenv("ANTHROPIC_API_KEY", "sk-official")
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "sk-relay-token")
    cfg = anthropic_relay.resolve_config()
    assert cfg["headers"].get("Authorization") == "Bearer sk-relay-token"
    assert "x-api-key" not in cfg["headers"]


def test_base_url_override_appends_messages_path(_clean_env):
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "t")
    _clean_env.setenv("ANTHROPIC_BASE_URL", "http://192.168.100.200:8317")
    cfg = anthropic_relay.resolve_config()
    assert cfg["url"] == "http://192.168.100.200:8317/v1/messages"


def test_base_url_trailing_slash(_clean_env):
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "t")
    _clean_env.setenv("ANTHROPIC_BASE_URL", "http://relay:8317/")
    cfg = anthropic_relay.resolve_config()
    assert cfg["url"] == "http://relay:8317/v1/messages"


def test_base_url_already_has_v1(_clean_env):
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "t")
    _clean_env.setenv("ANTHROPIC_BASE_URL", "http://relay:8317/v1")
    cfg = anthropic_relay.resolve_config()
    assert cfg["url"] == "http://relay:8317/v1/messages"


def test_base_url_full_messages_path_used_as_is(_clean_env):
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "t")
    _clean_env.setenv("ANTHROPIC_BASE_URL", "http://relay:8317/v1/messages")
    cfg = anthropic_relay.resolve_config()
    assert cfg["url"] == "http://relay:8317/v1/messages"


def test_model_override(_clean_env):
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "t")
    _clean_env.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4-8")
    cfg = anthropic_relay.resolve_config()
    assert cfg["model"] == "claude-opus-4-8"
