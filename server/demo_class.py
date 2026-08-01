# -*- coding: utf-8 -*-
"""demo_class.py — 決賽 demo 用的「全班總覽」模擬資料層。

為什麼是獨立模組而不是擴充 store：
    核心 schema 是單學生設計——``interactions`` 沒有 student_id，``diagnoses``
    拿 date 當 PRIMARY KEY（同一天多個學生會互相覆蓋）。要真的支援班級，
    得把主鍵改成 (student_id, date) 並連動所有讀取端與前端，那是決賽前夜
    不該動的東西。這個模組只服務「概念展示」：讓教師端有一張看得到全班的
    畫面，證明「老師指定教材、系統回報誰卡住」這個概念成立。

    正式版的做法寫在 ROADMAP：diagnoses 改複合主鍵、interactions 補
    student_id、班級與學生做成一對多。**不要把這個模組當成那條路的起點。**

契約：
    class_overview() -> dict     # 全班總覽，不觸網；除阿明那一列外皆確定性、不需 DB

九位虛構同學的資料是刻意寫死的：demo 最怕重跑一次數字就變，而且沒有做多學生
高併發，沒必要做真實實作。姓名用化名，不對應真實個案（隱私：痛點研究的個案
不得原樣搬上台）。

**阿明是例外**：他是決賽現場實際操作的帳號（``config.STUDENT_ID``），這一列
不能寫死——他的分數、開口量、字彙量、弱項全部即時算自真實 DB，見
``_aming_live_row()``。也因此本模組**不再是**「不需 DB」：呼叫
``class_overview()`` 會讀一次真實學生資料。
"""
from __future__ import annotations

import datetime

# 單元定義（編號、週次、句型、單字表）是**教材事實**，住在 server/seed_units.py，
# 與 lesson.py 選帶讀句時看的是同一份——單元一旦搬到別的一課，玩偶帶讀的句子
# 與教師端「本週教材」會一起跟著動，不會各說各話。這裡只 re-export，讓本模組
# 既有的讀取端維持原樣。
#
# 這個模組真正編造的是底下那十個學生。整個 demo 最有衝擊力的設定就在他們身上：
# **老師上到第 4 課，有孩子還停在第 1 課。** 進度落差用「週」為單位，比用分數
# 更痛也更真實——那正是痛點研究說的「學習成就雙峰化」。
from server.seed_units import CURRENT_UNIT_NO, UNITS, unit as _unit  # noqa: F401


# ``path``＝同一份教材下，系統為這個孩子選的**練習路徑**。
#   extend  超前 → 給下一單元，不讓他乾等
#   drill   差一點 → 同一批字多練幾次鞏固
#   echo    單字會、成句卡住 → 跟讀而不是重背
#   oneone  卡住 → 降到單字逐句帶，並請老師親自介入
_PATH_LABEL = {
    "extend": ("延伸下一課", "已達標，給更難的"),
    "drill":  ("鞏固練習", "差一點，同批字再練"),
    "echo":   ("跟讀模式", "單字會、成句卡住"),
    "oneone": ("需老師介入", "降到單字逐句帶"),
}

