# -*- coding: utf-8 -*-
"""test_cloud_llm_verified.py — CloudLLM 的「設定齊全 ≠ 跑得動」分離。

背景（同一個坑咬了三次）：

- 2026-07-29 裝置：`.env` 的 `ANTHROPIC_BASE_URL` 指向反向隧道，隧道沒建
  → `generate()` 26ms 就 Connection refused，但 `/api/status` 的 `cloud_llm`
  一路是 true。斷網彩排量到的「M1 ≈ 0」是假的：不是降級很快，是沒上過雲。
- 2026-07-30 `cloud_tts` 同型問題（commit 6fc96d7 已用 `verified()` 修掉）。
- 2026-07-30 裝置：`cloud_provider="relay"`，而 8317 上沒有任何行程在聽。

本檔釘住 `available()`（設定）與 `verified()`（證據）不得再被混用。
全程 monkeypatch，不觸網。
"""
from __future__ import annotations

import types

import pytest

from server import anthropic_relay, bedrock_converse, guardrails
from server.cloud_llm import CloudLLM


def _sc(target: str = "I like apples."):
    return types.SimpleNamespace(target_sentence=target)


@pytest.fixture(autouse=True)
def _no_backend(monkeypatch):
    """預設兩個後端都沒設定；各測試自行打開需要的那個。"""
    monkeypatch.setattr(bedrock_converse, "resolve_config", lambda role=None: None)
    monkeypatch.setattr(anthropic_relay, "resolve_config", lambda: None)
    monkeypatch.setattr(guardrails, "passes_guardrail", lambda _t: True)
    return monkeypatch


def _use_bedrock(monkeypatch, text_or_exc):
    monkeypatch.setattr(
        bedrock_converse, "resolve_config",
        lambda role=None: {"region": "us-east-1", "model_id": "m"},
    )

    def _converse(*_a, **_k):
        if isinstance(text_or_exc, Exception):
            raise text_or_exc
        return text_or_exc

    monkeypatch.setattr(bedrock_converse, "converse_text", _converse)


# ---------------------------------------------------------------------------

def test_verified_false_before_any_call(_no_backend):
    """沒跑過 → 沒有證據 → 不得報綠燈。"""
    _use_bedrock(_no_backend, "很棒！跟我說一遍：I like apples.")

    llm = CloudLLM()

    assert llm.available() is True       # 設定齊全
    assert llm.verified() is False       # 但還沒有證據
    assert llm.verified_backend() == "none"
    assert "尚未驗證" in llm.status_detail()


def test_verified_true_after_successful_generate(_no_backend):
    _use_bedrock(_no_backend, "很棒！跟我說一遍：I like apples.")

    llm = CloudLLM()
    out = llm.generate("我喜歡蘋果", _sc())

    assert out is not None
    assert llm.verified() is True
    assert llm.verified_backend() == "bedrock"
    assert "可用" in llm.status_detail()


def test_verified_false_after_connection_refused(_no_backend):
    """隧道沒建的實況：available 仍 True，但 verified 必須是 False。"""
    _no_backend.setattr(
        anthropic_relay, "resolve_config",
        lambda: {"url": "http://127.0.0.1:8317/v1/messages", "model": "m", "headers": {}},
    )

    def _boom(_req, timeout=None):
        raise ConnectionRefusedError("Connection refused")

    import server.cloud_llm as mod
    _no_backend.setattr(mod.urllib.request, "urlopen", _boom)

    llm = CloudLLM()
    out = llm.generate("我喜歡蘋果", _sc())

    assert out is None
    assert llm.available() is True       # ← 設定看起來完美
    assert llm.verified() is False       # ← 但實際上死的
    assert llm.verified_backend() == "none"
    detail = llm.status_detail()
    assert "降級" in detail and "relay" in detail


def test_throttling_exception_is_not_verified(_no_backend):
    """Bedrock 每日 token 配額用盡（2026-07-30 實況）→ 不得宣稱 bedrock 可用。"""
    _use_bedrock(_no_backend, RuntimeError("ThrottlingException: Too many tokens per day"))

    llm = CloudLLM()

    assert llm.generate("我喜歡蘋果", _sc()) is None
    assert llm.verified() is False
    assert llm.verified_backend() == "none"
    assert "ThrottlingException" in llm.status_detail()


def test_guardrail_hit_does_not_mark_cloud_dead(_no_backend):
    """護欄擋下內容 ≠ 雲端壞掉：連線與模型都正常，不該讓自檢誤報不可用。"""
    _use_bedrock(_no_backend, "有問題的內容")
    _no_backend.setattr(guardrails, "passes_guardrail", lambda _t: False)

    llm = CloudLLM()

    assert llm.generate("我喜歡蘋果", _sc()) is None   # 仍降級
    assert llm.verified() is True                      # 但雲端本身是通的
    assert llm.verified_backend() == "bedrock"


def test_status_detail_when_nothing_configured(_no_backend):
    llm = CloudLLM()

    assert llm.available() is False
    assert llm.configured_backend() == "none"
    assert "未啟用" in llm.status_detail()


def test_success_then_failure_reports_latest(_no_backend):
    """覆蓋式紀錄：要看的是**此刻**能不能用，不是歷史平均。"""
    _use_bedrock(_no_backend, "很棒！跟我說一遍：I like apples.")
    llm = CloudLLM()
    llm.generate("我喜歡蘋果", _sc())
    assert llm.verified() is True

    _use_bedrock(_no_backend, RuntimeError("boom"))
    llm.generate("我喜歡蘋果", _sc())

    assert llm.verified() is False
    assert llm.verified_backend() == "none"
