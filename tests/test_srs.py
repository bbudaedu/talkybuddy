# -*- coding: utf-8 -*-
"""test_srs.py — 間隔重複排程（server/srs.py）與 word_reviews 儲存。

驗的是行為不是實作細節：答對要拉長間隔、答錯要立刻回到題庫、
同一筆互動重跑不得重複計分、讀不到 DB 時不得拖垮教學迴圈。
"""

from __future__ import annotations

import datetime

from server import srs, store

_TZ = datetime.timezone(datetime.timedelta(hours=8))


def _at(day: int, hour: int = 9) -> str:
    return datetime.datetime(2026, 7, day, hour, tzinfo=_TZ).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 純函式排程
# ---------------------------------------------------------------------------

def test_first_correct_review_is_due_tomorrow():
    st = srs.schedule(None, True, now=_at(20))
    assert st["reps"] == 1
    assert st["interval_days"] == 1
    assert st["due_at"].startswith("2026-07-21")


def test_intervals_grow_with_consecutive_correct_answers():
    """答對要越隔越久——這才是間隔重複的重點，否則就只是每天重練。"""
    st = None
    intervals = []
    day = 20
    for _ in range(4):
        st = srs.schedule(st, True, now=_at(day))
        intervals.append(st["interval_days"])
        day += 1
    assert intervals == sorted(intervals), f"間隔沒有遞增：{intervals}"
    assert intervals[-1] > intervals[0]


def test_wrong_answer_makes_the_word_due_immediately():
    """答錯的詞必須立刻回到題庫。隔天才複習，對只練幾分鐘的孩子等於白錯。"""
    st = srs.schedule(None, True, now=_at(20))
    st = srs.schedule(st, False, now=_at(21))
    assert st["interval_days"] == 0
    assert st["reps"] == 0
    assert st["lapses"] == 1
    assert srs.is_due(st, now=_at(21, 10)) is True


def test_ease_drops_on_failure_and_has_a_floor():
    st = None
    for i in range(20):
        st = srs.schedule(st, False, now=_at(20 + i % 5))
    assert st["ease"] == srs.MIN_EASE, f"ease 沒有下限：{st['ease']}"


def test_ease_rises_on_success_and_has_a_ceiling():
    st = None
    for i in range(20):
        st = srs.schedule(st, True, now=_at(20))
    assert st["ease"] <= srs.MAX_EASE


def test_interval_is_capped():
    """一學期約 20 週，超過一學期的間隔沒有教學意義。"""
    st = None
    for _ in range(30):
        st = srs.schedule(st, True, now=_at(20))
    assert st["interval_days"] <= 120


def test_schedule_survives_garbage_state():
    """DB 讀出髒資料（字串 ease、None interval）也不得拋。"""
    st = srs.schedule({"ease": "壞掉", "interval_days": None, "reps": "x"}, True, now=_at(20))
    assert st["ease"] > 0 and st["interval_days"] >= 1


def test_not_due_before_the_scheduled_day():
    st = srs.schedule(None, True, now=_at(20))
    assert srs.is_due(st, now=_at(20, 23)) is False
    assert srs.is_due(st, now=_at(21, 9)) is True


# ---------------------------------------------------------------------------
# 從互動紀錄評分
# ---------------------------------------------------------------------------

def test_grade_interaction_marks_vocab_hits():
    """句子裡出現的詞庫詞要被抓到，判準與 profile 的掌握度一致。"""
    hits = srs.grade_interaction({
        "student_text": "I have a dog and an apple.",
        "ai_response_text": "很好！",
        "asr_confidence": 0.95,
    })
    assert hits, "應抓到詞庫詞"
    assert all(v is True for v in hits.values()), "高信心且沒被糾正 → 全部算答對"


def test_low_confidence_counts_as_wrong():
    hits = srs.grade_interaction({
        "student_text": "I have a dog.",
        "ai_response_text": "很好！",
        "asr_confidence": 0.3,
    })
    assert hits and all(v is False for v in hits.values())


def test_grade_interaction_never_raises_on_garbage():
    assert srs.grade_interaction(None) == {}
    assert srs.grade_interaction({}) == {}
    assert srs.grade_interaction({"student_text": None, "asr_confidence": "x"}) == {}


