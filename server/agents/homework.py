# -*- coding: utf-8 -*-
"""homework.py — 派作業 agent（子專案 B）。

公開契約：
    generate_homework(profile: dict, diagnosis: dict, *, allow_cloud: bool = True) -> dict

回傳 schema（雲端與離線格式完全一致）：
    {
        "focus": str,         # 針對哪個弱項（取 diagnosis 最低分維度）
        "items": [            # 3 到 5 題
            {"target_en": str, "prompt_zh": str, "why": str}
        ],
        "source": "cloud" | "rule"
    }

設計原則（與 cloud_llm / diagnose 一致）：
- 雲端路徑走 bedrock_converse.converse_text，cfg 用 resolve_config(role="diag")
  ——派作業是非同步路徑，跟診斷用同一顆大模型是對的。
- allow_cloud=False 完全不觸雲端，連 resolve_config 都不呼叫。
- 上雲前對 profile / diagnosis 自由文字經 guardrails.deidentify。
- 雲端回覆整體 JSON 字串經 guardrails.passes_guardrail；不通過降級回規則式。
- 任何例外不往外拋，一律靜默降級回規則式；規則式永遠能產出合法結果。
- scaffold.VOCAB 是唯一題庫來源；不自編題庫，保持與詞庫同步。
"""

from __future__ import annotations

import json
import logging
import re

from server import agentcore, bedrock_converse, guardrails
from server.scaffold import VOCAB

_log = logging.getLogger(__name__)

# 雲端呼叫逾時（秒）。派作業是非同步路徑，沿用 diagnose 的 12s 寬鬆值——
# 不在 1.5s 即時對話迴圈裡，品質優先過速度。
_TIMEOUT_S = 12.0

# 最多傳給雲端的 profile 自由文字長度（字元），避免 prompt 過大
_MAX_TEXT_LEN = 200

# 四維鍵（順序固定，與 diagnose 一致）
_DIM_KEYS = ("pronunciation", "fluency", "vocabulary", "grammar")

# 四維中文名稱（用於 focus 說明與 prompt 組裝）
_DIM_ZH = {
    "pronunciation": "發音",
    "fluency": "口說流暢度",
    "vocabulary": "詞彙量",
    "grammar": "文法",
}

# 規則式題庫：弱項維度 → 優先使用的詞庫分類
# 每個維度的分類組合必須互不相同，確保四個維度產出不同題目（D4）
_DIM_TO_CATS = {
    "pronunciation": ["animal", "color"],  # 母音/子音對比鮮明；color 句型為 "My favorite color is X."
    "fluency":       ["action", "school"], # 動作句讓句子更完整
    "vocabulary":    ["food", "family"],   # 擴充詞彙以常用主題為主
    "grammar":       ["school", "animal"], # 冠詞 a/an/the 在 school/animal 類出現最豐富
}

# 每個維度的 why 說明範本
_DIM_WHY_TEMPLATE = {
    "pronunciation": "練習清晰發音，特別注意字首母音的區分",
    "fluency":       "以完整句輸出提升口說流暢度",
    "vocabulary":    "認識並活用新詞彙，豐富表達",
    "grammar":       "練習冠詞 a/an 與句型結構，強化文法正確性",
}

