# -*- coding: utf-8 -*-
"""test_agent_orchestrator_stale_report.py — 週報過期補派（定期回報保底）。

為什麼有這道 floor：`_rule_based_decision` 只在 `overall_trend == "declining"`
時派 report，於是**一個持續進步的孩子，家長永遠收不到週報**。
2026-07-30 端到端驗收撞到這個形狀——demo 學生四維 89/67/67/64、趨勢 improving，
跑完六輪對話 `actions` 仍是 `[]`，教師儀表板只有四天前的舊卡片。

本檔釘住的性質：
- 過期就補（含從未產出過），不論趨勢
- 不繞過節流、不重複、不動 priority、不覆寫既有 reason、不改 source
- 雲端與規則式兩條路徑一視同仁
- store 讀不到時保守處理（不拋、不無限補派）

一律 monkeypatch `server.store.list_agent_outputs`，比照
`tests/test_agent_orchestrator.py` 既有寫法，**不得依賴實機 DB**。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from server.agents import orchestrator

_TZ = timezone(timedelta(hours=8))


def _ts(days_ago: float) -> str:
    return (datetime.now(_TZ) - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _mock_outputs(monkeypatch, report_days_ago: float | None, homework_days_ago=None):
    """讓 store.list_agent_outputs 回指定新舊的產出；None = 從未產出過。"""

    def _fake(kind=None, limit=20, student_id=None):
        age = report_days_ago if kind == "report" else homework_days_ago
        if age is None:
            return []
        return [{"kind": kind, "ts": _ts(age), "student_id": student_id}]

    monkeypatch.setattr("server.store.list_agent_outputs", _fake)


# 趨勢向上的學生：規則式 base decision 會是 actions=[]、priority=low
_PROFILE = {"student_id": "STUDENT-TEST-001"}
_DIAG_IMPROVING = {"scores": {"pronunciation": 89, "fluency": 67,
                              "vocabulary": 67, "grammar": 64}}
_HISTORY_IMPROVING = [
    {"scores": {"pronunciation": 60, "fluency": 55, "vocabulary": 58, "grammar": 52}},
    {"scores": {"pronunciation": 70, "fluency": 60, "vocabulary": 62, "grammar": 57}},
    {"scores": {"pronunciation": 80, "fluency": 64, "vocabulary": 65, "grammar": 61}},
]


def _decide():
    return orchestrator.decide_next_actions(
        _PROFILE, _DIAG_IMPROVING, _HISTORY_IMPROVING, 5, allow_cloud=False,
    )


# ---------------------------------------------------------------------------

def test_baseline_improving_student_gets_nothing_without_floor(monkeypatch):
    """對照組：週報是新的 → 維持原本的「什麼都不派」。

    先釘住 base 行為，後面的斷言才證明得了是 floor 起的作用。
    """
    _mock_outputs(monkeypatch, report_days_ago=1)

    result = _decide()

    assert result["actions"] == []
    assert result["priority"] == "low"


def test_stale_report_is_dispatched_for_improving_student(monkeypatch):
    """週報超過 7 天 → 即使趨勢向上也補派一份。"""
    _mock_outputs(monkeypatch, report_days_ago=8)

    result = _decide()

    assert "report" in result["actions"]
    assert "7 天" in result["reason"]
    assert "定期回報" in result["reason"]


def test_never_produced_report_counts_as_stale(monkeypatch):
    """從未產出過週報 → 視同過期（＝無限久以前），第一次就要補。"""
    _mock_outputs(monkeypatch, report_days_ago=None)

    assert "report" in _decide()["actions"]


def test_floor_does_not_raise_priority(monkeypatch):
    """定期回報不是高優先事件；也保護 test_Q1 的 priority 斷言。"""
    _mock_outputs(monkeypatch, report_days_ago=8)

    assert _decide()["priority"] == "low"


def test_floor_keeps_original_reason(monkeypatch):
    """原本那句趨勢判斷仍然成立，只能追加、不能覆寫。"""
    _mock_outputs(monkeypatch, report_days_ago=1)
    base_reason = _decide()["reason"]

    _mock_outputs(monkeypatch, report_days_ago=8)
    floored_reason = _decide()["reason"]

    assert base_reason.rstrip("。") in floored_reason
    assert len(floored_reason) > len(base_reason)


def test_floor_never_adds_homework(monkeypatch):
    """作業是需求驅動（弱項／到期詞），無條件補等於騷擾。"""
    _mock_outputs(monkeypatch, report_days_ago=None, homework_days_ago=None)

    assert "homework" not in _decide()["actions"]


def test_floor_does_not_duplicate_existing_report(monkeypatch):
    """base decision 已含 report → 不得出現兩筆。"""
    _mock_outputs(monkeypatch, report_days_ago=8)
    declining_history = [
        {"scores": {"pronunciation": 80, "fluency": 78, "vocabulary": 76, "grammar": 74}},
        {"scores": {"pronunciation": 70, "fluency": 68, "vocabulary": 66, "grammar": 64}},
        {"scores": {"pronunciation": 60, "fluency": 58, "vocabulary": 56, "grammar": 54}},
    ]
    diag_declining = {"scores": {"pronunciation": 55, "fluency": 53,
                                 "vocabulary": 51, "grammar": 49}}

    result = orchestrator.decide_next_actions(
        _PROFILE, diag_declining, declining_history, 5, allow_cloud=False,
    )

    assert result["actions"].count("report") == 1


def test_floor_applies_to_cloud_decision_and_keeps_source(monkeypatch):
    """雲端回 actions=[] 時 floor 一樣生效，且 source 仍是 cloud。

    定期回報不該取決於模型當下的判斷——這正是把 floor 放在共同出口的理由。
    """
    _mock_outputs(monkeypatch, report_days_ago=8)
    monkeypatch.setattr(orchestrator.guardrails, "consent_granted", lambda: True)
    monkeypatch.setattr(orchestrator.guardrails, "passes_guardrail", lambda _t: True)
    monkeypatch.setattr(orchestrator.agentcore, "resolve_config", lambda _r: None)
    monkeypatch.setattr(
        orchestrator.bedrock_converse, "resolve_config",
        lambda role=None: {"region": "us-east-1", "model_id": "m"},
    )
    monkeypatch.setattr(
        orchestrator.bedrock_converse, "converse_text",
        lambda *a, **k: '{"actions": [], "reason": "觀察中。", '
                        '"priority": "low", "source": "cloud"}',
    )

    result = orchestrator.decide_next_actions(
        _PROFILE, _DIAG_IMPROVING, _HISTORY_IMPROVING, 5, allow_cloud=True,
    )

    assert result["source"] == "cloud"
    assert "report" in result["actions"]


def test_store_failure_does_not_dispatch(monkeypatch):
    """store 讀取失敗 → 不拋例外，也**不補派**。

    「查不到」與「從沒產出過」是兩件事。DB 壞掉時我們根本沒有證據，
    此時每一輪刷新都多送一份週報只是在錯誤狀態上疊加動作。
    """

    def _boom(kind=None, limit=20, student_id=None):
        raise RuntimeError("DB 壞了")

    monkeypatch.setattr("server.store.list_agent_outputs", _boom)

    result = _decide()   # 不得拋

    assert result["actions"] == []
    assert result["source"] == "rule"


def test_missing_timestamp_does_not_dispatch(monkeypatch):
    """產出存在但 ts 欄位壞掉／缺失 → 同樣是「沒有證據」，不補派。"""

    def _no_ts(kind=None, limit=20, student_id=None):
        return [{"kind": kind, "ts": "", "student_id": student_id}]

    monkeypatch.setattr("server.store.list_agent_outputs", _no_ts)

    assert _decide()["actions"] == []


def test_stale_floor_does_not_bypass_throttle(monkeypatch):
    """節流窗（2 小時）內剛產出過 → 絕不可能同時滿足「超過 7 天」。

    釘住兩個時間窗的方向一致，避免日後有人把 _STALE_REPORT_S 調到比
    _THROTTLE_REPORT_S 還短，讓 floor 變成繞過節流的後門。
    """
    assert orchestrator._STALE_REPORT_S > orchestrator._THROTTLE_REPORT_S

    _mock_outputs(monkeypatch, report_days_ago=0.01)   # 約 15 分鐘前

    assert orchestrator._should_throttle("report", "STUDENT-TEST-001") is True
    assert orchestrator._report_is_stale("STUDENT-TEST-001") is False
