# -*- coding: utf-8 -*-
"""profile.py — B2：長期記憶 / 學生 profile 規則式抽取器（純標準庫）。

依 research/b_axis/B2_長期記憶學生profile.md §3 實作：
    build_profile(interactions, diagnoses, prev=None) -> dict

重用既有 code（零新詞庫、零新偵測邏輯）：
    - scaffold.VOCAB / scaffold._find_zh_vocab（興趣主題、字彙分類）
    - diagnose._chinese_ratio / _english_word_count / _has_article_correction（錯點偵測）

設計原則：純標準庫、import 期不連外、空互動不炸；即時對話路徑只讀不算，
本函式只在導師異步時機（切 cloud）被呼叫，見 §5.3。
"""

from __future__ import annotations

import datetime
import re

from server import diagnose, scaffold

# ---------------------------------------------------------------------------
# 由 scaffold.VOCAB 反查：英文詞 → {zh, cat}
# ---------------------------------------------------------------------------

_EN_INFO: dict[str, dict] = {
    v["en"].lower(): {"en": v["en"], "zh": zh, "cat": v["cat"]}
    for zh, v in scaffold.VOCAB.items()
}

# 分類 → 中文標籤（固定對照，§3.1）
_CAT_LABEL: dict[str, str] = {
    "food": "食物",
    "school": "學校",
    "animal": "動物",
    "family": "家庭",
    "action": "動作",
    "color": "顏色",
}

# 錯點類型 → 中文標籤（§3.3）
_ERROR_LABEL: dict[str, str] = {
    "article_ao": "冠詞 a/an 誤用",
    "mixed_lang": "中英夾雜比例偏高",
    "short_sent": "句子偏短（<4 英文詞）",
}

_TAIPEI_TZ = datetime.timezone(datetime.timedelta(hours=8))

_EN_TOKEN_RE = re.compile(r"[A-Za-z]+")


def _now() -> str:
    """台北時區 ISO8601（仿 store._now / diagnose._today）。"""
    return datetime.datetime.now(_TAIPEI_TZ).isoformat(timespec="seconds")


def _en_tokens(text: str) -> list[str]:
    """抽出英文詞（小寫），供對照 VOCAB en。"""
    return [t.lower() for t in _EN_TOKEN_RE.findall(text)]


def _zh_char_count(text: str) -> int:
    """中文字元數（與 diagnose._chinese_ratio 同一 CJK 判定）。"""
    return sum(1 for c in text if "一" <= c <= "鿿")


def _trend(hits_seq: list[int]) -> tuple[str, int]:
    """依 seq 升冪的逐筆命中（0/1），比較近 5 筆命中率 vs 全體命中率。

    回傳 (trend, recent_hits)。近 5 筆明顯較低 → improving；較高 → regressing；
    差距在 0.15 內 → stable（§3.3）。
    """
    if not hits_seq:
        return "stable", 0
    recent = hits_seq[-5:]
    recent_hits = sum(recent)
    overall_rate = sum(hits_seq) / len(hits_seq)
    recent_rate = recent_hits / len(recent)
    diff = recent_rate - overall_rate
    if diff < -0.15:
        return "improving", recent_hits
    if diff > 0.15:
        return "regressing", recent_hits
    return "stable", recent_hits


