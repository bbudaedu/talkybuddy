# -*- coding: utf-8 -*-
"""curriculum.py — B3：CEFR 難度階梯 + 主題迭代（純規則、stdlib-only）。

依 research/b_axis/B3_CEFR難度階梯與主題迭代.md：
把 diagnose 已算好的四維 scores（0–100）映射到可計算的 Level 微階梯
（Band 1–5 × Step 1–5＝25 微階），並定升/降級（含遲滯）與換主題規則，
輸出 level_state 供 B1 組 companion_directive。

不重寫 scaffold 規則、不引入重依賴；可 `python3 server/curriculum.py` 自測。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 常數（集中可調，§2）
# ---------------------------------------------------------------------------

# 四維鍵順序（沿用 diagnose._DIM_KEYS，避免另立 schema）
_DIM_KEYS = ("pronunciation", "fluency", "vocabulary", "grammar")

# CAS 加權：早期最該獎勵「願意開口(fluency)」「用得出詞(vocabulary)」（§2.1）
WEIGHTS = {"fluency": 0.30, "vocabulary": 0.25, "grammar": 0.25, "pronunciation": 0.20}

# CAS → band 切點：<35→1, 35–50→2, 50–65→3, 65–80→4, ≥80→5（§2.2）
BAND_CUTS = (35, 50, 65, 80)

# band → CEFR / YLE 標籤（§1.1）
_CEFR = {
    1: "pre-A1 (Starters 前)",
    2: "pre-A1 (Starters)",
    3: "A1 (Movers)",
    4: "A2 (Flyers 入門)",
    5: "A2 (Flyers)",
}

# 沉默/放棄語達此門檻 → demote（§3.3）
GIVEUP_DEMOTE_TH = 2


def compute_cas(scores: dict) -> int:
    """四維加權平均綜合能力分 CAS（0–100 整數）。缺維以 0 計，不炸。"""
    total = sum(float(scores.get(d, 0)) * WEIGHTS[d] for d in _DIM_KEYS)
    return int(round(total))


def _band_from_cas(cas: float) -> int:
    """CAS 落點 → band（1–5），依 BAND_CUTS。"""
    band = 1
    for cut in BAND_CUTS:
        if cas >= cut:
            band += 1
        else:
            break
    return band


def _gate_band(min_dim: float) -> int:
    """最弱維度限制的最高 band（防跛腳升級，§2.2）。"""
    if min_dim < 30:
        return 2
    if min_dim < 45:
        return 3
    if min_dim < 60:
        return 4
    return 5


def _band_range(band: int) -> tuple[int, int]:
    """band 的 CAS 區間 [low, high)（§2.3）。"""
    lows = {1: 0, 2: 35, 3: 50, 4: 65, 5: 80}
    highs = {1: 35, 2: 50, 3: 65, 4: 80, 5: 100}
    return lows[band], highs[band]


def scores_to_level(scores: dict) -> dict:
    """四維 → level_state 的基準（band/step/level_index/cefr/cas/explain）。§2。"""
    cas = compute_cas(scores)
    dim_vals = {d: float(scores.get(d, 0)) for d in _DIM_KEYS}
    min_dim = min(dim_vals.values()) if dim_vals else 0.0

    cas_band = _band_from_cas(cas)
    gate = _gate_band(min_dim)
    band = max(1, min(cas_band, gate))

    low, high = _band_range(band)
    pos = (cas - low) / (high - low) if high > low else 0.0
    pos = max(0.0, min(0.999999, pos))
    step = min(5, 1 + int(pos * 5))
    level_index = (band - 1) * 5 + step

    weakest = min(dim_vals, key=lambda d: dim_vals[d]) if dim_vals else "grammar"
    gated = gate < cas_band
    explain = (
        f"CAS {cas}（fluency {int(dim_vals['fluency'])}/vocab {int(dim_vals['vocabulary'])}"
        f"/grammar {int(dim_vals['grammar'])}/pron {int(dim_vals['pronunciation'])}）"
        + (f"；最弱維度 {weakest}={int(min_dim)} gate 上限 Band{gate}，band 被壓低"
           if gated else f"；最弱維度 {weakest}={int(min_dim)} 未觸 gate")
    )

    return {
        "level": f"{band}-{step}",
        "band": band,
        "step": step,
        "level_index": level_index,
        "cefr": _CEFR[band],
        "cas": cas,
        "explain": explain,
    }


# ---------------------------------------------------------------------------
# §3.2 升降級決策（含遲滯）
# ---------------------------------------------------------------------------

_DIM_ZH = {"pronunciation": "發音", "fluency": "口說流暢度",
           "vocabulary": "詞彙量", "grammar": "文法"}

# 主題庫（§4.1，第一版只用 scaffold 現有 6 類；space/weather 待擴詞條）
TOPIC_ORDER = ["animal", "food", "color", "family", "school", "action"]
TOPIC_UNLOCK = {"animal": 1, "food": 1, "color": 2,
                "family": 2, "school": 2, "action": 3}

# band → 目標語言形式（§1.2）
_TARGET_FORM = {
    1: "單字 / 名詞片語（an apple）",
    2: "固定框架短句 3–4 詞（I see a dog.）",
    3: "完整句＋冠詞/複數/形容詞（I see a big dog.）",
    4: "擴充句：介系詞/連接詞/疑問句（I see a dog in the park.）",
    5: "複合句＋原因/時態（I like dogs because they are cute.）",
}


def decide_move(base, prev, flags, cas_delta, giveup=0, fatigue=False) -> str:
    """回傳 move ∈ {promote, hold, lateral, demote}（§3.2）。

    - demote（快降護信心）：放棄語達門檻／CAS 明顯下滑／中文骨架持續兩窗。
    - promote（慢升，需連續證據）：整句英文、CAS 未跌；跨 band 需連兩窗遲滯，
      冠詞未清時不放行跨 band。
    - lateral：疲態但分數沒掉 → 換主題不降級。
    - hold：其餘（含冷啟動無 prev）。
    """
    if prev is None:
        return "hold"

    short = bool(flags.get("short_sentences"))
    high = bool(flags.get("high_chinese_ratio"))
    article = bool(flags.get("article_correction"))
    prev_both_regress = bool(prev.get("both_regress"))
    prev_promote_ready = bool(prev.get("promote_ready"))

    # demote（任一觸發）
    if giveup >= GIVEUP_DEMOTE_TH:
        return "demote"
    if cas_delta <= -1.5:
        return "demote"
    if short and high and prev_both_regress:
        return "demote"

    # promote（同時成立）
    ready = (not short) and (not high) and (cas_delta >= 0)
    if ready:
        crossing = base.get("step", 1) >= 5
        if crossing and (not prev_promote_ready or article):
            return "hold"          # 跨 band 遲滯 / 冠詞未清 → 先 hold
        return "promote"

    # lateral（疲態、分數沒掉）
    if fatigue:
        return "lateral"
    return "hold"


# ---------------------------------------------------------------------------
# §4 主題迭代
# ---------------------------------------------------------------------------

def pick_topic(base, profile, prev_topic, mastery):
    """挑本輪主題與下一主題（topic, next_topic）。§4.2/§4.3。

    已解鎖（unlock_band ≤ band）內，用 B2 興趣重排、已熟主題跳過；全部給
    安全預設，profile/mastery 缺省不炸。
    """
    band = int((base or {}).get("band", 1))
    unlocked = [t for t in TOPIC_ORDER if TOPIC_UNLOCK[t] <= band]
    if not unlocked:
        unlocked = ["animal"]

    interests = [
        (i.get("topic") if isinstance(i, dict) else i)
        for i in (profile or {}).get("interests", [])
    ]
    ranked = [t for t in interests if t in unlocked] + \
             [t for t in unlocked if t not in interests]

    mastered = set(mastery or [])
    fresh = [t for t in ranked if t not in mastered]
    queue = fresh if fresh else ranked

    # 延續：prev_topic 仍未熟且已解鎖 → 續玩；否則取隊首
    if prev_topic in unlocked and prev_topic not in mastered:
        topic = prev_topic
    else:
        topic = queue[0]

    rest = [t for t in queue if t != topic] or [t for t in ranked if t != topic]
    next_topic = rest[0] if rest else topic
    return topic, next_topic


# ---------------------------------------------------------------------------
# §5.1 整合輸出 level_state
# ---------------------------------------------------------------------------

def _mastered_topics(profile) -> set:
    """由 B2 profile.mastered_vocab 推該學生已熟的主題（cat）。粗略：出現即算。"""
    cats = set()
    for v in (profile or {}).get("mastered_vocab", []):
        if isinstance(v, dict) and v.get("cat"):
            cats.add(v["cat"])
    return cats


def compute_level_state(diagnosis, profile=None, prev_level_state=None,
                        flags=None, fatigue=False) -> dict:
    """整合 base level + move + topic + directive_hint → level_state（§5.1）。

    遲滯：有 prev 時 level_index 相對 prev 單窗最多 ±1；冷啟動用 base。
    cas_delta 以 CAS 差近似趨勢（emotional_status 為字串不易取 delta）。
    缺 prev / 缺欄一律安全退化，不炸。
    """
    scores = (diagnosis or {}).get("scores") or {}
    base = scores_to_level(scores)
    flags = flags or {}
    giveup = int(flags.get("giveup_signal", 0) or 0)
    short = bool(flags.get("short_sentences"))
    high = bool(flags.get("high_chinese_ratio"))
    article = bool(flags.get("article_correction"))

    prev = prev_level_state
    cas_delta = (base["cas"] - float(prev["cas"])) if (prev and "cas" in prev) else 0.0
    move = decide_move(base, prev, flags, cas_delta, giveup, fatigue)

    # 遲滯：相對 prev 單窗 ±1；冷啟動用 base
    if prev and "level_index" in prev:
        delta = {"promote": 1, "demote": -1}.get(move, 0)
        idx = max(1, min(25, int(prev["level_index"]) + delta))
    else:
        idx = base["level_index"]
    band = (idx - 1) // 5 + 1
    step = idx - (band - 1) * 5

    mastery = _mastered_topics(profile)
    prev_topic = prev.get("topic") if prev else None
    topic, next_topic = pick_topic({"band": band}, profile, prev_topic, mastery)

    # 提示強度：step 高/升級 → 減提示；降級/step 低 → 加提示
    if move == "demote" or step <= 2:
        prompt_strength = "increase"
    elif step >= 4 and move != "demote":
        prompt_strength = "reduce"
    else:
        prompt_strength = "keep"

    # focus（由 flags / 最弱維度）
    if article:
        focus = "冠詞 a/an"
    elif high:
        focus = "整句英文輸出、減少中文替代"
    elif short:
        focus = "把句子講長、加形容詞或地點"
    else:
        weakest = min(_DIM_KEYS, key=lambda d: float(scores.get(d, 0)))
        focus = f"鞏固{_DIM_ZH[weakest]}"

    target_form = _TARGET_FORM[band]
    move_zh = {"promote": "升一階", "demote": "換個更好玩的說法（降階護信心）",
               "lateral": "換個主題喘口氣", "hold": "維持"}[move]
    directive_hint = (
        f"目前 {band}-{step}（{base['cefr']}），本輪目標語言形式：{target_form}；"
        f"主題：{topic}（下一主題 {next_topic}）；重點：{focus}；"
        f"策略：{move_zh}，提示強度{'增強' if prompt_strength=='increase' else '減弱' if prompt_strength=='reduce' else '維持'}。"
    )

    return {
        "level": f"{band}-{step}",
        "band": band,
        "step": step,
        "level_index": idx,
        "cefr": _CEFR[band],
        "cas": base["cas"],
        "cas_delta": round(cas_delta, 1),
        "move": move,
        "topic": topic,
        "next_topic": next_topic,
        "target_form": target_form,
        "focus": focus,
        "prompt_strength": prompt_strength,
        "directive_hint": directive_hint,
        "explain": base["explain"],
        # 供下窗遲滯判斷
        "both_regress": short and high,
        "promote_ready": (not short) and (not high) and (cas_delta >= 0),
    }