# 規則式產題的 prompt_zh 範本，依詞條 cat 分組。
#
# 設計原則：
# 1. 不含任何 target_en 英文字（避免 D2 洩漏答案）
# 2. 明確要求說「完整的英文句子」（避免 D3 只說單字被判錯）
# 3. 代入 VOCAB 全部 44 個 zh_key 後均為通順的台灣用語（D1）
#    - food/school/animal/family 的 zh_key 是名詞 → 用名詞句型
#    - action 的 zh_key 是動詞（吃、喝、去…） → 用動詞句型
#    - color 的 zh_key 是顏色詞（紅色…） → 用顏色句型
# 每個 cat 有 3 個輪替範本，確保同組五題有句型變化（D5）。
_PROMPT_TEMPLATES_BY_CAT: dict[str, list[str]] = {
    "food": [
        "「{zh_key}」的英文怎麼說？試著說一句完整的英文句子給我聽！",
        "你喜歡吃{zh_key}嗎？說一句關於{zh_key}的完整英文句子！",
        "試試看，說一句有「{zh_key}」意思的完整英文句子！",
    ],
    "animal": [
        "你看過{zh_key}嗎？用英文說一句完整的句子給我聽！",
        "說說你對{zh_key}的感覺，說一句完整的英文句子！",
        "試著說一句關於{zh_key}的完整英文句子！",
    ],
    "school": [
        "說說看，「{zh_key}」的英文是什麼？試著說一個完整的英文句子！",
        # 第二個範本不用「用到」——school cat 含「學校」「教室」等地點型詞條，
        # 套「你每天都用到X嗎」會不通順；改成中性的「你認識」句型，地點與物品皆合適。
        "你認識「{zh_key}」的英文嗎？說一句完整的英文句子！",
        "試著說一句跟「{zh_key}」有關的完整英文句子！",
    ],
    "family": [
        "說說你家裡的{zh_key}，用一句完整的英文說給我聽！",
        "你的{zh_key}在家裡做什麼？說一句完整的英文句子！",
        "試著說一句提到家人「{zh_key}」的完整英文句子！",
    ],
    "action": [
        "你喜歡「{zh_key}」嗎？說一句包含這個動作的完整英文句子！",
        "你平常會「{zh_key}」嗎？用完整的英文句子說說看！",
        "試著說一句用「{zh_key}」這個動作的完整英文句子！",
    ],
    "color": [
        "你最喜歡的顏色是{zh_key}嗎？說一句完整的英文句子！",
        "試著說一句提到{zh_key}的完整英文句子！",
        "說說看，「{zh_key}」的英文怎麼說？再說一句完整的英文句子！",
    ],
}
# 當 cat 不在上面字典時的通用備援範本（不含 {sent}）
_PROMPT_TEMPLATES_FALLBACK: list[str] = [
    "「{zh_key}」的英文怎麼說？試著說一句完整的英文句子！",
    "說說看，跟「{zh_key}」有關的完整英文句子！",
    "試著用「{zh_key}」的概念說一句完整的英文句子！",
]


# ---------------------------------------------------------------------------
# 工具函式
# ---------------------------------------------------------------------------

def _find_lowest_dim(scores: dict) -> str:
    """找出四維中得分最低的維度；全部缺失則預設 grammar。"""
    if not scores:
        return "grammar"
    # 只考慮合法維度，缺的以 100 補（不影響其他維度的比較）
    valid = {d: float(scores.get(d, 100)) for d in _DIM_KEYS if d in scores}
    if not valid:
        return "grammar"
    return min(valid, key=lambda d: valid[d])


def _safe_str(obj, max_len: int = _MAX_TEXT_LEN) -> str:
    """把物件轉成字串，截斷過長內容，處理 None。"""
    if obj is None:
        return ""
    return str(obj)[:max_len]


def _deidentify_profile(profile: dict) -> dict:
    """對 profile 所有字串值去識別化；回傳新 dict，不改原物件。"""
    if not profile:
        return {}
    result: dict = {}
    for k, v in profile.items():
        if isinstance(v, str):
            result[k] = guardrails.deidentify(v[:_MAX_TEXT_LEN])
        elif isinstance(v, list):
            result[k] = [
                guardrails.deidentify(str(item)[:_MAX_TEXT_LEN])
                if isinstance(item, str) else item
                for item in v
            ]
        else:
            result[k] = v
    return result


def _deidentify_diagnosis(diagnosis: dict) -> dict:
    """對 diagnosis 自由文字欄位去識別化；回傳新 dict，不改原物件。"""
    if not diagnosis:
        return {}
    result: dict = dict(diagnosis)
    # 自由文字欄位：strengths / weaknesses / emotional_status
    for key in ("emotional_status",):
        if isinstance(result.get(key), str):
            result[key] = guardrails.deidentify(result[key][:_MAX_TEXT_LEN])
    for key in ("strengths", "weaknesses"):
        if isinstance(result.get(key), list):
            result[key] = [
                guardrails.deidentify(str(s)[:_MAX_TEXT_LEN])
                if isinstance(s, str) else s
                for s in result[key]
            ]
    return result


