# -*- coding: utf-8 -*-
"""test_cloud_llm.py — CloudLLM 雲端腦對話加值層（契約同 EdgeLLM）。

涵蓋：available 有無憑證；generate 成功命中 relay；上雲前去識別化；護欄命中→None；
目標句缺漏→自動補帶讀句；urlopen 例外→None。全程 monkeypatch urlopen、不觸網。
"""
from __future__ import annotations

import json

import pytest

from server import cloud_llm as cloud_llm_mod
from server.cloud_llm import CloudLLM

_ALL_ENV = [
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_MODEL",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ALL_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class _Sc:
    """假 ScaffoldResult：只需 target_sentence。"""
    target_sentence = "I like apples."


def _fake_resp(text: str):
    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(
                {"content": [{"type": "text", "text": text}]}
            ).encode("utf-8")

    return _R()


def test_available_false_without_credential(_clean_env):
    assert CloudLLM().available() is False


def test_available_true_with_credential(_clean_env):
    _clean_env.setenv("ANTHROPIC_API_KEY", "sk-x")
    assert CloudLLM().available() is True


def test_generate_success_hits_relay(_clean_env, monkeypatch):
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "sk-relay")
    _clean_env.setenv("ANTHROPIC_BASE_URL", "http://relay:8317")
    _clean_env.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4-8")
    captured = {}

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _fake_resp("你好棒！跟我說一遍：I like apples.")

    monkeypatch.setattr(cloud_llm_mod.urllib.request, "urlopen", _fake_urlopen)

    out = CloudLLM().generate("我喜歡蘋果", _Sc())
    assert out == "你好棒！跟我說一遍：I like apples."
    assert captured["url"] == "http://relay:8317/v1/messages"
    assert captured["headers"].get("authorization") == "Bearer sk-relay"
    assert captured["body"]["model"] == "claude-opus-4-8"


def test_generate_deidentifies_student_text(_clean_env, monkeypatch):
    _clean_env.setenv("ANTHROPIC_API_KEY", "sk-x")
    captured = {}

    def _fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _fake_resp("你好棒！跟我說一遍：I like apples.")

    monkeypatch.setattr(cloud_llm_mod.urllib.request, "urlopen", _fake_urlopen)

    CloudLLM().generate("我叫 Mimi", _Sc())
    user_content = captured["body"]["messages"][0]["content"]
    assert "Mimi" not in user_content
    assert "[名字]" in user_content


def test_generate_guardrail_reject_returns_none(_clean_env, monkeypatch):
    _clean_env.setenv("ANTHROPIC_API_KEY", "sk-x")

    def _fake_urlopen(req, timeout=None):
        return _fake_resp("任意輸出")

    monkeypatch.setattr(cloud_llm_mod.urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(cloud_llm_mod.guardrails, "passes_guardrail", lambda t: False)

    assert CloudLLM().generate("hi", _Sc()) is None


def test_generate_appends_missing_target(_clean_env, monkeypatch):
    _clean_env.setenv("ANTHROPIC_API_KEY", "sk-x")

    def _fake_urlopen(req, timeout=None):
        return _fake_resp("你今天好棒喔")  # 沒有目標英文句

    monkeypatch.setattr(cloud_llm_mod.urllib.request, "urlopen", _fake_urlopen)

    out = CloudLLM().generate("hi", _Sc())
    assert "跟我說一遍：I like apples." in out


def test_generate_urlopen_raises_returns_none(_clean_env, monkeypatch):
    _clean_env.setenv("ANTHROPIC_API_KEY", "sk-x")

    def _boom(req, timeout=None):
        raise TimeoutError("boom")

    monkeypatch.setattr(cloud_llm_mod.urllib.request, "urlopen", _boom)
    assert CloudLLM().generate("hi", _Sc()) is None
