# -*- coding: utf-8 -*-
"""test_gemini_llm.py — Gemini 直連 provider 的契約。

全程 monkeypatch urlopen、不觸網。重點釘住三件事：

1. 送出去的 body 形狀對（`systemInstruction` / `contents` / `generationConfig`）
2. 金鑰走 header 不走 query string——query string 會被各層 proxy 記下來
3. 被安全機制擋下（candidate 沒有 parts）時**拋例外**而不是回空字串，
   呼叫端才分得出「被擋」與「模型回空」
"""
from __future__ import annotations

import json

import pytest

from server import gemini_llm

_ENV = ["GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_MODEL", "GEMINI_BASE_URL"]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _capture(monkeypatch, payload: dict) -> dict:
    seen: dict = {}

    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    def _fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = dict(req.headers)
        seen["body"] = json.loads(req.data.decode("utf-8")) if req.data else None
        return _R()

    monkeypatch.setattr(gemini_llm.urllib.request, "urlopen", _fake_urlopen)
    return seen


def _ok(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def test_resolve_config_none_without_key(_clean_env):
    assert gemini_llm.resolve_config() is None


def test_resolve_config_accepts_either_env_name(_clean_env):
    _clean_env.setenv("GOOGLE_API_KEY", "k1")
    assert gemini_llm.resolve_config() is not None
    _clean_env.setenv("GEMINI_API_KEY", "k2")
    # GEMINI_API_KEY 優先
    assert gemini_llm.resolve_config()["headers"]["x-goog-api-key"] == "k2"


def test_blank_key_is_not_a_key(_clean_env):
    _clean_env.setenv("GEMINI_API_KEY", "   ")
    assert gemini_llm.resolve_config() is None


def test_key_goes_in_header_not_query_string(_clean_env, monkeypatch):
    """金鑰不可出現在 URL —— 會被各層存取紀錄留下來。"""
    _clean_env.setenv("GEMINI_API_KEY", "super-secret")
    seen = _capture(monkeypatch, _ok("好"))

    gemini_llm.generate_text("s", "u", cfg=gemini_llm.resolve_config())

    assert "super-secret" not in seen["url"]
    assert seen["headers"]["X-goog-api-key"] == "super-secret"


def test_request_body_shape(_clean_env, monkeypatch):
    _clean_env.setenv("GEMINI_API_KEY", "k")
    _clean_env.setenv("GEMINI_MODEL", "gemini-test")
    seen = _capture(monkeypatch, _ok("好"))

    gemini_llm.generate_text(
        "你是玩偶", "學生說：我想要蘋果", cfg=gemini_llm.resolve_config(),
        max_tokens=160, temperature=0.5,
    )

    assert seen["url"].endswith("/models/gemini-test:generateContent")
    body = seen["body"]
    assert body["systemInstruction"]["parts"][0]["text"] == "你是玩偶"
    assert body["contents"][0]["parts"][0]["text"] == "學生說：我想要蘋果"
    assert body["generationConfig"] == {"maxOutputTokens": 160, "temperature": 0.5}


def test_extracts_and_joins_all_text_parts(_clean_env, monkeypatch):
    _clean_env.setenv("GEMINI_API_KEY", "k")
    _capture(monkeypatch, {"candidates": [{"content": {"parts": [
        {"text": "很好！"}, {"text": "跟我說一遍：I want an apple."},
    ]}}]})

    out = gemini_llm.generate_text("s", "u", cfg=gemini_llm.resolve_config())

    assert out == "很好！跟我說一遍：I want an apple."


def test_blocked_response_raises_not_returns_empty(_clean_env, monkeypatch):
    """安全機制擋下時 candidate 沒有 parts —— 那是「被擋」不是「回了空字串」。"""
    _clean_env.setenv("GEMINI_API_KEY", "k")
    _capture(monkeypatch, {"candidates": [{"finishReason": "SAFETY"}]})

    with pytest.raises(gemini_llm.GeminiResponseError) as exc:
        gemini_llm.generate_text("s", "u", cfg=gemini_llm.resolve_config())
    assert "SAFETY" in str(exc.value)


def test_empty_candidates_raises(_clean_env, monkeypatch):
    _clean_env.setenv("GEMINI_API_KEY", "k")
    _capture(monkeypatch, {"candidates": []})

    with pytest.raises(gemini_llm.GeminiResponseError):
        gemini_llm.generate_text("s", "u", cfg=gemini_llm.resolve_config())


def test_base_url_is_overridable(_clean_env, monkeypatch):
    """自架代理／測試要能改端點。"""
    _clean_env.setenv("GEMINI_API_KEY", "k")
    _clean_env.setenv("GEMINI_BASE_URL", "http://127.0.0.1:9999/v1beta/")
    seen = _capture(monkeypatch, _ok("好"))

    gemini_llm.generate_text("s", "u", cfg=gemini_llm.resolve_config())

    assert seen["url"].startswith("http://127.0.0.1:9999/v1beta/models/")
