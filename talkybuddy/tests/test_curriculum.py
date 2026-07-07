# -*- coding: utf-8 -*-
"""test_curriculum.py — B3：CEFR 難度階梯 / 主題迭代（server/curriculum.py）。

契約依 research/b_axis/B3_CEFR難度階梯與主題迭代.md：
    compute_cas(scores) -> int                    §2.1
    scores_to_level(scores) -> dict               §2.2–2.4（band/step/level_index/cefr/cas）
純規則、stdlib-only，缺欄/極端輸入不炸。
"""

from __future__ import annotations

from server import curriculum


# ---------------------------------------------------------------------------
# §2.1 CAS 加權平均
# ---------------------------------------------------------------------------

def test_compute_cas_uniform_scores():
    """四維皆 60 → 加權平均仍 60。"""
    scores = {"pronunciation": 60, "fluency": 60, "vocabulary": 60, "grammar": 60}
    assert curriculum.compute_cas(scores) == 60


def test_compute_cas_weighted():
    """fluency 權重最高（0.30）→ 拉高整體。"""
    scores = {"pronunciation": 40, "fluency": 80, "vocabulary": 60, "grammar": 60}
    # 80*.30 + 60*.25 + 60*.25 + 40*.20 = 24+15+15+8 = 62
    assert curriculum.compute_cas(scores) == 62


# ---------------------------------------------------------------------------
# §2.2–2.4 scores_to_level
# ---------------------------------------------------------------------------

def test_scores_to_level_basic_band3():
    """四維皆 60 → CAS 60 落 Band 3（A1 Movers），level 字串 band-step。"""
    scores = {"pronunciation": 60, "fluency": 60, "vocabulary": 60, "grammar": 60}
    lv = curriculum.scores_to_level(scores)
    assert lv["cas"] == 60
    assert lv["band"] == 3
    assert lv["cefr"] == "A1 (Movers)"
    assert lv["level"] == f"{lv['band']}-{lv['step']}"
    assert lv["level_index"] == (lv["band"] - 1) * 5 + lv["step"]
    assert 1 <= lv["step"] <= 5


def test_scores_to_level_weakest_dim_gate_caps_band():
    """fluency 高但 grammar 25 → 最弱維度 gate 把 band 壓到 2（防跛腳升級）。"""
    scores = {"pronunciation": 70, "fluency": 90, "vocabulary": 70, "grammar": 25}
    lv = curriculum.scores_to_level(scores)
    # min_dim=25 < 30 → gate 上限 Band 2，縱使 CAS 落更高帶
    assert lv["band"] == 2
    assert "gate" in lv["explain"] or "grammar" in lv["explain"]


def test_scores_to_level_low_scores_band1():
    """四維皆 20 → Band 1（pre-A1），不炸。"""
    scores = {"pronunciation": 20, "fluency": 20, "vocabulary": 20, "grammar": 20}
    lv = curriculum.scores_to_level(scores)
    assert lv["band"] == 1
    assert lv["cefr"].startswith("pre-A1")
    assert lv["level_index"] == lv["step"]  # band1 → index == step


def test_scores_to_level_top_scores_band5():
    """四維皆 100 → Band 5（A2 Flyers），step 封頂 5、level_index 25。"""
    scores = {"pronunciation": 100, "fluency": 100, "vocabulary": 100, "grammar": 100}
    lv = curriculum.scores_to_level(scores)
    assert lv["band"] == 5
    assert lv["step"] == 5
    assert lv["level_index"] == 25


# ---------------------------------------------------------------------------
# §3.2 decide_move（升降級遲滯）
# ---------------------------------------------------------------------------

def _base(band=3, step=3):
    return {"band": band, "step": step, "level_index": (band - 1) * 5 + step, "cas": 58}


_CLEAN = {"short_sentences": False, "high_chinese_ratio": False,
          "article_correction": False, "giveup_signal": 0}


def test_decide_move_cold_start_holds():
    """無 prev（首次）→ hold，不炸。"""
    assert curriculum.decide_move(_base(), None, _CLEAN, 0.0, 0, False) == "hold"


def test_decide_move_giveup_demotes():
    """放棄語達門檻 → demote（快降護信心）。"""
    prev = {"cas": 58, "level_index": 13}
    assert curriculum.decide_move(_base(), prev, dict(_CLEAN, giveup_signal=2), 0.0, 2, False) == "demote"


