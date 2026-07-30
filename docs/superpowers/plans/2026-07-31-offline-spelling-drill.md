# 離線背單字訓練（spell_along）實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `server/games.py` 加第四個遊戲 `spell_along`——玩偶念單字、逐字母拼、說例句，孩子跟著念，裝置在**完全斷網**下判定命中率並把學習狀況寫進 SRS。

**Architecture:** 判定核心是新的純函式模組 `server/spelling.py`；遊戲本體走 `games.py` 既有的 `start/prompt/judge` 外掛契約，因此 `pipeline.py`、`app.py`、WS 協定、DB schema 一行都不動。感知＝本地 SenseVoice ASR 文字的模糊比對，決策＝`srs.py` 挑到期詞＋三步狀態機，行動＝本地 sherpa-onnx TTS 念三步腳本。

**Tech Stack:** Python 3.12、標準函式庫（`re` / `logging` / `dataclasses`）、既有 `server/{scaffold,srs,store,games}.py`、pytest。**零新第三方依賴、零新模型、零新詞庫。**

設計文件：`docs/superpowers/specs/2026-07-31-offline-spelling-drill-design.md`
實測腳本：`edge/probes/probe_spell_tts.py`

## Global Constraints

- **字母念法字串是規格不是風格。** `letters_for_tts("apple")` 必須逐字產出 `"A, P, P, L, E,"`（大寫、`", "` 分隔、**結尾有一個逗號**）。2026-07-31 開發機實測：只有這個寫法能被本地 TTS 正確念成字母並被 SenseVoice 完整讀回；`"A. P. P. L. E."`→`"A T, T, L, E."`、`"A P P L E"`→`"Ppili."`、`"A-P-P-L-E"`→`"AP."` 全部壞掉。
- 判定門檻 `PASS_THRESHOLD = 0.6`、重試上限 `MAX_RETRIES = 2`（同一步的上限，每進入新的一步歸零）。
- 一局 `SPELL_TARGET_COUNT = 3` 個詞，每個詞三步 `("say_word", "spell", "sentence")`。
- 所有重依賴一律 lazy import + try/except，import 期不得炸掉（`CONTRACTS.md` 第 20 行）。
- `GameState` 是 `frozen=True` dataclass：每回合 `dataclasses.replace` 產生新的，**不原地改**。
- 新增的 `GameState` 欄位一律帶預設值，既有三個遊戲的行為必須逐字不變。
- 判定路徑**一次都不准碰雲端**。`tests/test_games_wiring.py::test_game_turn_never_calls_the_cloud` 是這條的機制保證。
- 註解與 docstring 用繁體中文（台灣用語），程式識別字用英文——與 `server/` 既有風格一致。
- 測試指令一律從 repo 根目錄執行：`.venv/bin/python -m pytest tests/... -q`

## File Structure

| 檔案 | 責任 | 動作 |
|---|---|---|
| `server/spelling.py` | 判定核心：字母字串產生、ASR 文字模糊比對、SRS 寫入 | 新增 |
| `server/games.py` | 遊戲本體：三步狀態機、選詞、註冊進遊戲目錄 | 修改 |
| `tests/test_spelling.py` | `spelling.py` 純函式單元測試（免模型、免 DB） | 新增 |
| `tests/test_games_spell.py` | 一局完整流程、重試上限、斷網一致性 | 新增 |
| `docs/GAMES.md` | 補第四個遊戲與已知邊界 | 修改 |

---

### Task 1: `server/spelling.py` 純函式核心

**Files:**
- Create: `server/spelling.py`
- Test: `tests/test_spelling.py`

**Interfaces:**
- Consumes: `server/scaffold.py` 的 `split_tts_segments`（僅測試用）
- Produces（Task 4、5 會直接呼叫這些名字，簽章必須逐字一致）:
  - `PASS_THRESHOLD: float = 0.6`
  - `MAX_RETRIES: int = 2`
  - `letters_for_tts(word: str) -> str`
  - `ref_letters(word: str) -> list[str]`
  - `heard_letters(asr_text) -> list[str]`
  - `letter_hit_rate(ref: list[str], heard: list[str]) -> float`
  - `word_hit_rate(ref_word: str, asr_text) -> float`

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_spelling.py`：

```python
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
```

- [ ] **Step 2: 跑測試確認它失敗**

Run: `.venv/bin/python -m pytest tests/test_spelling.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.spelling'`

- [ ] **Step 3: 寫最小實作**

建立 `server/spelling.py`：

```python
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
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_spelling.py -q`
Expected: PASS（20 條）

- [ ] **Step 5: 提交**

```bash
git add server/spelling.py tests/test_spelling.py
git commit -m "feat(spelling): 背單字判定核心 — 字母格式是實測選出來的，測試釘住它

五種 TTS 字母寫法只有 \"A, P, P, L, E,\" 能被 SenseVoice 完整讀回，
其餘四種（句點/空白/連字號/整字）實測全壞。這個格式因此是規格不是風格，
test_letter_format_is_the_one_that_survived_the_spike 直接釘住它。

門檻 0.6 也是被實測逼出來的：ASR 會把 apple 聽成 Bbble，嚴格比對等於
讓孩子替 ASR 的問題受罰。"
```

---

### Task 2: SRS 寫入 `record_word_result`

**Files:**
- Modify: `server/spelling.py`（在檔案末尾追加）
- Test: `tests/test_spelling.py`（追加一個新 section）

