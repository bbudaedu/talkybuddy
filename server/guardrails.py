# -*- coding: utf-8 -*-
"""B4 內容安全與隱私最小化共用模組（純標準函式庫，零依賴）。

三個對外介面（見 research/b_axis/B4_隱私與Guardrails.md）：
- ``CHILD_SAFETY_CLAUSE``：兒童安全明文護欄（L2），供 edge/雲端 system prompt 共用。
- ``passes_guardrail(text)``：輸出後置過濾（L3），True=安全可採用；edge 現用、
  雲端陪聊未來必經（防禦縱深，任一層漏掉下一層還能擋）。
- ``deidentify(text)``：上雲前去識別化（§3），遮罩人名/電話/住址類，保留詞庫學習詞。

設計原則：與 scaffold/diagnose 一致的「純規則、失敗保守降級、不影響主流程」。
"""

from __future__ import annotations

import re
from functools import lru_cache

# ---------------------------------------------------------------------------
# L2：兒童安全明文護欄（system prompt 共用常數）
# ---------------------------------------------------------------------------

CHILD_SAFETY_CLAUSE = (
    "五、你的對象是國小兒童：不得談論暴力、血腥、成人、藥物、恐怖或色情內容；"
    "不得索取或覆述姓名、住址、電話、學校等個人資料。"
    "六、若學生表達難過、害怕、想傷害自己或被欺負，"
    "先用繁體中文溫柔安撫，鼓勵他告訴老師或家人，"
    "不要追問細節、不要給處置建議。"
)


# ---------------------------------------------------------------------------
# L3：輸出後置過濾（edge/雲端共用；True = 安全可採用）
# ---------------------------------------------------------------------------

def passes_guardrail(text) -> bool:
    """LLM 輸出是否安全可採用：空/None → False；命中禁詞 → False。

    包一層 scaffold.safety_check（True=命中）；安全模組不可用時保守回 False
    （寧可降級回確定性 scaffold 輸出，也不放行未過濾內容）。
    """
    if not text or not str(text).strip():
        return False
    try:
        from server import scaffold
        return not scaffold.safety_check(str(text))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# §2.3：家長同意 gate（consent）—— 啟用雲端路徑前的單一守門
# ---------------------------------------------------------------------------

def consent_granted() -> bool:
    """是否已取得家長同意可啟用雲端路徑（B4-5 單一守門）。

    單一真實來源 = ``config.CONSENT_GRANTED``（demo 預設 True，正式版接書面同意書）。
    讀取失敗時保守回 ``False``（未同意 → 強制 edge-only、資料不出境），
    與 ``passes_guardrail`` 同樣「寧可降級也不放行」的隱私優先原則。
    """
    try:
        from server import config
        return bool(config.CONSENT_GRANTED)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# §3：上雲前去識別化
# ---------------------------------------------------------------------------

# 明確的中文個資類詞（住址/電話號碼/身分證/密碼）→ [個資]
_CN_PII_WORDS = ("電話號碼", "身分證字號", "身分證", "住址", "地址", "門牌", "密碼")

# 常用英文詞白名單（避免把句首大寫或常用詞誤判為人名）
_COMMON_EN = {
    "i", "you", "he", "she", "we", "they", "it", "me", "my", "your",
    "hi", "hello", "hey", "ok", "okay", "yes", "no", "yeah", "please",
    "thank", "thanks", "great", "good", "nice", "wow", "cool", "sorry",
    "what", "how", "who", "why", "where", "when", "which", "do", "does",
    "is", "are", "am", "was", "were", "the", "a", "an", "and", "or",
    "let", "lets", "today", "teacher", "english", "monday", "tuesday",
    "wednesday", "thursday", "friday", "saturday", "sunday",
}


@lru_cache(maxsize=1)
def _safe_en_words() -> frozenset:
    """常用詞 ∪ scaffold.VOCAB 內的英文學習詞（小寫），供人名判定的白名單。"""
    words = set(_COMMON_EN)
    try:
        from server import scaffold
        for v in getattr(scaffold, "VOCAB", {}).values():
            for key in ("en", "np", "sent"):
                for w in re.findall(r"[a-z]+", str(v.get(key, "")).lower()):
                    words.add(w)
    except Exception:
        pass
    return frozenset(words)


@lru_cache(maxsize=1)
def _converter():
    """OpenCC s2twp 轉換器（懶載入、單例）；不可用回 None，不 throw。"""
    try:
        import opencc
        from server.config import OPENCC_CONFIG
        return opencc.OpenCC(OPENCC_CONFIG)
    except Exception:
        return None


