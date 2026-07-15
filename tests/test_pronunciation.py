# -*- coding: utf-8 -*-
"""server/pronunciation.py 測試。

純函式（_align_score / _g2p_to_ipa / _ctc_collapse / available）快、免模型；
整合測試（score 端到端）需載模型，標 slow 並在無依賴時 skip。
"""
from __future__ import annotations

import os

import pytest

from server import pronunciation as P


# ---------------- _align_score（純、免模型） ----------------

def test_align_identical_is_100():
    seq = ["a", "b", "c", "d"]
    assert P._align_score(seq, seq) == 100.0


def test_align_empty_ref_is_zero():
    assert P._align_score([], ["a", "b"]) == 0.0


def test_align_disjoint_is_low():
    assert P._align_score(["a", "b", "c"], ["x", "y", "z"]) == 0.0


def test_align_partial_monotonic():
    ref = ["a", "b", "c", "d", "e"]
    full = P._align_score(ref, ref)
    half = P._align_score(ref, ["a", "b", "c", "x", "y"])
    none = P._align_score(ref, ["v", "w", "x", "y", "z"])
    assert full > half > none


# ---------------- _ctc_collapse（純、免模型） ----------------

def test_ctc_collapse_folds_repeats_and_pad():
    id2tok = {0: "[PAD]", 1: "a", 2: "b", 3: "|"}
    # 連續重複 a a、PAD 間隔、word 分隔 |、再 b
    ids = [1, 1, 0, 1, 3, 2, 2, 0]
    # 期望：a（折疊）、a（PAD 打斷後的新 a）、b；| 與 PAD 濾掉
    assert P._ctc_collapse(ids, id2tok, pad_id=0) == ["a", "a", "b"]


def test_ctc_collapse_filters_non_phone_tokens():
    id2tok = {0: "[PAD]", 1: "<s>", 2: "</s>", 3: "p"}
    ids = [1, 3, 2]
    assert P._ctc_collapse(ids, id2tok, pad_id=0) == ["p"]


# ---------------- _g2p_to_ipa（需 g2p_en，但免聲學模型） ----------------

@pytest.mark.skipif(
    __import__("importlib").util.find_spec("g2p_en") is None,
    reason="g2p_en 未安裝",
)
def test_g2p_to_ipa_maps_into_model_vocab():
    ipa = P._g2p_to_ipa("I want to eat an apple.")
    assert ipa, "應解出音素"
    # 全部落在 ARPA_TO_IPA 的值域（＝模型 vocab 子集）
    assert set(ipa) <= set(P.ARPA_TO_IPA.values())
    # apple 的收尾 æ p l 應出現
    joined = " ".join(ipa)
    assert "æ" in joined and "p" in joined and "l" in joined


# ---------------- available（不拋、回 bool） ----------------

def test_available_returns_bool_and_never_raises():
    assert isinstance(P.available(), bool)


def test_score_none_on_empty_inputs():
    assert P.score("", "hello") is None
    assert P.score("/x.wav", "") is None


# ---------------- 整合（端到端，需模型，標 slow） ----------------

_FIXTURE = os.path.join(
    os.path.dirname(__file__), "..", "spike", "pron_assess", "clean_en_apple.wav"
)


@pytest.mark.slow
@pytest.mark.skipif(not P.available(), reason="聲學模型依賴未就緒")
@pytest.mark.skipif(not os.path.exists(_FIXTURE), reason="缺 clean_en_apple.wav fixture")
def test_score_discriminates_correct_vs_wrong():
    correct = P.score(_FIXTURE, "I want to eat an apple.")
    wrong = P.score(_FIXTURE, "The quick brown fox jumps over the lazy dog.")
    assert correct is not None and wrong is not None
    assert correct - wrong >= 20, (correct, wrong)
