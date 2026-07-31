# -*- coding: utf-8 -*-
"""agent_backends.py — 三個 agent 的雲端後端解析（降級鏈的單一真相來源）。

降級鏈：**AgentCore Harness → Bedrock Converse → 規則式**。

為什麼需要這個模組
------------------
三個 agent 各自抄了同一段解析程式碼。抄三份的代價已經付過一次：

    cfg = None if ac_cfg else bedrock_converse.resolve_config(role="diag")

這一行在三個檔案裡一模一樣地錯著——AgentCore 設定一存在就把 Bedrock 設成
None，於是 Harness 失敗直接摔到規則式，**撥開關反而比不撥更差**。三份複本
意味著三個地方要各改一次，也意味著下次還會有第四份。

而 ``scripts/aws_preflight.py`` 需要回報「現在的降級鏈長什麼樣」。它若自己
再判斷一遍就是第四份複本——**一個會漂移的 preflight 比沒有 preflight 更糟**，
它會言之鑿鑿地報告一條實際上不成立的鏈，而現場的人會相信它。

本模組只讀環境變數：不觸網、不碰憑證、不 import boto3。
"""

from __future__ import annotations

from server import agentcore, bedrock_converse

# 三個 agent 的雲端診斷全部用 diag 這顆模型（12s 非同步預算，非對話路徑）。
_BEDROCK_ROLE = "diag"

# 保底。永遠在鏈尾，任何設定組合下都不會消失。
_RULE = "rule"


def resolve(role: str) -> tuple[dict | None, dict | None]:
    """回傳 ``(agentcore_cfg, bedrock_cfg)``；未設定的那個是 None。

    **兩個都要解析。** 這正是本模組存在的理由：先前的寫法讓第二層在第一層
    存在時消失，降級鏈中間整層被跳過。要讓 AgentCore 失敗有地方可降，
    Bedrock 的設定就必須先備好。
    """
    ac_cfg = agentcore.resolve_config(role)
    cfg = bedrock_converse.resolve_config(role=_BEDROCK_ROLE)
    return ac_cfg, cfg


def chain(role: str) -> list[str]:
    """該角色**此刻實際可用**的降級順序，例如 ``["agentcore", "bedrock", "rule"]``。

    給 preflight 印出來用。現場最容易犯又最難察覺的設定錯誤是「撥了
    AgentCore 開關，卻漏設某個角色的 harness ARN」——開關看起來撥了，
    那個角色其實根本沒走 AgentCore。把這條鏈印出來就一眼看得出來。

    這是設定讀數，不是證據：它說的是「會照這個順序試」，不是「試過而且成功」。
    """
    ac_cfg, cfg = resolve(role)
    out: list[str] = []
    if ac_cfg is not None:
        out.append("agentcore")
    if cfg is not None:
        out.append("bedrock")
    out.append(_RULE)
    return out
