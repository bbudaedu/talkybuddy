# -*- coding: utf-8 -*-
"""test_llm.py — EdgeLLM.generate 的 directive 注入（B1）。

用 fake model 攔截送進 create_chat_completion 的 messages，驗證：
- directive=None → user_prompt 與現況一致（不含策略區塊）。
- 帶 directive → user_prompt 插入策略區塊。
- 護欄不變：target 未出現於回覆時仍補「跟我說一遍」帶讀句。
不載入真 llama_cpp 權重（monkeypatch _get_model）。
"""

from __future__ import annotations

import types

from server import scaffold as scaffold_mod
from server.llm import EdgeLLM


class _FakeModel:
    """攔截 messages 的假模型；回傳可設定內容。"""

    def __init__(self, reply: str):
        self.captured_messages = None
        self._reply = reply

    def create_chat_completion(self, messages, **kwargs):
        self.captured_messages = messages
        return {"choices": [{"message": {"content": self._reply}}]}


def _sc(target: str = "I like apples."):
    """最小 scaffold 結果：只需 target_sentence 屬性。"""
    return types.SimpleNamespace(target_sentence=target)


def _user_content(fake: _FakeModel) -> str:
    """取出 fake 攔截到的 user 訊息內容。"""
    return next(m["content"] for m in fake.captured_messages if m["role"] == "user")


def test_generate_without_directive_has_no_strategy_block(monkeypatch):
    """directive=None（預設）→ prompt 不含策略區塊，行為與現況一致。"""
    fake = _FakeModel("很棒！跟我說一遍：I like apples.")
    edge = EdgeLLM()
    monkeypatch.setattr(edge, "_get_model", lambda: fake)
    monkeypatch.setattr(scaffold_mod, "safety_check", lambda _t: False)

    out = edge.generate("我喜歡蘋果", _sc())

    assert out == "很棒！跟我說一遍：I like apples."
    assert "【本輪教學策略】" not in _user_content(fake)


def test_generate_with_directive_injects_strategy_block(monkeypatch):
    """帶 directive → user_prompt 含該策略字串。"""
    fake = _FakeModel("很棒！跟我說一遍：I like apples.")
    edge = EdgeLLM()
    monkeypatch.setattr(edge, "_get_model", lambda: fake)
    monkeypatch.setattr(scaffold_mod, "safety_check", lambda _t: False)

    directive = "【本輪教學策略】目標：升級句型；話題：喜歡的事物。"
    out = edge.generate("我喜歡蘋果", _sc(), directive)

    assert out is not None
    content = _user_content(fake)
    assert "【本輪教學策略】" in content
    assert "升級句型" in content


def test_generate_appends_target_when_missing(monkeypatch):
    """護欄：回覆漏掉目標句 → 自動補「跟我說一遍：<target>」。"""
    fake = _FakeModel("很棒喔，你好厲害！")  # 不含 target
    edge = EdgeLLM()
    monkeypatch.setattr(edge, "_get_model", lambda: fake)
    monkeypatch.setattr(scaffold_mod, "safety_check", lambda _t: False)

    out = edge.generate("我喜歡蘋果", _sc("I like apples."), "【本輪教學策略】...")

    assert "跟我說一遍：I like apples." in out


def test_generate_empty_directive_treated_as_none(monkeypatch):
    """空白 directive → 視同 None，不插入空區塊。"""
    fake = _FakeModel("很棒！跟我說一遍：I like apples.")
    edge = EdgeLLM()
    monkeypatch.setattr(edge, "_get_model", lambda: fake)
    monkeypatch.setattr(scaffold_mod, "safety_check", lambda _t: False)

    edge.generate("我喜歡蘋果", _sc(), "   ")

    assert "【本輪教學策略】" not in _user_content(fake)
