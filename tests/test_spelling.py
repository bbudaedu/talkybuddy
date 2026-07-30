# -*- coding: utf-8 -*-
"""test_spelling.py — 背單字判定核心（server/spelling.py）。

這裡的門檻與字串格式**不是設計出來的，是實測選出來的**。
2026-07-31 在開發機上把五種字母寫法丟給本地 TTS 合成、再用 SenseVoice
讀回，只有 "A, P, P, L, E," 完美來回。所以下面第一條測試釘住的是一個
實測結論，不是風格偏好——改它之前先重跑 edge/probes/probe_spell_tts.py。
"""

from __future__ import annotations

from server import scaffold, spelling


# ---------------------------------------------------------------------------
# 字母念法：實測選出來的唯一可靠格式
# ---------------------------------------------------------------------------

def test_letter_format_is_the_one_that_survived_the_spike():
    """大寫、", " 分隔、結尾一個逗號。四種替代寫法實測全部壞掉。"""
    assert spelling.letters_for_tts("apple") == "A, P, P, L, E,"


def test_letter_format_handles_short_and_dirty_words():
    assert spelling.letters_for_tts("I") == "I,"
    assert spelling.letters_for_tts("ice cream") == "I, C, E, C, R, E, A, M,"
    assert spelling.letters_for_tts("") == ""
    assert spelling.letters_for_tts(None) == ""


def test_letter_sequence_becomes_a_single_english_tts_segment():
    """字母序列必須整段進英文 voice，不能被中英切段切碎。"""
    text = f"我們來拼：{spelling.letters_for_tts('apple')}"
    assert ("en", "A, P, P, L, E,") in scaffold.split_tts_segments(text)


def test_ref_letters_is_the_comparison_sequence():
    assert spelling.ref_letters("apple") == ["A", "P", "P", "L", "E"]
    assert spelling.ref_letters("") == []


# ---------------------------------------------------------------------------
# 聽回來的字母
# ---------------------------------------------------------------------------

def test_heard_letters_reads_a_normal_spelling():
    assert spelling.heard_letters("A, P, P, L, E.") == ["A", "P", "P", "L", "E"]
    assert spelling.heard_letters("a p p l e") == ["A", "P", "P", "L", "E"]


def test_heard_letters_falls_back_when_asr_glues_them_together():
    """ASR 把字母黏成一個字時退而求其次逐字元拆。

    **這代表分不出「孩子在拼」與「孩子在唸整個單字」**——兩者的 ASR 文字
    一模一樣。分不出來就不假裝分得出來，一律當作拼對了（已知邊界）。
    """
    assert spelling.heard_letters("Apple.") == ["A", "P", "P", "L", "E"]


def test_heard_letters_never_raises_on_garbage():
    for junk in (None, "", "。。。", "我不會", 12345):
        assert isinstance(spelling.heard_letters(junk), list)


# ---------------------------------------------------------------------------
# 命中率
# ---------------------------------------------------------------------------

def test_letter_hit_rate_is_full_when_perfect():
    assert spelling.letter_hit_rate(["A", "P", "P", "L", "E"],
                                    ["A", "P", "P", "L", "E"]) == 1.0


def test_letter_hit_rate_tolerates_one_wrong_letter():
    """唸錯一個字母＝80%，在 0.6 門檻之上——寬鬆鼓勵制的具體樣子。"""
    rate = spelling.letter_hit_rate(["A", "P", "P", "L", "E"],
                                    ["A", "P", "P", "O", "E"])
    assert rate == 0.8
    assert rate >= spelling.PASS_THRESHOLD


def test_letter_hit_rate_fails_when_most_letters_are_missing():
    """只唸兩個字母＝40%，該重來。"""
    rate = spelling.letter_hit_rate(["A", "P", "P", "L", "E"], ["A", "P"])
    assert rate == 0.4
    assert rate < spelling.PASS_THRESHOLD


def test_letter_hit_rate_handles_empty_input():
    assert spelling.letter_hit_rate([], ["A"]) == 0.0
    assert spelling.letter_hit_rate(["A", "B"], []) == 0.0


# ---------------------------------------------------------------------------
# 拼字命中率：真迴路實測字串
#
# 下面每一個 ASR 字串都是 2026-07-31 真的跑出來的：本地 TTS 念字母 →
# SenseVoice 聽回。不是手編的——手編的字串永遠想不到 "W ATE R." 這種形狀，
# 而正是那種形狀讓單一讀法的判定誤判。
# ---------------------------------------------------------------------------

_REAL_ASR = [
    ("apple", "AP,, P, L E."),          # 前兩個字母黏住
    ("dog", "D, O G."),                 # 後兩個字母黏住
    ("book", "B, O, Ok."),              # 只黏最後兩個 → 單字母讀法會整段丟掉
    ("banana", "The A N A N the."),     # 混進 The/the → 全拆字母讀法會被汙染
    ("cat", "C, A."),                   # 尾字母漏聽
    ("pencil", "D, E, N, C, I, L."),    # 首字母聽錯
    ("water", "W ATE R."),              # 中間三個字母黏成一團
    ("mom", "M Om."),
]


