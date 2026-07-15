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
    assert "放慢" in p and "咬字" in p         # 語速放慢指示（Nova Sonic 無原生 speed 參數）


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
