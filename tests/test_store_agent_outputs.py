# -*- coding: utf-8 -*-
"""test_store_agent_outputs.py — agent 產出的持久化（作業／週報）。

派作業 agent（子專案 B）與週報 agent（子專案 C）的產出必須存得下來，
教師儀表板才看得到；否則 agent 跑完就消失，demo 上什麼都證明不了。

用一張 agent_outputs 表以 kind 區分，而非兩張近乎相同的表——之後編排 agent
（子專案 E）若要加新的產出類型，不必再改 schema。
"""

from __future__ import annotations

import pytest

from server import store

_HOMEWORK = {
    "focus": "發音（pronunciation）",
    "items": [
        {"target_en": "I see a dog.", "prompt_zh": "你看過狗嗎？說一句完整的英文句子！", "why": "練習清晰發音"},
    ],
    "source": "rule",
}

_REPORT = {
    "period": "最近 5 次練習",
    "summary": "這週的發音進步明顯。",
    "highlights": ["發音分數上升"],
    "concerns": ["文法仍需加強"],
    "suggestions": ["每天陪讀五分鐘"],
    "source": "rule",
}


def test_add_agent_output_returns_incrementing_seq(tmp_db):
    seq1 = store.add_agent_output("homework", _HOMEWORK)
    seq2 = store.add_agent_output("report", _REPORT)
    assert seq1 == 1
    assert seq2 == seq1 + 1


def test_list_agent_outputs_is_new_to_old(tmp_db):
    store.add_agent_output("homework", dict(_HOMEWORK, focus="第一份"))
    store.add_agent_output("homework", dict(_HOMEWORK, focus="第二份"))
    rows = store.list_agent_outputs()
    assert [r["focus"] for r in rows] == ["第二份", "第一份"]


def test_kind_filter_separates_homework_from_report(tmp_db):
    """儀表板要分開顯示作業與週報，混在一起就沒有意義。"""
    store.add_agent_output("homework", _HOMEWORK)
    store.add_agent_output("report", _REPORT)

    hw = store.list_agent_outputs(kind="homework")
    rp = store.list_agent_outputs(kind="report")

    assert len(hw) == 1 and hw[0]["focus"] == _HOMEWORK["focus"]
    assert len(rp) == 1 and rp[0]["summary"] == _REPORT["summary"]


def test_row_carries_seq_kind_and_ts(tmp_db):
    """每筆都要能回答「這是什麼、什麼時候產的」，否則儀表板排不了序。"""
    store.add_agent_output("homework", _HOMEWORK)
    row = store.list_agent_outputs()[0]
    assert row["seq"] == 1
    assert row["kind"] == "homework"
    assert isinstance(row["ts"], str) and row["ts"]


def test_nested_payload_round_trips(tmp_db):
    """items 是巢狀 list[dict]，存取後結構必須完全一致。"""
    store.add_agent_output("homework", _HOMEWORK)
    row = store.list_agent_outputs()[0]
    assert row["items"] == _HOMEWORK["items"]


def test_limit_is_respected(tmp_db):
    for i in range(5):
        store.add_agent_output("homework", dict(_HOMEWORK, focus=f"第{i}份"))
    assert len(store.list_agent_outputs(limit=2)) == 2


def test_empty_returns_empty_list(tmp_db):
    assert store.list_agent_outputs() == []
    assert store.list_agent_outputs(kind="report") == []


def test_unknown_kind_is_rejected(tmp_db):
    """打錯 kind 會讓產出掉進儀表板永遠讀不到的桶子裡，必須當場失敗而非靜默寫入。"""
    with pytest.raises(ValueError):
        store.add_agent_output("homwork", _HOMEWORK)  # 故意拼錯
