# -*- coding: utf-8 -*-
"""test_tcloud_e2e.py — TCLOUD-01/02 端到端閉環驗證（Phase 11 Plan 04）。

本檔驗 TCLOUD-01/02 的端到端閉環，分兩個任務累積：

Task 1（本次）：`diagnose.generate_diagnosis()` 恆回傳 `source`
（"cloud" | "rule"），雲端成功才標 cloud、任何降級（allow_cloud=False /
consent 未授權 / Bedrock 與 relay 皆失敗）一律誠實標 rule。這是 SC4
「顯示真實（非 mock）診斷」唯一的可稽核依據——降級鏈本身是刻意靜默的
（demo 韌性優先），沒有這個欄位沒人分辨得出畫面上那張診斷卡是雲端產出
還是本地規則式湊出來的。

Task 2（後續）：三道出境閘門（allow_cloud、consent_granted、
deidentify）在真實資料上的端到端驗證，以及離線視窗鏈路。
"""

from __future__ import annotations

import pytest

from server import config, diagnose


# ---------------------------------------------------------------------------
# 共用 fixtures / helpers
# ---------------------------------------------------------------------------


def _valid_cloud_diag() -> dict:
    """一份符合 `_validate_diagnosis` schema 的合法診斷 dict（雲端假回覆用）。"""
    return {
        "date": "2026-07-27",
        "scores": {"pronunciation": 62, "fluency": 58, "vocabulary": 65, "grammar": 54},
        "strengths": ["願意開口嘗試"],
        "weaknesses": ["冠詞 a/an 仍不穩定"],
        "emotional_status": "學習態度積極。",
        "instructions": {
            "classroom": "老師可多鼓勵發音。",
            "device": "麥克風收音正常。",
            "peer": "可安排同儕練習配對。",
        },
        "companion_directive": {
            "level": "L2",
            "difficulty": "hold",
            "next_goal": "鞏固現在式句型",
            "topic": "動物",
            "example_questions": ["What color is the pig?"],
            "fallback_hint": "沉默時改問二選一問句",
        },
    }


def _fake_bedrock_cfg(role=None):
    return {"region": "ap-east-2", "model_id": "fake-model"}


# ---------------------------------------------------------------------------
# Task 1：generate_diagnosis() 的 source 欄位 —— 六個 behavior
# ---------------------------------------------------------------------------


def test_source_is_rule_when_no_cloud_credentials(monkeypatch):
    """無任何雲端憑證（resolve_config 皆回 None）→ source 為 rule。"""
    monkeypatch.setattr(diagnose.bedrock_converse, "resolve_config", lambda role=None: None)
    monkeypatch.setattr(diagnose.anthropic_relay, "resolve_config", lambda: None)

    result = diagnose.generate_diagnosis([{"student_text": "hi", "scores": {}}], None)

    assert result["source"] == "rule"


def test_source_is_cloud_when_bedrock_branch_succeeds(monkeypatch):
    """Bedrock 分支成功回傳 → source 為 cloud。"""
    monkeypatch.setattr(diagnose.bedrock_converse, "resolve_config", _fake_bedrock_cfg)
    monkeypatch.setattr(
        diagnose, "_call_bedrock_api",
        lambda interactions, prev, cfg: _valid_cloud_diag(),
    )

    result = diagnose.generate_diagnosis([{"student_text": "hi", "scores": {}}], None)

    assert result["source"] == "cloud"


def test_source_is_rule_when_bedrock_raises_and_relay_unavailable(monkeypatch):
    """Bedrock 分支拋例外、relay 也不可用 → source 為 rule（降級誠實標記）。"""
    monkeypatch.setattr(diagnose.bedrock_converse, "resolve_config", _fake_bedrock_cfg)

    def _boom(interactions, prev, cfg):
        raise RuntimeError("模擬 Bedrock 逾時")

    monkeypatch.setattr(diagnose, "_call_bedrock_api", _boom)
    monkeypatch.setattr(diagnose.anthropic_relay, "resolve_config", lambda: None)

    result = diagnose.generate_diagnosis([{"student_text": "hi", "scores": {}}], None)

    assert result["source"] == "rule"


def test_source_is_rule_and_no_cloud_call_when_allow_cloud_false(monkeypatch):
    """allow_cloud=False → source 為 rule，且 Bedrock 與 relay 皆未被呼叫。"""
    monkeypatch.setattr(diagnose.bedrock_converse, "resolve_config", _fake_bedrock_cfg)
    calls = []

    def _spy(*args, **kwargs):
        calls.append(1)
        return _valid_cloud_diag()

    monkeypatch.setattr(diagnose, "_call_bedrock_api", _spy)
    monkeypatch.setattr(diagnose, "_call_anthropic_api", _spy)

    result = diagnose.generate_diagnosis(
        [{"student_text": "hi", "scores": {}}], None, allow_cloud=False)

    assert result["source"] == "rule"
    assert calls == []


def test_source_is_rule_and_no_cloud_call_when_consent_not_granted(monkeypatch):
    """consent_granted() 為 False → source 為 rule，且 Bedrock 與 relay 皆未被呼叫。"""
    monkeypatch.setattr(config, "CONSENT_GRANTED", False)
    monkeypatch.setattr(diagnose.bedrock_converse, "resolve_config", _fake_bedrock_cfg)
    calls = []

    def _spy(*args, **kwargs):
        calls.append(1)
        return _valid_cloud_diag()

    monkeypatch.setattr(diagnose, "_call_bedrock_api", _spy)
    monkeypatch.setattr(diagnose, "_call_anthropic_api", _spy)

    result = diagnose.generate_diagnosis([{"student_text": "hi", "scores": {}}], None)

    assert result["source"] == "rule"
    assert calls == []


@pytest.mark.parametrize("allow_cloud", [True, False])
def test_source_domain_is_closed_to_cloud_or_rule(allow_cloud):
    """source 值域恆為 "cloud" 或 "rule" 二者之一，不會是 None 或缺鍵。"""
    result = diagnose.generate_diagnosis(
        [{"student_text": "hi", "scores": {}}], None, allow_cloud=allow_cloud)

    assert "source" in result
    assert result["source"] in ("cloud", "rule")
