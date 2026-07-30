# -*- coding: utf-8 -*-
"""spelling.py — 背單字訓練的判定核心（純函式）。

為什麼判定要獨立成一個檔案
--------------------------
因為這裡的規則是**被實測選出來的，不是想出來的**。2026-07-31 在開發機上
把五種字母寫法丟給本地 TTS 合成、再用 SenseVoice 讀回：

    TTS 輸入            ASR 讀回
    "A, P, P, L, E,"    "A, P, P, L, E."   ← 唯一完美來回
    "A. P. P. L. E."    "A T, T, L, E."    句點被當成縮寫
    "A P P L E"         "Ppili."           空白不夠，黏成一個字
    "A-P-P-L-E"         "AP."              連字號吞掉後半
    "apple"（整字）      "Bbble."           整字反而聽錯

所以 ``letters_for_tts`` 的輸出格式是**規格，不是風格**。要改它之前
先重跑 ``edge/probes/probe_spell_tts.py``，別憑感覺換成看起來比較漂亮的
``A-P-P-L-E``——那個實測是壞的。

同一批實測帶出兩個結論，決定了下面的門檻怎麼訂：

1. **ASR 對字母序列比對整個單字準得多。** 反直覺但站得住：字母離散、
   有停頓、音節短，正是 ASR 擅長的。→ 判定主力放在拼音那一步。
2. **SenseVoice 的 asr_confidence 一律回 1.0**（四個測試詞皆是），
   `ASR_CONF_THRESHOLD` 那道閘門在這裡幫不上忙 → 必須自己做模糊比對。

除 ``record_word_result`` 之外全部是純函式：不碰 DB、不讀時鐘、不載模型，
可以完全離線單元測試。
"""

from __future__ import annotations

import logging
import re

_log = logging.getLogger(__name__)

# 命中率門檻。寬鬆鼓勵制：唸出大部分就算過。
# 嚴格比對等於讓孩子替 ASR 的誤判受罰（apple → "Bbble."），而卡在同一個
# 詞出不去是現場最糟的失敗模式，比答錯還糟。
PASS_THRESHOLD = 0.6

# 同一步最多重試幾次；第 MAX_RETRIES+1 次一律往下走。
MAX_RETRIES = 2

# ASR 文字長度上限，防爆走輸入把 DP 撐爆
_MAX_TEXT = 200


def letters_for_tts(word: str) -> str:
    """``"apple"`` → ``"A, P, P, L, E,"``：本地 TTS 唯一可靠的字母念法。

    大寫、``", "`` 分隔、**結尾保留一個逗號**（讓最後一個字母也有停頓，
    否則末字母會被黏進句尾語調）。見模組 docstring 的實測表。
    """
    chars = [c.upper() for c in str(word or "") if c.isalpha()]
    return ", ".join(chars) + "," if chars else ""


def ref_letters(word: str) -> list[str]:
    """``"apple"`` → ``["A","P","P","L","E"]``：比對用的參考序列。"""
    return [c.upper() for c in str(word or "") if c.isalpha()]


def heard_letters(asr_text) -> list[str]:
    """ASR 文字 → 聽到的字母序列。

    孩子拼 A-P-P-L-E 時 ASR 有兩種輸出形狀，兩種都要吃：

    - ``"A, P, P, L, E."`` → 單字母 token（正常情況）
    - ``"Apple."``         → 被黏成一個字（ASR 自作聰明）

    **黏成一個字時無法分辨孩子是在拼還是在唸整個單字**——兩者的 ASR 文字
    一模一樣。分不出來就不要假裝分得出來，一律當作拼對了。這是已知邊界，
    寫在 docs/GAMES.md，不要試圖在這裡「修好」它。
    """
    tokens = re.findall(r"[A-Za-z]+", str(asr_text or "")[:_MAX_TEXT])
    singles = [t.upper() for t in tokens if len(t) == 1]
    if len(singles) >= 2:
        return singles
    return list("".join(tokens).upper())


def _edit_distance(a: list, b: list) -> int:
    """Levenshtein 距離。與 pronunciation._align_score 同一套 DP，比字母不比音素。"""
    n, m = len(a), len(b)
    if n == 0:
        return m
    if m == 0:
        return n
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
    return dp[n][m]


def letter_hit_rate(ref: list[str], heard: list[str]) -> float:
    """參考字母序列被唸出來的比率 0–1。``ref`` 為空回 0（沒有參考就沒有分數）。"""
    ref = list(ref or [])
    if not ref:
        return 0.0
    hits = max(0, len(ref) - _edit_distance(ref, list(heard or [])))
    return round(hits / len(ref), 3)


def word_hit_rate(ref_word: str, asr_text) -> float:
    """孩子有沒有把某個英文單字唸出來，0–1。

    做法：把 ASR 文字切成英文 token，每個 token 與 ``ref_word`` 算相似度，
    **取最高的那一個**。例句那一步的參考一樣是目標單字而不是整句——
    整句逐字比對對國小生太嚴，只要例句裡把目標詞唸出來就算數。
    """
    ref = [c for c in str(ref_word or "").lower() if c.isalpha()]
    if not ref:
        return 0.0
    best = 0.0
    for tok in re.findall(r"[a-z]+", str(asr_text or "").lower()[:_MAX_TEXT]):
        sim = 1.0 - _edit_distance(ref, list(tok)) / max(len(ref), len(tok))
        best = max(best, sim)
    return round(max(0.0, best), 3)


# ---------------------------------------------------------------------------
# 唯一有副作用的函式：把練習結果折算成間隔重複排程
# ---------------------------------------------------------------------------

def record_word_result(student_id, word_zh: str, correct: bool, *, now=None) -> bool:
    """把一個詞的練習結果寫入 ``store.word_reviews``；回傳有沒有寫成功。

    排程演算法完全沿用 ``srs.schedule``（SM-2 二元變體），**不另立一套**：
    兩處各寫一套，兩邊對「答對」的定義就會慢慢漂開。

    任何失敗只記 log 不外拋——記錄是加值功能，不得拖垮教學迴圈
    （與 ``games._due_first`` 讀取端同一個原則）。

    ``last_seq`` 沿用既有值而非歸零：``srs.record_interactions`` 靠它判斷
    「這筆互動算過了沒」，洗掉就會讓背景刷新把舊互動重複計分。
    """
    if not student_id or not word_zh:
        return False
    try:
        from server import srs, store

        prev = store.get_word_review(student_id, word_zh)
        state = srs.schedule(prev, bool(correct), now=now)
        last_seq = int((prev or {}).get("last_seq") or 0)
        store.upsert_word_review(student_id, word_zh, state, last_seq=last_seq)
        return True
    except Exception:
        _log.warning("背單字結果寫入失敗：word=%r", word_zh, exc_info=True)
        return False