**Interfaces:**
- Consumes: `server/srs.py::schedule(state, correct, *, now=None) -> dict`、`server/store.py::get_word_review(student_id, word) -> dict | None`、`server/store.py::upsert_word_review(student_id, word, state, *, last_seq=0) -> None`
- Produces（Task 5 會呼叫）: `record_word_result(student_id, word_zh: str, correct: bool, *, now=None) -> bool`

- [ ] **Step 1: 寫失敗的測試**

在 `tests/test_spelling.py` 末尾追加：

```python
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
```

- [ ] **Step 2: 跑測試確認它失敗**

Run: `.venv/bin/python -m pytest tests/test_spelling.py -q -k record or due or preserves`
Expected: FAIL — `AttributeError: module 'server.spelling' has no attribute 'record_word_result'`

- [ ] **Step 3: 寫最小實作**

在 `server/spelling.py` 末尾追加：

```python
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
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_spelling.py -q`
Expected: PASS（25 條）

- [ ] **Step 5: 提交**

```bash
git add server/spelling.py tests/test_spelling.py
git commit -m "feat(spelling): 練習結果寫進 SRS — 答錯的詞下一局第一個練

排程沿用 srs.schedule 不另寫一套，否則兩邊對「答對」的定義會慢慢漂開。
last_seq 刻意沿用舊值：srs.record_interactions 靠它去重，洗成 0 會讓
背景刷新把舊互動重複計分。"
```

---

### Task 3: `games.py` 選詞重構與新狀態欄位

**Files:**
- Modify: `server/games.py:65-79`（`GameState` 加兩個欄位）、`server/games.py:149-167`（`_due_first` 抽出共用）
- Test: `tests/test_games_spell.py`（新建，先只放這一段）

**Interfaces:**
- Consumes: Task 1、2 的 `server/spelling.py`
- Produces（Task 4 會用）:
  - `GameState.retries: int = 0`、`GameState.student_id: str = ""`
  - `_due_words_from(pool, student_id, limit: int) -> tuple`

**背景：** 既有的 `_due_first(topic, student_id, limit)` 以**分類**為單位取詞（內部呼叫 `_words_in_cat(topic)`），但背單字要跨分類練「這孩子到期的詞」。把取詞邏輯抽成吃 `pool` 的版本，`_due_first` 變成它的薄包裝——既有三個遊戲的行為必須逐字不變。

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_games_spell.py`：

```python
# -*- coding: utf-8 -*-
"""test_games_spell.py — 遊戲 D「背單字」（Spell Along）。

一個詞三步：念單字 → 逐字母拼 → 例句。判定主力是拼音那一步
（2026-07-31 實測：ASR 對字母序列比對整個單字準得多）。

與另外三個遊戲共用的原則（見 server/games.py）：判定是規則式純函式、
斷網與連網逐字相同、狀態不可變。
"""

from __future__ import annotations

import pytest

from server import games, scaffold, spelling


# ---------------------------------------------------------------------------
# 選詞：到期優先，跨分類
# ---------------------------------------------------------------------------

def test_state_has_the_two_new_fields_with_safe_defaults():
    """新欄位一律帶預設值，既有三個遊戲不受影響。"""
    st = games.GameState(game="i_spy")
    assert st.retries == 0
    assert st.student_id == ""


def test_due_words_from_falls_back_to_pool_order_without_a_student():
    pool = ["蘋果", "香蕉", "狗"]
    assert games._due_words_from(pool, None, 2) == ("蘋果", "香蕉")


def test_due_words_from_puts_due_words_first(tmp_db):
    """上次拼錯的詞排到最前面——這是間隔重複在教學迴圈裡的出口。"""
    spelling.record_word_result("STU-SPELL", "狗", False)  # 錯 → 立刻到期
    pool = ["蘋果", "香蕉", "狗"]
    assert games._due_words_from(pool, "STU-SPELL", 3)[0] == "狗"


def test_due_words_from_handles_an_empty_pool():
    assert games._due_words_from([], "STU-SPELL", 3) == ()
    assert games._due_words_from(None, None, 3) == ()


def test_due_first_still_behaves_exactly_as_before():
    """重構不得改動既有三個遊戲的取詞行為。"""
    hints = games._due_first("animal", None)
    assert hints == tuple(games._words_in_cat("animal")[:games._MAX_HINTS])
```

- [ ] **Step 2: 跑測試確認它失敗**

Run: `.venv/bin/python -m pytest tests/test_games_spell.py -q`
Expected: FAIL — `AttributeError: module 'server.games' has no attribute '_due_words_from'`（以及 `GameState` 沒有 `retries`）

- [ ] **Step 3: 寫最小實作**

3a. 在 `server/games.py` 的 `GameState`（約第 66–79 行）末尾追加兩個欄位：

```python
    order: tuple = ()         # 遊戲 C 點了什麼
    step: str = ""            # 遊戲 C 的腳本階段／遊戲 D 的教學步驟
    retries: int = 0          # 遊戲 D：同一步已重試幾次（進入新的一步歸零）
    student_id: str = ""      # 遊戲 D：判定時要寫學習紀錄，開局時記下來
