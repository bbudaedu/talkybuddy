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
