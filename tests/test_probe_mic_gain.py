# -*- coding: utf-8 -*-
"""增益掃描的合理性檢查：壞數據不准產生建議值。

2026-07-30 第一版掃描的實際結果：

    增益 147 → peak 0.131      增益 100 → peak 0.061
    增益 120 → peak 0.067      增益  80 → peak 0.838   ← 比滿檔高 6 倍

**增益降低反而 peak 變高，物理上不可能。** 那一版把兩個隨時間變動的東西
綁在一起了：增益隨掃描進度變化，人聲音量也隨時間變化——受測者前幾格還沒
開口。量到的是說話音量，不是增益的影響。

而第一版照樣輸出了「建議增益：60」。**從壞數據算出來的建議值比不給建議
更糟**：它看起來一樣權威，但會讓人把麥克風調到錯誤的設定，而且之後沒人
會回頭懷疑這個數字。

所以合理性檢查本身必須有測試——它是唯一擋得住這件事的東西。
"""

from edge.probes import probe_mic_gain as p


# ---------------------------------------------------------------------------
# 單調性：增益越高 peak 必須越高
# ---------------------------------------------------------------------------

def test_a_clean_sweep_is_accepted():
    """增益由低到高、peak 隨之升高——正常的量測長這樣。"""
    assert p._is_monotonic([(60, 0.21), (80, 0.30), (100, 0.42),
                            (120, 0.55), (147, 0.71)]) is True


def test_the_2026_07_30_bad_sweep_is_rejected():
    """用當天真實量到的數字回歸測試。

    這組數字曾經產生「建議增益：60」，那是錯的。
    """
    assert p._is_monotonic([(147, 0.131), (120, 0.067), (100, 0.061),
                            (80, 0.838), (60, 0.272)]) is False


def test_input_order_does_not_matter():
    """檢查前要先依增益排序——呼叫端傳進來的順序是掃描順序，不是增益順序。"""
    rising = [(147, 0.71), (60, 0.21), (100, 0.42)]
    assert p._is_monotonic(rising) is True


def test_small_dips_are_tolerated_because_speech_is_not_a_test_tone():
    """人聲本來就有起伏，取中位數也消不乾淨。

    要求嚴格單調會把正常量測誤判成失敗，那又是一種假警告。
    """
    assert p._is_monotonic([(60, 0.30), (80, 0.29), (100, 0.45)]) is True


def test_a_real_reversal_is_still_caught():
    """容許誤差不能大到把真正的反轉也放過去。"""
    assert p._is_monotonic([(60, 0.30), (80, 0.10), (100, 0.45)]) is False


def test_a_single_measurement_cannot_be_non_monotonic():
    assert p._is_monotonic([(100, 0.5)]) is True


# ---------------------------------------------------------------------------
# 掃描順序：回合間要反轉
# ---------------------------------------------------------------------------

def test_alternate_rounds_reverse_the_order():
    """若每回合都同一個方向，「音量隨時間漂移」仍會與增益對齊。

    反轉之後，同一個增益在不同回合落在錄音時序的不同位置，漂移被打散成
    雜訊而不是系統性偏差。
    """
    assert p._sweep_order(0) == p.GAINS
    assert p._sweep_order(1) == list(reversed(p.GAINS))
    assert p._sweep_order(2) == p.GAINS


def test_every_gain_is_measured_more_than_once():
    """單次量測沒有辦法分辨「增益的影響」與「這一秒剛好講比較大聲」。"""
    assert p.ROUNDS >= 2


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------

def test_clipping_beats_everything_else():
    """削波是要避免的頭號問題，不能被其他判定蓋過。"""
    assert p._verdict(peak=0.99, clip=0.05, band=0.6) == "削波"


def test_a_healthy_level_is_good():
    assert p._verdict(peak=0.7, clip=0.0, band=0.6) == "good"


def test_too_quiet_is_flagged():
    assert p._verdict(peak=0.05, clip=0.0, band=0.6) == "太小"


def test_riding_the_ceiling_is_flagged_even_without_measured_clipping():
    """peak 0.9 以上代表隨時會爆——現在沒削波不代表孩子大聲一點也不會。"""
    assert p._verdict(peak=0.95, clip=0.0, band=0.6) == "太接近頂"


def test_clip_threshold_is_shared_with_preflight():
    """兩支工具的判準必須同源，否則掃描說 OK、自檢說削波。"""
    from edge.runtime import preflight

    assert p._verdict(
        peak=0.7, clip=preflight.MIC_CLIP_RATIO_MAX * 1.1, band=0.6) == "削波"
    assert p._verdict(
        peak=0.7, clip=preflight.MIC_CLIP_RATIO_MAX * 0.9, band=0.6) == "good"
