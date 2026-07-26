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


# ---------------------------------------------------------------------------
# 學生範圍隔離
#
# interactions 與 diagnoses 都有 student_id 隔離，agent_outputs 沒有的話，
# 學生 A 的 token 會讀到學生 B 的作業與家長週報。單一學生 demo 看不出來，
# 但 _resolve_student 的存在說明這個 codebase 有在建模多學生。
# ---------------------------------------------------------------------------

def test_outputs_are_scoped_by_student(tmp_db):
    store.add_agent_output("homework", _HOMEWORK, student_id="alice")
    store.add_agent_output("homework", dict(_HOMEWORK, focus="bob 的"), student_id="bob")

    alice = store.list_agent_outputs(student_id="alice")
    bob = store.list_agent_outputs(student_id="bob")

    assert [r["focus"] for r in alice] == [_HOMEWORK["focus"]]
    assert [r["focus"] for r in bob] == ["bob 的"]


def test_student_filter_combines_with_kind_and_limit(tmp_db):
    """三個條件要能疊加，否則儀表板分頁時會漏資料或串生。"""
    for i in range(3):
        store.add_agent_output("homework", dict(_HOMEWORK, focus=f"a{i}"), student_id="alice")
        store.add_agent_output("report", dict(_REPORT, summary=f"b{i}"), student_id="bob")

    rows = store.list_agent_outputs(kind="homework", student_id="alice", limit=2)
    assert len(rows) == 2
    assert all(r["kind"] == "homework" for r in rows)
    assert [r["focus"] for r in rows] == ["a2", "a1"]


def test_limit_applies_after_student_filter(tmp_db):
    """limit 必須套在過濾後的結果上。

    若先取 limit 筆再過濾，別的學生的資料會把配額吃光，
    當事人反而讀到空清單——這種 bug 在單一學生 demo 上永遠不會現形。
    """
    for i in range(5):
        store.add_agent_output("homework", dict(_HOMEWORK, focus=f"noise{i}"), student_id="bob")
    store.add_agent_output("homework", dict(_HOMEWORK, focus="alice 唯一的"), student_id="alice")

    rows = store.list_agent_outputs(student_id="alice", limit=3)
    assert [r["focus"] for r in rows] == ["alice 唯一的"]


def test_student_id_omitted_returns_all(tmp_db):
    """省略 student_id 維持舊行為（回全部），與 list_diagnoses 一致。"""
    store.add_agent_output("homework", _HOMEWORK, student_id="alice")
    store.add_agent_output("report", _REPORT, student_id="bob")
    assert len(store.list_agent_outputs()) == 2


# ---------------------------------------------------------------------------
# 保留鍵衝突（code review W6）
# ---------------------------------------------------------------------------

def test_payload_reserved_keys_are_rejected(tmp_db):
    """payload 帶 seq / kind / student_id / ts 時必須當場失敗，不得靜默覆寫。

    讀取端會把 DB 欄位攤平進 payload，無條件覆寫同名鍵。若 payload 自己
    也有 ts，寫進去的值讀出來就變成別的東西，而且沒有任何跡象——
    這種靜默的資料損毀，比當場拋錯難查一個數量級。
    """
    import pytest

    for reserved in ("seq", "kind", "student_id", "ts"):
        with pytest.raises(ValueError):
            store.add_agent_output("homework", dict(_HOMEWORK, **{reserved: "x"}))


def test_normal_payload_still_accepted(tmp_db):
    """反例：沒有保留鍵的 payload 照常寫入。"""
    seq = store.add_agent_output("homework", _HOMEWORK)
    assert seq > 0