# ---------------------------------------------------------------------------
# 規則式路徑（離線 fallback，永遠可用）
# ---------------------------------------------------------------------------

def _pick_vocab_entries(dim: str, n: int = 5) -> list[dict]:
    """從 scaffold.VOCAB 按弱項維度挑出 n 個詞條。

    使用 round-robin 跨分類交錯取題：每輪從各 preferred_cat 各取 1 個，
    直到湊滿 n 個為止。這樣確保：
    - 每個 preferred_cat 都有貢獻（解決 D4 同維度題目單調問題）
    - 不同 cat 的 sent 句型不同，避免 n 題同句型（解決 D5）
    - 不足時從其他分類補齊

    所有題目都來自 VOCAB，不自編。
    """
    preferred_cats = _DIM_TO_CATS.get(dim, ["food", "animal"])

    # 先依分類分桶，保留 VOCAB 原始順序（確定性）
    cat_buckets: dict[str, list[dict]] = {cat: [] for cat in preferred_cats}
    for zh_key, info in VOCAB.items():
        if info["cat"] in cat_buckets:
            cat_buckets[info["cat"]].append({"zh_key": zh_key, **info})

    # round-robin 交錯取題：確保各 cat 都有貢獻（D4/D5 修法核心）
    # seen_sents 對 target_en（即 sent）去重，避免同份作業出現一字不差的重複題（D6）
    entries: list[dict] = []
    seen_sents: set[str] = set()
    cat_indices = {cat: 0 for cat in preferred_cats}
    while len(entries) < n:
        added_this_round = False
        for cat in preferred_cats:
            if len(entries) >= n:
                break
            idx = cat_indices[cat]
            bucket = cat_buckets[cat]
            # 向前掃直到找到 sent 未重複的詞條，或此 bucket 取盡
            while idx < len(bucket):
                candidate = bucket[idx]
                idx += 1
                if candidate["sent"] not in seen_sents:
                    entries.append(candidate)
                    seen_sents.add(candidate["sent"])
                    added_this_round = True
                    break
            cat_indices[cat] = idx
        if not added_this_round:
            # preferred cats 已取盡，從其他分類補
            break

    # 不足時從其他分類補齊（同樣對 sent 去重）
    if len(entries) < n:
        seen_keys = {e["zh_key"] for e in entries}
        for zh_key, info in VOCAB.items():
            if len(entries) >= n:
                break
            if zh_key not in seen_keys and info["cat"] not in preferred_cats:
                if info["sent"] not in seen_sents:
                    entries.append({"zh_key": zh_key, **info})
                    seen_keys.add(zh_key)
                    seen_sents.add(info["sent"])

    return entries[:n]


def _build_rule_items(dim: str) -> list[dict]:
    """規則式產出 3-5 道習題，題目全取自 scaffold.VOCAB。"""
    entries = _pick_vocab_entries(dim, n=5)
    why_base = _DIM_WHY_TEMPLATE.get(dim, "加強練習")
    items: list[dict] = []
    for i, entry in enumerate(entries[:5]):
        zh_key = entry["zh_key"]
        cat = entry["cat"]
        sent = entry["sent"]
        np = entry["np"]
        # 用 sent 作為 target_en（scaffold 已確保是合法英文句）
        target_en = sent
        # 依 cat 選對應句型組，每組有 3 個輪替範本（i % 3），確保同份作業句型有變化（D5）
        templates = _PROMPT_TEMPLATES_BY_CAT.get(cat, _PROMPT_TEMPLATES_FALLBACK)
        tmpl = templates[i % len(templates)]
        # 代入 zh_key（絕不代入 sent / target_en，避免洩漏答案 D2）
        prompt_zh = tmpl.format(zh_key=zh_key)
        # why 加上具體的詞（增加教學意義）
        why = f"{why_base}（{zh_key} → {np}）"
        items.append({
            "target_en": target_en,
            "prompt_zh": prompt_zh,
            "why": why,
        })
    # 保證至少 3 題（entries 來自 VOCAB，最少有 44 個詞條，不可能 < 3）
    return items[:5] if len(items) >= 3 else items