```

> 兩個欄位都有預設值，`replace()` 產生新狀態時其他三個遊戲完全不受影響。
> `student_id` 存在 state 裡是因為**判定時**才需要寫紀錄，而 `judge()` 的
> 簽章只吃 `(state, student_text)`——不存進去就傳不到。

3b. 把 `_due_first`（約第 149–167 行）整段換成：

```python
def _due_words_from(pool, student_id, limit: int) -> tuple:
    """從 ``pool`` 挑詞：間隔重複到期的排前面，其餘照 pool 原順序補。

    排程讀不到（DB 沒建、離線、壞掉）就退回純 pool 順序——
    挑詞是加值，不能因為它失敗就開不了局。
    """
    pool = list(pool or [])
    if not pool:
        return ()
    due: list[str] = []
    if student_id:
        try:
            from server import srs
            due = [w for w in srs.due_words(student_id, limit=max(1, limit) * 3)
                   if w in pool]
        except Exception:
            _log.warning("讀取到期詞失敗，改用 pool 原順序", exc_info=True)
            due = []
    ordered = due + [w for w in pool if w not in due]
    return tuple(ordered[:limit])


def _due_first(topic: str, student_id: str | None, limit: int = _MAX_HINTS) -> tuple:
    """某分類的提示詞（到期優先）。行為與重構前逐字相同。

    背單字要跨分類挑詞，所以取詞邏輯抽到 ``_due_words_from``；
    這裡只負責「pool＝這個分類的詞」這個決定。
    """
    return _due_words_from(_words_in_cat(topic), student_id, limit)
```

- [ ] **Step 4: 跑測試確認通過，並確認沒有回歸**

Run: `.venv/bin/python -m pytest tests/test_games_spell.py tests/test_games_i_spy.py tests/test_games_guess_who.py tests/test_games_restaurant.py tests/test_games_wiring.py -q`
Expected: PASS，既有三個遊戲的測試**一條都不能壞**

- [ ] **Step 5: 提交**

```bash
git add server/games.py tests/test_games_spell.py
git commit -m "refactor(games): 取詞邏輯抽成吃 pool 的版本 — 背單字要跨分類挑到期詞

_due_first 以分類為單位取詞，但背單字練的是「這孩子到期的詞」，
跨分類。抽出 _due_words_from(pool, ...)，_due_first 變成它的薄包裝，
既有三個遊戲的行為逐字不變（測試守著）。

GameState 同時加 retries 與 student_id 兩個欄位（都有預設值）：
判定時才需要寫學習紀錄，而 judge() 只吃 (state, text)，不存進去就傳不到。"
```

---

### Task 4: 開局與開場白

**Files:**
- Modify: `server/games.py`（在遊戲 C 之後、`GAMES` 目錄之前新增一節）
- Test: `tests/test_games_spell.py`（追加）

**Interfaces:**
- Consumes: Task 3 的 `_due_words_from`、`GameState.retries`、`GameState.student_id`；Task 1 的 `spelling.letters_for_tts`
- Produces（Task 5 會用）:
  - `SPELL_STEPS = ("say_word", "spell", "sentence")`
  - `SPELL_TARGET_COUNT = 3`
  - `start_spell_along(topic: str = "", *, target_count: int = SPELL_TARGET_COUNT, student_id: str | None = None) -> GameState`
  - `spell_along_prompt(state: GameState) -> Line`
  - `_spell_step_en(step: str, word_zh: str) -> str`

- [ ] **Step 1: 寫失敗的測試**

在 `tests/test_games_spell.py` 追加：

```python
# ---------------------------------------------------------------------------
# 開局
# ---------------------------------------------------------------------------

def test_start_picks_three_words_and_stands_on_the_first_step():
    st = games.start_spell_along()
    assert st.game == "spell_along"
    assert len(st.hints) == games.SPELL_TARGET_COUNT
    assert st.secret == st.hints[0]
    assert st.step == "say_word"
    assert st.retries == 0
    assert st.found == ()
    assert not st.done


def test_start_without_a_topic_draws_from_the_whole_vocabulary():
    """背單字要跨分類練到期詞，不綁單一場景。"""
    st = games.start_spell_along(target_count=5)
    assert all(w in scaffold.VOCAB for w in st.hints)
    assert st.topic == ""


def test_start_with_a_topic_stays_inside_that_category():
    st = games.start_spell_along(topic="animal")
    assert st.topic == "animal"
    assert all(scaffold.VOCAB[w]["cat"] == "animal" for w in st.hints)


def test_start_remembers_the_student_so_judging_can_record():
    st = games.start_spell_along(student_id="STU-9")
    assert st.student_id == "STU-9"


def test_start_survives_a_nonsense_target_count():
    assert games.start_spell_along(target_count="很多").target_count >= 1
    assert games.start_spell_along(target_count=0).target_count >= 1


# ---------------------------------------------------------------------------
# 開場白
# ---------------------------------------------------------------------------

def test_opening_line_names_the_first_word_in_both_languages():
    st = games.start_spell_along(topic="animal")
    line = games.spell_along_prompt(st)
    assert st.secret in line.zh
    assert line.en == scaffold.VOCAB[st.secret]["en"]


def test_step_content_is_what_the_child_repeats():
    """每一步要孩子跟著念的英文內容。拼音那一步必須是實測選出來的格式。"""
    assert games._spell_step_en("say_word", "蘋果") == "apple"
    assert games._spell_step_en("spell", "蘋果") == "A, P, P, L, E,"
    assert games._spell_step_en("sentence", "蘋果") == "I want to eat an apple."
    assert games._spell_step_en("spell", "不存在的詞") == ""


# ---------------------------------------------------------------------------
# 註冊進遊戲目錄
# ---------------------------------------------------------------------------

