# -*- coding: utf-8 -*-
"""child_brief.py — 把「這個孩子是誰」濃縮成一段可以塞進 system prompt 的話。

## 為什麼在這裡做，而不是讓玩偶去查

`profile.build_profile` 早就抽出了 `interests`（喜歡什麼）、`learning_vocab`
（正在學的字）、`error_patterns`（常犯的錯）、`emotional_recent`（最近的情緒），
`store.list_due_word_reviews` 也早就知道哪些字該複習了。**缺的不是資料，是沒有
人把它餵給玩偶。** 那些東西目前只流向教師儀表板，玩偶自己看不到。

但玩偶**不可以每一輪**去查——對話路徑的預算是 `cloud_llm._TIMEOUT_S`（1.5s），
多一次 I/O 就少一分餘裕。所以這個模組只在**開場呼叫一次**，把結果塞進 system
prompt，之後每輪都是零成本。

`docs/AGENTCORE_ARCHITECTURE.md` §7 的結論也是同一句：「對話路徑不該上
AgentCore……建議只搬非同步路徑」。記憶的**產生**可以是雲端、非同步、慢慢來；
記憶的**使用**必須是本地、開場一次、快。

## 為什麼是一段中文，不是一包 JSON

它要進 system prompt 給 LLM 讀。人話比結構化欄位更容易被模型正確使用，也更
容易在出事時一眼看出哪裡不對——現場要 debug 的是「玩偶為什麼講這句」，
不是「第三個欄位是不是 null」。

## 沒有資料時回 None

第一次見到這個孩子就不該假裝認識他。回 None，呼叫端就不注入，玩偶用預設的
開場——**寧可不說，也不要說錯**。
"""

from __future__ import annotations

# 摘要裡最多列幾個項目。列太多會佔掉 system prompt 的篇幅，也會讓模型抓不到
# 重點——它只需要「足以講出一句像樣的招呼」，不是完整病歷。
_MAX_ITEMS = 3


def _names(items, key: str = "en") -> list[str]:
    """從 profile 的詞條清單取名字；容忍 dict 與純字串兩種形狀。"""
    out: list[str] = []
    for v in items or []:
        name = v.get(key) if isinstance(v, dict) else v
        if name and str(name).strip():
            out.append(str(name).strip())
        if len(out) >= _MAX_ITEMS:
            break
    return out


def build_child_brief(profile=None, due_words=None, diagnoses=None) -> str | None:
    """組出一段「我認識這個孩子」的中文摘要；資料不足回 None。

    Args:
        profile: ``store.get_profile()`` 的輸出（可 None）。
        due_words: ``store.list_due_word_reviews()`` 的輸出（可 None）。
        diagnoses: ``store.list_diagnoses()`` 的輸出（可 None），只用最後一筆。

    Returns:
        可直接接進 system prompt 的一段話；完全沒有資料時回 None。
    """
    profile = profile or {}
    parts: list[str] = []

    try:
        rounds = int(profile.get("interaction_count") or 0)
    except (TypeError, ValueError):
        rounds = 0
    if rounds > 0:
        parts.append(f"你以前跟這個孩子聊過 {rounds} 次了，不是第一次見面。")

    # 優先用中文 label（動物／動作），不是英文 topic key。玩偶會把它唸出來，
    # 講「他喜歡聊 animal」在一個台灣國小的對話裡非常突兀。
    interests = (
        _names(profile.get("interests"), key="label")
        or _names(profile.get("interests"), key="topic")
        or _names(profile.get("interests"))
    )
    if interests:
        parts.append(
            "他平常喜歡聊" + "、".join(interests) + "，"
            "想舉例子或換話題的時候優先用這些他有興趣的東西。"
        )

    learning = _names(profile.get("learning_vocab"))
    if learning:
        parts.append("他最近在學的字有 " + "、".join(learning) + "。")

    due = _names(due_words, key="word")
    if due:
        parts.append(
            "這幾個字他之前學過但該複習了：" + "、".join(due)
            + "，找機會自然地帶到，不要像考試。"
        )

    mastered = _names(profile.get("mastered_vocab"))
    if mastered:
        parts.append("他已經很熟的字有 " + "、".join(mastered) + "，可以拿來稱讚他。")

    emotional = profile.get("emotional_recent")
    if emotional and str(emotional).strip():
        parts.append(f"上次聊天他的狀態是「{str(emotional).strip()}」，今天留意一下。")

    try:
        latest = (diagnoses or [])[-1] if (diagnoses or []) else None
    except Exception:
        latest = None
    if isinstance(latest, dict):
        weak = latest.get("weakest_dim") or latest.get("weakest")
        if weak and str(weak).strip():
            parts.append(f"他目前最弱的是{str(weak).strip()}，多給一點這方面的練習。")

    if not parts:
        return None
    # 開頭那句是給模型的使用說明——沒有它，模型容易把畫像當成要唸出來的內容。
    return (
        "【你對這個孩子的記憶】"
        + "".join(parts)
        + "這些是你記得的事，**不要一口氣講出來**，"
        "只在自然的時候提一兩件，讓他覺得你真的記得他。"
    )