def test_every_real_asr_reading_passes_the_threshold():
    """八個詞的真迴路輸出全部要過。

    只取單字母的讀法在 book／water 上是 0.5 / 0.4（拼對了卻判沒過），
    全拆字母的讀法在 banana 上會被 The/the 汙染。兩種都算取較好的，
    八個才全過。
    """
    for en, heard in _REAL_ASR:
        rate = spelling.spell_hit_rate(en, heard)
        assert rate >= spelling.PASS_THRESHOLD, f"{en}: {heard!r} 只拿到 {rate}"


def test_single_reading_alone_would_misjudge_book_and_water():
    """釘住「為什麼需要兩種讀法」——這條紅了代表有人把它改回單一讀法。"""
    for en, heard in (("book", "B, O, Ok."), ("water", "W ATE R.")):
        naive = spelling.letter_hit_rate(spelling.ref_letters(en),
                                         spelling.heard_letters(heard))
        assert naive < spelling.PASS_THRESHOLD, f"{en} 的單一讀法不再誤判了？"
        assert spelling.spell_hit_rate(en, heard) >= spelling.PASS_THRESHOLD


def test_all_letters_reading_keeps_glued_tokens():
    assert spelling.all_letters("B, O, Ok.") == ["B", "O", "O", "K"]
    assert spelling.all_letters("W ATE R.") == ["W", "A", "T", "E", "R"]
    assert spelling.all_letters("") == []


def test_spell_hit_rate_still_rejects_a_child_who_said_nothing_useful():
    """寬鬆不等於沒有判定——兩種讀法都算，該不過的還是不過。"""
    assert spelling.spell_hit_rate("apple", "我不會") == 0.0
    assert spelling.spell_hit_rate("apple", "") == 0.0
    assert spelling.spell_hit_rate("", "A, P, P, L, E.") == 0.0
    assert spelling.spell_hit_rate("banana", "A, B.") < spelling.PASS_THRESHOLD


# ---------------------------------------------------------------------------
# 整字／例句命中
# ---------------------------------------------------------------------------

def test_word_hit_rate_is_full_on_an_exact_match():
    assert spelling.word_hit_rate("apple", "Apple.") == 1.0


def test_word_hit_rate_finds_the_word_inside_a_sentence():
    """例句那一步比的是**目標單字**，不是整句——整句逐字比對對國小生太嚴。"""
    assert spelling.word_hit_rate("apple", "I want to eat an apple.") == 1.0


def test_word_hit_rate_is_low_when_asr_mangles_the_word():
    """實測：TTS 念 apple 被 SenseVoice 聽成 Bbble。整字跟讀本來就脆弱，
    所以判定主力放在拼音那一步，而且重試有上限、不會卡死。"""
    assert spelling.word_hit_rate("apple", "Bbble.") < spelling.PASS_THRESHOLD


def test_word_hit_rate_is_zero_without_english():
    assert spelling.word_hit_rate("apple", "我不知道") == 0.0
    assert spelling.word_hit_rate("apple", "") == 0.0
    assert spelling.word_hit_rate("", "apple") == 0.0


# ---------------------------------------------------------------------------
# 學習狀況寫入（唯一有副作用的函式）
# ---------------------------------------------------------------------------

def test_a_wrong_word_becomes_due_immediately(tmp_db):
    """答錯的詞 interval 歸零＝立刻到期，下一局第一個就會挑到它。

    這是整個功能「紀錄確認學習狀況」的可見出口：上禮拜拼錯的詞，
    今天第一個練。
    """
    from server import store

    assert spelling.record_word_result("STU-1", "蘋果", False) is True
    row = store.get_word_review("STU-1", "蘋果")
    assert row is not None
    assert row["interval_days"] == 0
    assert row["lapses"] == 1


def test_a_correct_word_gets_pushed_into_the_future(tmp_db):
    from server import store

    assert spelling.record_word_result("STU-1", "蘋果", True) is True
    row = store.get_word_review("STU-1", "蘋果")
    assert row["interval_days"] >= 1
    assert row["reps"] == 1


def test_recording_preserves_last_seq_so_the_background_pass_stays_deduped(tmp_db):
    """不能把 last_seq 洗成 0。

    srs.record_interactions 用 last_seq 判斷「這筆互動算過了沒」。
    洗掉它，背景刷新就會把舊互動重新計分一次。
    """
    from server import srs, store

    store.upsert_word_review("STU-1", "蘋果", srs.initial_state(), last_seq=42)
    spelling.record_word_result("STU-1", "蘋果", True)
    assert store.get_word_review("STU-1", "蘋果")["last_seq"] == 42


def test_recording_never_raises_even_when_the_store_is_broken(tmp_db, monkeypatch):
    """記錄是加值，不得拖垮教學迴圈——與 games._due_first 讀取端同一個原則。"""
    from server import store

    def _boom(*a, **kw):
        raise RuntimeError("DB 壞了")

    monkeypatch.setattr(store, "upsert_word_review", _boom)
    assert spelling.record_word_result("STU-1", "蘋果", True) is False


def test_recording_is_a_noop_without_a_student(tmp_db):
    """沒有 student_id（例如測試或未登入）就不寫，也不該炸。"""
    assert spelling.record_word_result("", "蘋果", True) is False
    assert spelling.record_word_result(None, "蘋果", True) is False
    assert spelling.record_word_result("STU-1", "", True) is False