def to_traditional(text) -> str:
    """把 LLM 輸出轉成台灣繁體（OpenCC s2twp）；失敗回原文。

    2026-07-29 真機實測，edge LLM 回過「看到一只兔子」——簡體用字直接進字幕。
    繁化原本**只套在 ASR 路徑**（`asr_sensevoice.py`），LLM 輸出沒有經過。
    發音沒差（同音），但字幕會露簡體字。

    降級比照 `asr_sensevoice.py`：opencc 缺失或轉換失敗 → 回原文。
    這條路徑在回覆送出前，**繁化失敗只是字醜，讓對話中斷才是真的壞掉**。
    英文不受影響（OpenCC 只動漢字），所以帶讀的目標句安全。
    """
    s = str(text or "")
    if not s.strip():
        return s
    cc = _converter()
    if cc is None:
        return s
    try:
        return cc.convert(s)
    except Exception:
        return s


READALONG_MARKER = "跟我說一遍："

# 帶讀引導語的其他說法（LLM 不照格式時常見）。限長 12 字且不跨句，
# 避免把前面的稱讚語一起吃掉。
_LEADIN = (
    r"(?:[^。！？!?\n]{0,12}?"
    r"(?:說一遍|唸一遍|跟我唸|說說看|說看看|repeat after me)\s*[：:]?\s*)?"
)


def _normalise_readalong(s: str) -> str:
    """比對用正規化：去 `<>` 包裹、中文句號視同英文句點、大小寫與空白統一。"""
    s = s.replace("<", " ").replace(">", " ").replace("。", ".")
    return re.sub(r"\s+", " ", s.lower()).strip()


def ensure_readalong(text, target) -> str:
    """確保回覆恰好含一句合規的「跟我說一遍：<目標英文句>」帶讀。

    取代原本 edge/雲端各寫一份的 ``if target not in text`` 子字串比對，該寫法
    實測漏掉兩種情況（`edge/PR7_MERGE_VALIDATION_2026-07-29.md` §三）：
    ``<>`` 包裹時比對為真而不補正（格式跑掉沒人管），中文句號時比對為假而
    重複補一次（同一句被唸兩遍）。改為**正規化後檢查帶讀格式**。

    誠實限制：若 LLM 帶讀了「別的句子」（非本輪 target），這裡只保證正確的
    target 一定被帶讀，不會去刪那句——刪掉看不懂的內容比多一句更危險。
    那屬於教學內容選取問題（PR #7 的 lesson_target_sentence）。
    """
    s = str(text or "")
    tgt = str(target or "").strip()
    if not tgt:
        return s

    # 已是合規格式 → 一字不動
    norm_target = _normalise_readalong(tgt)
    for seg in _normalise_readalong(s).split(READALONG_MARKER)[1:]:
        if seg.strip().startswith(norm_target):
            return s

    # 否則：清掉格式跑掉的那句（連同它的引導語），再補一句合規的
    core = tgt.rstrip(".!?。！？").strip()
    tokens = [re.escape(t) for t in core.split() if t]
    if tokens:
        pat = re.compile(
            _LEADIN
            + r"[<\[（(]?\s*"
            + r"\s+".join(tokens)
            + r"\s*[.!?。！？]*\s*[>\]）)]?",
            re.IGNORECASE,
        )
        s = pat.sub("", s)
    s = s.strip()
    return f"{s} {READALONG_MARKER}{tgt}".strip()


def deidentify(text) -> str:
    """遮罩明顯個資（人名/電話/住址），保留詞庫學習詞。純字串處理、不炸。

    - 中文個資詞（住址/身分證/密碼…）→ [個資]
    - 三位以上連續數字（電話/門牌）→ [數字]
    - 詞庫外的英文 Title-case 專名（如 Mimi）→ [名字]
    誠實限制：擋不住諧音/拼音規避與間接個資，正式版需語意層（B4 之後做）。
    """
    if not text:
        return text or ""
    s = str(text)
    for w in _CN_PII_WORDS:
        if w in s:
            s = s.replace(w, "[個資]")
    s = re.sub(r"\d{3,}", "[數字]", s)
    safe = _safe_en_words()

    def _mask(m: "re.Match") -> str:
        tok = m.group(0)
        return tok if tok.lower() in safe else "[名字]"

    return re.sub(r"\b[A-Z][a-zA-Z]+\b", _mask, s)
