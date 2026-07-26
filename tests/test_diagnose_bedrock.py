# -*- coding: utf-8 -*-
"""test_diagnose_bedrock.py — diagnose 的原生 Bedrock Converse 分支（TCLOUD-02）。

降級鏈：Bedrock →（失敗）Anthropic relay →（失敗）規則式 mock。
既有兩道出境閘門（allow_cloud kill-switch、家長同意）對 Bedrock 分支
同樣有效——雲端後端換人不得鑽出隱私護欄的漏洞。
"""

from __future__ import annotations

import json

import pytest

from server import bedrock_converse, diagnose

_VALID_DIAG = {
    "scores": {
        "pronunciation": 71,
        "fluency": 66,
        "vocabulary": 70,
        "grammar": 64,
    },
    "strengths": ["來自-BEDROCK-的優點"],
    "weaknesses": ["冠詞 a/an 誤用"],
    "emotional_status": "自信提升",
    "instructions": {"classroom": "a", "device": "b", "peer": "c"},
}


@pytest.fixture(autouse=True)
def _bedrock_selected(monkeypatch):
    """預設把 provider 切到 bedrock；個別測試可再覆蓋。"""
    monkeypatch.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.delenv("BEDROCK_MODEL_ID_CHAT", raising=False)
    monkeypatch.delenv("BEDROCK_MODEL_ID_DIAG", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    return monkeypatch


def _no_relay():
    return None


def _fake_relay_cfg():
    return {"url": "https://example.invalid/v1/messages", "headers": {}, "model": "fake"}


def _forbid_anthropic(monkeypatch):
    def _spy(*a, **k):
        raise AssertionError("不得呼叫 _call_anthropic_api")

    monkeypatch.setattr(diagnose, "_call_anthropic_api", _spy)


# ---------------------------------------------------------------------------
# 1. 正常路徑：provider=bedrock → 走 Bedrock，不碰 relay
# ---------------------------------------------------------------------------

def test_generate_diagnosis_uses_bedrock_when_selected(_bedrock_selected, monkeypatch):
    monkeypatch.setattr(diagnose.anthropic_relay, "resolve_config", _no_relay)
    _forbid_anthropic(monkeypatch)

    captured = {}

    def _fake_converse(system, user, *, cfg, **kwargs):
        captured["system"] = system
        captured["user"] = user
        captured["cfg"] = cfg
        return json.dumps(_VALID_DIAG, ensure_ascii=False)

    monkeypatch.setattr(bedrock_converse, "converse_text", _fake_converse)

    result = diagnose.generate_diagnosis([], None)

    assert result["strengths"] == ["來自-BEDROCK-的優點"]
    assert result["scores"]["pronunciation"] == 71
    assert captured["cfg"]["model_id"] == bedrock_converse.DEFAULT_MODEL_ID
    assert "JSON" in captured["user"] or "json" in captured["user"]


def test_bedrock_diagnosis_uses_diag_model_not_chat_model(
    _bedrock_selected, monkeypatch
):
    """診斷是非同步路徑（12s 上界），該用推理品質較好的大模型，
    不可誤取對話路徑那顆為 1.5s 上界挑的小模型。"""
    monkeypatch.setattr(diagnose.anthropic_relay, "resolve_config", _no_relay)
    monkeypatch.setenv("BEDROCK_MODEL_ID_CHAT", "us.anthropic.fast-chat-v1:0")
    monkeypatch.setenv("BEDROCK_MODEL_ID_DIAG", "us.anthropic.smart-diag-v1:0")

    captured = {}

    def _fake_converse(system, user, *, cfg, **kwargs):
        captured["cfg"] = cfg
        return json.dumps(_VALID_DIAG, ensure_ascii=False)

    monkeypatch.setattr(bedrock_converse, "converse_text", _fake_converse)
    diagnose.generate_diagnosis([], None)

    assert captured["cfg"]["model_id"] == "us.anthropic.smart-diag-v1:0"


def test_bedrock_branch_tolerates_markdown_fence(_bedrock_selected, monkeypatch):
    """模型仍包 ```json 圍欄時要能剝掉，與 relay 分支行為一致。"""
    monkeypatch.setattr(diagnose.anthropic_relay, "resolve_config", _no_relay)
    fenced = "```json\n" + json.dumps(_VALID_DIAG, ensure_ascii=False) + "\n```"
    monkeypatch.setattr(
        bedrock_converse, "converse_text", lambda *a, **k: fenced
    )

    result = diagnose.generate_diagnosis([], None)
    assert result["strengths"] == ["來自-BEDROCK-的優點"]


# ---------------------------------------------------------------------------
# 2. 降級鏈
# ---------------------------------------------------------------------------

def test_bedrock_failure_falls_back_to_relay(_bedrock_selected, monkeypatch):
    monkeypatch.setattr(diagnose.anthropic_relay, "resolve_config", _fake_relay_cfg)

    def _boom(*a, **k):
        raise RuntimeError("bedrock 掛了")

    monkeypatch.setattr(bedrock_converse, "converse_text", _boom)

    relay_diag = dict(_VALID_DIAG, strengths=["來自-RELAY-的優點"])
    monkeypatch.setattr(
        diagnose,
        "_call_anthropic_api",
        lambda *a, **k: diagnose._validate_diagnosis(relay_diag),
    )

    result = diagnose.generate_diagnosis([], None)
    assert result["strengths"] == ["來自-RELAY-的優點"]


def test_bedrock_and_relay_failure_falls_back_to_rule_based(
    _bedrock_selected, monkeypatch
):
    monkeypatch.setattr(diagnose.anthropic_relay, "resolve_config", _fake_relay_cfg)
    monkeypatch.setattr(
        bedrock_converse,
        "converse_text",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bedrock 掛了")),
    )
    monkeypatch.setattr(
        diagnose,
        "_call_anthropic_api",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("relay 也掛了")),
    )

    result = diagnose.generate_diagnosis([], None)

    assert result is not None
    assert "scores" in result
    assert result.get("companion_directive")
    assert result["strengths"] != ["來自-BEDROCK-的優點"]