# 阿明的 student_id 刻意用 config.STUDENT_ID 的真實值（STUDENT-AMING-004），
# 不是 demo-0xx：教師端下半部的「單一學生深入分析」查的就是他，兩邊指同一個
# 孩子才說得通。
# unit＝這孩子**現在實際在練的單元**（不是老師教到哪）。
# levels＝四個單元代表句型的掌握度 0-3，順序同 UNITS。
_STUDENTS = [
    {"seat": 3,  "name": "宥廷", "student_id": "demo-001", "unit": 6, "progress": 7,
     "scores": {"pronunciation": 84, "fluency": 82, "vocabulary": 87, "grammar": 83},
     "weakness": "無明顯弱項", "status": "steady", "path": "extend",
     "note": "四個單元全數跟上，已開始練 Unit 6 的現在進行式。",
     "spoken_week": 61, "spoken_prev": 57,
     "vocab": {"mastered": 63, "learning": 6, "new_week": 8}, "levels": [3, 3, 3, 2]},

    {"seat": 12, "name": "羽彤", "student_id": "demo-002", "unit": 6, "progress": 5,
     "scores": {"pronunciation": 79, "fluency": 77, "vocabulary": 83, "grammar": 78},
     "weakness": "長句語尾音量下降", "status": "steady", "path": "extend",
     "note": "跟上本週進度，可帶入 Can we...? 的延伸問答。",
     "spoken_week": 54, "spoken_prev": 49,
     "vocab": {"mastered": 58, "learning": 8, "new_week": 7}, "levels": [3, 3, 2, 2]},

    {"seat": 5,  "name": "冠廷", "student_id": "demo-003", "unit": 5, "progress": 6,
     "scores": {"pronunciation": 70, "fluency": 68, "vocabulary": 72, "grammar": 66},
     "weakness": "It's 縮寫常漏 s", "status": "improving", "path": "drill",
     "note": "落後一週，時間句型再練幾次就能追上。",
     "spoken_week": 44, "spoken_prev": 33,
     "vocab": {"mastered": 41, "learning": 10, "new_week": 6}, "levels": [3, 3, 2, 0]},

    # 阿明是決賽現場實際操作的帳號，這一列**不寫死**——scores/weakness/note/
    # spoken_week/vocab/levels/progress 全部由 _aming_live_row() 即時算自真實
    # DB，這裡只留 class_overview() 拿來比對、替換用的座位錨點。任何欄位改到
    # 這裡都不會生效，改 _aming_live_row()。
    {"seat": 7,  "name": "阿明", "student_id": "STUDENT-AMING-004", "unit": 5, "progress": 0,
     "scores": {"pronunciation": 60, "fluency": 60, "vocabulary": 60, "grammar": 60},
     "weakness": "尚無資料", "status": "needs_attention", "path": "oneone",
     "note": "尚無資料", "spoken_week": 0, "spoken_prev": 0,
     "vocab": {"mastered": 0, "learning": 0, "new_week": 0}, "levels": [0, 0, 0, 0]},

    {"seat": 18, "name": "思妤", "student_id": "demo-005", "unit": 5, "progress": 3,
     "scores": {"pronunciation": 66, "fluency": 63, "vocabulary": 69, "grammar": 62},
     "weakness": "get up / thirty 尾音吞掉", "status": "improving", "path": "drill",
     "note": "落後一週，兩個尾音需要再帶。",
     "spoken_week": 37, "spoken_prev": 28,
     "vocab": {"mastered": 30, "learning": 12, "new_week": 5}, "levels": [3, 2, 1, 0]},

    {"seat": 25, "name": "小婷", "student_id": "demo-006", "unit": 4, "progress": 6,
     "scores": {"pronunciation": 61, "fluency": 58, "vocabulary": 70, "grammar": 63},
     "weakness": "流暢度：句間停頓過長", "status": "improving", "path": "echo",
     "note": "單字認得，連成句子時卡住——適合跟讀而非背誦。",
     "spoken_week": 29, "spoken_prev": 24,
     "vocab": {"mastered": 22, "learning": 16, "new_week": 4}, "levels": [3, 2, 0, 0]},

    {"seat": 9,  "name": "柏睿", "student_id": "demo-007", "unit": 4, "progress": 4,
     "scores": {"pronunciation": 59, "fluency": 55, "vocabulary": 64, "grammar": 58},
     "weakness": "問句語調上揚不足", "status": "improving", "path": "echo",
     "note": "疑問句聽起來像直述句，跟讀時特別帶語調。",
     "spoken_week": 26, "spoken_prev": 21,
     "vocab": {"mastered": 20, "learning": 15, "new_week": 4}, "levels": [2, 2, 0, 0]},

    {"seat": 30, "name": "郁婕", "student_id": "demo-008", "unit": 4, "progress": 2,
     "scores": {"pronunciation": 57, "fluency": 54, "vocabulary": 60, "grammar": 55},
     "weakness": "bedroom 重音位置", "status": "improving", "path": "echo",
     "note": "願意開口但常自我修正到中斷，跟讀可降低壓力。",
     "spoken_week": 22, "spoken_prev": 15,
     "vocab": {"mastered": 17, "learning": 15, "new_week": 3}, "levels": [2, 1, 0, 0]},

    {"seat": 21, "name": "阿凱", "student_id": "demo-009", "unit": 3, "progress": 3,
     "scores": {"pronunciation": 54, "fluency": 48, "vocabulary": 52, "grammar": 50},
     "weakness": "母音 /æ/ 發音、句子中斷", "status": "needs_attention", "path": "oneone",
     "note": "全班上到 Unit 6，他還在 Unit 3。八個字只練了三個且多次中途停下。",
     "spoken_week": 13, "spoken_prev": 6,
     "vocab": {"mastered": 9, "learning": 14, "new_week": 2}, "levels": [1, 0, 0, 0]},

    {"seat": 14, "name": "承恩", "student_id": "demo-010", "unit": 3, "progress": 1,
     "scores": {"pronunciation": 49, "fluency": 45, "vocabulary": 47, "grammar": 46},
     "weakness": "多數時間不開口", "status": "needs_attention", "path": "oneone",
     "note": "四週下來仍停在第一課。本週僅開口 8 次且多為單字，需先建立信心。",
     "spoken_week": 8, "spoken_prev": 3,
     "vocab": {"mastered": 5, "learning": 11, "new_week": 1}, "levels": [0, 0, 0, 0]},
]

