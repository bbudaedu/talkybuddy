# -*- coding: utf-8 -*-
"""test_warmup_llama_server.py — 開機暖身呼叫（08-05 冷啟動延遲 mitigation）。

驗證：
- 成功呼叫回 True，且送出的 system prompt 與 EdgeLLM._SYSTEM_PROMPT 逐字相同
  （KV cache 靠前綴比對命中，兩處字串必須完全一致，不可各自維護一份）。
- urlopen 拋例外（連線被拒/逾時）→ 回 False，不拋出（暖身失敗不可讓開機掛掉）。
不觸網、不啟動真的 llama-server（monkeypatch urllib.request.urlopen）。
"""

from __future__ import annotations

import json

from edge.runtime import warmup_llama_server
from server.llm import EdgeLLM


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return b'{"choices":[{"message":{"content":"ok"}}]}'


def test_warmup_success_sends_matching_system_prompt(monkeypatch):
    captured = {}

    def _fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(warmup_llama_server.urllib.request, "urlopen", _fake_urlopen)

    assert warmup_llama_server.warmup("http://127.0.0.1:8080") is True
    system_msg = next(m for m in captured["body"]["messages"] if m["role"] == "system")
    assert system_msg["content"] == EdgeLLM._SYSTEM_PROMPT


def test_warmup_returns_false_on_connection_error(monkeypatch):
    def _raise(req, timeout=None):
        raise ConnectionRefusedError("no server listening")

    monkeypatch.setattr(warmup_llama_server.urllib.request, "urlopen", _raise)

    assert warmup_llama_server.warmup("http://127.0.0.1:8080") is False
