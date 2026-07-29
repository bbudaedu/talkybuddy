# -*- coding: utf-8 -*-
"""遊戲意圖偵測：孩子講什麼算是要開局、要結束、要答應。

**為什麼是純規則、不經 LLM**：邊緣終端沒有螢幕，開局只能靠聲音，而開局是這條
路徑上最需要確定性的動作。走 LLM 有三個代價：edge 一輪 4–5 秒、輸出不可測、
而且得動 `user_prompt`——`PROMPT_ORDERING_FINDING.md` 已經證明動那裡會讓中文
稱讚的合規率從 5/5 掉到 0/5。純規則則是斷網與連網一模一樣，這本身就是要展示的。

遊戲名一律從 `games.GAMES` 讀。**不在這裡硬編第二份清單**——`games.py` 的註解
已經警告過：兩份清單遲早不同步，而不同步的症狀是「開局沒反應」。
"""

from __future__ import annotations

import re

# 「我要玩」「來玩」「開始玩」……：意圖詞與遊戲名必須同時命中才開局。
# 只講遊戲名不算——「點餐時間到了」是在聊餐廳，不是要玩點餐遊戲。
_START_INTENT = ("玩", "來一局", "開始", "我要", "我想", "play", "let's")

_STOP_RE = re.compile(
    r"不玩|不想玩|別玩|結束遊戲|結束這局|停止|停下來|不要玩|stop|quit",
    re.IGNORECASE,
)

# 回答邀請只認**答句形狀的短句**（整句比對，不是找子字串）。
# 2026-07-29 實測教訓：用子字串比對時，「我心情真的很好」含一個「好」就被當成
# 答應而擅自開局——孩子只是在講心情。答「要不要玩？」的話一定很短，
# 長句一律當作沒回答（交給呼叫端不糾纏地放掉）。
#
# 否定要先比對：「不要」含「要」，順序反過來孩子說不要卻會被開局。
_PARTICLES = r"[啊呀喔哦耶的了吧嘛呢！!。．.～~]*"
_NO_RE = re.compile(
    rf"^(?:不要|不用|不想|不好|不然|別|沒有|no|nope|nah){_PARTICLES}$",
    re.IGNORECASE,
)
_YES_RE = re.compile(
    rf"^(?:好|好啊|要|我要|嗯|對|可以|沒問題|yes|yeah|ok|okay|sure){_PARTICLES}$",
    re.IGNORECASE,
)


def _norm(text) -> str:
    """比對用正規化：去空白、統一大小寫。空/None → 空字串（呼叫端不必先擋）。"""
    return re.sub(r"\s+", "", str(text or "")).lower()


def detect_start(text) -> str | None:
    """要開哪一局？意圖詞與遊戲名同時命中才回 kind，否則 None。"""
    from server import games

    s = _norm(text)
    if not s:
        return None
    if not any(w in s for w in _START_INTENT):
        return None
    for g in games.GAMES:
        for name in (g.get("zh"), g.get("en"), g.get("kind")):
            if name and _norm(name) in s:
                return g["kind"]
    return None


def detect_stop(text) -> bool:
    """是不是想結束這一局。"""
    s = str(text or "")
    return bool(s.strip()) and _STOP_RE.search(s) is not None


def detect_yes_no(text) -> bool | None:
    """對邀請的回應：答應 True／拒絕 False／聽不出來 None。

    None 交給呼叫端當「沒回應」處理——孩子已經卡關了，追問是二次挫折。
    """
    s = _norm(text)
    if not s:
        return None
    if _NO_RE.search(s):
        return False
    if _YES_RE.search(s):
        return True
    return None
