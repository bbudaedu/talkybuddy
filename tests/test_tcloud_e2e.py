# -*- coding: utf-8 -*-
"""test_tcloud_e2e.py — TCLOUD-01/02 端到端閉環驗證（Phase 11 Plan 04）。

本檔驗 TCLOUD-01/02 的端到端閉環，分兩個任務累積：

Task 1（本次）：`diagnose.generate_diagnosis()` 恆回傳 `source`
（"cloud" | "rule"），雲端成功才標 cloud、任何降級（allow_cloud=False /
consent 未授權 / Bedrock 與 relay 皆失敗）一律誠實標 rule。這是 SC4
「顯示真實（非 mock）診斷」唯一的可稽核依據——降級鏈本身是刻意靜默的
（demo 韌性優先），沒有這個欄位沒人分辨得出畫面上那張診斷卡是雲端產出
還是本地規則式湊出來的。

Task 2：三道出境閘門（allow_cloud、consent_granted、deidentify）在
**真實資料**上的端到端驗證，以及決賽橋段「插回網路 → 不必等孩子再說話 →
儀表板出現新診斷」的離線視窗鏈路。攔截手法照抄
`tests/test_agent_privacy.py`：不 mock `guardrails.deidentify`（用
真的），只攔截送上雲的 prompt 字串本身做斷言——這比「斷言 deidentify
被呼叫過」強得多，因為 deidentify 遮不掉中文姓名，只遮個資詞／連續
數字／詞庫外的 Title-case 英文專名。

已知殘留風險（不在本 phase 修，CONTEXT.md `<deferred>` 已排除
`deidentify` 語意層強化）：`server/diagnose.py::_build_diagnosis_prompt`
組 prompt 時，`student_text` 經 `guardrails.deidentify()`，但
`ai_response_text` **沒有**。若 AI 學伴的回覆覆述了孩子的名字（例如
「Hi Mimi!」），該名字會原文隨 `ai_response_text` 進入 Bedrock prompt。
這是既有實作的邊界，不是本 phase 引入的退步——見 T-11-13 威脅登錄項與
SUMMARY 的 Known-Gap 記錄。
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from server import app as app_mod
from server import auth, bedrock_converse, config, diagnose, store


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


def _tok(sub="TUTOR-001", role="tutor"):
    return auth.issue_token(sub, role)


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


# ---------------------------------------------------------------------------
# Task 2：離線視窗鏈路 + 三道出境閘門的真實資料驗證
# ---------------------------------------------------------------------------


def test_offline_window_pending_zeroes_and_new_diagnosis_appears():
    """離線視窗鏈路：3 筆 pending（edge 期間寫入）→ 切 cloud →
    pending_count() 歸零，且 list_diagnoses() 多出一筆新診斷。

    這是決賽橋段「插回網路 → 不必等孩子再講話 → 儀表板出現新診斷」的
    自動化版本，欄位形狀比照 server/pipeline.py:303-316 實際寫入的形狀。
    """
    for i in range(3):
        store.add_interaction({
            "device_id": "GENIO-520-DEMO",
            "student_id": store._student_id(),
            "ts": f"2026-07-27T10:0{i}:00",
            "network_mode": "edge",
            "student_text": f"student turn {i}",
            "asr_confidence": 0.9,
            "ai_response_text": f"ai turn {i}",
            "scores": {"fluency": 60, "vocabulary": 60, "grammar": 60},
            "latency_ms": {"round_total": 800},
            "synced": False,
        })
    before = len(store.list_diagnoses())
    client = TestClient(app_mod.app)
    h = {"Authorization": f"Bearer {_tok()}"}

    res = client.post("/api/network_mode", json={"mode": "cloud"}, headers=h)

    assert res.status_code == 200
    body = res.json()
    assert body["synced"] == 3
    assert store.pending_count() == 0
    assert len(store.list_diagnoses()) == before + 1


def test_edge_mode_zero_egress_calls(monkeypatch):
    """network_mode="edge" 時呼叫診斷（allow_cloud=False）→
    攔截器記錄到的出境呼叫次數為 0。

    攔截點刻意選在 bedrock_converse.converse_text（比 _call_bedrock_api
    更靠近真正跨出裝置的那一行），與 Task 2 要求的攔截手法一致。
    """
    intercepted = []

    def fake_converse_text(system, user, *, cfg, max_tokens=1024,
                            temperature=0.7, timeout_s=12.0):
        intercepted.append((system, user))
        return json.dumps(_valid_cloud_diag(), ensure_ascii=False)

    monkeypatch.setattr(bedrock_converse, "converse_text", fake_converse_text)
    monkeypatch.setattr(bedrock_converse, "resolve_config", _fake_bedrock_cfg)

    result = diagnose.generate_diagnosis(
        [{"student_text": "hi", "scores": {}}], None, allow_cloud=False)

    assert intercepted == []
    assert result["source"] == "rule"


def test_consent_not_granted_zero_egress_calls(monkeypatch):
    """config.CONSENT_GRANTED=False 時呼叫診斷 → 出境呼叫次數為 0，
    且回傳 source 為 rule。
    """
    monkeypatch.setattr(config, "CONSENT_GRANTED", False)
    intercepted = []

    def fake_converse_text(system, user, *, cfg, max_tokens=1024,
                            temperature=0.7, timeout_s=12.0):
        intercepted.append((system, user))
        return json.dumps(_valid_cloud_diag(), ensure_ascii=False)

    monkeypatch.setattr(bedrock_converse, "converse_text", fake_converse_text)
    monkeypatch.setattr(bedrock_converse, "resolve_config", _fake_bedrock_cfg)

    result = diagnose.generate_diagnosis([{"student_text": "hi", "scores": {}}], None)

    assert intercepted == []
    assert result["source"] == "rule"


def test_prompt_excludes_phone_number_via_real_deidentify(monkeypatch):
    """攔截送往 Bedrock 的 prompt 字串 → 其中不含互動原文裡的電話數字。

    guardrails.deidentify 未被 mock（用真的）——驗的是它在真實資料上的
    效果，而不是「有沒有被呼叫過」。
    """
    captured = {}

    def fake_converse_text(system, user, *, cfg, max_tokens=1024,
                            temperature=0.7, timeout_s=12.0):
        captured["prompt"] = user
        return json.dumps(_valid_cloud_diag(), ensure_ascii=False)

    monkeypatch.setattr(bedrock_converse, "converse_text", fake_converse_text)
    monkeypatch.setattr(bedrock_converse, "resolve_config", _fake_bedrock_cfg)

    phone = "0912345678"
    interactions = [{
        "student_text": f"我的電話是{phone}，晚點聯絡",
        "ai_response_text": "好的，我知道了。",
        "scores": {"fluency": 60},
    }]

    result = diagnose.generate_diagnosis(interactions, None)

    assert result["source"] == "cloud"
    assert "prompt" in captured
    assert phone not in captured["prompt"]


def test_prompt_excludes_student_display_name(monkeypatch):
    """攔截送往 Bedrock 的 prompt 字串 → 其中不含
    store.student_display_name() 的值（姓名不得隨診斷 prompt 出境）。

    D-05 鎖定：身分欄位（student_id/姓名）不經 deidentify、但也不該
    出現在對話內容的 prompt 裡——教師儀表板讀身分是另一條路徑
    （/api/student_profile），不是這裡。
    """
    captured = {}

    def fake_converse_text(system, user, *, cfg, max_tokens=1024,
                            temperature=0.7, timeout_s=12.0):
        captured["prompt"] = user
        return json.dumps(_valid_cloud_diag(), ensure_ascii=False)

    monkeypatch.setattr(bedrock_converse, "converse_text", fake_converse_text)
    monkeypatch.setattr(bedrock_converse, "resolve_config", _fake_bedrock_cfg)

    interactions = [{
        "student_text": "I like apples",
        "ai_response_text": "Great job!",
        "scores": {"fluency": 60},
    }]

    result = diagnose.generate_diagnosis(interactions, None)
    name = store.student_display_name()

    assert result["source"] == "cloud"
    assert "prompt" in captured
    assert name not in captured["prompt"]
