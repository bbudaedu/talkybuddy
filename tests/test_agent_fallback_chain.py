# -*- coding: utf-8 -*-
"""test_agent_fallback_chain.py — 三個 agent 的三層降級鏈。

正確順序：**AgentCore Harness → Bedrock Converse → 規則式**。

為什麼要獨立一個測試檔
----------------------
`tests/test_agent_backend_switch.py` 驗的是「開關有沒有生效」，它的
`test_agentcore_failure_falls_back_to_rule` 在 `TALKYBUDDY_CLOUD_PROVIDER`
**未設**的情況下斷言 `source == "rule"`——那是對的，因為當時 Bedrock 本來就
沒設定。本檔補的是它沒涵蓋、而且真的會出事的那一格：

    AgentCore 有設定且失敗 + Bedrock 也有設定 → 應該走 Bedrock，不是掉到規則式。

三個 agent 原本寫成 ``cfg = None if ac_cfg else bedrock_converse.resolve_config(...)``，
AgentCore 設定一存在，Bedrock 就被硬設成 None。結果是**撥了 AgentCore 開關反而
比不撥更差**：Harness 一失敗就直接掉到規則式，中間那層雲端能力整個被跳過。
決賽現場「開了新功能結果品質變差」是最難當場診斷的一種失敗。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from server import agentcore, bedrock_converse, scaffold
from server.agents import homework, material, orchestrator, report

_ENV = [
    "TALKYBUDDY_AGENT_BACKEND", "TALKYBUDDY_CLOUD_PROVIDER",
    "AGENTCORE_REGION", "AGENTCORE_MEMORY_ARN",
    "AGENTCORE_HARNESS_ORCHESTRATOR", "AGENTCORE_HARNESS_HOMEWORK",
    "AGENTCORE_HARNESS_REPORT", "AGENTCORE_HARNESS_MATERIAL",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for n in _ENV:
        monkeypatch.delenv(n, raising=False)
    # 與 test_agent_backend_switch.py 同樣的理由：擋掉 orchestrator 的定期回報
    # 保底讀實機 DB，否則斷言會隨 data/talkybuddy.db 內容與當天日期時綠時紅。
    day_ago = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=1)
               ).isoformat(timespec="seconds")
    monkeypatch.setattr(
        "server.store.list_agent_outputs",
        lambda kind=None, limit=20, student_id=None: [
            {"kind": kind, "ts": day_ago, "student_id": student_id},
        ],
    )
    return monkeypatch


def _diag(g: int = 40) -> dict:
    return {
        "date": "07-24",
        "scores": {"pronunciation": 70, "fluency": 70, "vocabulary": 70, "grammar": g},
        "strengths": ["敢開口"], "weaknesses": ["冠詞誤用"],
    }


_HW_JSON = json.dumps({
    "focus": "文法（來自 Bedrock Converse）",
    "items": [{"target_en": "I see a dog.", "prompt_zh": "說一句完整的英文句子！",
               "why": "練文法"}] * 3,
}, ensure_ascii=False)

_RPT_JSON = json.dumps({
    "period": "最近 3 次", "summary": "來自 Bedrock Converse 的週報。",
    "highlights": ["h"], "concerns": ["c"], "suggestions": ["s"],
}, ensure_ascii=False)

_ORCH_JSON = json.dumps({
    "actions": ["homework"], "reason": "來自 Bedrock Converse 的決策。",
    "priority": "high",
}, ensure_ascii=False)

_MATERIAL_JSON = json.dumps({
    "topic": "動物園教材（來自 Bedrock Converse）",
    "entries": [{"en": "koala", "zh": "無尾熊", "cat": "animal",
                 "np": "a koala", "sent": "I see a koala at the zoo today."}],
    "source": "cloud",
}, ensure_ascii=False)


@pytest.fixture
def _restore_vocab():
    """material.extract_vocab 的雲端路徑會呼叫 register_material_vocab，原地
    mutate 全域 VOCAB——用完要還原，避免污染同檔案其他測試或其他測試檔案。"""
    snapshot = {zh: dict(v) for zh, v in scaffold.VOCAB.items()}
    yield
    scaffold.VOCAB.clear()
    scaffold.VOCAB.update(snapshot)


def _enable_both(mp, harness_env: str) -> None:
    """同時啟用 AgentCore 與 Bedrock——這正是決賽現場的部署形態。"""
    mp.setenv("TALKYBUDDY_AGENT_BACKEND", "agentcore")
    mp.setenv(harness_env, "arn:aws:bedrock-agentcore:us-west-2:1:harness/x")
    mp.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")


def _boom(*a, **k):
    raise RuntimeError("harness 掛了")


# ---------------------------------------------------------------------------
# 第一層失敗 → 落到第二層（Bedrock），不是直接摔到規則式
# ---------------------------------------------------------------------------

def test_homework_agentcore_failure_falls_back_to_bedrock(_clean_env, monkeypatch):
    _enable_both(_clean_env, "AGENTCORE_HARNESS_HOMEWORK")
    monkeypatch.setattr(agentcore, "invoke", _boom)
    called: list[str] = []
    monkeypatch.setattr(
        bedrock_converse, "converse_text",
        lambda *a, **k: (called.append("converse"), _HW_JSON)[1],
    )

    out = homework.generate_homework({}, _diag(), allow_cloud=True)

    assert called == ["converse"], "AgentCore 失敗後必須改打 Bedrock Converse"
    assert out["source"] == "cloud"
    assert out["focus"] == "文法（來自 Bedrock Converse）"


def test_report_agentcore_failure_falls_back_to_bedrock(_clean_env, monkeypatch):
    _enable_both(_clean_env, "AGENTCORE_HARNESS_REPORT")
    monkeypatch.setattr(agentcore, "invoke", _boom)
    called: list[str] = []
    monkeypatch.setattr(
        bedrock_converse, "converse_text",
        lambda *a, **k: (called.append("converse"), _RPT_JSON)[1],
    )

    out = report.generate_report({}, [_diag(), _diag(50)], allow_cloud=True)

    assert called == ["converse"], "AgentCore 失敗後必須改打 Bedrock Converse"
    assert out["source"] == "cloud"
    assert out["summary"] == "來自 Bedrock Converse 的週報。"


def test_orchestrator_agentcore_failure_falls_back_to_bedrock(_clean_env, monkeypatch):
    _enable_both(_clean_env, "AGENTCORE_HARNESS_ORCHESTRATOR")
    monkeypatch.setattr(agentcore, "invoke", _boom)
    called: list[str] = []
    monkeypatch.setattr(
        bedrock_converse, "converse_text",
        lambda *a, **k: (called.append("converse"), _ORCH_JSON)[1],
    )

    out = orchestrator.decide_next_actions({}, _diag(), [_diag(), _diag(50)], 12,
                                           allow_cloud=True)

    assert called == ["converse"], "AgentCore 失敗後必須改打 Bedrock Converse"
    assert out["source"] == "cloud"
    assert out["reason"] == "來自 Bedrock Converse 的決策。"


def test_material_agentcore_failure_falls_back_to_bedrock(
    _clean_env, monkeypatch, _restore_vocab
):
    """material 的訊號跟其他三個 agent 完全不同（extract_vocab(text,
    allow_cloud=...) 而不是 (profile, diagnosis, ...)），但降級鏈契約一致：
    AgentCore 失敗要落到 Bedrock，不是直接摔到規則式。"""
    _enable_both(_clean_env, "AGENTCORE_HARNESS_MATERIAL")
    monkeypatch.setattr(agentcore, "invoke", _boom)
    called: list[str] = []
    monkeypatch.setattr(
        bedrock_converse, "converse_text",
        lambda *a, **k: (called.append("converse"), _MATERIAL_JSON)[1],
    )

    out = material.extract_vocab("今天去動物園看了一隻無尾熊。", allow_cloud=True)

    assert called == ["converse"], "AgentCore 失敗後必須改打 Bedrock Converse"
    assert out["source"] == "cloud"
    assert out["accepted_count"] == 1
    assert "無尾熊" in scaffold.VOCAB, "驗證通過的詞條應已合併進 VOCAB"


# ---------------------------------------------------------------------------
# 兩層都失敗 → 規則式保底（原本的保證不能因為多一層而消失）
# ---------------------------------------------------------------------------

def test_both_cloud_backends_failing_still_falls_back_to_rule(_clean_env, monkeypatch):
    _enable_both(_clean_env, "AGENTCORE_HARNESS_HOMEWORK")
    monkeypatch.setattr(agentcore, "invoke", _boom)
    monkeypatch.setattr(bedrock_converse, "converse_text", _boom)

    out = homework.generate_homework({}, _diag(), allow_cloud=True)

    assert out["source"] == "rule"
    assert len(out["items"]) >= 3


def test_agentcore_failure_without_bedrock_configured_falls_back_to_rule(
    _clean_env, monkeypatch
):
    """只設 AgentCore、沒設 Bedrock 時，維持既有行為：直接規則式。"""
    _clean_env.setenv("TALKYBUDDY_AGENT_BACKEND", "agentcore")
    _clean_env.setenv("AGENTCORE_HARNESS_HOMEWORK", "arn:hw")
    monkeypatch.setattr(agentcore, "invoke", _boom)
    monkeypatch.setattr(
        bedrock_converse, "converse_text",
        lambda *a, **k: pytest.fail("Bedrock 未設定時不得呼叫 converse_text"),
    )

    out = homework.generate_homework({}, _diag(), allow_cloud=True)

    assert out["source"] == "rule"


# ---------------------------------------------------------------------------
# 第一層成功 → 不得多打第二層（成本與延遲）
# ---------------------------------------------------------------------------

def test_agentcore_success_does_not_also_call_bedrock(_clean_env, monkeypatch):
    """兩個後端都設定時，AgentCore 成功就該收工——多打一次是白花錢又慢。"""
    _enable_both(_clean_env, "AGENTCORE_HARNESS_HOMEWORK")
    monkeypatch.setattr(
        agentcore, "invoke",
        lambda *a, **k: json.dumps({
            "focus": "文法（來自 AgentCore）",
            "items": [{"target_en": "I see a dog.", "prompt_zh": "說說看！",
                       "why": "練文法"}] * 3,
        }, ensure_ascii=False),
    )
    monkeypatch.setattr(
        bedrock_converse, "converse_text",
        lambda *a, **k: pytest.fail("AgentCore 已成功，不得再打 Bedrock"),
    )

    out = homework.generate_homework({}, _diag(), allow_cloud=True)

    assert out["focus"] == "文法（來自 AgentCore）"
    assert out["source"] == "cloud"


def test_material_agentcore_success_does_not_also_call_bedrock(
    _clean_env, monkeypatch, _restore_vocab
):
    """material 一樣受這條規則約束：AgentCore 成功就收工，不多打 Bedrock。"""
    _enable_both(_clean_env, "AGENTCORE_HARNESS_MATERIAL")
    monkeypatch.setattr(agentcore, "invoke", lambda *a, **k: _MATERIAL_JSON)
    monkeypatch.setattr(
        bedrock_converse, "converse_text",
        lambda *a, **k: pytest.fail("AgentCore 已成功，不得再打 Bedrock"),
    )

    out = material.extract_vocab("今天去動物園看了一隻無尾熊。", allow_cloud=True)

    assert out["source"] == "cloud"
    assert out["accepted_count"] == 1


# ---------------------------------------------------------------------------
# 降級鏈不得繞過既有閘門
# ---------------------------------------------------------------------------

def test_allow_cloud_false_blocks_the_whole_chain(_clean_env, monkeypatch):
    """斷網 kill-switch 要擋掉整條鏈，包含新增的第二層。"""
    _enable_both(_clean_env, "AGENTCORE_HARNESS_HOMEWORK")
    monkeypatch.setattr(
        agentcore, "resolve_config",
        lambda *a, **k: pytest.fail("edge 模式連 resolve_config 都不該呼叫"),
    )
    monkeypatch.setattr(
        bedrock_converse, "converse_text",
        lambda *a, **k: pytest.fail("edge 模式不得呼叫 Bedrock"),
    )

    out = homework.generate_homework({}, _diag(), allow_cloud=False)

    assert out["source"] == "rule"