# ---------------------------------------------------------------------------
# 3. 兩道出境閘門對 Bedrock 分支同樣有效
# ---------------------------------------------------------------------------

def test_allow_cloud_false_never_calls_bedrock(_bedrock_selected, monkeypatch):
    """D-01 斷網 kill-switch：allow_cloud=False 時 Bedrock 分支零呼叫。"""
    monkeypatch.setattr(diagnose.anthropic_relay, "resolve_config", _fake_relay_cfg)

    def _spy(*a, **k):
        raise AssertionError("allow_cloud=False 時不得呼叫 Bedrock")

    monkeypatch.setattr(bedrock_converse, "converse_text", _spy)
    _forbid_anthropic(monkeypatch)

    result = diagnose.generate_diagnosis([], None, allow_cloud=False)
    assert "scores" in result


def test_no_consent_never_calls_bedrock(_bedrock_selected, monkeypatch):
    """B4-5 家長同意閘門：未同意時 Bedrock 分支零呼叫（資料不出境）。"""
    monkeypatch.setattr(diagnose.guardrails, "consent_granted", lambda: False)

    def _spy(*a, **k):
        raise AssertionError("未取得家長同意時不得呼叫 Bedrock")

    monkeypatch.setattr(bedrock_converse, "converse_text", _spy)
    _forbid_anthropic(monkeypatch)

    result = diagnose.generate_diagnosis([], None)
    assert "scores" in result


# ---------------------------------------------------------------------------
# 4. provider 未切換時完全不碰 Bedrock（既有 relay 行為零迴歸）
# ---------------------------------------------------------------------------

def test_provider_unset_keeps_relay_path(_bedrock_selected, monkeypatch):
    monkeypatch.delenv("TALKYBUDDY_CLOUD_PROVIDER", raising=False)
    monkeypatch.setattr(diagnose.anthropic_relay, "resolve_config", _fake_relay_cfg)

    def _spy(*a, **k):
        raise AssertionError("provider 未切到 bedrock 時不得呼叫 Bedrock")

    monkeypatch.setattr(bedrock_converse, "converse_text", _spy)

    relay_diag = dict(_VALID_DIAG, strengths=["來自-RELAY-的優點"])
    monkeypatch.setattr(
        diagnose,
        "_call_anthropic_api",
        lambda *a, **k: diagnose._validate_diagnosis(relay_diag),
    )

    result = diagnose.generate_diagnosis([], None)
    assert result["strengths"] == ["來自-RELAY-的優點"]


# ---------------------------------------------------------------------------
# 5. 上雲前去識別化（護欄不得因換後端而失效）
# ---------------------------------------------------------------------------

def test_bedrock_prompt_is_deidentified(_bedrock_selected, monkeypatch):
    monkeypatch.setattr(diagnose.anthropic_relay, "resolve_config", _no_relay)
    captured = {}

    def _fake_converse(system, user, *, cfg, **kwargs):
        captured["user"] = user
        return json.dumps(_VALID_DIAG, ensure_ascii=False)

    monkeypatch.setattr(bedrock_converse, "converse_text", _fake_converse)

    raw = "我的電話是 0912345678"
    interactions = [
        {"student_text": raw, "ai_response_text": "ok", "asr_confidence": 0.9}
    ]
    diagnose.generate_diagnosis(interactions, None)

    assert "0912345678" not in captured["user"]
