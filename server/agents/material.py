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

import hashlib
import json
import logging
import re

from server import agent_backends, agentcore, bedrock_converse, guardrails, scaffold

_log = logging.getLogger(__name__)

# 單一真相來源：scaffold.MATERIAL_MAX_ENTRIES（register_material_vocab 本身
# 也用它做上限）。這裡不重複寫死 8，避免兩處常數各自改各自漂移。
_MAX_ENTRIES = scaffold.MATERIAL_MAX_ENTRIES

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


# ---------------------------------------------------------------------------
# 雲端路徑（AgentCore Harness → Bedrock Converse → 規則式）
# ---------------------------------------------------------------------------

_TIMEOUT_S = 12.0
_MAX_TEXT_LEN = 2000  # 教材原文送雲端的長度上限

# agentcore.invoke() 對非真值 actor_id 一律拋 ValueError（見 server/agentcore.py
# 的 actor_id 守門：漏傳會讓所有孩子共用同一份長期記憶，那道守門對
# homework/report/orchestrator 三個「學生維度」的 agent 是對的，不能弱化）。
# 教材提煉刻意不分學生——教材是全域共用詞庫的擴充，不屬於任何一個孩子——
# 但仍需要一個「真值」滿足 invoke() 的守門，否則 AgentCore 分支每次都會在
# 第一步就被那道守門擋下，即使現場正確設定了 AGENTCORE_HARNESS_MATERIAL
# 也永遠降級到 Bedrock，讓 agent_backends.chain("material") 回報的鏈變成謊言。
# 用固定字串而非 None：語意上代表「這通呼叫屬於全域教材上傳流程，不屬於
# 任何個別學生」，不會被誤判成某個孩子的 id，也不會撞到 Memory 分群。
_MATERIAL_ACTOR_ID = "material-upload"

_SYSTEM_PROMPT = (
    "你是台灣國小英語教材分析專家。從老師提供的教材文字中，"
    "挑出最多 8 個適合國小生學習的詞彙。"
    "每個詞附：英文（en）、繁體中文（zh）、分類（cat，只能是 "
    "food/school/animal/family/action/color 之一）、"
    "含正確冠詞的名詞片語（np）、一句用到這個詞的目標英文例句（sent）。"
    "同時給這份教材一個簡短的主題描述（topic，繁體中文）。"
    "只輸出一個 JSON 物件，不得有 markdown 圍欄或額外文字。"
)


def _build_user_prompt(text: str) -> str:
    schema_example = {
        "topic": "動物園一日遊",
        "entries": [
            {"en": "lion", "zh": "獅子", "cat": "animal",
             "np": "a lion", "sent": "I see a lion."},
        ],
        "source": "cloud",
    }
    return (
        f"教材內容：\n{text[:_MAX_TEXT_LEN]}\n\n"
        "請從上述教材挑出最多 8 個適合國小生的詞彙。"
        "cat 只能是 food/school/animal/family/action/color 之一。"
        "僅輸出符合以下 schema 的 JSON 物件（source 固定為 \"cloud\"）：\n"
        + json.dumps(schema_example, ensure_ascii=False)
    )


def _parse_cloud_response(raw_text: str) -> dict | None:
    """解析雲端回傳的 JSON 字串並做最基本的形狀檢查；任何問題回 None。"""
    text = (raw_text or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if not (isinstance(data.get("topic"), str) and data["topic"].strip()):
        return None
    if not isinstance(data.get("entries"), list):
        return None
    return data


def extract_vocab(text: str, *, allow_cloud: bool = True) -> dict:
    """教材提煉 agent 主入口。

    流程：
    1. allow_cloud=False 或未取得家長同意 → 直接走規則式，不碰任何雲端呼叫。
    2. allow_cloud=True → 嘗試 agent_backends.resolve("material")：
       AgentCore Harness → Bedrock Converse。
    3. 雲端回覆整體字串經 guardrails.passes_guardrail；不通過 → 降級。
    4. 解析 JSON，對提議詞條呼叫 scaffold.register_material_vocab 逐條驗證。
    5. 任何例外不往外拋，一律降級回規則式；規則式路徑永遠能產出合法結果。
    """
    try:
        return _extract_vocab(text, allow_cloud=allow_cloud)
    except Exception:
        _log.exception("extract_vocab 全數路徑失敗，回傳最小合法結果")
        return {
            "topic": "教材解析暫時失敗", "entries": [],
            "accepted_count": 0, "rejected_count": 0, "source": "rule",
        }


def _extract_vocab(text: str, *, allow_cloud: bool) -> dict:
    text = text if isinstance(text, str) else ""

    if not allow_cloud or not guardrails.consent_granted():
        return _rule_based_extract(text)

    try:
        ac_cfg, cfg = agent_backends.resolve("material")
        if ac_cfg is None and cfg is None:
            return _rule_based_extract(text)

        # 老師貼上的教材原文可能含學生姓名等個資，其他雲端出口
        # （cloud_llm.py／diagnose.py／sync_client.py／agents/privacy.py）
        # 送雲端前都先過 guardrails.deidentify，這裡補齊同樣的處理。
        # 只用在建 prompt 這裡——雜湊 session_id、規則式 fallback 仍用原文，
        # 不受影響。
        user_prompt = _build_user_prompt(guardrails.deidentify(text))

        raw_text = None
        if ac_cfg is not None:
            try:
                raw_text = agentcore.invoke(
                    ac_cfg, user_prompt,
                    actor_id=_MATERIAL_ACTOR_ID,
                    # 由教材文字本身推導，不可用固定字串：actor_id 已是固定的
                    # 非個人化 sentinel，若 session_id 也固定，
                    # _normalize_session_id 會把兩者雜湊成同一個
                    # runtimeSessionId——系統上所有老師、所有次上傳全部落在
                    # 同一個 Harness session。一旦設定 AGENTCORE_MEMORY_ARN，
                    # 等於所有教材上傳共用一份無上限成長的對話歷史：
                    # 不相關老師的內容互相污染，且隨時間拖垮品質。
                    # 用文字雜湊：同一份教材重複送落在同一個 session
                    # （決定性、可重現），不同教材、不同老師不會撞在一起。
                    session_id=f"material-{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}",
                )
            except Exception:
                _log.exception("extract_vocab AgentCore 失敗，改試 Bedrock Converse")
                raw_text = None

        if raw_text is None:
            if cfg is None:
                return _rule_based_extract(text)
            raw_text = bedrock_converse.converse_text(
                _SYSTEM_PROMPT, user_prompt, cfg=cfg,
                max_tokens=768, timeout_s=_TIMEOUT_S,
            )

        if not guardrails.passes_guardrail(raw_text):
            _log.warning("extract_vocab 雲端回覆未通過護欄，降級回規則式")
            return _rule_based_extract(text)

        parsed = _parse_cloud_response(raw_text)
        if parsed is None:
            _log.warning("extract_vocab 雲端回覆 schema 不合法，降級回規則式")
            return _rule_based_extract(text)

        accepted_entries, rejected = scaffold.register_material_vocab(parsed["entries"])
        return {
            "topic": parsed["topic"],
            "entries": accepted_entries,
            "accepted_count": len(accepted_entries),
            "rejected_count": rejected,
            "source": "cloud",
        }
    except Exception:
        _log.exception("extract_vocab 雲端路徑失敗，降級回規則式")
        return _rule_based_extract(text)