def _rule_based_homework(profile: dict, diagnosis: dict) -> dict:
    """規則式派作業（完全離線，零外部依賴）。"""
    scores = (diagnosis or {}).get("scores") or {}
    dim = _find_lowest_dim(scores)
    focus = f"{_DIM_ZH.get(dim, dim)}（{dim}）"
    items = _build_rule_items(dim)
    return {
        "focus": focus,
        "items": items,
        "source": "rule",
    }


# ---------------------------------------------------------------------------
# 雲端回應驗證
# ---------------------------------------------------------------------------

def _validate_cloud_response(data: dict) -> bool:
    """驗證雲端回傳的 dict 符合 homework schema；不符回 False。"""
    if not isinstance(data, dict):
        return False
    # focus
    if not (isinstance(data.get("focus"), str) and str(data["focus"]).strip()):
        return False
    # items
    items = data.get("items")
    if not isinstance(items, list):
        return False
    if not (3 <= len(items) <= 5):
        return False
    for item in items:
        if not isinstance(item, dict):
            return False
        for key in ("target_en", "prompt_zh", "why"):
            if not (isinstance(item.get(key), str) and str(item[key]).strip()):
                return False
    return True


def _parse_cloud_response(text: str) -> dict | None:
    """解析雲端回傳的 JSON 字串並驗證 schema；任何問題回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    # 容忍 ```json 圍欄（與 diagnose._parse_diagnosis_text 同樣的寬容策略）
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not _validate_cloud_response(data):
        return None
    return data


# ---------------------------------------------------------------------------
# 雲端 prompt 組裝
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "你是台灣國小英語學習「派作業」專家。根據學生診斷資料，"
    "針對最弱的學習維度出 3 到 5 道英語口說練習題，"
    "每題包含目標英文句（target_en）、繁體中文提示語（prompt_zh）、"
    "以及為什麼練這一題（why）。"
    "只輸出一個 JSON 物件，schema 如範例所示，不得有 markdown 圍欄或額外文字。"
    "所有 prompt_zh / why / focus 用繁體中文（台灣用語）；target_en 用英文。"
)


def _build_user_prompt(
    profile_safe: dict,
    diag_safe: dict,
    dim: str,
) -> str:
    """組裝送給雲端的 user prompt（已去識別化）。"""
    focus_zh = _DIM_ZH.get(dim, dim)
    schema_example = {
        "focus": f"{focus_zh}（{dim}）",
        "items": [
            {
                "target_en": "I have a dog.",
                "prompt_zh": "告訴我你有什麼動物？",
                "why": "練習冠詞 a/an，強化文法正確性",
            },
            {
                "target_en": "I want to eat an apple.",
                "prompt_zh": "試試說說你想吃什麼？",
                "why": "an 用於母音開頭名詞（apple），冠詞選用練習",
            },
            {
                "target_en": "I see a cat.",
                "prompt_zh": "說說你看到了什麼動物？",
                "why": "用 a 修飾子音開頭名詞（cat），冠詞選用練習",
            },
        ],
        "source": "cloud",
    }
    return (
        f"學生目前最弱的維度是「{focus_zh}」（診斷分數：{diag_safe.get('scores', {}).get(dim, '?')} 分）。\n"
        f"學生 profile（已去識別化）：{json.dumps(profile_safe, ensure_ascii=False)[:300]}\n"
        f"最新弱點描述：{diag_safe.get('weaknesses', [])}\n\n"
        "請針對此弱項出 3 到 5 道口說練習題，題目盡量用國小常用詞彙。\n"
        "僅輸出符合以下 schema 的 JSON 物件（source 固定為 \"cloud\"）：\n"
        + json.dumps(schema_example, ensure_ascii=False)
    )


# ---------------------------------------------------------------------------
# 公開契約入口
# ---------------------------------------------------------------------------

def generate_homework(
    profile: dict,
    diagnosis: dict,
    *,
    allow_cloud: bool = True,
) -> dict:
    """派作業 agent 主入口。

    流程：
    1. allow_cloud=False → 直接走規則式，不碰任何雲端呼叫。
    2. allow_cloud=True → 嘗試 Bedrock Converse：
       a. 去識別化 profile / diagnosis 自由文字。
       b. 呼叫 bedrock_converse.converse_text（cfg=resolve_config(role="diag")）。
       c. 驗證回傳 JSON schema；不合法 → 降級。
       d. 對整體回傳 JSON 字串做 passes_guardrail；不通過 → 降級。
       e. 任何例外 → 靜默 log 後降級。
    3. 規則式（離線 fallback）永遠是保底，一定能產出合法結果。

    任何情況都不往外拋例外。
    """
    # 防禦性正規化：None 輸入轉成空 dict
    profile = profile or {}
    diagnosis = diagnosis or {}

    # allow_cloud=False：最高優先閘門，連 resolve_config 都不呼叫
    if not allow_cloud:
        return _rule_based_homework(profile, diagnosis)

    # 雲端路徑。後端優先序：AgentCore Harness → Bedrock Converse → 規則式。
    try:
        # 先看有沒有啟用 AgentCore。resolve_config 只讀環境變數不觸網，
        # 放在去識別化之前是為了在「兩個後端都沒設定」時儘早走規則式。
        ac_cfg = agentcore.resolve_config("homework")
        cfg = None if ac_cfg else bedrock_converse.resolve_config(role="diag")
        if ac_cfg is None and cfg is None:
            # 兩個雲端後端都沒設定 → 直接走規則式
            return _rule_based_homework(profile, diagnosis)

        # 去識別化（上雲前對自由文字遮罩個資）
        profile_safe = _deidentify_profile(profile)
        diag_safe = _deidentify_diagnosis(diagnosis)

        # 找最低分維度（在去識別化後的 diagnosis 中取 scores，安全）
        scores = diagnosis.get("scores") or {}
        dim = _find_lowest_dim(scores)

        user_prompt = _build_user_prompt(profile_safe, diag_safe, dim)

        if ac_cfg is not None:
            # AgentCore：system prompt 在 Harness 建立時就宣告好了，這裡只送訊息。
            # actor_id 是 Memory 的分群鍵，漏傳會讓所有孩子共用同一份長期記憶。
            raw_text = agentcore.invoke(
                ac_cfg, user_prompt,
                actor_id=(profile or {}).get("student_id"),
                session_id=f"hw-{diagnosis.get('date') or 'na'}",
            )
        else:
            raw_text = bedrock_converse.converse_text(
                _SYSTEM_PROMPT,
                user_prompt,
                cfg=cfg,
                max_tokens=512,
                timeout_s=_TIMEOUT_S,
            )

        # 護欄：整體回傳字串過安全過濾（任一禁詞命中 → 降級）
        if not guardrails.passes_guardrail(raw_text):
            _log.warning("generate_homework 雲端回覆未通過護欄，降級回規則式")
            return _rule_based_homework(profile, diagnosis)

        # 解析與 schema 驗證（格式錯誤 / items 數量不符 → 降級）
        parsed = _parse_cloud_response(raw_text)
        if parsed is None:
            _log.warning("generate_homework 雲端回覆 schema 不合法，降級回規則式")
            return _rule_based_homework(profile, diagnosis)

        # 標記 source 為 cloud（雲端回傳可能已含此欄，統一覆寫確保正確）
        parsed["source"] = "cloud"
        return parsed

    except Exception:
        # 任何例外（網路、逾時、boto3 未安裝…）都不往外拋
        _log.exception("generate_homework 雲端路徑失敗，降級回規則式")
        return _rule_based_homework(profile, diagnosis)
