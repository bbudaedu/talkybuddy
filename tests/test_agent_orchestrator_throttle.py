# -*- coding: utf-8 -*-
"""test_agent_orchestrator_throttle.py — 節流機制對「真實 store」的行為。

為什麼要另開一個檔而不是加進 test_agent_orchestrator.py：
那個檔的節流測試自己 mock 了 store.list_agent_outputs，回傳 naive 的
`datetime.now().isoformat()`，而真實的 store.add_agent_output 寫出來的是
**aware** 的 `+08:00` 時間戳。於是測試驗的是自己造的假資料，不是真實依賴的
行為——測試全綠，功能卻完全失效（naive 減 aware 拋 TypeError，被
`except Exception: pass` 吞掉，節流永不觸發）。

本檔一律用真實 store（tmp_db fixture 導向暫存 DB），不 mock。
"""

from __future__ import annotations

from server import store
from server.agents import orchestrator


def _diag(date: str, grammar: int) -> dict:
    return {
        "date": date,
        "scores": {
            "pronunciation": 70,
            "fluency": 70,
            "vocabulary": 70,
            "grammar": grammar,
        },
        "strengths": [],
        "weaknesses": [],
    }


_HISTORY = [_diag("07-20", 42), _diag("07-22", 40), _diag("07-24", 38)]


def test_real_store_timestamp_is_parsed_without_error(tmp_db):
    """真實 store 寫出的 ts 必須能被 _should_throttle 正確判讀。

    store 寫的是 aware（+08:00）時間戳。若比較時混用 naive datetime，
    會拋 TypeError 而被吞掉，節流靜默失效——這正是原本的缺陷。
    """
    store.add_agent_output(
        "homework", {"focus": "x", "items": [], "source": "rule"}, student_id="demo"
    )
    # 剛寫入 → 必定在節流窗內
    assert orchestrator._should_throttle("homework", "demo") is True


def test_no_output_means_no_throttle(tmp_db):
    """反例：DB 內沒有任何產出時不得節流，否則第一次永遠派不出去。"""
    assert orchestrator._should_throttle("homework", "demo") is False


def test_throttle_is_scoped_by_student(tmp_db):
    """別的學生剛拿到作業，不該讓這個學生被節流。"""
    store.add_agent_output(
        "homework", {"focus": "x", "items": [], "source": "rule"}, student_id="alice"
    )
    assert orchestrator._should_throttle("homework", "bob") is False


def test_throttle_is_scoped_by_kind(tmp_db):
    """剛發過週報不該擋住派作業，兩者節流各自獨立。"""
    store.add_agent_output(
        "report", {"period": "x", "summary": "y", "source": "rule"}, student_id="demo"
    )
    assert orchestrator._should_throttle("homework", "demo") is False


def test_repeated_decisions_do_not_spam_homework(tmp_db):
    """端到端：連續 6 次決策，每次派發都真的寫進 store。

    孩子每兩回合收到一份新作業是騷擾不是服務。這條測試模擬真實迴圈，
    不 mock 任何東西。

    斷言必須是**恰好 1 次**，不是「小於 6 次」：節流窗（數小時）遠大於這
    6 次迴圈的耗時，所以第一次派發之後全部都該被擋下。寫成 `< 6` 的話，
    派 5 次也會過——那等於沒驗到節流。
    """
    issued = 0
    for turn in range(12, 24, 2):
        decision = orchestrator.decide_next_actions(
            {"student_id": "demo"}, _HISTORY[-1], _HISTORY, turn, allow_cloud=False
        )
        if "homework" in decision["actions"]:
            issued += 1
            store.add_agent_output(
                "homework", {"focus": "x", "items": [], "source": "rule"},
                student_id="demo",
            )
    assert issued == 1, f"6 次決策派了 {issued} 次作業，節流未生效（正解是 1）"