# 落後群（needs_attention）兩週前的四維平均，用來算「追上來了多少」。
_LAGGING_AVG_TWO_WEEKS_AGO = 38.0

PATTERN_LEVEL_LABEL = ["還沒開始", "帶著才會", "自己說得出", "能換字自己造句"]


def _avg(scores: dict) -> float:
    return round(sum(scores.values()) / len(scores), 1)


def _zh_keys_for_words(en_words: list[str]) -> set[str]:
    """英文教材詞（seed_units 的 words）→ scaffold.VOCAB 中文鍵。

    srs 用中文鍵記錄複習狀態（見 srs.grade_interaction），教材詞表存的是
    英文，兩邊對得上才能算「這個孩子這個單元練了幾個字」。
    """
    from server.scaffold import VOCAB
    en_set = set(en_words)
    return {zh for zh, info in VOCAB.items() if info.get("en") in en_set}


def _aming_live_row() -> dict:
    """阿明那一列的即時版本——取代 _STUDENTS 裡的座位錨點。

    任何一步讀取失敗都退回保守預設（0 分/0 次/needs_attention），不讓全班
    總覽被一個人的資料壞掉；但**不靜默吞掉例外去湊假數字**，失敗時的樣子
    就是「這孩子還沒有資料」，跟真實狀況一致。
    """
    from server import config, lesson, store
    from server.agents import report as report_agent

    sid = config.STUDENT_ID
    try:
        diagnoses = store.list_diagnoses(sid) or []
    except Exception:
        diagnoses = []
    try:
        interactions = store.list_interactions(limit=2000, student_id=sid) or []
    except Exception:
        interactions = []
    try:
        profile = store.get_profile(sid) or {}
    except Exception:
        profile = {}
    try:
        reviews = store.list_word_reviews(sid) or []
    except Exception:
        reviews = []

    dim_scores = report_agent._extract_dim_scores(diagnoses)
    latest = report_agent._latest_scores(dim_scores)
    weakest = report_agent._weakest_dim(latest)
    trend = report_agent._trend(dim_scores.get(weakest, []))
    average = round(sum(latest.values()) / len(latest), 1) if latest else 0.0

    # status/path：demo_class 自創的分類，其他真人路徑沒算過，這裡用平均分數
    # ＋趨勢寫一條清楚的判斷式（不是編出來的數字，門檻可依需求調整）。
    if not diagnoses:
        status, path = "needs_attention", "oneone"
    elif average >= 80:
        status, path = "steady", "extend"
    elif average < 55:
        status, path = "needs_attention", "oneone" if trend != "improving" else "echo"
    elif trend == "improving":
        status, path = "improving", "drill"
    elif trend == "declining":
        status, path = "needs_attention", "echo"
    else:
        status, path = "steady", "drill"

    latest_diag = diagnoses[-1] if diagnoses else {}
    weaknesses = latest_diag.get("weaknesses") or []
    weak_zh = report_agent._DIM_ZH.get(weakest, weakest)
    note = weaknesses[0] if weaknesses else f"{weak_zh}尚在累積練習資料，還看不出明確弱項"

    lp = lesson.build_lesson(diagnoses, profile)
    unit_no = lp.unit_no or CURRENT_UNIT_NO
    u = _unit(unit_no)

    mastered_zh = {r["word"] for r in reviews if r.get("reps", 0) >= 3}
    learning_zh = {r["word"] for r in reviews if 0 < r.get("reps", 0) < 3}
    week_ago = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
    new_week_zh = {r["word"] for r in reviews
                   if r.get("reps", 0) <= 1 and str(r.get("last_seen") or "")[:10] >= week_ago}

    unit_zh_keys = _zh_keys_for_words(u["words"])
    progress = len(unit_zh_keys & (mastered_zh | learning_zh))

    def _spoken_count(days_from: int, days_to: int) -> int:
        lo = (datetime.date.today() - datetime.timedelta(days=days_to)).isoformat()
        hi = (datetime.date.today() - datetime.timedelta(days=days_from)).isoformat()
        return sum(
            1 for it in interactions
            if (it.get("student_text") or "").strip()
            and lo <= str(it.get("ts") or "")[:10] < hi
        )

    spoken_week = _spoken_count(0, 7)
    spoken_prev = _spoken_count(7, 14)

    # 已練過的單元用平均分數估掌握度；還沒教到的單元一律 0——
    # 跟其他九位同學的 levels 語意一致（0-3，見 PATTERN_LEVEL_LABEL）。
    levels = []
    for u2 in UNITS:
        if u2["no"] > unit_no:
            levels.append(0)
        elif average >= 80:
            levels.append(3)
        elif average >= 65:
            levels.append(2)
        elif average >= 50:
            levels.append(1)
        else:
            levels.append(0)

    return {
        "seat": 7, "name": "阿明", "student_id": sid,
        "unit": unit_no, "progress": progress,
        "scores": {d: round(v) for d, v in latest.items()} if latest else
                  {d: 0 for d in ("pronunciation", "fluency", "vocabulary", "grammar")},
        "weakness": note, "status": status, "path": path, "note": note,
        "spoken_week": spoken_week, "spoken_prev": spoken_prev,
        "vocab": {"mastered": len(mastered_zh), "learning": len(learning_zh),
                  "new_week": len(new_week_zh)},
        "levels": levels,
    }


