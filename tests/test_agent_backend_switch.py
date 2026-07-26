# -*- coding: utf-8 -*-
"""test_agent_backend_switch.py — 三個 agent 的雲端後端切換（AgentCore ↔ 直呼模型）。

後端優先序：AgentCore Harness → Bedrock Converse → 規則式。

**預設必須完全不變**：未設 TALKYBUDDY_AGENT_BACKEND=agentcore 時，
三個 agent 一行行為都不能變——這是 flag 存在的全部意義。
AgentCore 路徑到 2026-07-26 為止一次都沒真的產出過內容（帳號驗證中），
所以它必須是「開了才走」，不能是「預設走、壞了再退」。
"""

from __future__ import annotations

import json

import pytest

from server import agentcore, bedrock_converse
from server.agents import homework, orchestrator, report

_ENV = [
    "TALKYBUDDY_AGENT_BACKEND", "TALKYBUDDY_CLOUD_PROVIDER",
    "AGENTCORE_REGION", "AGENTCORE_MEMORY_ARN",
    "AGENTCORE_HARNESS_ORCHESTRATOR", "AGENTCORE_HARNESS_HOMEWORK",
    "AGENTCORE_HARNESS_REPORT",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for n in _ENV:
        monkeypatch.delenv(n, raising=False)
    return monkeypatch


def _diag(g: int = 40) -> dict:
    return {
        "date": "07-24",
        "scores": {"pronunciation": 70, "fluency": 70, "vocabulary": 70, "grammar": g},
        "strengths": ["敢開口"], "weaknesses": ["冠詞誤用"],
    }


_HW_JSON = json.dumps({
    "focus": "文法（來自 AgentCore）",
    "items": [{"target_en": "I see a dog.", "prompt_zh": "說一句完整的英文句子！", "why": "練文法"}] * 3,
}, ensure_ascii=False)

_RPT_JSON = json.dumps({
    "period": "最近 3 次", "summary": "來自 AgentCore 的週報。",
    "highlights": ["h"], "concerns": ["c"], "suggestions": ["s"],
}, ensure_ascii=False)

_ORCH_JSON = json.dumps({
    "actions": ["homework"], "reason": "來自 AgentCore 的決策。", "priority": "high",
}, ensure_ascii=False)


def _enable(mp, **arns):
    mp.setenv("TALKYBUDDY_AGENT_BACKEND", "agentcore")
    for k, v in arns.items():
        mp.setenv(k, v)


# ---------------------------------------------------------------------------
# 預設關閉：行為零變更
# ---------------------------------------------------------------------------

def test_agentcore_not_invoked_when_flag_off(_clean_env, monkeypatch):
    """flag 未開時，三個 agent 都不得真的呼叫 AgentCore。

    這裡只擋 invoke 不擋 resolve_config：後者只讀環境變數、不觸網，
    正是偵測 flag 的機制本身。「連 resolve_config 都不碰」的要求
    只適用於 allow_cloud=False 的斷網 kill-switch 情境（見下方另一條測試）。
    """
    def _boom(*a, **k):
        pytest.fail("flag 關閉時不得呼叫 AgentCore")

    monkeypatch.setattr(agentcore, "invoke", _boom)

    homework.generate_homework({}, _diag(), allow_cloud=True)
    report.generate_report({}, [_diag()], allow_cloud=True)
    orchestrator.decide_next_actions({}, _diag(), [_diag()], 12, allow_cloud=True)


def test_flag_off_still_uses_bedrock_converse(_clean_env, monkeypatch):
    """關閉 AgentCore 後，既有的 Bedrock 直呼路徑必須照常運作。"""
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    called: list[str] = []
    monkeypatch.setattr(
        bedrock_converse, "converse_text",
        lambda *a, **k: (called.append("converse"), _HW_JSON)[1],
    )
    out = homework.generate_homework({}, _diag(), allow_cloud=True)
    assert called == ["converse"]
    assert out["source"] == "cloud"


# ---------------------------------------------------------------------------
# 開啟後：走 AgentCore
# ---------------------------------------------------------------------------

def test_homework_uses_agentcore_when_enabled(_clean_env, monkeypatch):
    _enable(_clean_env, AGENTCORE_HARNESS_HOMEWORK="arn:hw")
    monkeypatch.setattr(
        bedrock_converse, "converse_text",
        lambda *a, **k: pytest.fail("啟用 AgentCore 時不得直呼模型"),
    )
    monkeypatch.setattr(agentcore, "invoke", lambda *a, **k: _HW_JSON)

    out = homework.generate_homework({}, _diag(), allow_cloud=True)
    assert out["focus"] == "文法（來自 AgentCore）"
    assert out["source"] == "cloud"


def test_report_uses_agentcore_when_enabled(_clean_env, monkeypatch):
    _enable(_clean_env, AGENTCORE_HARNESS_REPORT="arn:rp")
    monkeypatch.setattr(agentcore, "invoke", lambda *a, **k: _RPT_JSON)
    out = report.generate_report({}, [_diag(), _diag(50)], allow_cloud=True)
    assert out["summary"] == "來自 AgentCore 的週報。"


def test_orchestrator_uses_agentcore_when_enabled(_clean_env, monkeypatch):
    _enable(_clean_env, AGENTCORE_HARNESS_ORCHESTRATOR="arn:or")
    monkeypatch.setattr(agentcore, "invoke", lambda *a, **k: _ORCH_JSON)
    out = orchestrator.decide_next_actions({}, _diag(), [_diag(), _diag(50)], 12,
                                           allow_cloud=True)
    assert out["reason"] == "來自 AgentCore 的決策。"


# ---------------------------------------------------------------------------
# 降級與閘門
# ---------------------------------------------------------------------------

def test_agentcore_failure_falls_back_to_rule(_clean_env, monkeypatch):
    """AgentCore 掛掉不得讓 agent 整個失效，仍要有規則式保底。"""
    _enable(_clean_env, AGENTCORE_HARNESS_HOMEWORK="arn:hw")
    monkeypatch.setattr(
        agentcore, "invoke",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("harness 掛了")),
    )
    out = homework.generate_homework({}, _diag(), allow_cloud=True)
    assert out["source"] == "rule"
    assert len(out["items"]) >= 3


