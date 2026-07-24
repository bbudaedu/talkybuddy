# -*- coding: utf-8 -*-
"""test_llm.py — EdgeLLM.generate 的 directive 注入（B1）+ HTTP client 降級語意（08-02）。

用 monkeypatch 攔截 EdgeLLM._call_llama_server（唯一 HTTP 呼叫點），驗證：
- directive=None → user_prompt 與現況一致（不含策略區塊）。
- 帶 directive → user_prompt 插入策略區塊。
- 護欄不變：target 未出現於回覆時仍補「跟我說一遍」帶讀句。
- available()：/health 連線失敗（例外）→ False，絕不拋出。
- generate()：_call_llama_server 拋例外 → 回 None（降級鏈不斷）。
不觸網、不啟動真的 llama-server（monkeypatch _call_llama_server / urlopen）。
"""

from __future__ import annotations

import types

from server import scaffold as scaffold_mod
from server.llm import EdgeLLM


def _sc(target: str = "I like apples."):
    """最小 scaffold 結果：只需 target_sentence 屬性。"""
    return types.SimpleNamespace(target_sentence=target)


def _fake_call_factory(reply: str):
    """建立捕捉 messages 的 _call_llama_server 替身；呼叫後 .captured 存最後一次 messages。"""

    def _fake_call(messages):
        _fake_call.captured = messages
        return reply

    _fake_call.captured = None
    return _fake_call


def _user_content(fake_call) -> str:
    """取出 fake_call 攔截到的 user 訊息內容。"""
    return next(m["content"] for m in fake_call.captured if m["role"] == "user")


def test_generate_without_directive_has_no_strategy_block(monkeypatch):
    """directive=None（預設）→ prompt 不含策略區塊，行為與現況一致。"""
    fake_call = _fake_call_factory("很棒！跟我說一遍：I like apples.")
    edge = EdgeLLM()
    monkeypatch.setattr(edge, "_call_llama_server", fake_call)
    monkeypatch.setattr(scaffold_mod, "safety_check", lambda _t: False)

    out = edge.generate("我喜歡蘋果", _sc())

    assert out == "很棒！跟我說一遍：I like apples."
    assert "【本輪教學策略】" not in _user_content(fake_call)


def test_generate_with_directive_injects_strategy_block(monkeypatch):
    """帶 directive → user_prompt 含該策略字串。"""
    fake_call = _fake_call_factory("很棒！跟我說一遍：I like apples.")
    edge = EdgeLLM()
    monkeypatch.setattr(edge, "_call_llama_server", fake_call)
    monkeypatch.setattr(scaffold_mod, "safety_check", lambda _t: False)

    directive = "【本輪教學策略】目標：升級句型；話題：喜歡的事物。"
    out = edge.generate("我喜歡蘋果", _sc(), directive)

    assert out is not None
    content = _user_content(fake_call)
    assert "【本輪教學策略】" in content
    assert "升級句型" in content


def test_generate_appends_target_when_missing(monkeypatch):
    """護欄：回覆漏掉目標句 → 自動補「跟我說一遍：<target>」。"""
    fake_call = _fake_call_factory("很棒喔，你好厲害！")  # 不含 target
    edge = EdgeLLM()
    monkeypatch.setattr(edge, "_call_llama_server", fake_call)
    monkeypatch.setattr(scaffold_mod, "safety_check", lambda _t: False)

    out = edge.generate("我喜歡蘋果", _sc("I like apples."), "【本輪教學策略】...")

    assert "跟我說一遍：I like apples." in out


def test_generate_empty_directive_treated_as_none(monkeypatch):
    """空白 directive → 視同 None，不插入空區塊。"""
    fake_call = _fake_call_factory("很棒！跟我說一遍：I like apples.")
    edge = EdgeLLM()
    monkeypatch.setattr(edge, "_call_llama_server", fake_call)
    monkeypatch.setattr(scaffold_mod, "safety_check", lambda _t: False)

    edge.generate("我喜歡蘋果", _sc(), "   ")

    assert "【本輪教學策略】" not in _user_content(fake_call)


def test_available_false_on_connection_error(monkeypatch):
    """/health 連線被拒/逾時（任何例外）→ available() 回 False，絕不拋出。"""
    from server import config

    # 指向一個幾乎不可能有服務在監聽的 port，urlopen 會拋 ConnectionError/URLError
    monkeypatch.setattr(config, "LLM_SERVER_PORT", 1)

    edge = EdgeLLM()

    assert edge.available() is False


def test_generate_returns_none_when_call_llama_server_raises(monkeypatch):
    """_call_llama_server 拋例外 → generate() 回 None，例外不逸出（降級鏈不斷）。"""

    def _raise(messages):
        raise ConnectionError("llama-server 未啟動")

    edge = EdgeLLM()
    monkeypatch.setattr(edge, "_call_llama_server", _raise)
    monkeypatch.setattr(scaffold_mod, "safety_check", lambda _t: False)

    out = edge.generate("我喜歡蘋果", _sc())

    assert out is None