def _raw_students() -> list[dict]:
    """全班原始資料：阿明即時算自真實 DB，其餘九位是固定展示資料。"""
    from server import config
    return [_aming_live_row() if s["student_id"] == config.STUDENT_ID else s
            for s in _STUDENTS]


def class_overview() -> dict:
    """全班總覽。阿明那一列即時讀真實 DB，其餘九位固定——見模組說明。"""
    cur = _unit(CURRENT_UNIT_NO)
    students = []
    for s in _raw_students():
        u = _unit(s["unit"])
        total = len(u["words"])
        label, why = _PATH_LABEL.get(s.get("path", ""), ("\u2014", ""))
        spoken, prev = s.get("spoken_week", 0), s.get("spoken_prev", 0)
        students.append({
            **s,
            "unit_title": f"Unit {u['no']}",
            "unit_name": u["title"],
            "unit_zh": u["zh"],
            # 落後幾週＝老師教到的單元 減 他實際在練的單元。這個數字比分數更痛。
            "weeks_behind": CURRENT_UNIT_NO - s["unit"],
            "progress_total": total,
            "progress_pct": round(s["progress"] / total * 100),
            "average": _avg(s["scores"]),
            "path_label": label,
            "path_why": why,
            "spoken_delta_pct": (round((spoken - prev) / prev * 100) if prev else 0),
            "patterns": [{"pat": u2["pattern"], "unit": u2["no"], "level": lv}
                         for u2, lv in zip(UNITS, s.get("levels", []))],
            "words_done": u["words"][:s["progress"]],
            "words_todo": u["words"][s["progress"]:],
        })
    order = {"needs_attention": 0, "improving": 1, "steady": 2}
    students.sort(key=lambda x: (order.get(x["status"], 9), x["average"]))

    on_track = sum(1 for s in students if s["weeks_behind"] == 0)
    lagging = [s for s in students if s["status"] == "needs_attention"]
    lagging_now = (round(sum(s["average"] for s in lagging) / len(lagging), 1)
                   if lagging else 0.0)
    return {
        "unit": f"Unit {cur['no']}: {cur['title']}",
        "unit_zh": cur["zh"],
        "week_no": cur["week"],
        "topic": f"Unit {cur['no']} {cur['zh']}",
        "words": cur["words"],
        "patterns": [u["pattern"] for u in UNITS],
        "units": [{"no": u["no"], "week": u["week"], "title": u["title"],
                   "zh": u["zh"], "pattern": u["pattern"], "words": u["words"]}
                  for u in UNITS],
        "class_size": len(students),
        "on_track_count": on_track,
        "completed_count": on_track,
        "attention_count": len(lagging),
        "max_weeks_behind": max((s["weeks_behind"] for s in students), default=0),
        "class_average": round(sum(s["average"] for s in students) / len(students), 1),
        "spoken_week_total": sum(s["spoken_week"] for s in students),
        "spoken_prev_total": sum(s["spoken_prev"] for s in students),
        "lagging_avg_now": lagging_now,
        "lagging_avg_before": _LAGGING_AVG_TWO_WEEKS_AGO,
        "lagging_gain": round(lagging_now - _LAGGING_AVG_TWO_WEEKS_AGO, 1),
        "pattern_level_label": PATTERN_LEVEL_LABEL,
        "students": students,
        "source": "demo",
    }
