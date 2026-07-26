# -*- coding: utf-8 -*-
"""privacy.py — 上雲前的資料最小化（B4）。

三個 agent（homework / report / orchestrator）共用同一套投影規則：
**白名單挑欄位，不是黑名單遮欄位。**

為什麼非白名單不可
------------------
diagnosis 有兩個欄位是 LLM 依孩子講的話生成的自由文字：

- ``companion_directive``：陪聊指令，孩子說「我是王小明，今天跟哥哥去…」，
  名字就直接落在裡面
- ``instructions``：給老師／裝置／同儕的三段指示，同樣是自由文字

而 ``guardrails.deidentify`` **遮不掉中文姓名**——它只遮明確的個資詞
（住址／身分證…）、三位以上連續數字、以及非詞庫的 Title-case 英文專名。
所以「有呼叫 deidentify」不等於「沒外洩」。

黑名單（列出要遮的欄位）的失敗模式是：diagnose 新增一個欄位、沒人記得同步
遮罩清單，資料就靜默上雲，而且會被 AgentCore Memory 長期保存。白名單的失敗
模式相反：忘記更新 → 該送的欄位沒送 → 雲端產出品質下降，看得見、可回復。
隱私的預設值必須是「不送」。

對外三個函式
------------
- ``safe_profile(profile)``  → 只留結構化學習訊號，去掉 student_id / 自由文字
- ``safe_diagnosis(diag)``   → 只留 date / scores / 三個已遮罩欄位
- ``safe_diagnoses(list)``   → 批次版

全部純函式、不拋例外（垃圾輸入回空 dict），支撐三個 agent 的「絕不拋」契約。
"""

from __future__ import annotations

from server import guardrails

# 單一自由文字欄位送上雲的長度上限（字元）
MAX_TEXT_LEN = 200

# list 型欄位最多送幾個元素（strengths / weaknesses / interests…）
MAX_LIST_ITEMS = 8

# 四維鍵（與 diagnose / homework / report / orchestrator 一致）
_DIM_KEYS = ("pronunciation", "fluency", "vocabulary", "grammar")

# --- diagnosis 白名單 -------------------------------------------------------
# date / scores 是結構化資料；後三個是自由文字，經 deidentify 後才送。
# 明確不送：companion_directive、instructions，以及任何未來新增的欄位。
_DIAG_TEXT_KEYS = ("emotional_status",)
_DIAG_LIST_KEYS = ("strengths", "weaknesses")

# --- profile 白名單 ---------------------------------------------------------
# 只留「這孩子怎麼學」的結構化訊號。student_id 不進 prompt——AgentCore 那邊
# 由 actor_id 攜帶，塞進 prompt 只是讓模型有機會把它覆述進輸出裡。
_PROFILE_KEYS = (
    "interaction_count",
    "interests",
    "mastered_vocab",
    "learning_vocab",
    "error_patterns",
    "difficulty",
    "emotional_recent",
)

# profile 巢狀結構的遞迴上限。超過就整段丟掉——結構化欄位不該有那麼深。
_MAX_DEPTH = 3


def _clean_text(value) -> str:
    """字串 → 截斷 → 去識別化。非字串一律轉字串處理。"""
    try:
        return guardrails.deidentify(str(value)[:MAX_TEXT_LEN])
    except Exception:
        return ""


def _clean_str_list(value) -> list[str]:
    """list[str] → 每個元素截斷去識別化；非 list 或空的回 []。"""
    if not isinstance(value, list):
        return []
    return [_clean_text(item) for item in value[:MAX_LIST_ITEMS] if item is not None]


def _safe_scores(value) -> dict:
    """只取四維、只取能轉成 float 的值；其餘丟棄。"""
    if not isinstance(value, dict):
        return {}
    out: dict = {}
    for dim in _DIM_KEYS:
        raw = value.get(dim)
        if isinstance(raw, bool) or raw is None:
            continue
        try:
            out[dim] = float(raw) if not isinstance(raw, int) else raw
        except (TypeError, ValueError):
            continue
    return out


def _scrub(value, depth: int = 0):
    """遞迴清理結構化容器：字串去識別化、數值原樣、未知型別丟棄。

    只用在 profile 的白名單欄位上（interests / difficulty / vocab 清單…），
    這些欄位是規則式 profile.build_profile 產出的結構化資料。
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _clean_text(value)
    if depth >= _MAX_DEPTH:
        return None
    if isinstance(value, (list, tuple)):
        return [_scrub(v, depth + 1) for v in list(value)[:MAX_LIST_ITEMS]]
    if isinstance(value, dict):
        return {
            str(k): _scrub(v, depth + 1)
            for k, v in list(value.items())[:MAX_LIST_ITEMS]
        }
    # 未知型別（物件、bytes…）一律不送
    return None


def safe_diagnosis(diagnosis) -> dict:
    """把一筆 diagnosis 投影成可上雲的最小集合。

    輸出只可能含這五個鍵，且只在來源真的有值時才出現：
    ``date`` / ``scores`` / ``emotional_status`` / ``strengths`` / ``weaknesses``。
    """
    if not isinstance(diagnosis, dict) or not diagnosis:
        return {}
    out: dict = {}

    date = diagnosis.get("date")
    if isinstance(date, str) and date.strip():
        # 日期是結構化欄位，不走 deidentify（否則 2026-07-20 會被當成連續數字遮掉）
        out["date"] = date.strip()[:32]

    scores = _safe_scores(diagnosis.get("scores"))
    if scores:
        out["scores"] = scores

    for key in _DIAG_TEXT_KEYS:
        val = diagnosis.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = _clean_text(val)

    for key in _DIAG_LIST_KEYS:
        cleaned = _clean_str_list(diagnosis.get(key))
        if cleaned:
            out[key] = cleaned

    return out


def safe_diagnoses(diagnoses) -> list[dict]:
    """批次版：對每一筆做 safe_diagnosis。None / 非 list 回 []。"""
    if not isinstance(diagnoses, (list, tuple)):
        return []
    return [safe_diagnosis(d) for d in diagnoses]


def safe_profile(profile) -> dict:
    """把 profile 投影成可上雲的最小集合（見 ``_PROFILE_KEYS``）。"""
    if not isinstance(profile, dict) or not profile:
        return {}
    out: dict = {}
    for key in _PROFILE_KEYS:
        if key not in profile:
            continue
        cleaned = _scrub(profile[key])
        if cleaned is None or cleaned == [] or cleaned == {}:
            continue
        out[key] = cleaned
    return out