# ---------------------------------------------------------------------------
# 寫入與讀取（真實 store）
# ---------------------------------------------------------------------------

def test_record_interactions_persists_reviews(tmp_db):
    n = srs.record_interactions([{
        "seq": 1,
        "ts": _at(20),
        "student_text": "I have a dog.",
        "ai_response_text": "很好！",
        "asr_confidence": 0.95,
    }], student_id="alice")
    assert n > 0
    assert store.get_word_review("alice", "狗") is not None


def test_same_interaction_is_not_counted_twice(tmp_db):
    """背景刷新每次都讀最近 10 筆，重跑不得重複計分。"""
    inter = [{
        "seq": 7,
        "ts": _at(20),
        "student_text": "I have a dog.",
        "ai_response_text": "很好！",
        "asr_confidence": 0.95,
    }]
    srs.record_interactions(inter, student_id="alice")
    first = store.get_word_review("alice", "狗")
    srs.record_interactions(inter, student_id="alice")
    again = store.get_word_review("alice", "狗")
    assert again["reps"] == first["reps"], "同一筆互動被算了兩次"
    assert again["due_at"] == first["due_at"]


def test_reviews_are_scoped_by_student(tmp_db):
    inter = [{"seq": 1, "ts": _at(20), "student_text": "I have a dog.",
              "ai_response_text": "好", "asr_confidence": 0.95}]
    srs.record_interactions(inter, student_id="alice")
    assert store.get_word_review("bob", "狗") is None


def test_due_words_only_returns_words_past_their_due_date(tmp_db):
    # 答對 → 明天到期；答錯 → 立刻到期
    srs.record_interactions(
        [{"seq": 1, "ts": _at(20), "student_text": "I have a dog.",
          "ai_response_text": "好", "asr_confidence": 0.95}],
        student_id="alice",
    )
    srs.record_interactions(
        [{"seq": 2, "ts": _at(20), "student_text": "I see a cat.",
          "ai_response_text": "好", "asr_confidence": 0.2}],
        student_id="alice",
    )
    due = [r["word"] for r in store.list_due_word_reviews("alice", now=_at(20, 12))]
    assert "貓" in due, "答錯的詞應立刻到期"
    assert "狗" not in due, "答對的詞不該當天又冒出來"


def test_due_words_returns_empty_when_db_unavailable(tmp_db, monkeypatch):
    """排程是加值功能：讀不到就當沒有，不得把例外往教學迴圈丟。"""
    def _boom(*a, **kw):
        raise RuntimeError("DB 壞了")

    monkeypatch.setattr(store, "list_due_word_reviews", _boom)
    assert srs.due_words("alice") == []


# ---------------------------------------------------------------------------
# 接線：派作業優先挑到期詞
# ---------------------------------------------------------------------------

_WEAK_GRAMMAR = {"scores": {"pronunciation": 80, "fluency": 78,
                            "vocabulary": 75, "grammar": 40}}


def test_homework_prioritises_due_words(tmp_db):
    """答錯過、而且到了複習時間的詞，必須排在這份作業的最前面。

    這是間隔重複對使用者唯一可見的效果——出題順序真的變了。
    刻意挑「香蕉」：文法弱項的預設取題順序裡沒有它，所以它出現在
    第一題只可能來自複習排程。斷言位置而不只是「有出現」，否則詞庫裡
    本來就會出的詞會讓測試在功能失效時照樣全綠。
    """
    from server.agents import homework

    baseline = homework.generate_homework({}, _WEAK_GRAMMAR, allow_cloud=False)
    assert not any("banana" in it["target_en"].lower() for it in baseline["items"]), \
        "前提不成立：香蕉本來就會被選中，這條測試就驗不到排程了"

    # 讓「香蕉」答錯 → 立刻到期。句子只放 banana 一個詞庫詞，
    # 否則同句的 eat（吃）也會一起到期，第一題就不一定是香蕉。
    srs.record_interactions(
        [{"seq": 1, "ts": _at(20), "student_text": "banana",
          "ai_response_text": "好", "asr_confidence": 0.2}],
        student_id="alice",
    )

    out = homework.generate_homework({"student_id": "alice"}, _WEAK_GRAMMAR, allow_cloud=False)
    assert "banana" in out["items"][0]["target_en"].lower(), \
        f"到期的『香蕉』沒有排在第一題：{[i['target_en'] for i in out['items']]}"


