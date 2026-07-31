# -*- coding: utf-8 -*-
"""material.py — 教材提煉 agent（子專案 F）。

公開契約：
    extract_vocab(text: str, *, allow_cloud: bool = True) -> dict

回傳固定 schema（雲端與規則式格式一致）：
    {
        "topic": str,              # 這份教材的主題，人話描述
        "entries": [               # 通過驗證、已合併進 VOCAB 的詞條
            {"en": str, "zh": str, "cat": str, "np": str, "sent": str}
        ],
        "accepted_count": int,
        "rejected_count": int,
        "source": "cloud" | "rule",
    }

設計原則（與 homework / report / orchestrator 一致）：
- 雲端路徑走 agent_backends.resolve("material")：AgentCore Harness → Bedrock Converse。
- allow_cloud=False 完全不觸雲端，連 resolve_config 都不呼叫。
- 雲端回覆整體字串經 guardrails.passes_guardrail；不通過降級回規則式。
- 任何例外不往外拋，一律靜默降級回規則式；規則式路徑永遠能產出合法結果。
- 規則式路徑不發明新詞：只在教材文字裡比對既有 scaffold.VOCAB，命中的詞
  就是這份教材的重點詞，保證不可能弄壞全域字典。這些詞本來就已經在
  VOCAB 裡，因此規則式路徑不呼叫 register_material_vocab。
"""

from __future__ import annotations

import logging
import re

from server import scaffold

_log = logging.getLogger(__name__)

_MAX_ENTRIES = 8

_CAT_ZH = {
    "food": "食物", "school": "學校", "animal": "動物",
    "family": "家庭", "action": "動作", "color": "顏色",
}


def _rule_based_extract(text: str) -> dict:
    """規則式教材提煉（完全離線，零外部依賴）。

    不生成任何新詞——只在文字裡比對既有 scaffold.VOCAB 的中文鍵/英文詞，
    命中的詞就是這份教材的重點詞。
    任何非字串輸入都降級處理成空字串，保證不拋例外。
    """
    # 類型守衛：非字串輸入降級成空字串，永遠不拋例外
    if not isinstance(text, str):
        text = ""

    # 用既有 scaffold._find_zh_vocab 做中文比對（處理長詞優先、避免短詞搶掠）
    zh_hits = scaffold._find_zh_vocab(text)
    text_lower = text.lower()

    # 追蹤已匹配的詞（中文優先），避免重複
    matched_zh = set(zh_hits)
    hits: list[dict] = []

    # 先加入中文命中
    for zh in zh_hits:
        if len(hits) >= _MAX_ENTRIES:
            break
        info = scaffold.VOCAB[zh]
        hits.append({"en": info["en"], "zh": zh, "cat": info["cat"],
                     "np": info["np"], "sent": info["sent"]})

    # 再檢查英文（詞邊界匹配，避免子字串誤配）
    for zh, info in scaffold.VOCAB.items():
        if len(hits) >= _MAX_ENTRIES:
            break
        if zh in matched_zh:
            continue
        # 英文詞邊界匹配（如 scaffold.safety_check）
        en_word = info["en"]
        if re.search(r"\b" + re.escape(en_word) + r"\b", text_lower):
            hits.append({"en": en_word, "zh": zh, "cat": info["cat"],
                         "np": info["np"], "sent": info["sent"]})
            matched_zh.add(zh)

    if hits:
        cat_counts: dict[str, int] = {}
        for h in hits:
            cat_counts[h["cat"]] = cat_counts.get(h["cat"], 0) + 1
        top_cat = max(cat_counts, key=lambda c: cat_counts[c])
        topic = f"教材中的{_CAT_ZH.get(top_cat, top_cat)}主題詞彙"
    else:
        topic = "教材中未找到對應課綱詞彙"

    return {
        "topic": topic,
        "entries": hits,
        "accepted_count": len(hits),
        "rejected_count": 0,
        "source": "rule",
    }