def test_the_game_is_in_the_public_catalog():
    """前端拿 GAMES 畫按鈕、game_intent 拿它認名字，沒註冊等於開局沒反應。"""
    entry = next(g for g in games.GAMES if g["kind"] == "spell_along")
    assert entry["zh"] == "背單字"
    assert "spell_along" in games.GAME_KINDS


def test_the_game_name_is_easy_for_asr_and_for_a_child():
    """2026-07-29 真機實測「火眼金睛」被聽成「佛火眼鏡」——冷僻用字對
    ASR 和對孩子都難。名字刻意選常用字。"""
    entry = next(g for g in games.GAMES if g["kind"] == "spell_along")
    assert len(entry["zh"]) <= 4


def test_generic_start_dispatches_through_the_shared_contract():
    st = games.start("spell_along")
    assert st.game == "spell_along"
    assert games.prompt(st).en
```

- [ ] **Step 2: 跑測試確認它失敗**

Run: `.venv/bin/python -m pytest tests/test_games_spell.py -q`
Expected: FAIL — `AttributeError: module 'server.games' has no attribute 'start_spell_along'`

- [ ] **Step 3: 寫最小實作**

3a. 在 `server/games.py` 的遊戲 C 之後、`GAMES` 目錄註解之前，插入：

```python
# ---------------------------------------------------------------------------
# 遊戲 D：背單字 Spell Along
#
# 一個詞三步：念單字 → 逐字母拼 → 例句，孩子每一步都跟著念。
# 判定主力是**拼音那一步**——2026-07-31 開發機實測，ASR 對字母序列
# 比對整個單字準得多（apple 被聽成 Bbble，A, P, P, L, E 卻一字不差）。
# 詳見 server/spelling.py 的模組 docstring。
#
# 感知（ASR 模糊比對）→ 決策（SRS 挑詞 + 前進/重來）→ 行動（TTS 三步腳本），
# 三段全在本地跑，斷網與連網逐字相同。
#
# 零新詞庫：拼音由 scaffold.VOCAB 的 en 現算，例句直接用 sent。
# ---------------------------------------------------------------------------

SPELL_STEPS = ("say_word", "spell", "sentence")

# 一局幾個詞。既有遊戲是 5 題，這裡只有 3——一個詞要三步＝三個回合，
# 3 個詞已經是 9 回合，比其他遊戲都長。
SPELL_TARGET_COUNT = 3


def start_spell_along(topic: str = "", *, target_count: int = SPELL_TARGET_COUNT,
                      student_id: str | None = None) -> GameState:
    """開一局背單字。

    ``topic`` 給了就只練該分類，空字串＝**跨分類練整個詞庫**。
    到期詞排前面：上禮拜拼錯的詞，今天第一個練。
    """
    valid_topic = topic if topic in _CAT_ZH else ""
    pool = _words_in_cat(valid_topic) if valid_topic else list(scaffold.VOCAB.keys())
    try:
        count = max(1, min(int(target_count), len(pool)))
    except (TypeError, ValueError):
        count = min(SPELL_TARGET_COUNT, len(pool))
    words = _due_words_from(pool, student_id, count)
    return GameState(
        game="spell_along",
        topic=valid_topic,
        target_count=len(words) or count,
        hints=words,
        secret=words[0] if words else "",
        step=SPELL_STEPS[0],
        student_id=str(student_id or ""),
    )


def _spell_step_en(step: str, word_zh: str) -> str:
    """某一步要孩子跟著念的英文內容；查不到詞回空字串。"""
    from server import spelling

    info = scaffold.VOCAB.get(word_zh) or {}
    if step == "spell":
        return spelling.letters_for_tts(info.get("en", ""))
    if step == "sentence":
        return str(info.get("sent", ""))
    return str(info.get("en", ""))


def spell_along_prompt(state: GameState) -> Line:
    """開場白：講清楚玩法，並直接念出第一個詞。

    裝置沒有螢幕，規則說明只能用聽的（與另外三個遊戲同一個限制）。
    """
    if not state.secret:
        return Line(zh="今天沒有要背的單字，我們玩別的好嗎？")
    return Line(
        zh=f"我們來背單字！我念一次，你跟著念，一共 {state.target_count} 個。"
           f"第一個是「{state.secret}」，跟我念：",
        en=_spell_step_en("say_word", state.secret),
    )
```

3b. 在 `GAMES` tuple（約第 607–632 行）末尾追加第四個條目：

```python
    {
        "kind": "spell_along",
        "zh": "背單字",
        "en": "Spell Along",
        "en_pattern": "A, P, P, L, E,",
        "function": "Spelling and reading aloud familiar words",
        "desc": "我念單字、拼字母、說例句，你跟著念",
    },
