# -*- coding: utf-8 -*-
"""srs.py — 間隔重複（spaced repetition）與知識追蹤。

為什麼是間隔重複而不是推薦演算法
--------------------------------
「分析聊天紀錄推薦課程」聽起來像推薦系統，但協同過濾在這個場景會失效：
只有一個孩子（冷啟動）、資料稀疏、而且問錯了問題——要答的不是「相似的
人喜歡什麼」，是「**這個孩子哪個字還沒學會**」。

間隔重複是語言學習領域數十年的實證做法，而且對本專案有一個決定性的好處：
**演算法是純函式、可離線跑**。斷網橋段完全不受影響，這點推薦系統做不到。

演算法：SM-2 的二元評分變體
---------------------------
原版 SM-2 用 0–5 的自評分數，但這裡沒有自評——只有「這回合這個詞用對了
沒有」。所以簡化成二元：

- 答對：複習次數 +1，間隔依 1 → 6 → interval × ease 遞增，ease 微幅上調
- 答錯：複習次數歸零，間隔設 0（下次出題就會挑到），ease 下調（下限 1.3）

``schedule()`` 是純函式，時間由呼叫端傳入——不藏時鐘，測試才驗得動。
"""

from __future__ import annotations

import datetime
import logging

_log = logging.getLogger(__name__)

_TAIPEI_TZ = datetime.timezone(datetime.timedelta(hours=8))

# SM-2 的 ease factor（難度係數）：越大代表這個詞對這個孩子越容易，間隔拉越長
DEFAULT_EASE = 2.5
MIN_EASE = 1.3
MAX_EASE = 3.0

# 前兩次成功複習的固定間隔（天）。第三次起改為 interval × ease。
_FIRST_INTERVAL = 1
_SECOND_INTERVAL = 6

# 間隔上限（天）。國小英語一學期約 20 週，超過一學期的間隔沒有意義。
_MAX_INTERVAL = 120


def _now() -> datetime.datetime:
    return datetime.datetime.now(_TAIPEI_TZ)


