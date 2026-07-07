# -*- coding: utf-8 -*-
"""test_profile.py — B2：server/profile.build_profile 規則式抽取器。

契約（依 research/b_axis/B2_長期記憶學生profile.md §3）：
    build_profile(interactions, diagnoses, prev=None) -> dict
產出 interests / mastered_vocab / learning_vocab / error_patterns /
difficulty / emotional_recent / source_seq / interaction_count / generated_by。
重用 scaffold.VOCAB 與 diagnose 的偵測函式，純標準庫、空互動不炸。
"""

from __future__ import annotations

from server import profile


def _inter(seq, text, conf, reply, scores=None):
    """組一筆 interaction（仿 store.list_interactions 攤回的結構）。"""
    return {
        "seq": seq,
        "student_id": "STUDENT-X",
        "student_text": text,
        "asr_confidence": conf,
        "ai_response_text": reply,
        "scores": scores or {"fluency": 60, "vocabulary": 62, "grammar": 58},
    }


def test_build_profile_empty_does_not_crash():
    """空互動 → 回合法 dict，不炸，generated_by=rule。"""
    prof = profile.build_profile([], [])
    assert prof["interaction_count"] == 0
    assert prof["interests"] == []
    assert prof["mastered_vocab"] == []
    assert prof["generated_by"] == "rule"


def test_interests_counts_by_category_desc():
    """依 VOCAB cat 命中次數排序：動物出現最多 → interests[0].topic=animal。"""
    inters = [
        _inter(1, "I see a cat and a dog", 0.9, "很好！"),
        _inter(2, "I like fish", 0.9, "很棒！"),
        _inter(3, "我想吃 apple", 0.9, "蘋果很好吃！"),
    ]
    prof = profile.build_profile(inters, [])
    assert prof["interests"][0]["topic"] == "animal"
    assert prof["interests"][0]["hits"] == 3  # cat + dog + fish
    assert prof["interests"][0]["label"] == "動物"


def test_mastered_vs_learning_vocab():
    """apple 兩次清楚且無糾錯 → mastered；orange 一次被糾錯 → learning。"""
    inters = [
        _inter(1, "I want an apple", 0.9, "完全正確，太棒了！"),
        _inter(2, "I have an apple", 0.9, "說得很好！"),
        _inter(3, "I want a orange", 0.9, "記得橘子要用 an 喔"),
    ]
    prof = profile.build_profile(inters, [])
    mastered_en = {v["en"] for v in prof["mastered_vocab"]}
    learning_en = {v["en"] for v in prof["learning_vocab"]}
    assert "apple" in mastered_en
    assert "orange" in learning_en
    assert "orange" not in mastered_en


def test_error_patterns_detected_with_hits():
    """三類錯點都能累積 hits，trend 為合法值。"""
    inters = [
        # 冠詞糾錯 + 短句
        _inter(1, "I have dog", 0.9, "記得小狗前面要加 a 喔：I have a dog."),
        # 中文比例高
        _inter(2, "我今天很開心想要玩", 0.9, "好的！"),
    ]
    prof = profile.build_profile(inters, [])
    types = {e["type"]: e for e in prof["error_patterns"]}
    assert types["article_ao"]["hits"] >= 1
    assert types["mixed_lang"]["hits"] >= 1
    assert types["short_sent"]["hits"] >= 1
    for e in prof["error_patterns"]:
        assert e["trend"] in ("improving", "regressing", "stable")


def test_difficulty_level_from_latest_diagnosis_score_avg():
    """difficulty.level 由最新診斷四維平均分段：avg≈65 → level 3。"""
    diags = [
        {"date": "2026-07-06", "scores": {"pronunciation": 50, "fluency": 50,
                                          "vocabulary": 50, "grammar": 50},
         "emotional_status": "舊的"},
        {"date": "2026-07-07", "scores": {"pronunciation": 66, "fluency": 63,
                                          "vocabulary": 68, "grammar": 63},
         "emotional_status": "自信心明顯提升"},
    ]
    prof = profile.build_profile([_inter(1, "I like a cat", 0.9, "好")], diags)
    assert prof["difficulty"]["level"] == 3
    assert prof["emotional_recent"] == "自信心明顯提升"


def test_source_seq_and_interaction_count():
    """source_seq = 最大 seq；interaction_count = 筆數。"""
    inters = [
        _inter(5, "I like a dog", 0.9, "好"),
        _inter(9, "I like a cat", 0.9, "好"),
        _inter(3, "I like fish", 0.9, "好"),
    ]
    prof = profile.build_profile(inters, [])
    assert prof["source_seq"] == 9
    assert prof["interaction_count"] == 3
