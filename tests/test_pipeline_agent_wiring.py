# -*- coding: utf-8 -*-
"""test_pipeline_agent_wiring.py — 三個 agent 接進 _refresh_directive 的行為。

接線點選在 _refresh_directive 而非即時路徑：那裡是既有的回合後背景鉤子，
跑在 asyncio.to_thread、已有 allow_cloud 閘門。即時語音迴圈只有 1.5 秒預算，
任何新的同步決策都會吃掉它。

本檔驗的是「接線」本身，不是各 agent 的內部邏輯（那些有自己的測試檔）。
"""

from __future__ import annotations

import pytest

from server import store
from server.agents import orchestrator
from server.pipeline import VoicePipeline


class _StubTTS:
    def available(self) -> bool:
        return False

    def synth(self, *a, **k):
        return None


def _pipeline(mode: str = "cloud") -> VoicePipeline:
    vp = VoicePipeline(asr=None, llm=None, tts=_StubTTS())
    vp.network_mode = mode
    return vp


@pytest.mark.anyio
async def test_orchestrator_decision_is_persisted_as_agent_output(tmp_db, monkeypatch):
    """編排決定派作業時，作業必須真的被產出並存進 store。

    沒有這一步，agent 跑完產出就消失，教師儀表板什麼都看不到。
    """
    monkeypatch.setattr(
        orchestrator, "decide_next_actions",
        lambda *a, **k: {"actions": ["homework"], "reason": "測試用",
                         "priority": "high", "source": "rule"},
    )

    await _pipeline()._refresh_directive()

    rows = store.list_agent_outputs(kind="homework")
    assert len(rows) == 1
    assert rows[0]["kind"] == "homework"


@pytest.mark.anyio
async def test_empty_actions_persists_nothing(tmp_db, monkeypatch):
    """編排決定什麼都不做時，不可以硬派——那是騷擾。"""
    monkeypatch.setattr(
        orchestrator, "decide_next_actions",
        lambda *a, **k: {"actions": [], "reason": "觀察中",
                         "priority": "low", "source": "rule"},
    )

    await _pipeline()._refresh_directive()

    assert store.list_agent_outputs() == []


@pytest.mark.anyio
async def test_both_actions_persist_both_kinds(tmp_db, monkeypatch):
    monkeypatch.setattr(
        orchestrator, "decide_next_actions",
        lambda *a, **k: {"actions": ["homework", "report"], "reason": "兩者都要",
                         "priority": "high", "source": "rule"},
    )

    await _pipeline()._refresh_directive()

    assert len(store.list_agent_outputs(kind="homework")) == 1
    assert len(store.list_agent_outputs(kind="report")) == 1


@pytest.mark.anyio
async def test_edge_mode_propagates_allow_cloud_false_to_all_agents(tmp_db, monkeypatch):
    """斷網橋段的核心：kill-switch 必須傳到三個 agent，不能只擋住 diagnose。

    漏傳任何一個，離線示範時就會有元件偷偷出境，這正是 NETCUT 要防的事。
    """
    seen: dict = {}

    monkeypatch.setattr(
        orchestrator, "decide_next_actions",
        lambda p, d, h, t, *, allow_cloud=True: (
            seen.__setitem__("orchestrator", allow_cloud),
            {"actions": ["homework", "report"], "reason": "r",
             "priority": "normal", "source": "rule"},
        )[1],
    )
    from server.agents import homework, report
    monkeypatch.setattr(
        homework, "generate_homework",
        lambda p, d, *, allow_cloud=True: (
            seen.__setitem__("homework", allow_cloud),
            {"focus": "f", "items": [], "source": "rule"},
        )[1],
    )
    monkeypatch.setattr(
        report, "generate_report",
        lambda p, ds, *, allow_cloud=True: (
            seen.__setitem__("report", allow_cloud),
            {"period": "p", "summary": "s", "highlights": [], "concerns": [],
             "suggestions": [], "source": "rule"},
        )[1],
    )

    await _pipeline("edge")._refresh_directive()

    assert seen == {"orchestrator": False, "homework": False, "report": False}


@pytest.mark.anyio
async def test_agent_failure_does_not_break_the_turn_loop(tmp_db, monkeypatch):
    """任一 agent 爆炸都不可以讓背景刷新整個掛掉。

    _refresh_directive 失敗 = directive 停止更新 = 導師層在現場悄悄死掉。
    """
    monkeypatch.setattr(
        orchestrator, "decide_next_actions",
        lambda *a, **k: {"actions": ["homework", "report"], "reason": "r",
                         "priority": "normal", "source": "rule"},
    )
    from server.agents import homework
    monkeypatch.setattr(
        homework, "generate_homework",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("作業 agent 爆了")),
    )

    vp = _pipeline()
    await vp._refresh_directive()  # 不得拋出

    # 壞掉的那個沒產出，另一個仍要照常完成
    assert store.list_agent_outputs(kind="homework") == []
    assert len(store.list_agent_outputs(kind="report")) == 1


@pytest.mark.anyio
async def test_orchestrator_failure_falls_back_to_no_actions(tmp_db, monkeypatch):
    """編排本身爆掉時，寧可什麼都不派，也不要亂派。"""
    monkeypatch.setattr(
        orchestrator, "decide_next_actions",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("編排爆了")),
    )

    await _pipeline()._refresh_directive()  # 不得拋出

    assert store.list_agent_outputs() == []