```

3c. 在 `_STARTERS` 與 `_PROMPTS` 各加一行（`_JUDGES` 留到 Task 5）：

```python
_STARTERS = {
    "i_spy": start_i_spy,
    "guess_who": start_guess_who,
    "restaurant": start_restaurant,
    "spell_along": start_spell_along,
}
_PROMPTS = {
    "i_spy": i_spy_prompt,
    "guess_who": guess_who_prompt,
    "restaurant": restaurant_prompt,
    "spell_along": spell_along_prompt,
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_games_spell.py -q`
Expected: PASS

接著跑既有遊戲測試找回歸——**特別注意 `_start_game_line` 的 ANY_GAME 開場白會列出「另外幾個遊戲」的名字，多一個遊戲會改變那句話**：

Run: `.venv/bin/python -m pytest tests/test_games_i_spy.py tests/test_games_guess_who.py tests/test_games_restaurant.py tests/test_games_wiring.py tests/test_games_ws_talk.py -q`
Expected: PASS。若有測試斷言了開場白的完整字串而失敗，**更新那條測試的期望值**（新增遊戲讓那句話多一個名字是正確行為，不是 bug）；不要為了讓測試綠而把新遊戲從 `GAMES` 拿掉。

- [ ] **Step 5: 提交**

```bash
git add server/games.py tests/test_games_spell.py
git commit -m "feat(games): 背單字開局 — 跨分類挑到期詞，三步腳本站在第一步

零新詞庫：拼音由 scaffold.VOCAB 的 en 現算，例句直接用 sent。
名字叫「背單字」而不是四字成語是刻意的：2026-07-29 真機實測
「火眼金睛」被 SenseVoice 聽成「佛火眼鏡」，冷僻用字對 ASR 和對孩子都難。

judge 留到下一個 commit，這一版開得了局但還不會判定。"
```

---

### Task 5: 三步判定狀態機

**Files:**
- Modify: `server/games.py`（接在 Task 4 新增的那一節之後）、`_JUDGES` 註冊
- Test: `tests/test_games_spell.py`（追加）

**Interfaces:**
- Consumes: Task 1、2 的 `spelling.{ref_letters, heard_letters, letter_hit_rate, word_hit_rate, record_word_result, PASS_THRESHOLD, MAX_RETRIES}`；Task 4 的 `SPELL_STEPS`、`_spell_step_en`
- Produces: `judge_spell_along(state: GameState, student_text) -> GameTurn`

**回覆怎麼被念出來：** `pipeline._process_text` 對遊戲回合做的是
`reply_text = turn.reply_zh + " 跟我說一遍：" + turn.target_en`。
所以每一步要孩子跟讀的內容一律放 `target_en`，`reply_zh` 只寫中文引導。

- [ ] **Step 1: 寫失敗的測試**

在 `tests/test_games_spell.py` 追加：

```python
# ---------------------------------------------------------------------------
# 三步狀態機
# ---------------------------------------------------------------------------

def _game_on(word_zh="蘋果", **kw):
    """開一局並強制第一個詞，讓測試不依賴選詞順序。"""
    st = games.start_spell_along(**kw)
    return games.replace(st, hints=(word_zh, "狗", "書"), secret=word_zh)


def test_saying_the_word_advances_to_spelling():
    turn = games.judge_spell_along(_game_on(), "apple")
    assert turn.correct
    assert turn.state.step == "spell"
    assert turn.target_en == "A, P, P, L, E,"


def test_spelling_correctly_advances_to_the_sentence():
    st = games.replace(_game_on(), step="spell")
    turn = games.judge_spell_along(st, "A, P, P, L, E.")
    assert turn.correct
    assert turn.state.step == "sentence"
    assert turn.target_en == "I want to eat an apple."


def test_one_wrong_letter_still_passes():
    """寬鬆鼓勵制：80% 命中就過。"""
    st = games.replace(_game_on(), step="spell")
    turn = games.judge_spell_along(st, "A, P, P, O, E.")
    assert turn.correct
    assert turn.state.step == "sentence"


def test_a_bad_attempt_repeats_the_same_step_instead_of_advancing():
    st = games.replace(_game_on(), step="spell")
    turn = games.judge_spell_along(st, "我不會")
    assert not turn.correct
    assert turn.state.step == "spell", "沒過卻前進了"
    assert turn.state.retries == 1
    assert turn.target_en == "A, P, P, L, E,", "重來時要再念一次同樣的內容"


def test_the_child_is_never_stuck_on_one_step():
    """第 MAX_RETRIES+1 次一律往下走。卡在同一個詞出不去是最糟的失敗模式。"""
    st = games.replace(_game_on(), step="spell")
    for _ in range(spelling.MAX_RETRIES):
        st = games.judge_spell_along(st, "我不會").state
        assert st.step == "spell"
    turn = games.judge_spell_along(st, "我不會")
    assert turn.state.step == "sentence", "重試用完仍卡在原地"
    assert not turn.correct, "往下走不代表判定成功"


def test_retries_reset_when_a_new_step_begins():
    """重試上限是「同一步」的上限，不是整個詞的上限——否則第一步用掉配額，
    後面兩步一次機會都沒有。"""
    st = games.judge_spell_along(games.replace(_game_on()), "我不會").state
    assert st.retries == 1
    turn = games.judge_spell_along(st, "apple")
    assert turn.state.step == "spell"
    assert turn.state.retries == 0


def test_finishing_the_sentence_moves_to_the_next_word():
    st = games.replace(_game_on(), step="sentence")
    turn = games.judge_spell_along(st, "I want to eat an apple.")
    assert turn.state.found == ("蘋果",)
    assert turn.state.secret == "狗"
    assert turn.state.step == "say_word"
    assert turn.state.retries == 0
    assert not turn.done


def test_the_last_word_ends_the_round():
    st = games.replace(_game_on(), step="sentence", target_count=1)
    turn = games.judge_spell_along(st, "I want to eat an apple.")
    assert turn.done
    assert turn.state.done
    assert "背了 1 個" in turn.reply_zh


def test_judging_a_finished_round_does_not_crash():
    st = games.replace(_game_on(), done=True, secret="")
    turn = games.judge_spell_along(st, "apple")
    assert not turn.correct
    assert turn.reply_zh


def test_judging_never_raises_on_garbage():
    for junk in (None, "", 12345, "。" * 600):
        assert games.judge_spell_along(_game_on(), junk).reply_zh


# ---------------------------------------------------------------------------
# 學習狀況記錄
# ---------------------------------------------------------------------------

def test_a_clean_spelling_is_recorded_as_learned(tmp_db):
    from server import store

    st = games.replace(_game_on(student_id="STU-R"), step="spell")
    games.judge_spell_along(st, "A, P, P, L, E.")
    row = store.get_word_review("STU-R", "蘋果")
    assert row is not None and row["reps"] == 1


def test_a_spelling_that_needed_retries_is_not_counted_as_learned(tmp_db):
    """重試才過的不算學會——記下來的必須是真的會了。"""
    from server import store

    st = games.replace(_game_on(student_id="STU-R"), step="spell")
    st = games.judge_spell_along(st, "我不會").state      # retries → 1
    games.judge_spell_along(st, "A, P, P, L, E.")         # 這次過了
    assert store.get_word_review("STU-R", "蘋果")["interval_days"] == 0


def test_recording_happens_at_the_spelling_step_not_the_sentence_step(tmp_db):
    """拼音是判定主力，例句那一步的 ASR 太糊，不參與對錯判定。"""
    from server import store

    st = games.replace(_game_on(student_id="STU-R"), step="sentence")
    games.judge_spell_along(st, "完全不相干的話")
    assert store.get_word_review("STU-R", "蘋果") is None


def test_a_round_without_a_student_still_plays(tmp_db):
    """沒有 student_id 就不寫紀錄，但遊戲照樣玩得完。"""
    st = games.replace(_game_on(), step="spell")
    assert games.judge_spell_along(st, "A, P, P, L, E.").state.step == "sentence"
```

> 註：測試用到 `games.replace`——`games.py` 已經 `from dataclasses import dataclass, replace`，
> 所以 `games.replace` 就是 `dataclasses.replace`，可直接用。

- [ ] **Step 2: 跑測試確認它失敗**

Run: `.venv/bin/python -m pytest tests/test_games_spell.py -q`
Expected: FAIL — `AttributeError: module 'server.games' has no attribute 'judge_spell_along'`

- [ ] **Step 3: 寫最小實作**

3a. 在 `server/games.py` 的 `spell_along_prompt` 之後追加：

```python
def judge_spell_along(state: GameState, student_text) -> GameTurn:
    """判定一次跟讀。純規則、離線、不拋。"""
    try:
        return _judge_spell_along(state, student_text)
    except Exception:
        _log.exception("judge_spell_along 失敗，回中性提示")
        return GameTurn(state=state, correct=False,
                        reply_zh="我沒有聽清楚，再說一次好嗎？")


def _judge_spell_along(state: GameState, student_text) -> GameTurn:
    from server import spelling

    if state.done or not state.secret:
        return GameTurn(state=state, correct=False,
                        reply_zh="這一局已經背完囉！要不要再來一局？")

    word = state.secret
    info = scaffold.VOCAB.get(word) or {}
    en = str(info.get("en", ""))
    turns = state.turns + 1

    # --- 感知：這一步唸得對不對 -------------------------------------------
    # 拼音那一步比字母序列，其餘兩步比目標單字。這個分工是實測結論：
    # ASR 對字母序列比對整個單字準得多。
    if state.step == "spell":
        rate = spelling.letter_hit_rate(
            spelling.ref_letters(en), spelling.heard_letters(student_text)
        )
    else:
        rate = spelling.word_hit_rate(en, student_text)
    passed = rate >= spelling.PASS_THRESHOLD

    # --- 決策 1：沒過而且還有重試額度 → 放慢再來一次，不前進 --------------
    if not passed and state.retries < spelling.MAX_RETRIES:
        return GameTurn(
            state=replace(state, turns=turns, retries=state.retries + 1),
            correct=False, word=word,
            target_en=_spell_step_en(state.step, word),
            reply_zh="沒關係，我再慢慢念一次，你跟著我：",
        )

    # --- 決策 2：前進。過了要前進，重試用完也要前進 -----------------------
    # 卡在同一個詞出不去是現場最糟的失敗模式，比答錯還糟。
    if state.step == "say_word":
        return GameTurn(
            state=replace(state, turns=turns, step="spell", retries=0),
            correct=passed, word=word,
            target_en=_spell_step_en("spell", word),
            reply_zh="很棒！我們來拼拼看：" if passed else "沒關係，我們先來拼拼看：",
        )

    if state.step == "spell":
        # 判定主力就在這一步 → 學習狀況也在這裡記。
        # correct 的定義是「第一次嘗試就過」：重試才過的不算學會，
        # 記下來的必須是真的會了，否則教師端看到的是灌水的數字。
        spelling.record_word_result(
            state.student_id, word, passed and state.retries == 0
        )
        return GameTurn(
            state=replace(state, turns=turns, step="sentence", retries=0),
            correct=passed, word=word,
            target_en=_spell_step_en("sentence", word),
            reply_zh=(f"拼對了！「{word}」就是 {en}。用一句話說說看："
                      if passed else f"「{word}」是 {en}。我們用一句話說說看："),
        )

    # --- step == "sentence"：這個詞完成，換下一個 ------------------------
    found = state.found + (word,)
    remaining = [w for w in state.hints if w not in found]
    if len(found) >= state.target_count or not remaining:
        return GameTurn(
            state=replace(state, found=found, turns=turns, done=True,
                          secret="", step="", retries=0),
            correct=passed, word=word, done=True,
            reply_zh=f"太棒了！今天我們背了 {len(found)} 個單字，"
                     f"最後一個是「{word}」。你好厲害！",
        )

    nxt = remaining[0]
    return GameTurn(
        state=replace(state, found=found, turns=turns,
                      secret=nxt, step=SPELL_STEPS[0], retries=0),
        correct=passed, word=word,
        target_en=_spell_step_en("say_word", nxt),
        reply_zh=f"很好！「{word}」背完了。下一個是「{nxt}」，跟我念：",
    )
```

3b. 在 `_JUDGES` 加一行：

```python
_JUDGES = {
    "i_spy": judge_i_spy,
    "guess_who": judge_guess_who,
    "restaurant": judge_restaurant,
    "spell_along": judge_spell_along,
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_games_spell.py tests/test_spelling.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add server/games.py tests/test_games_spell.py
git commit -m "feat(games): 背單字三步判定 — 過了要前進，重試用完也要前進

卡在同一個詞出不去是現場最糟的失敗模式，比答錯還糟。所以同一步最多
重試 2 次，第 3 次一律往下走，而且往下走時 correct 仍是 False——
前進與判定成功是兩件事。

學習紀錄寫在拼音那一步（判定主力），correct 定義為「第一次嘗試就過」：
重試才過的不算學會，否則教師端看到的是灌水的數字。"
```

---

### Task 6: 整合驗收與文件

**Files:**
- Test: `tests/test_games_spell.py`（追加整合段）
- Test: `tests/test_games_wiring.py`（追加一條斷網一致性測試）
- Modify: `docs/GAMES.md`

**Interfaces:**
- Consumes: Task 1–5 的全部產出、`server/pipeline.py::VoicePipeline.{start_game, play_turn}`、`server/game_intent.py::detect_start`

> **這一個 Task 沒有 red 階段。** 前五個 Task 若都做對了，這些測試寫完就是綠的——
> 它們是**驗收測試**，不是驅動實作的測試。有任何一條紅的，代表前面漏了東西，
> 回去補前面的 Task，不要在這裡改實作繞過。

- [ ] **Step 1: 寫驗收測試（純函式層）**

在 `tests/test_games_spell.py` 追加：

```python
# ---------------------------------------------------------------------------
# 整合：語音開局、斷網一致性
# ---------------------------------------------------------------------------

def test_a_child_can_start_it_by_voice():
    """裝置沒有螢幕，用講的開局是唯一的入口。

    game_intent 自動從 games.GAMES 讀名字，所以只要註冊了就該認得——
    這條測試守的是「有沒有真的註冊」。
    """
    from server import game_intent

    assert game_intent.detect_start("我要玩背單字") == "spell_along"
    assert game_intent.detect_start("我想玩背單字遊戲") == "spell_along"


def test_talking_about_memorising_is_not_a_start_command():
    """光講名字不算——意圖詞是必要條件（與另外三個遊戲同一條規則）。"""
    from server import game_intent

    assert game_intent.detect_start("背單字好難") is None


def test_a_full_round_reaches_the_end_even_when_every_answer_is_wrong(tmp_db):
    """一路唸錯也要走得完，絕不卡死。

    一律唸錯 → 每一步都用掉 MAX_RETRIES 次重試才前進，所以最壞情況的
    回合數是「詞數 × 步數 × (重試上限 + 1)」。上限用算式而不是寫死數字，
    調 MAX_RETRIES 時測試才不會假性失敗。
    """
    st = games.start_spell_along(target_count=3, student_id="STU-FULL")
    words = len(st.hints)
    limit = words * len(games.SPELL_STEPS) * (spelling.MAX_RETRIES + 1) + 2
    for _ in range(limit):
        turn = games.judge_spell_along(st, "我不會")
        st = turn.state
        if turn.done:
            break
    assert st.done, f"{limit} 回合還沒走完：found={st.found} step={st.step}"


def test_every_wrong_word_is_scheduled_for_immediate_review(tmp_db):
    """一路唸錯的詞全部立刻到期——下一局會先練它們。"""
    from server import store

    st = games.start_spell_along(target_count=3, student_id="STU-FULL2")
    words = list(st.hints)
    limit = len(words) * len(games.SPELL_STEPS) * (spelling.MAX_RETRIES + 1) + 2
    for _ in range(limit):
        turn = games.judge_spell_along(st, "我不會")
        st = turn.state
        if turn.done:
            break
    for w in words:
        row = store.get_word_review("STU-FULL2", w)
        assert row is not None and row["interval_days"] == 0, f"{w} 沒被排進複習"
```

- [ ] **Step 2: 寫驗收測試（接線層）**

斷網一致性要驗的是**接線**，不是純函式——所以它屬於 `tests/test_games_wiring.py`，
那裡已經有 `_pipeline(mode)` helper 與另外三個遊戲的同款測試。在該檔末尾追加：

```python
@pytest.mark.parametrize("mode", ["edge", "cloud"])
async def test_spell_along_judgement_is_identical_online_and_offline(mode, tmp_db):
    """**背單字在斷網與連網下的判定必須一模一樣。**

    這是整個功能的主張：背單字訓練在斷網的裝置上完整可用。
    判定若走雲端，斷網那一刻行為就會變——現場最不能發生的事。
    """
    vp = _pipeline(mode)
    vp.start_game("spell_along", target_count=1)
    vp.game = games.replace(vp.game, hints=("蘋果",), secret="蘋果")

    a = vp.play_turn("apple")
    assert (a.correct, a.state.step) == (True, "spell")
    assert a.target_en == "A, P, P, L, E,"

    b = vp.play_turn("A, P, P, L, E.")
    assert (b.correct, b.state.step) == (True, "sentence")
    assert b.target_en == "I want to eat an apple."


async def test_spell_along_never_calls_the_cloud(tmp_db, monkeypatch):
    """上一條的機制保證：判定路徑一次都不准碰雲端。"""
    from server import cloud_llm

    def _boom(*a, **kw):
        raise AssertionError("背單字判定不該呼叫雲端 LLM")

    monkeypatch.setattr(cloud_llm.CloudLLM, "generate", _boom)
    vp = _pipeline("cloud")
    vp.start_game("spell_along", target_count=1)
    for text in ("apple", "A, P, P, L, E.", "I want to eat an apple."):
        vp.play_turn(text)
```

> `vp.play_turn` 在一局結束時會把 `vp.game` 清成 `None`（見
> `pipeline.play_turn` 與 `test_finished_game_is_cleared_automatically`），
> 所以第二條測試最後一輪之後 `vp.game` 是 `None`——這是正確行為，不要 assert 它還在。

- [ ] **Step 3: 跑這兩檔的測試**

Run: `.venv/bin/python -m pytest tests/test_games_spell.py tests/test_games_wiring.py -q`
Expected: PASS。**任何一條紅的都代表 Task 1–5 漏了東西**，回去補那個 Task。

- [ ] **Step 4: 跑完整測試套件**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS，**一條既有測試都不能壞**。若 `_start_game_line` 的 ANY_GAME 開場白相關測試失敗，依 Task 4 Step 4 的說明更新期望值。

- [ ] **Step 5: 更新 `docs/GAMES.md`**

把標題 `# 三個互動小遊戲 — 現場操作` 改成 `# 四個互動小遊戲 — 現場操作`，
在遊戲表格追加一列：

```markdown
| 背單字 Spell Along | `Spelling and reading aloud familiar words` | `A, P, P, L, E,` | 全詞庫 136 詞，到期詞優先 |
```

並在「已知邊界」段落追加：

```markdown
- **背單字的字母念法是實測選出來的。** 只有 `"A, P, P, L, E,"` 這個格式能被
  本地 TTS 正確念成字母並被 SenseVoice 完整讀回；句點、空白、連字號三種
  寫法實測全壞。改格式前先跑 `edge/probes/probe_spell_tts.py`
- **ASR 把字母黏成一個字時，分不出孩子是在拼還是在唸整個單字**
  （兩者的 ASR 文字都是 `Apple.`）。分不出來就不假裝分得出來，一律當作拼對
- **判定靠 ASR 文字，不是真發音評分。** 腔調很怪但字母對，仍然會過。
  真音素評分（`server/pronunciation.py`）需要 torch，Genio 520 上能不能跑未驗證，
  且它的定位是背景診斷層、不進即時路徑
- **孩子真聲的字母辨識率尚未驗證。** 實測用的是 TTS 合成音當代理，不是童聲。
  開發機沒有錄音裝置，這件事只能上真機驗收——這是本功能最大的未驗證假設
```

- [ ] **Step 6: 提交**

```bash
git add tests/test_games_spell.py tests/test_games_wiring.py docs/GAMES.md
git commit -m "test(games): 背單字斷網一致性與整局走完 — 順手把已知邊界寫進文件

斷網一致性是這個功能的主張：判定若走雲端，斷網那一刻行為就會變。
一律唸錯也要能走完整局的測試守著「絕不卡死」這條。

文件補三條不要在台上講過頭的邊界，最重要的一條是：實測用的是 TTS
合成音當代理，孩子真聲的字母辨識率還沒驗證過。"
```

---

## 真機驗收（不在自動測試涵蓋範圍）

自動測試證明不了的一件事：**孩子的真聲拼字母，SenseVoice 認不認得。**
開發機沒有錄音裝置，所以這條只能在 Genio 520 上做：

1. 部署到裝置（`edge/deploy/push.sh` + `install_services.sh`，見 `edge/BOOT_SOP.md`）
2. 按 power 鍵短按觸發，說「我要玩背單字」→ 應該聽到開場白與第一個單字
3. 跟著念單字 → 應該進到拼音步驟並聽到 `A, P, P, L, E,`（**逐字母，不是 apple**）
4. 跟著拼 → 應該進到例句步驟
5. 用 `edge/runtime/dump_recent_turns.py` 看每一輪的 ASR 文字，確認字母有被聽成分開的 token
6. **拔網路重跑步驟 2–5，行為必須一模一樣**

若步驟 3 聽到的是整個單字而不是字母，先跑 `edge/probes/probe_spell_tts.py`
在裝置上重驗格式——裝置的 espeak-ng-data 版本可能與開發機不同。

若步驟 5 顯示童聲字母一直被黏成一個字，`spelling.PASS_THRESHOLD` 是可調常數，
調低它即可，不必改邏輯。