def test_decide_move_score_drop_demotes():
    """CAS 明顯下滑（delta ≤ -1.5）→ demote。"""
    prev = {"cas": 62, "level_index": 13}
    assert curriculum.decide_move(_base(), prev, _CLEAN, -4.0, 0, False) == "demote"


def test_decide_move_clean_improving_promotes():
    """整句英文、無中文骨架、CAS 上升 → promote。"""
    prev = {"cas": 55, "level_index": 12, "promote_ready": True}
    assert curriculum.decide_move(_base(step=3), prev, _CLEAN, 3.0, 0, False) == "promote"


def test_decide_move_cross_band_needs_two_windows():
    """在 step 5（將跨 band），上窗未達升級條件 → 遲滯 hold。"""
    prev = {"cas": 60, "level_index": 15, "promote_ready": False}
    assert curriculum.decide_move(_base(band=3, step=5), prev, _CLEAN, 2.0, 0, False) == "hold"


def test_decide_move_fatigue_lateral():
    """疲態、分數沒掉 → lateral（換主題不降級）。"""
    prev = {"cas": 58, "level_index": 13, "promote_ready": False}
    flags = {"short_sentences": True, "high_chinese_ratio": False,
             "article_correction": False, "giveup_signal": 0}
    assert curriculum.decide_move(_base(), prev, flags, 0.0, 0, True) == "lateral"


# ---------------------------------------------------------------------------
# §4 pick_topic（主題迭代）
# ---------------------------------------------------------------------------

def test_pick_topic_band1_only_unlocks_animal_food():
    """Band 1 → 只解鎖 animal/food（color 需 Band2）。"""
    topic, nxt = curriculum.pick_topic({"band": 1}, None, None, None)
    assert topic in ("animal", "food")
    assert nxt in ("animal", "food")
    assert topic != nxt


def test_pick_topic_interest_reranks():
    """B2 興趣把 food 排前 → 先玩 food。"""
    profile = {"interests": [{"topic": "food", "label": "食物", "hits": 9}]}
    topic, _ = curriculum.pick_topic({"band": 2}, profile, None, None)
    assert topic == "food"


def test_pick_topic_mastered_topic_advances():
    """已熟主題被跳過，前進到下一個未熟主題。"""
    topic, _ = curriculum.pick_topic({"band": 2}, None, "animal", {"animal"})
    assert topic != "animal"


# ---------------------------------------------------------------------------
# §5.1 compute_level_state（整合輸出）
# ---------------------------------------------------------------------------

def test_compute_level_state_cold_start():
    """首次（無 prev）→ move=hold，level 依 base，含 directive_hint/topic。"""
    diag = {"scores": {"pronunciation": 60, "fluency": 60, "vocabulary": 60, "grammar": 60}}
    ls = curriculum.compute_level_state(diag, None, None)
    assert ls["move"] == "hold"
    assert ls["band"] == 3
    assert ls["topic"]
    assert ls["directive_hint"]
    assert ls["target_form"]


def test_compute_level_state_hysteresis_single_step():
    """CAS 大跳，但遲滯限制單窗最多 +1 step（相對 prev level_index）。"""
    diag = {"scores": {"pronunciation": 80, "fluency": 90, "vocabulary": 85, "grammar": 80}}
    prev = {"cas": 55, "level_index": 12, "band": 3, "step": 2,
            "promote_ready": True, "topic": "animal"}
    ls = curriculum.compute_level_state(diag, None, prev)
    assert ls["level_index"] == 13  # 12 + 1，不因 CAS 暴衝而跳多階


def test_compute_level_state_giveup_demotes_index():
    """flags 帶 giveup → demote，level_index 相對 prev 降一階。"""
    diag = {"scores": {"pronunciation": 55, "fluency": 55, "vocabulary": 55, "grammar": 55}}
    prev = {"cas": 55, "level_index": 12, "band": 3, "step": 2, "topic": "animal"}
    flags = {"short_sentences": True, "high_chinese_ratio": True,
             "article_correction": False, "giveup_signal": 3}
    ls = curriculum.compute_level_state(diag, None, prev, flags=flags)
    assert ls["move"] == "demote"
    assert ls["level_index"] == 11
