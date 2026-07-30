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


# ---------------------------------------------------------------------------
# 2026-07-30 真機 S2S 實測暴露的兩件事
# ---------------------------------------------------------------------------

def test_off_topic_questions_get_answered_before_steering_back():
    """孩子問今日主題以外的東西時，要先回應再導回，不能當沒聽到。

    實測：今天的主題是蘋果／雞蛋／香蕉，使用者問「可以教我說鯨魚嗎？」，
    玩偶完全沒有反應、繼續講它的課程。原因是 prompt 寫了「不要漫無目的閒聊」，
    模型照做了。

    這對 demo 是致命的——評審一定會隨口問東西，而「充耳不聞」會被解讀成
    「聽不懂」，不會有人解讀成「守紀律」。教學上也站不住腳：順著孩子的好奇心
    教一個他當下真的想學的字，比硬拉回課程有效得多。
    """
    p = scaffold.build_live_system_prompt("I see a dog.", None, topic="animal")
    assert "不要漫無目的閒聊" not in p, "這句話會讓模型忽略孩子的提問"
    # 要明確授權「先回應題外話」，否則模型仍會傾向守著任務
    assert "問" in p and ("先" in p or "回應" in p)


def test_the_reply_length_limit_is_stated_in_a_way_the_model_actually_follows():
    """回覆長度限制要講得夠具體，否則模型不當一回事。

    實測：prompt 已經寫了「每次回覆不超過兩句話」，但逐字稿每次都是 4 句以上，
    單場下行音訊累積 102 秒。這不只是囉唆——**玩偶講話時上行閘門是關的**
    （半雙工，裝置裝不了 AEC），它講 102 秒，孩子就有 102 秒不能說話。
    使用者的提問就是這樣掉進空檔裡的。

    所以長度限制不是風格偏好，是**讓孩子有機會開口的必要條件**。
    """
    p = scaffold.build_live_system_prompt(None, None)
    assert "兩句" in p
    # 要講出「為什麼」——單純的規則模型會權衡掉，講清楚後果比較守得住。
    # 這裡找的是「說完要把話語權交還給孩子」這個意思，不是任何含「停」的字
    # （既有 prompt 有「稍微停頓」，那是講語速，會讓這條斷言因錯的理由通過）。
    assert "等孩子" in p
