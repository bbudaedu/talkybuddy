# -*- coding: utf-8 -*-
"""test_diagnose_relay.py — diagnose 的雲端呼叫整合測試（cfg 由 anthropic_relay 提供）。

驗證抽出共用解析後，_call_anthropic_api 仍依 cfg 命中中轉 url、Bearer、覆蓋 model。
"""
from __future__ import annotations

import pytest

from server import anthropic_relay, diagnose

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


def test_call_anthropic_api_uses_resolved_config(_clean_env, monkeypatch):
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "sk-relay-token")
    _clean_env.setenv("ANTHROPIC_BASE_URL", "http://192.168.100.200:8317")
    _clean_env.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4-8")

    captured = {}

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            import json

            valid = {
                "scores": {"pronunciation": 60, "fluency": 60,
                           "vocabulary": 60, "grammar": 60},
                "strengths": ["願意開口"],
                "weaknesses": ["句子偏短"],
                "emotional_status": "穩定",
                "instructions": {"classroom": "a", "device": "b", "peer": "c"},
            }
            return json.dumps(
                {"content": [{"type": "text", "text": json.dumps(valid)}]}
            ).encode("utf-8")

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        import json as _j
        captured["body"] = _j.loads(req.data.decode("utf-8"))
        return _FakeResp()

    monkeypatch.setattr(diagnose.urllib.request, "urlopen", _fake_urlopen)

    cfg = anthropic_relay.resolve_config()
    out = diagnose._call_anthropic_api([], None, cfg)

    assert captured["url"] == "http://192.168.100.200:8317/v1/messages"
    hdrs = {k.lower(): v for k, v in captured["headers"].items()}
    assert hdrs.get("authorization") == "Bearer sk-relay-token"
    assert "x-api-key" not in hdrs
    assert captured["body"]["model"] == "claude-opus-4-8"
    assert out["scores"]["fluency"] == 60
