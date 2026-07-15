# -*- coding: utf-8 -*-
"""build_live_system_prompt：即時 S2S 鷹架 system prompt 組裝（純函式）。"""
from __future__ import annotations

from server import scaffold


def test_static_frame_present():
    p = scaffold.build_live_system_prompt(None, None)
    assert "說說學伴" in p                    # 企鵝角色
    assert "繁體中文" in p                     # 語言規則
    assert "英" in p                           # 帶讀英文
    # 兒童安全護欄折入（重用 guardrails 常數的關鍵字）
    assert "個人資料" in p or "個資" in p or "難過" in p
    assert "教練" in p and "跟讀" in p         # 教練角色 + 跟讀迴圈
    assert "放慢" in p                          # 語速放慢指示（Nova Sonic 無原生 speed 參數）


def test_dynamic_target_and_directive_folded():
    p = scaffold.build_live_system_prompt(
        "I want to eat an apple.",
        "【本輪教學策略】目標：食物主題；難度：維持難度。",
    )
    assert "I want to eat an apple." in p
    assert "本輪教學策略" in p


def test_none_directive_no_crash():
    p = scaffold.build_live_system_prompt("How are you today?", None)
    assert "How are you today?" in p
    assert isinstance(p, str) and len(p) > 0


def test_live_prompt_coach_and_shadowing():
    p = scaffold.build_live_system_prompt("I see a dog.", None, topic="animal")
    assert "教練" in p
    assert "跟讀" in p
    assert "I see a dog." in p
    assert "animal" in p


def test_live_prompt_backward_compat_two_args():
    p = scaffold.build_live_system_prompt(None, None)
    assert isinstance(p, str) and p
    assert "說說學伴" in p


def test_live_prompt_folds_directive():
    p = scaffold.build_live_system_prompt("Hi.", "【本輪策略】多鼓勵。", topic="food")
    assert "【本輪策略】多鼓勵。" in p
