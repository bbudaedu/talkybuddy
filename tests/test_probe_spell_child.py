# -*- coding: utf-8 -*-
"""童聲拼字母量測的合理性檢查：壞數據不准長得像結論。

這支探針的產出會被拿去調 `server/spelling.py` 的 `PASS_THRESHOLD`——
一個直接決定「孩子拼對了算不算過」的常數。所以它跟 probe_mic_gain 有
一模一樣的風險：

    孩子鬧脾氣只做了三次 → 中位數照樣算得出來
    → 印出來跟做滿 15 次的中位數長得一模一樣
    → 有人拿它去調門檻，之後也沒人回頭懷疑這個數字

**從壞數據算出來的建議值比不給建議更糟。** 擋住這件事的只有下面這些檢查，
所以檢查本身必須有測試。

另外釘住兩個量測方法上的決定：反轉詞序（抗熟練度漂移）與錄音長度隨字數變
（4 秒會把 banana 從中間切斷，而被切斷的錄音判出來的低命中率長得跟
「孩子不會拼」一模一樣）。
"""

from __future__ import annotations

from edge.probes import probe_spell_child as p


# ---------------------------------------------------------------------------
# 抗混淆：每輪反轉詞序
# ---------------------------------------------------------------------------

def test_consecutive_rounds_never_use_the_same_order():
    """孩子的熟練度隨時間漂移，固定順序會讓「第幾個唸的」混進結果。

    這是 probe_mic_gain 第一版被咬出來的那個坑的同一個形狀。
    """
    for rnd in range(p.ROUNDS - 1):
        assert p._order_for_round(rnd) != p._order_for_round(rnd + 1)


def test_every_round_still_covers_every_word():
    """換順序不能換掉題目——少測一個詞，那個詞就沒有資料。"""
    for rnd in range(p.ROUNDS):
        assert sorted(p._order_for_round(rnd)) == sorted(p.WORDS)


# ---------------------------------------------------------------------------
# 錄音長度：不准把孩子的話切斷
# ---------------------------------------------------------------------------

def test_longer_words_get_longer_recordings():
    assert p._record_seconds("banana") > p._record_seconds("dog")


def test_even_the_shortest_word_beats_the_conversational_default():
    """對話用的預設是 4 秒。拼三個字母含停頓就不只 4 秒了。"""
    for word in p.WORDS:
        assert p._record_seconds(word) > 4.0, f"{word} 的錄音長度會切斷孩子"


def test_recording_length_is_capped():
    """避免詞庫日後加入超長詞時錄一分鐘——孩子早就走掉了。"""
    assert p._record_seconds("a" * 200) <= p._MAX_SECONDS


# ---------------------------------------------------------------------------
# 樣本不足時不准下結論
# ---------------------------------------------------------------------------

def _report_text(results, capsys):
    p._report(results)
    return capsys.readouterr().out


def test_a_thin_sample_refuses_to_give_a_recommendation(capsys):
    """三次就收工的資料不准印出判讀規則——那會被當成結論。"""
    out = _report_text({"dog": [1.0], "cat": [0.67], "book": [0.75]}, capsys)
    assert "不要拿這張表去調" in out
    assert "怎麼讀這張表" not in out


def test_a_full_sample_does_give_the_recommendation(capsys):
    full = {w: [0.8, 0.7, 0.9] for w in p.WORDS}
    out = _report_text(full, capsys)
    assert "怎麼讀這張表" in out
    assert "不要拿這張表去調" not in out


def test_an_empty_run_says_so_instead_of_crashing(capsys):
    """孩子一次都沒開口（或麥克風壞了）也不能拋例外。"""
    out = _report_text({w: [] for w in p.WORDS}, capsys)
    assert "沒得報告" in out


def test_a_dead_microphone_points_at_the_microphone(capsys):
    """全 0 命中率不是門檻的問題。報表要把人導向 probe_mic_gain，
    而不是讓人把 PASS_THRESHOLD 一路調低到 0。"""
    out = _report_text({w: [0.0, 0.0, 0.0] for w in p.WORDS}, capsys)
    assert "probe_mic_gain" in out