def _parse_dt(value) -> datetime.datetime | None:
    """寬容解析 ISO8601；解析不出來回 None（呼叫端自行決定退路）。"""
    if isinstance(value, datetime.datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return dt.replace(tzinfo=_TAIPEI_TZ) if dt.tzinfo is None else dt


def initial_state() -> dict:
    """一個詞第一次被看到時的狀態。"""
    return {"ease": DEFAULT_EASE, "interval_days": 0, "reps": 0, "lapses": 0}


def schedule(state: dict | None, correct: bool, *, now=None) -> dict:
    """依 SM-2（二元評分）算出下一次複習狀態。純函式，不碰 DB、不讀時鐘。

    回傳 ``{ease, interval_days, reps, lapses, due_at, last_seen}``。
    ``now`` 省略時才取系統時間——正式呼叫端都應該傳入互動當下的時間戳，
    否則批次補算歷史資料會全部擠到「現在」。
    """
    base = state if isinstance(state, dict) else {}
    try:
        ease = float(base.get("ease", DEFAULT_EASE))
    except (TypeError, ValueError):
        ease = DEFAULT_EASE
    try:
        interval = int(base.get("interval_days", 0))
    except (TypeError, ValueError):
        interval = 0
    try:
        reps = int(base.get("reps", 0))
    except (TypeError, ValueError):
        reps = 0
    try:
        lapses = int(base.get("lapses", 0))
    except (TypeError, ValueError):
        lapses = 0

    moment = _parse_dt(now) or _now()

    if correct:
        reps += 1
        if reps == 1:
            interval = _FIRST_INTERVAL
        elif reps == 2:
            interval = _SECOND_INTERVAL
        else:
            interval = max(1, round(max(interval, 1) * ease))
        interval = min(interval, _MAX_INTERVAL)
        ease = min(MAX_EASE, ease + 0.1)
    else:
        reps = 0
        lapses += 1
        # 間隔 0 = 立刻到期：下一次出題就該挑到它。答錯的詞隔天才複習，
        # 對只練幾分鐘的孩子來說等於這一輪白錯了。
        interval = 0
        ease = max(MIN_EASE, ease - 0.2)

    return {
        "ease": round(ease, 3),
        "interval_days": interval,
        "reps": reps,
        "lapses": lapses,
        "last_seen": moment.isoformat(timespec="seconds"),
        "due_at": (moment + datetime.timedelta(days=interval)).isoformat(timespec="seconds"),
    }


def is_due(state: dict, *, now=None) -> bool:
    """這個詞是否已到複習時間。due_at 讀不出來時保守視為到期（寧可多練）。"""
    due = _parse_dt((state or {}).get("due_at"))
    if due is None:
        return True
    return due <= (_parse_dt(now) or _now())


# ---------------------------------------------------------------------------
# 從互動紀錄產生評分（重用 profile 的詞庫命中與錯點偵測，不另立一套）
# ---------------------------------------------------------------------------

def grade_interaction(interaction: dict) -> dict[str, bool]:
    """一筆互動裡命中的詞庫詞 → 這次用得對不對。

    判準與 ``profile.build_profile`` 的 vocab 掌握度完全一致（ASR 信心足夠
    且 AI 沒有糾正冠詞），刻意重用同一組判斷：兩處若各寫一套，兩邊對
    「答對」的定義就會慢慢漂開。

    key 用 ``scaffold.VOCAB`` 的中文鍵，讓出題端可以直接查表。
    """
    from server import diagnose, profile

    if not isinstance(interaction, dict):
        return {}
    text = interaction.get("student_text") or ""
    reply = interaction.get("ai_response_text") or ""
    try:
        conf = float(interaction.get("asr_confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0

    corrected = diagnose._has_article_correction(reply)
    good = conf >= 0.8 and not corrected

    hits: dict[str, bool] = {}
    en_info = profile._en_info()
    for token in set(profile._en_tokens(text)):
        info = en_info.get(token)
        if info:
            hits[info["zh"]] = good
    return hits


def record_interactions(interactions, student_id: str) -> int:
    """把互動紀錄折算成複習排程並寫入 store；回傳更新的詞數。

    可重複執行：每個詞記著自己處理過的最大 seq，同一筆互動再跑一次不會
    重複計分。背景刷新每次都讀最近 10 筆，沒有這道保護就會反覆加分。

    任何失敗都只記 log 不外拋——排程是加值功能，不得拖垮教學迴圈。
    """
    from server import store

    if not interactions or not student_id:
        return 0
    updated = 0
    try:
        ordered = sorted(
            (it for it in interactions if isinstance(it, dict)),
            key=lambda it: int(it.get("seq") or 0),
        )
    except (TypeError, ValueError):
        ordered = [it for it in interactions if isinstance(it, dict)]

    for inter in ordered:
        try:
            seq = int(inter.get("seq") or 0)
        except (TypeError, ValueError):
            seq = 0
        ts = inter.get("ts")
        for word, correct in grade_interaction(inter).items():
            try:
                prev = store.get_word_review(student_id, word)
                if prev and int(prev.get("last_seq") or 0) >= seq > 0:
                    continue  # 這筆互動已經算過了
                state = schedule(prev, correct, now=ts)
                store.upsert_word_review(student_id, word, state, last_seq=seq)
                updated += 1
            except Exception:
                _log.warning("複習排程寫入失敗：word=%r", word, exc_info=True)
    return updated


def due_words(student_id: str, *, limit: int = 20) -> list[str]:
    """該學生已到期的複習詞（最久沒複習的排前面）；讀不到就回空清單。"""
    from server import store

    try:
        rows = store.list_due_word_reviews(student_id, limit=limit)
    except Exception:
        _log.warning("讀取到期複習詞失敗，本次不做排程加權", exc_info=True)
        return []
    return [r["word"] for r in rows if r.get("word")]
