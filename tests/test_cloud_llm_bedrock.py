# -*- coding: utf-8 -*-
"""test_cloud_llm_bedrock.py — 對話大腦（CloudLLM）的原生 Bedrock 分支。

補上「大腦 100% 在 Bedrock」合規的另一半：diagnose.py 走的是事後教師診斷，
本檔是孩子講完話當下的**對話回覆**路徑。

關鍵不變式（每條都有專屬測試）：
1. Bedrock 逾時必須沿用 cloud_llm._TIMEOUT_S（斷網橋段 D-03 的快速失敗上界，
   預設 1.5s）。若誤用 bedrock_converse 的 12s 預設，NETCUT「恢復 <1-2 秒」
   的驗收條件會直接破功。
2. 上雲前去識別化、輸出後置護欄、目標句不可漏 —— 換後端後全數仍生效。
3. provider 未切到 bedrock 時，既有 relay 行為零變更。
"""
from __future__ import annotations

import json

import pytest

from server import bedrock_converse, cloud_llm as cloud_llm_mod
from server.cloud_llm import CloudLLM

_ALL_ENV = [
    "TALKYBUDDY_CLOUD_PROVIDER", "BEDROCK_REGION", "AWS_REGION",
    "AWS_DEFAULT_REGION", "BEDROCK_MODEL_ID",
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_MODEL",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ALL_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class _Sc:
    target_sentence = "I like apples."


def _forbid_urlopen(monkeypatch):
    def _spy(*a, **k):
        raise AssertionError("走 Bedrock 時不得呼叫 relay 的 urlopen")

    monkeypatch.setattr(cloud_llm_mod.urllib.request, "urlopen", _spy)


# ---------------------------------------------------------------------------
# available()
# ---------------------------------------------------------------------------

def test_available_true_with_only_bedrock_provider(_clean_env):
    """只設 Bedrock（無 Anthropic 金鑰）時也要 available，否則 pipeline 不會走雲端。"""
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    assert CloudLLM().available() is True


def test_available_false_with_neither_backend(_clean_env):
    assert CloudLLM().available() is False


# ---------------------------------------------------------------------------
# generate() — Bedrock 分支
# ---------------------------------------------------------------------------

def test_generate_uses_bedrock_when_selected(_clean_env, monkeypatch):
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    _forbid_urlopen(monkeypatch)
    captured = {}

    def _fake(system, user, *, cfg, **kwargs):
        captured.update(system=system, user=user, cfg=cfg, kwargs=kwargs)
        return "好棒！跟我說一遍：I like apples."

    monkeypatch.setattr(bedrock_converse, "converse_text", _fake)

    out = CloudLLM().generate("我喜歡蘋果", _Sc())

    assert out == "好棒！跟我說一遍：I like apples."
    assert "鷹架家教" in captured["system"]


def test_bedrock_call_uses_cloud_llm_timeout_not_bedrock_default(
    _clean_env, monkeypatch
):
    """D-03 斷網快速失敗：必須用 cloud_llm._TIMEOUT_S，不可用 bedrock 的 12s。"""
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    captured = {}

    def _fake(system, user, *, cfg, **kwargs):
        captured.update(kwargs)
        return "好棒！跟我說一遍：I like apples."

    monkeypatch.setattr(bedrock_converse, "converse_text", _fake)
    CloudLLM().generate("我喜歡蘋果", _Sc())

    assert captured["timeout_s"] == cloud_llm_mod._TIMEOUT_S
    assert captured["timeout_s"] < bedrock_converse.DEFAULT_TIMEOUT_S


def test_bedrock_failure_returns_none_not_raise(_clean_env, monkeypatch):
    """任何失敗回 None，讓 pipeline 降級到 edge/scaffold——絕不拋進 pipeline。"""
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    monkeypatch.setattr(
        bedrock_converse,
        "converse_text",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bedrock 掛了")),
    )
    assert CloudLLM().generate("我喜歡蘋果", _Sc()) is None


def test_bedrock_output_still_passes_guardrail(_clean_env, monkeypatch):
    """輸出後置護欄不得因換後端而失效：不安全內容 → None（降級回 edge/scaffold）。"""
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    monkeypatch.setattr(bedrock_converse, "converse_text", lambda *a, **k: "安全回覆")
    monkeypatch.setattr(cloud_llm_mod.guardrails, "passes_guardrail", lambda t: False)
    assert CloudLLM().generate("我喜歡蘋果", _Sc()) is None


def test_bedrock_prompt_is_deidentified(_clean_env, monkeypatch):
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    captured = {}

    def _fake(system, user, *, cfg, **kwargs):
        captured["user"] = user
        return "好棒！跟我說一遍：I like apples."

    monkeypatch.setattr(bedrock_converse, "converse_text", _fake)
    CloudLLM().generate("我的電話是 0912345678", _Sc())
    assert "0912345678" not in captured["user"]


def test_bedrock_missing_target_sentence_is_appended(_clean_env, monkeypatch):
    """帶讀不可漏句：目標英文句一定要在回覆中（與 relay 分支行為一致）。"""
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    monkeypatch.setattr(bedrock_converse, "converse_text", lambda *a, **k: "你好棒！")
    out = CloudLLM().generate("我喜歡蘋果", _Sc())
    assert "I like apples." in out


# ---------------------------------------------------------------------------
# 零迴歸：provider 未切換時仍走 relay
# ---------------------------------------------------------------------------

def test_relay_path_unchanged_when_provider_unset(_clean_env, monkeypatch):
    _clean_env.setenv("ANTHROPIC_API_KEY", "sk-x")

    def _spy(*a, **k):
        raise AssertionError("provider 未切到 bedrock 時不得呼叫 Bedrock")

    monkeypatch.setattr(bedrock_converse, "converse_text", _spy)

    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(
                {"content": [{"type": "text", "text": "來自 relay：跟我說一遍：I like apples."}]}
            ).encode("utf-8")

    monkeypatch.setattr(
        cloud_llm_mod.urllib.request, "urlopen", lambda req, timeout=None: _R()
    )

    out = CloudLLM().generate("我喜歡蘋果", _Sc())
    assert out is not None and "來自 relay" in out


def test_bedrock_takes_priority_over_relay(_clean_env, monkeypatch):
    """兩者都設定時 Bedrock 優先（合規要求大腦在 Bedrock）。"""
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    _clean_env.setenv("ANTHROPIC_API_KEY", "sk-x")
    _forbid_urlopen(monkeypatch)
    monkeypatch.setattr(
        bedrock_converse, "converse_text",
        lambda *a, **k: "來自 Bedrock：跟我說一遍：I like apples.",
    )
    assert "Bedrock" in CloudLLM().generate("我喜歡蘋果", _Sc())
