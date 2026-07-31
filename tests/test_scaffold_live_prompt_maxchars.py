# -*- coding: utf-8 -*-
"""build_live_system_prompt 的 max_chars：半雙工玩偶必須講短話。

2026-07-31 模擬對話量到：即時陪聊契約下玩偶回覆最長 76 字，板子上唸出來約
17 秒。**那 17 秒孩子的麥克風是關著的**——喇叭與麥克風同在玩偶內、板子裝不了
AEC，所以玩偶講話時一律不聽（`AlwaysUserMuteStrategy`）。

教練 prompt 自己就寫著「你多講一句，他就多一句話的時間不能開口」，但它給的
限制是「不超過兩句話」，模型照樣寫出三個子句的長句。回合式那份寫的是「不超過
60個字」——**具體字數才管得住**。

所以補一個可選的字數上限。預設 None＝行為完全不變，`/ws/live`（Nova Sonic）
那條路不受影響。
"""
from __future__ import annotations

from server.scaffold import build_live_system_prompt


def test_default_has_no_hard_char_limit():
    """不給就跟以前一樣——/ws/live 零迴歸。"""
    out = build_live_system_prompt("I see a dog.", None, "animal")
    assert "個字" not in out.split("每次回覆不超過兩句話")[-1][:200] or "40" not in out


def test_max_chars_appends_an_explicit_limit():
    out = build_live_system_prompt("I see a dog.", None, "animal", max_chars=40)
    assert "40" in out
    assert "字" in out


def test_max_chars_keeps_everything_else():
    """加上限不可以把教練人設或目標句擠掉。"""
    base = build_live_system_prompt("I see a dog.", "本輪策略：多鼓勵", "animal")
    out = build_live_system_prompt("I see a dog.", "本輪策略：多鼓勵", "animal",
                                   max_chars=40)
    assert base in out
    assert "I see a dog." in out
    assert "本輪策略：多鼓勵" in out


def test_zero_or_none_is_ignored():
    """0 或負數視同沒給，不要產生「不超過0個字」這種指令。"""
    for bad in (0, -5, None):
        out = build_live_system_prompt("I see a dog.", None, "animal", max_chars=bad)
        assert "不超過0個字" not in out and "不超過-5個字" not in out