def test_homework_falls_back_to_dimension_picks_when_nothing_is_due(tmp_db):
    """沒有到期詞時，題目全部來自該弱項維度的分類，而且仍然合法。

    （不同學生會拿到不同的一批——那是取題輪轉，見
    test_homework_differs_between_students_on_the_same_day。這裡驗的是
    「排程沒東西可給時，出題不會開天窗」。）
    """
    from server import scaffold
    from server.agents import homework

    out = homework.generate_homework({"student_id": "nobody"}, _WEAK_GRAMMAR,
                                     allow_cloud=False)
    assert 3 <= len(out["items"]) <= 5
    bank = {v["sent"] for v in scaffold.VOCAB.values()}
    for item in out["items"]:
        assert item["target_en"] in bank, f"題目不在詞庫內：{item['target_en']}"


def test_homework_without_student_id_still_works(tmp_db):
    """沒有 student_id（第一次上線、DB 剛重置）時照原邏輯出題，不得爆。"""
    from server.agents import homework

    out = homework.generate_homework(
        {}, {"scores": {"pronunciation": 80, "fluency": 78,
                        "vocabulary": 75, "grammar": 40}},
        allow_cloud=False,
    )
    assert 3 <= len(out["items"]) <= 5


def test_homework_survives_srs_failure(tmp_db, monkeypatch):
    """排程層爆炸不得讓作業出不來——它是加值，不是主幹。"""
    from server.agents import homework

    def _boom(*a, **kw):
        raise RuntimeError("排程爆了")

    monkeypatch.setattr(srs, "due_words", _boom)
    out = homework.generate_homework(
        {"student_id": "alice"},
        {"scores": {"pronunciation": 80, "fluency": 78,
                    "vocabulary": 75, "grammar": 40}},
        allow_cloud=False,
    )
    assert 3 <= len(out["items"]) <= 5


# ---------------------------------------------------------------------------
# 出題輪轉：詞庫擴充後，後面的詞也要出得來
# ---------------------------------------------------------------------------

def test_homework_rotates_across_days(tmp_db):
    """換一天要換一批詞。沒有輪轉的話，136 個詞永遠只出得到前 5 個。"""
    from server.agents import homework

    seen = []
    for day in ("2026-07-20", "2026-07-21", "2026-07-22"):
        out = homework.generate_homework(
            {"student_id": "alice"}, dict(_WEAK_GRAMMAR, date=day), allow_cloud=False
        )
        seen.append(tuple(i["target_en"] for i in out["items"]))

    assert len(set(seen)) == 3, f"三天拿到同一份作業：{seen}"


def test_homework_is_reproducible_within_a_day(tmp_db):
    """同一天同一個孩子拿到同一份作業——現場要能重現，不能每次刷新都變。"""
    from server.agents import homework

    args = ({"student_id": "alice"}, dict(_WEAK_GRAMMAR, date="2026-07-20"))
    a = homework.generate_homework(*args, allow_cloud=False)
    b = homework.generate_homework(*args, allow_cloud=False)
    assert a["items"] == b["items"]


def test_homework_differs_between_students_on_the_same_day(tmp_db):
    from server.agents import homework

    day = dict(_WEAK_GRAMMAR, date="2026-07-20")
    a = homework.generate_homework({"student_id": "alice"}, day, allow_cloud=False)
    b = homework.generate_homework({"student_id": "bob"}, day, allow_cloud=False)
    assert a["items"] != b["items"]


def test_rotation_eventually_reaches_the_whole_word_bank(tmp_db):
    """跑滿一年份的輪轉，被出過的詞要涵蓋詞庫的多數。

    這條是擴充詞庫的**驗收**：詞加了但出不來，等於沒加。
    """
    from server import scaffold
    from server.agents import homework

    used = set()
    for day in range(1, 200):
        items = homework._build_rule_items("grammar", rotation=day)
        used.update(i["target_en"] for i in items)

    grammar_bank = {
        v["sent"] for v in scaffold.VOCAB.values()
        if v["cat"] in homework._DIM_TO_CATS["grammar"]
    }
    covered = len(used & grammar_bank) / len(grammar_bank)
    assert covered >= 0.8, f"輪轉只碰到文法題庫的 {covered:.0%}"
