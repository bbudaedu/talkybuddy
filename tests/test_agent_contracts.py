# -*- coding: utf-8 -*-
"""test_agent_contracts.py — 三個 agent 的公開契約（code review W1 / W4 / W7）。

契約寫在每個 agent 的 docstring 裡：「任何情況都不往外拋例外」。
但先前的實作並不成立：

- W1：except 區塊裡呼叫的 `_rule_based_*` 自己也可能拋（scaffold.VOCAB 讀壞、
  KeyError…），那一拋就直接穿出公開介面；而且 `allow_cloud=False` 分支
  根本在 try 之外，離線路徑完全沒有保護。呼叫端（pipeline._run_agents）
  雖然有 try，但它的降級是「這一輪什麼都不派」，不是「產出規則式結果」。
- W4：diagnose.py 有 `guardrails.consent_granted()` 這道出境閘門，三個
  agent 卻沒有。同一份資料換個路徑就繞過家長同意。
- W7：規則式作業可能少於契約下限 3 題，而且沒有任何告警。

這些測試把契約當契約驗：把依賴打爆，看公開介面還守不守得住。
"""

from __future__ import annotations

import logging

import pytest


def _boom(*a, **kw):
    raise RuntimeError("依賴爆炸")


_DIAG = {
    "date": "2026-07-20",
    "scores": {"pronunciation": 70, "fluency": 68, "vocabulary": 65, "grammar": 42},
    "strengths": ["願意開口"],
    "weaknesses": ["冠詞不穩"],
    "emotional_status": "積極",
}


# ---------------------------------------------------------------------------
# W1：規則式路徑自己爆掉時，公開介面仍不得拋
# ---------------------------------------------------------------------------

def test_homework_never_raises_when_rule_path_breaks(monkeypatch):
    from server.agents import homework

    monkeypatch.setattr(homework, "_rule_based_homework", _boom)

    out = homework.generate_homework({"student_id": "s1"}, _DIAG, allow_cloud=False)
    assert isinstance(out, dict)
    assert isinstance(out.get("focus"), str) and out["focus"].strip()
    assert isinstance(out.get("items"), list) and len(out["items"]) >= 3
    for item in out["items"]:
        for key in ("target_en", "prompt_zh", "why"):
            assert isinstance(item.get(key), str) and item[key].strip()
    assert out["source"] == "rule"


def test_report_never_raises_when_rule_path_breaks(monkeypatch):
    from server.agents import report

    monkeypatch.setattr(report, "_rule_based_report", _boom)

    out = report.generate_report({"student_id": "s1"}, [_DIAG], allow_cloud=False)
    assert isinstance(out, dict)
    for key in ("period", "summary"):
        assert isinstance(out.get(key), str) and out[key].strip()
    for key in ("highlights", "concerns", "suggestions"):
        assert isinstance(out.get(key), list)
    assert out["source"] == "rule"


def test_orchestrator_never_raises_when_rule_path_breaks(monkeypatch):
    from server.agents import orchestrator

    monkeypatch.setattr(orchestrator, "_rule_based_decision", _boom)

    out = orchestrator.decide_next_actions(
        {"student_id": "s1"}, _DIAG, [_DIAG], 4, allow_cloud=False
    )
    assert isinstance(out, dict)
    # 決策失敗時寧可什麼都不派，也不要亂派
    assert out["actions"] == []
    assert isinstance(out.get("reason"), str) and out["reason"].strip()
    assert out["priority"] in ("low", "normal", "high")
    assert out["source"] == "rule"


@pytest.mark.parametrize("allow_cloud", [True, False])
def test_agents_never_raise_on_garbage_input(allow_cloud):
    """垃圾輸入（None / 型別錯）也不得拋——上游 store 壞掉時就長這樣。"""
    from server.agents import homework, orchestrator, report

    assert isinstance(homework.generate_homework(None, None, allow_cloud=allow_cloud), dict)
    assert isinstance(report.generate_report(None, None, allow_cloud=allow_cloud), dict)
    assert isinstance(
        orchestrator.decide_next_actions(None, None, None, None, allow_cloud=allow_cloud), dict
    )
    assert isinstance(
        homework.generate_homework("不是 dict", 123, allow_cloud=allow_cloud), dict
    )


