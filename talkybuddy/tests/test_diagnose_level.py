# -*- coding: utf-8 -*-
"""test_diagnose_level.py — B3 接線：diagnose 併入 level_state + giveup_signal。"""

from __future__ import annotations

from server import diagnose


def test_generate_diagnosis_attaches_level_state():
    """generate_diagnosis 產出含 level_state（band/level/move/topic）。"""
    inters = [
        {"student_text": "I see a cat", "asr_confidence": 0.9,
         "ai_response_text": "很好！", "scores": {"fluency": 60, "vocabulary": 62, "grammar": 58}},
    ]
    d = diagnose.generate_diagnosis(inters, None)
    ls = d["level_state"]
    assert 1 <= ls["band"] <= 5
    assert ls["level"] == f"{ls['band']}-{ls['step']}"
    assert ls["move"] in ("promote", "hold", "lateral", "demote")
    assert ls["topic"]


def test_detect_patterns_counts_giveup_signal():
    """student_text 命中放棄語 → giveup_signal 計數（§3.3-A）。"""
    inters = [
        {"student_text": "我不會", "asr_confidence": 0.5, "ai_response_text": "沒關係"},
        {"student_text": "I don't know", "asr_confidence": 0.5, "ai_response_text": "慢慢來"},
        {"student_text": "I see a dog", "asr_confidence": 0.9, "ai_response_text": "很好"},
    ]
    flags = diagnose._detect_patterns(inters)
    assert flags["giveup_signal"] == 2


def test_giveup_drives_demote_via_level_state():
    """近窗多次放棄語 + 有 prev level_state → move=demote。"""
    prev = {"date": "2026-07-07", "scores": {"pronunciation": 60, "fluency": 60,
            "vocabulary": 60, "grammar": 60},
            "level_state": {"cas": 60, "level_index": 13, "band": 3, "step": 3,
                            "topic": "animal"}}
    inters = [
        {"student_text": "我不會", "asr_confidence": 0.4, "ai_response_text": "沒關係",
         "scores": {"fluency": 55, "vocabulary": 55, "grammar": 55}},
        {"student_text": "不知道啦", "asr_confidence": 0.4, "ai_response_text": "慢慢來",
         "scores": {"fluency": 55, "vocabulary": 55, "grammar": 55}},
    ]
    d = diagnose.generate_diagnosis(inters, prev)
    assert d["level_state"]["move"] == "demote"
