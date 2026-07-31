# -*- coding: utf-8 -*-
"""aws_only.py — 競賽合規閘門：執行期不得有非 AWS 的雲端出境。

為什麼需要這個檔案
------------------
2026 雲湧智生黑客松「AWS 完整開發環境」路線的規範原文：

    競賽僅限使用 Amazon Bedrock、SageMaker AI 所提供之基礎模型、Kiro，
    及 AWS 相關雲端服務進行系統與功能建置

而這個專案在開發期為了把雲端線先跑通，接了三個**非 AWS** 的後端：

    server/gemini_llm.py       → generativelanguage.googleapis.com（Google）
    server/anthropic_relay.py  → api.anthropic.com（Anthropic）
    server/cloud_tts.py        → api.elevenlabs.io（ElevenLabs）

三個都能用、也都跑過真機。**但在競賽期間用任何一個都是違規。**

為什麼是閘門而不是刪掉
----------------------
刪掉會毀掉一條已經驗證過的降級路徑，而且賽後還要用。閘門的好處是：

- 賽後 ``TALKYBUDDY_AWS_ONLY=0`` 一行就恢復，程式碼零損失
- 評審 ``grep`` 得到這個檔，看得出合規是**機制**不是口頭承諾
- 失敗模式是「雲端關掉、退回邊緣離線」——那正是本專案的主張，不是災難

為什麼預設是「開」
------------------
因為忘記開的代價是**失格**，忘記關的代價只是「雲端沒接上、退回離線」。
兩者不對稱，所以預設站在安全那邊。本機開發要用 Gemini/Anthropic/ElevenLabs
時自行 ``export TALKYBUDDY_AWS_ONLY=0``。

這條原則跟 ``server/auth.py`` 的 JWT secret 守衛一樣：**把「記得做某件事」
換成「不做就會被擋下來」**。趕死線的人記不住事，但擋得下來的東西擋得住。
"""

from __future__ import annotations

import os

# 非 AWS 後端的識別字串。與 cloud_llm.configured_backend() 的回傳值一致。
NON_AWS_LLM_BACKENDS = ("gemini", "relay")


def enabled() -> bool:
    """競賽合規模式是否啟用。預設 True（見模組 docstring）。"""
    return os.environ.get("TALKYBUDDY_AWS_ONLY", "1").strip().lower() not in (
        "0", "false", "no", "off", ""
    )


def llm_backend_allowed(backend: str) -> bool:
    """這個 LLM 後端在目前模式下可不可以用。"""
    if not enabled():
        return True
    return str(backend or "").lower() not in NON_AWS_LLM_BACKENDS


def cloud_tts_allowed() -> bool:
    """雲端 TTS（ElevenLabs）可不可以用。競賽模式下一律不行。

    擋掉之後語音自動回落到邊緣 Piper/sherpa-onnx——那是本地合成，
    零出境，而且斷網橋段本來就是靠它。
    """
    return not enabled()


def blocked_reason(backend: str = "") -> str:
    """給 /api/status 用的一句話說明，讓現場看得懂為什麼是關的。"""
    if not enabled():
        return ""
    who = f"（{backend}）" if backend else ""
    return (
        f"競賽合規模式已啟用：非 AWS 後端{who}停用。"
        "賽後可設 TALKYBUDDY_AWS_ONLY=0 恢復。"
    )
