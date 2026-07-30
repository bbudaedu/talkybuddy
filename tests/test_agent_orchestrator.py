# -*- coding: utf-8 -*-
"""test_agent_orchestrator.py — 決策判斷／中央編排 agent 測試（子專案 E）。

測試策略：
- monkeypatch 取代 converse_text，不觸網
- 驗證規格書「行為要求」的全部七條
- 額外驗證品質底線：優異穩定/連續退步/資料不足三種情境的 actions/reason/priority 不同
- 額外驗證節流：最近剛產出過作業時，不會又回傳 homework
- 額外驗證 actions 內容必須是 ["homework","report"] 的子集且不重複
- history 為空/一筆/五筆 三種都不爆
- reason 不得包含趨勢字眼當 history 不足以支撐趨勢時
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

# orchestrator 尚未實作，預期 import 會失敗


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _recent_report(monkeypatch):
    """讓「定期回報保底」在本檔一律不觸發，斷言只反映決策邏輯本身。

    `orchestrator._apply_periodic_report_floor` 會在週報過期（或從未產出過）
    時追加一個 `report` action 與一句 reason。它讀的是**實機 DB**
    （`data/talkybuddy.db`），所以不擋掉的話，本檔多支斷言確切 actions／reason
    的測試會隨那個檔案的內容與當天日期時綠時紅——最糟的一種測試。

    只動 `report`，而且刻意設成 **1 天前**——落在兩個時間窗之間：
    比節流窗（2 小時）舊 → 不節流，退步情境照樣派報告（本檔多支斷言靠這個）；
    比過期窗（7 天）新 → floor 不觸發。其餘 kind 回空（＝從未產出過＝不節流），
    與本檔既有斷言預期的作業派發行為一致。

    需要驗 floor 本身的測試在 `tests/test_agent_orchestrator_stale_report.py`；
    需要自訂產出時間的測試（如 Q4 節流）在函式內再 setattr 一次即可覆蓋這裡。
    """
    from datetime import datetime, timedelta, timezone

    day_ago = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=1)
               ).isoformat(timespec="seconds")

    def _fake(kind=None, limit=20, student_id=None):
        if kind != "report":
            return []
        return [{"kind": kind, "ts": day_ago, "student_id": student_id}]

    monkeypatch.setattr("server.store.list_agent_outputs", _fake)


@pytest.fixture
def profile_mock() -> dict:
    """模擬學生 profile（已去識別化）。"""
    return {
        "student_id": "s001",
        "name": "小明",
        "age": 8,
        "interests": ["動物", "遊戲"],
    }


@pytest.fixture
def diag_excellent() -> dict:
    """模擬優異診斷（四維皆高分）。"""
    return {
        "scores": {
            "pronunciation": 88.0,
            "fluency": 85.0,
            "vocabulary": 90.0,
            "grammar": 87.0,
        },
        "strengths": ["發音清晰", "詞彙豐富"],
        "weaknesses": [],
        "emotional_status": "積極",
    }


@pytest.fixture
def diag_weak_grammar() -> dict:
    """模擬文法弱項診斷（grammar 低分）。"""
    return {
        "scores": {
            "pronunciation": 75.0,
            "fluency": 72.0,
            "vocabulary": 70.0,
            "grammar": 42.0,
        },
        "strengths": [],
        "weaknesses": ["文法需加強", "冠詞錯誤多"],
        "emotional_status": "挫折感",
    }


@pytest.fixture
def history_stable() -> list[dict]:
    """模擬穩定歷史診斷（五筆，分數小幅波動）。"""
    return [
        {"date": "2026-07-20", "scores": {"pronunciation": 74, "fluency": 71, "vocabulary": 69, "grammar": 70}},
        {"date": "2026-07-21", "scores": {"pronunciation": 75, "fluency": 72, "vocabulary": 70, "grammar": 71}},
        {"date": "2026-07-22", "scores": {"pronunciation": 74, "fluency": 73, "vocabulary": 71, "grammar": 70}},
        {"date": "2026-07-23", "scores": {"pronunciation": 75, "fluency": 72, "vocabulary": 70, "grammar": 71}},
        {"date": "2026-07-24", "scores": {"pronunciation": 75, "fluency": 72, "vocabulary": 70, "grammar": 72}},
    ]


@pytest.fixture
def history_declining() -> list[dict]:
    """模擬連續退步診斷（五筆，分數明顯下降）。"""
    return [
        {"date": "2026-07-20", "scores": {"pronunciation": 80, "fluency": 78, "vocabulary": 75, "grammar": 74}},
        {"date": "2026-07-21", "scores": {"pronunciation": 75, "fluency": 73, "vocabulary": 71, "grammar": 68}},
        {"date": "2026-07-22", "scores": {"pronunciation": 70, "fluency": 68, "vocabulary": 66, "grammar": 62}},
        {"date": "2026-07-23", "scores": {"pronunciation": 65, "fluency": 63, "vocabulary": 61, "grammar": 56}},
        {"date": "2026-07-24", "scores": {"pronunciation": 60, "fluency": 58, "vocabulary": 56, "grammar": 50}},
    ]


@pytest.fixture
def history_improving() -> list[dict]:
    """模擬連續進步診斷（五筆，分數明顯上升）。"""
    return [
        {"date": "2026-07-20", "scores": {"pronunciation": 50, "fluency": 48, "vocabulary": 46, "grammar": 44}},
        {"date": "2026-07-21", "scores": {"pronunciation": 55, "fluency": 53, "vocabulary": 51, "grammar": 49}},
        {"date": "2026-07-22", "scores": {"pronunciation": 60, "fluency": 58, "vocabulary": 56, "grammar": 54}},
        {"date": "2026-07-23", "scores": {"pronunciation": 65, "fluency": 63, "vocabulary": 61, "grammar": 59}},
        {"date": "2026-07-24", "scores": {"pronunciation": 70, "fluency": 68, "vocabulary": 66, "grammar": 64}},
    ]


@pytest.fixture
def cloud_mock_decision(monkeypatch) -> MagicMock:
    """模擬雲端回傳合法決策 JSON。"""
    mock = MagicMock(return_value=json.dumps({
        "actions": ["homework"],
        "reason": "根據診斷資料，學生在文法面向需要加強練習，建議派發針對性作業",
        "priority": "high",
        "source": "cloud",
    }))
    monkeypatch.setattr("server.bedrock_converse.converse_text", mock)
    return mock


# ---------------------------------------------------------------------------
# 規格書七條行為要求（B1-B7）
# ---------------------------------------------------------------------------

def test_B1_cloud_path_uses_bedrock_converse(profile_mock, diag_weak_grammar, history_stable, cloud_mock_decision, monkeypatch):
    """B1：雲端路徑走 bedrock_converse.converse_text，cfg=resolve_config(role="diag")。"""
    from server.agents import orchestrator

    resolve_mock = MagicMock(return_value={"provider": "bedrock", "model_id": "anthropic.test"})
    monkeypatch.setattr("server.bedrock_converse.resolve_config", resolve_mock)

    result = orchestrator.decide_next_actions(
        profile=profile_mock,
        diagnosis=diag_weak_grammar,
        history=history_stable,
        turn_count=10,
        allow_cloud=True,
    )

    resolve_mock.assert_called_once_with(role="diag")
    cloud_mock_decision.assert_called_once()
    assert result["source"] == "cloud"
    assert "actions" in result


def test_B2_allow_cloud_false_no_network(profile_mock, diag_weak_grammar, history_stable, monkeypatch):
    """B2：allow_cloud=False 時完全不碰雲端，連 resolve_config 都不呼叫。"""
    from server.agents import orchestrator

    resolve_mock = MagicMock()
    converse_mock = MagicMock()
    monkeypatch.setattr("server.bedrock_converse.resolve_config", resolve_mock)
    monkeypatch.setattr("server.bedrock_converse.converse_text", converse_mock)

    result = orchestrator.decide_next_actions(
        profile=profile_mock,
        diagnosis=diag_weak_grammar,
        history=history_stable,
        turn_count=10,
        allow_cloud=False,
    )

    resolve_mock.assert_not_called()
    converse_mock.assert_not_called()
    assert result["source"] == "rule"
    assert "actions" in result
    assert "reason" in result
    assert "priority" in result


def test_B3_deidentify_before_cloud(profile_mock, diag_weak_grammar, history_stable, monkeypatch):
    """B3：上雲前經 guardrails.deidentify。"""
    from server.agents import orchestrator

    deidentify_calls = []

    def track_deidentify(text: str) -> str:
        deidentify_calls.append(text)
        return text.replace("小明", "***")

    monkeypatch.setattr("server.guardrails.deidentify", track_deidentify)
    resolve_mock = MagicMock(return_value={"provider": "bedrock", "model_id": "test"})
    monkeypatch.setattr("server.bedrock_converse.resolve_config", resolve_mock)
    converse_mock = MagicMock(return_value=json.dumps({
        "actions": ["report"],
        "reason": "趨勢分析",
        "priority": "normal",
        "source": "cloud",
    }))
    monkeypatch.setattr("server.bedrock_converse.converse_text", converse_mock)

    orchestrator.decide_next_actions(
        profile=profile_mock,
        diagnosis=diag_weak_grammar,
        history=history_stable,
        turn_count=10,
        allow_cloud=True,
    )

    # 至少呼叫過 deidentify（profile 中的 name / diagnosis 自由文字）
    assert len(deidentify_calls) > 0


def test_B4_guardrail_check_on_cloud_response(profile_mock, diag_weak_grammar, history_stable, monkeypatch):
    """B4：雲端回覆經 guardrails.passes_guardrail，不通過降級。"""
    from server.agents import orchestrator

    resolve_mock = MagicMock(return_value={"provider": "bedrock", "model_id": "test"})
    monkeypatch.setattr("server.bedrock_converse.resolve_config", resolve_mock)
    converse_mock = MagicMock(return_value="包含禁詞的回應")
    monkeypatch.setattr("server.bedrock_converse.converse_text", converse_mock)
    guardrail_mock = MagicMock(return_value=False)
    monkeypatch.setattr("server.guardrails.passes_guardrail", guardrail_mock)

    result = orchestrator.decide_next_actions(
        profile=profile_mock,
        diagnosis=diag_weak_grammar,
        history=history_stable,
        turn_count=10,
        allow_cloud=True,
    )

    guardrail_mock.assert_called_once()
    # 護欄不通過 → 降級回規則式
    assert result["source"] == "rule"


def test_B5_exception_fallback_to_rule(profile_mock, diag_weak_grammar, history_stable, monkeypatch):
    """B5：任何例外不外拋，一律降級回規則式。"""
    from server.agents import orchestrator

    resolve_mock = MagicMock(return_value={"provider": "bedrock", "model_id": "test"})
    monkeypatch.setattr("server.bedrock_converse.resolve_config", resolve_mock)
    converse_mock = MagicMock(side_effect=RuntimeError("模擬網路失敗"))
    monkeypatch.setattr("server.bedrock_converse.converse_text", converse_mock)

    # 不應拋例外
    result = orchestrator.decide_next_actions(
        profile=profile_mock,
        diagnosis=diag_weak_grammar,
        history=history_stable,
        turn_count=10,
        allow_cloud=True,
    )

    assert result["source"] == "rule"
    assert "actions" in result


def test_B6_rule_based_always_valid_output(profile_mock):
    """B6：規則式路徑永遠能產出合法結果，包含 history 空、diagnosis 空 dict、turn_count=0。"""
    from server.agents import orchestrator

    # history 為空
    result_empty_history = orchestrator.decide_next_actions(
        profile=profile_mock,
        diagnosis={"scores": {"pronunciation": 60, "fluency": 60, "vocabulary": 60, "grammar": 50}},
        history=[],
        turn_count=0,
        allow_cloud=False,
    )
    assert result_empty_history["source"] == "rule"
    assert isinstance(result_empty_history["actions"], list)
    assert isinstance(result_empty_history["reason"], str)
    assert result_empty_history["priority"] in ("low", "normal", "high")

    # diagnosis 為空 dict
    result_empty_diag = orchestrator.decide_next_actions(
        profile=profile_mock,
        diagnosis={},
        history=[{"date": "2026-07-20", "scores": {"pronunciation": 70, "fluency": 70, "vocabulary": 70, "grammar": 70}}],
        turn_count=5,
        allow_cloud=False,
    )
    assert result_empty_diag["source"] == "rule"
    assert isinstance(result_empty_diag["actions"], list)

    # 全部輸入極端值
    result_all_edge = orchestrator.decide_next_actions(
        profile={},
        diagnosis={},
        history=[],
        turn_count=0,
        allow_cloud=False,
    )
    assert result_all_edge["source"] == "rule"
    assert isinstance(result_all_edge["actions"], list)
    assert isinstance(result_all_edge["reason"], str)
    assert result_all_edge["priority"] in ("low", "normal", "high")


def test_B7_actions_must_be_subset_of_valid_list():
    """B7：actions 內容必須是 ["homework", "report"] 的子集，且不得重複。"""
    from server.agents import orchestrator

    # 正向測試：合法 actions
    result_hw = orchestrator.decide_next_actions(
        profile={},
        diagnosis={"scores": {"pronunciation": 60, "fluency": 60, "vocabulary": 60, "grammar": 40}},
        history=[],
        turn_count=5,
        allow_cloud=False,
    )
    actions = result_hw["actions"]
    assert isinstance(actions, list)
    for a in actions:
        assert a in ["homework", "report"], f"actions 包含非法值：{a}"
    # 不得重複
    assert len(actions) == len(set(actions)), "actions 包含重複元素"

    # 空 actions 也合法
    result_empty_actions = orchestrator.decide_next_actions(
        profile={},
        diagnosis={"scores": {"pronunciation": 85, "fluency": 85, "vocabulary": 85, "grammar": 85}},
        history=[{"date": "2026-07-20", "scores": {"pronunciation": 84, "fluency": 84, "vocabulary": 84, "grammar": 84}}],
        turn_count=1,
        allow_cloud=False,
    )
    assert isinstance(result_empty_actions["actions"], list)


# ---------------------------------------------------------------------------
# 品質底線測試（Q1-Q5）
# ---------------------------------------------------------------------------

def test_Q1_different_inputs_produce_different_outputs(diag_excellent, diag_weak_grammar, history_stable, history_declining, profile_mock):
    """Q1：不同輸入產生不同決策（優異穩定 vs 連續退步，actions/reason/priority 三者都不同）。"""
    from server.agents import orchestrator

    # 優異穩定情境：分數高且穩定
    result_excellent = orchestrator.decide_next_actions(
        profile=profile_mock,
        diagnosis=diag_excellent,
        history=history_stable,
        turn_count=10,
        allow_cloud=False,
    )

    # 連續退步情境：分數低且下降
    result_declining = orchestrator.decide_next_actions(
        profile=profile_mock,
        diagnosis=diag_weak_grammar,
        history=history_declining,
        turn_count=10,
        allow_cloud=False,
    )

    # actions 應不同（優異穩定傾向空或低頻，退步傾向派作業/報告）
    assert result_excellent["actions"] != result_declining["actions"], "優異與退步情境的 actions 應不同"

    # reason 應不同（內容描述不同情況）
    assert result_excellent["reason"] != result_declining["reason"], "優異與退步情境的 reason 應不同"

    # priority 應不同（退步情境優先級應更高）
    assert result_excellent["priority"] != result_declining["priority"], "優異與退步情境的 priority 應不同"


def test_Q2_insufficient_data_honest_reporting(profile_mock):
    """Q2：資料不足時不虛構趨勢，reason 誠實說明「觀察中，資料還不夠」。"""
    from server.agents import orchestrator

    # history 為空
    result_empty = orchestrator.decide_next_actions(
        profile=profile_mock,
        diagnosis={"scores": {"pronunciation": 60, "fluency": 60, "vocabulary": 60, "grammar": 50}},
        history=[],
        turn_count=1,
        allow_cloud=False,
    )
    reason_empty = result_empty["reason"]
    # reason 不得包含「進步」「退步」「趨勢」等關鍵詞
    forbidden = ["進步", "退步", "趨勢", "持續", "下降", "上升"]
    for word in forbidden:
        assert word not in reason_empty, f"history 為空時 reason 不應包含「{word}」"

    # history 只有一筆
    result_one = orchestrator.decide_next_actions(
        profile=profile_mock,
        diagnosis={"scores": {"pronunciation": 65, "fluency": 63, "vocabulary": 60, "grammar": 55}},
        history=[{"date": "2026-07-20", "scores": {"pronunciation": 70, "fluency": 70, "vocabulary": 70, "grammar": 70}}],
        turn_count=2,
        allow_cloud=False,
    )
    reason_one = result_one["reason"]
    for word in forbidden:
        assert word not in reason_one, f"history 只有一筆時 reason 不應包含「{word}」"


def test_Q3_reason_is_complete_sentence(profile_mock, diag_weak_grammar, history_stable):
    """Q3：reason 必須是通順完整的中文句子，不是欄位拼接。"""
    from server.agents import orchestrator

    result = orchestrator.decide_next_actions(
        profile=profile_mock,
        diagnosis=diag_weak_grammar,
        history=history_stable,
        turn_count=10,
        allow_cloud=False,
    )

    reason = result["reason"]
    assert len(reason) > 10, "reason 太短，不像完整句子"
    # 簡單語意檢查：不應是純數字或純英文鍵名拼接
    assert not reason.replace(" ", "").replace(",", "").isdigit(), "reason 不應是純數字"
    # 應包含中文標點或至少一個中文句號
    assert any(c in reason for c in ["，", "。", "、", "；"]), "reason 缺少中文標點，不像通順句子"


def test_Q4_throttling_based_on_recent_outputs(profile_mock, diag_weak_grammar, history_stable, monkeypatch):
    """Q4：節流機制——最近剛產出過作業時，不會又回傳 homework。"""
    from server.agents import orchestrator

    # mock store.list_agent_outputs 回傳最近剛產出過 homework
    def mock_list_agent_outputs(kind=None, limit=20, student_id=None):
        if kind == "homework":
            # 模擬最近 1 分鐘內產出過 homework
            from datetime import datetime, timedelta
            recent_ts = (datetime.now() - timedelta(seconds=30)).isoformat()
            return [
                {
                    "seq": 1,
                    "kind": "homework",
                    "student_id": "s001",
                    "ts": recent_ts,
                    "focus": "文法",
                    "items": [],
                    "source": "rule",
                }
            ]
        return []

    monkeypatch.setattr("server.store.list_agent_outputs", mock_list_agent_outputs)

    result = orchestrator.decide_next_actions(
        profile=profile_mock,
        diagnosis=diag_weak_grammar,
        history=history_stable,
        turn_count=10,
        allow_cloud=False,
    )

    # 最近剛產出過 homework → 不應再回傳 homework
    assert "homework" not in result["actions"], "最近剛產出過作業，不應再派作業（節流失敗）"


def test_Q5_history_length_variations(profile_mock, diag_weak_grammar, history_stable):
    """Q5：history 為空/一筆/五筆 三種都不爆。"""
    from server.agents import orchestrator

    # 空
    result_empty = orchestrator.decide_next_actions(
        profile=profile_mock,
        diagnosis=diag_weak_grammar,
        history=[],
        turn_count=1,
        allow_cloud=False,
    )
    assert "actions" in result_empty

    # 一筆
    result_one = orchestrator.decide_next_actions(
        profile=profile_mock,
        diagnosis=diag_weak_grammar,
        history=history_stable[:1],
        turn_count=2,
        allow_cloud=False,
    )
    assert "actions" in result_one

    # 五筆
    result_five = orchestrator.decide_next_actions(
        profile=profile_mock,
        diagnosis=diag_weak_grammar,
        history=history_stable,
        turn_count=10,
        allow_cloud=False,
    )
    assert "actions" in result_five


# ---------------------------------------------------------------------------
# 進步情境測試（額外驗證不同趨勢差異）
# ---------------------------------------------------------------------------

def test_improving_trend_different_from_stable(profile_mock, diag_weak_grammar, history_improving, history_stable):
    """進步情境與穩定情境的決策應不同。"""
    from server.agents import orchestrator

    result_improving = orchestrator.decide_next_actions(
        profile=profile_mock,
        diagnosis=diag_weak_grammar,
        history=history_improving,
        turn_count=10,
        allow_cloud=False,
    )

    result_stable = orchestrator.decide_next_actions(
        profile=profile_mock,
        diagnosis=diag_weak_grammar,
        history=history_stable,
        turn_count=10,
        allow_cloud=False,
    )

    # reason 應不同（進步 vs 穩定的描述不同）
    assert result_improving["reason"] != result_stable["reason"], "進步與穩定情境的 reason 應不同"
    # priority 可能不同（進步可能優先級較低）
    # actions 可能不同（進步情境可能減少派作業頻率）