# ---------------------------------------------------------------------------
# W4：家長同意閘門（consent gate）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("agent_name", ["homework", "report", "orchestrator"])
def test_no_consent_means_no_cloud_at_all(agent_name, monkeypatch):
    """未取得家長同意時，連 resolve_config 都不該呼叫（比照 allow_cloud=False）。

    diagnose.py 有這道閘門，三個 agent 沒有——同一份資料換個路徑就出境。
    """
    from server import agentcore, bedrock_converse, guardrails
    from server.agents import homework, orchestrator, report

    touched: list[str] = []

    def _track(name):
        def _f(*a, **kw):
            touched.append(name)
            return None
        return _f

    monkeypatch.setattr(guardrails, "consent_granted", lambda: False)
    monkeypatch.setattr(bedrock_converse, "resolve_config", _track("bedrock.resolve_config"))
    monkeypatch.setattr(bedrock_converse, "converse_text", _track("bedrock.converse_text"))
    monkeypatch.setattr(agentcore, "resolve_config", _track("agentcore.resolve_config"))
    monkeypatch.setattr(agentcore, "invoke", _track("agentcore.invoke"))

    if agent_name == "homework":
        out = homework.generate_homework({"student_id": "s1"}, _DIAG, allow_cloud=True)
    elif agent_name == "report":
        out = report.generate_report({"student_id": "s1"}, [_DIAG], allow_cloud=True)
    else:
        out = orchestrator.decide_next_actions(
            {"student_id": "s1"}, _DIAG, [_DIAG], 4, allow_cloud=True
        )
    assert out["source"] == "rule", "未同意時不得有雲端產出"
    assert touched == [], f"未同意時仍碰了雲端函式：{touched}"


# ---------------------------------------------------------------------------
# W7：規則式作業的題數下限
# ---------------------------------------------------------------------------

def test_rule_homework_meets_minimum_items_and_warns(monkeypatch, caplog):
    """詞庫縮到只剩一個詞時，仍須湊滿契約下限 3 題，而且要留下告警。

    契約（雲端 schema 驗證）要求 3–5 題。規則式路徑若靜默產出 1 題，
    儀表板上就是一份殘缺的作業，而沒有任何線索指向詞庫。
    """
    from server.agents import homework

    monkeypatch.setattr(homework, "VOCAB", {"蘋果": {"en": "apple", "cat": "food",
                                                    "np": "an apple",
                                                    "sent": "I eat an apple."}})

    with caplog.at_level(logging.WARNING):
        out = homework.generate_homework({}, _DIAG, allow_cloud=False)

    assert len(out["items"]) >= 3, "規則式作業不得少於契約下限 3 題"
    assert any("題" in r.message or "items" in r.message.lower() for r in caplog.records), \
        "題數不足時必須留下告警，否則現場查不到是詞庫的問題"


# ---------------------------------------------------------------------------
# W3：節流缺 student_id 時不得退化成全域
# ---------------------------------------------------------------------------

def test_throttle_without_student_id_does_not_go_global(tmp_db):
    """student_id 缺失時，節流查詢不可以不加 WHERE。

    `store.list_agent_outputs(student_id=None)` 是「所有學生」，於是
    任何一個孩子剛拿到作業，就會把其他所有孩子一起擋住。
    """
    from server import store
    from server.agents import orchestrator

    store.add_agent_output(
        "homework", {"focus": "x", "items": [], "source": "rule"}, student_id="alice"
    )
    assert orchestrator._should_throttle("homework", None) is False, \
        "alice 的作業不該擋住預設學生"