def test_allow_cloud_false_blocks_agentcore_too(_clean_env, monkeypatch):
    """斷網 kill-switch 必須同時擋住 AgentCore——它是雲端服務。

    漏擋這條，離線示範時裝置會偷偷連新加坡，NETCUT 直接破功。
    """
    _enable(_clean_env, AGENTCORE_HARNESS_HOMEWORK="arn:hw")
    monkeypatch.setattr(
        agentcore, "invoke", lambda *a, **k: pytest.fail("edge 模式不得呼叫 AgentCore")
    )
    monkeypatch.setattr(
        agentcore, "resolve_config",
        lambda *a, **k: pytest.fail("edge 模式連 resolve_config 都不該呼叫"),
    )
    out = homework.generate_homework({}, _diag(), allow_cloud=False)
    assert out["source"] == "rule"


def test_agentcore_output_still_passes_guardrail(_clean_env, monkeypatch):
    """換後端不得繞過輸出護欄——兒童安全是硬限制。"""
    _enable(_clean_env, AGENTCORE_HARNESS_HOMEWORK="arn:hw")
    monkeypatch.setattr(agentcore, "invoke", lambda *a, **k: _HW_JSON)
    monkeypatch.setattr(homework.guardrails, "passes_guardrail", lambda t: False)
    out = homework.generate_homework({}, _diag(), allow_cloud=True)
    assert out["source"] == "rule"


def test_agentcore_receives_actor_id_for_memory_scoping(_clean_env, monkeypatch):
    """actor_id 必須傳給 AgentCore，否則所有孩子共用同一份長期記憶。"""
    _enable(_clean_env, AGENTCORE_HARNESS_HOMEWORK="arn:hw")
    seen: dict = {}

    def _spy(cfg, msg, *, session_id=None, actor_id=None, **k):
        seen["actor_id"] = actor_id
        return _HW_JSON

    monkeypatch.setattr(agentcore, "invoke", _spy)
    homework.generate_homework({"student_id": "STUDENT-AMING-004"}, _diag(),
                               allow_cloud=True)
    assert seen["actor_id"] == "STUDENT-AMING-004"