def build_profile(
    interactions: list[dict],
    diagnoses: list[dict],
    prev: dict | None = None,
) -> dict:
    """規則式抽取學生 profile（§3.1–3.4）。全量重算、空互動不炸。"""
    # 依 seq 升冪排序（store.list_interactions 回傳新→舊；trend 需時間序）
    inters = sorted(interactions, key=lambda it: it.get("seq", 0))

    student_id = (
        (inters[0].get("student_id") if inters else None)
        or (prev or {}).get("student_id")
    )

    # --- 興趣主題 interests（§3.1）------------------------------------------
    cat_counter: dict[str, int] = {}
    # --- 字彙 seen / correct / last_seq（§3.2）-----------------------------
    vocab_stat: dict[str, dict] = {}
    # --- 錯點逐筆命中序列（§3.3）-------------------------------------------
    err_hits: dict[str, list[int]] = {"article_ao": [], "mixed_lang": [], "short_sent": []}
    # --- 難度統計（§3.4）---------------------------------------------------
    en_word_counts: list[int] = []
    en_ratios: list[float] = []

    for it in inters:
        text = it.get("student_text", "") or ""
        reply = it.get("ai_response_text", "") or ""
        conf = float(it.get("asr_confidence", 0.0) or 0.0)
        seq = int(it.get("seq", 0) or 0)

        # 興趣：中文詞庫命中 + 英文詞庫命中，各累加到 cat
        for zh in scaffold._find_zh_vocab(text):
            cat = scaffold.VOCAB[zh]["cat"]
            cat_counter[cat] = cat_counter.get(cat, 0) + 1
        tokens = _en_tokens(text)
        for token in tokens:
            info = _EN_INFO.get(token)
            if info:
                cat_counter[info["cat"]] = cat_counter.get(info["cat"], 0) + 1

        # 字彙掌握：每筆對出現過的英文詞去重計 seen，清楚且無糾錯計 correct
        corrected = diagnose._has_article_correction(reply)
        good = conf >= 0.8 and not corrected
        for token in set(tokens):
            info = _EN_INFO.get(token)
            if not info:
                continue
            st = vocab_stat.setdefault(
                token,
                {"en": info["en"], "zh": info["zh"], "cat": info["cat"],
                 "seen": 0, "correct": 0, "last_seq": 0},
            )
            st["seen"] += 1
            if good:
                st["correct"] += 1
            st["last_seq"] = max(st["last_seq"], seq)

        # 錯點：逐筆 0/1（升冪，供 trend）
        err_hits["article_ao"].append(1 if corrected else 0)
        err_hits["mixed_lang"].append(1 if diagnose._chinese_ratio(text) > 0.40 else 0)
        en_words = diagnose._english_word_count(text)
        err_hits["short_sent"].append(1 if en_words < 4 else 0)

        # 難度統計
        en_word_counts.append(en_words)
        zh_chars = _zh_char_count(text)
        denom = en_words + zh_chars
        en_ratios.append((en_words / denom) if denom else 0.0)

    # interests：依命中次數排序取全部（demo 規模小）
    interests = [
        {"topic": cat, "label": _CAT_LABEL.get(cat, cat), "hits": hits}
        for cat, hits in sorted(cat_counter.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    # 字彙分流：correct>=2 且 correct/seen>=0.6 → mastered，其餘出現過 → learning
    mastered_vocab: list[dict] = []
    learning_vocab: list[dict] = []
    for st in sorted(vocab_stat.values(), key=lambda s: (-s["correct"], -s["seen"], s["en"])):
        entry = {
            "en": st["en"], "zh": st["zh"], "cat": st["cat"],
            "seen": st["seen"], "correct": st["correct"], "last_seq": st["last_seq"],
        }
        if st["correct"] >= 2 and st["seen"] > 0 and st["correct"] / st["seen"] >= 0.6:
            mastered_vocab.append(entry)
        else:
            learning_vocab.append(entry)

    # error_patterns：三類固定輸出（穩定 schema），帶 hits / recent_hits / trend
    error_patterns: list[dict] = []
    for etype in ("article_ao", "mixed_lang", "short_sent"):
        seq_hits = err_hits[etype]
        trend, recent_hits = _trend(seq_hits)
        error_patterns.append({
            "type": etype,
            "label": _ERROR_LABEL[etype],
            "hits": sum(seq_hits),
            "recent_hits": recent_hits,
            "trend": trend,
        })

    # difficulty：score_avg 取最新診斷四維平均（§3.4）
    latest = diagnoses[-1] if diagnoses else None
    if latest and isinstance(latest.get("scores"), dict) and latest["scores"]:
        vals = [float(v) for v in latest["scores"].values()]
        score_avg = round(sum(vals) / len(vals))
    else:
        score_avg = 0
    avg_en_words = round(sum(en_word_counts) / len(en_word_counts), 1) if en_word_counts else 0.0
    en_ratio = round(sum(en_ratios) / len(en_ratios), 2) if en_ratios else 0.0
    difficulty = {
        "level": _level_for(score_avg),
        "avg_en_words": avg_en_words,
        "en_ratio": en_ratio,
        "score_avg": score_avg,
    }

    emotional_recent = (latest or {}).get("emotional_status", "") if latest else ""
    source_seq = max((int(it.get("seq", 0) or 0) for it in inters), default=0)

    return {
        "student_id": student_id,
        "updated_at": _now(),
        "source_seq": source_seq,
        "interaction_count": len(inters),
        "interests": interests,
        "mastered_vocab": mastered_vocab,
        "learning_vocab": learning_vocab,
        "error_patterns": error_patterns,
        "difficulty": difficulty,
        "emotional_recent": emotional_recent,
        "generated_by": "rule",
    }


def _level_for(score_avg: float) -> int:
    """粗略難度落點 1-5（種子；正式 CEFR 微階梯屬 B3）。§3.4 分段。"""
    if score_avg < 50:
        return 1
    if score_avg < 60:
        return 2
    if score_avg < 70:
        return 3
    if score_avg < 80:
        return 4
    return 5
