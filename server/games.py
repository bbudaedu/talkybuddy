# -*- coding: utf-8 -*-
"""games.py — 三個互動小遊戲的規則式核心（純函式、離線完整可玩）。

| 遊戲 | 課綱溝通功能（附錄四） | 句型 |
|---|---|---|
| A 火眼金睛 I Spy | Naming common toys and household objects／Talking about location | `I see a ___.` |
| B 猜猜我是誰 20 Questions | Asking about abilities／Asking about ownership | `Is it ___?` |
| C 點餐時間 Restaurant | Ordering food & drinks | `I want a ___.` |

四條設計原則
------------
1. **離線必須完整可玩。** 斷網橋段是決賽主軸，遊戲斷線就掛等於自打嘴巴。
   判定、計分、鼓勵語全部是規則式純函式，這個模組**不 import 任何雲端東西**。
   雲端只在 pipeline 那一層做加值（追問、場景敘述），沒有它遊戲照樣完整。

2. **狀態不可變。** 每回合回傳新的 state，不原地改。跨回合的隱藏耦合是
   這種多輪玩法最容易出的 bug，用 frozen dataclass 從型別上擋掉。

3. **零新詞庫。** 三個遊戲的題目全部來自 `scaffold.VOCAB`——它的 `cat`
   分類、`np`（冠詞已正確）、`sent`（目標句）本來就是為這件事準備的。
   新造一份遊戲專用詞庫的話，兩份遲早不同步。

4. **任何輸入都不拋。** 這層跑在語音迴圈裡，孩子講什麼都可能進來
   （ASR 亂碼、空字串、5000 字）。拋例外 = 遊戲當掉 = 現場停擺。
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, replace

from server import scaffold

_log = logging.getLogger(__name__)

# 分類 → 中文場景名（沿用 profile._CAT_LABEL 的說法，不另立一套）
_CAT_ZH = {
    "food": "食物", "school": "學校", "animal": "動物",
    "family": "家庭", "action": "動作", "color": "顏色",
}

# 可玩的場景：詞數 ≥5 才排得出一局（測試 test_every_playable_topic 守著）
I_SPY_TOPICS = ("animal", "food", "school")

# 一局的長度。五題約 2–3 分鐘，是國小生的專注力上限。
DEFAULT_TARGET_COUNT = 5

# 提示詞數：給太多等於直接給答案
_MAX_HINTS = 3


# ---------------------------------------------------------------------------
# 共用型別
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Line:
    """一句要說出口的話：中文鷹架 + 英文目標。"""
    zh: str
    en: str = ""


@dataclass(frozen=True)
class GameState:
    """一局遊戲的完整狀態。frozen——每回合產生新的，不原地改。"""
    game: str
    topic: str = ""
    found: tuple = ()
    target_count: int = DEFAULT_TARGET_COUNT
    turns: int = 0
    done: bool = False
    hints: tuple = ()
    secret: str = ""          # 遊戲 B 的謎底
    asked: tuple = ()         # 遊戲 B 問過的問題種類
    order: tuple = ()         # 遊戲 C 點了什麼
    step: str = ""            # 遊戲 C 的腳本階段／遊戲 D 的教學步驟
    retries: int = 0          # 遊戲 D：同一步已重試幾次（進入新的一步歸零）
    student_id: str = ""      # 遊戲 D：判定時要寫學習紀錄，開局時記下來


@dataclass(frozen=True)
class GameTurn:
    """判定一次作答的結果。

    ``answer`` 只有遊戲 B 用：``yes`` / ``no`` / ``unknown``。
    ``unknown`` 是**誠實的做不到**，不是「我猜不是」——離線屬性表答不出來
    的問題，瞎猜會讓孩子學到錯的東西。
    """
    state: GameState
    correct: bool
    reply_zh: str
    word: str | None = None
    target_en: str | None = None
    done: bool = False
    answer: str = ""
    reply_en: str = ""


# ---------------------------------------------------------------------------
# 共用小工具
# ---------------------------------------------------------------------------

def _safe_text(value) -> str:
    """把任何輸入正規化成可處理的字串。長度上限防 ASR 爆走。"""
    if value is None:
        return ""
    try:
        return str(value)[:500].strip()
    except Exception:
        return ""


def _words_in_cat(cat: str) -> list[str]:
    """某分類底下的中文詞鍵，保留 VOCAB 原始順序（確定性）。"""
    return [k for k, v in scaffold.VOCAB.items() if v.get("cat") == cat]


def _detect_vocab(text: str) -> str | None:
    """從孩子的話裡找出詞庫詞，中英文都吃；找不到回 None。

    中文走 scaffold._find_zh_vocab（長詞優先，「書包」不會被「書」搶走）；
    英文比對 VOCAB 的 en 欄位。兩者都命中時以中文優先——孩子講中文時
    通常是主要意圖，英文可能只是句型框架裡的字。
    """
    text = _safe_text(text)
    if not text:
        return None
    try:
        zh_hits = scaffold._find_zh_vocab(text)
        if zh_hits:
            return zh_hits[0]
    except Exception:
        _log.warning("中文詞庫比對失敗", exc_info=True)

    lowered = text.lower()
    tokens = set(re.findall(r"[a-z]+", lowered))
    # 先比多字片語（ice cream），再比單字，避免片語被拆掉
    for zh, info in scaffold.VOCAB.items():
        en = str(info.get("en", "")).lower()
        if " " in en and en in lowered:
            return zh
    for zh, info in scaffold.VOCAB.items():
        en = str(info.get("en", "")).lower()
        if en and en in tokens:
            return zh
    return None


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


# ---------------------------------------------------------------------------
# 遊戲 A：火眼金睛 I Spy
# ---------------------------------------------------------------------------

def start_i_spy(topic: str = "animal", *, target_count: int = DEFAULT_TARGET_COUNT,
                student_id: str | None = None) -> GameState:
    """開一局火眼金睛。

    ``topic`` 不在可玩清單時退回第一個可玩場景——寧可換場景，
    也不要開一個玩兩題就沒詞的局。
    """
    if topic not in I_SPY_TOPICS:
        topic = I_SPY_TOPICS[0]
    try:
        count = max(1, min(int(target_count), len(_words_in_cat(topic))))
    except (TypeError, ValueError):
        count = DEFAULT_TARGET_COUNT
    return GameState(
        game="i_spy",
        topic=topic,
        target_count=count,
        hints=_due_first(topic, student_id),
    )


def i_spy_prompt(state: GameState) -> Line:
    """開場白：講清楚場景與句型。"""
    zh_topic = _CAT_ZH.get(state.topic, state.topic)
    return Line(
        zh=f"我們來玩「火眼金睛」！這一關是{zh_topic}。"
           f"看到什麼就說 I see a …，一共要找 {state.target_count} 個喔！",
        en="I see a dog.",
    )


def judge_i_spy(state: GameState, student_text) -> GameTurn:
    """判定一次作答。純規則、離線、不拋。"""
    try:
        return _judge_i_spy(state, student_text)
    except Exception:
        _log.exception("judge_i_spy 失敗，回中性提示")
        return GameTurn(state=state, correct=False,
                        reply_zh="我沒有聽清楚，再說一次好嗎？")


def _judge_i_spy(state: GameState, student_text) -> GameTurn:
    zh_topic = _CAT_ZH.get(state.topic, state.topic)

    if state.done:
        return GameTurn(state=state, correct=False,
                        reply_zh="這一關已經完成囉！要不要換一個場景？")

    word = _detect_vocab(student_text)
    turns = state.turns + 1

    if word is None:
        return GameTurn(
            state=replace(state, turns=turns), correct=False,
            reply_zh=f"再說一次好嗎？想想看，{zh_topic}裡面有什麼呢？",
        )

    info = scaffold.VOCAB[word]

    if word in state.found:
        return GameTurn(
            state=replace(state, turns=turns), correct=False, word=word,
            reply_zh=f"「{word}」剛剛已經說過了，換一個試試看！",
        )

    if info["cat"] != state.topic:
        return GameTurn(
            state=replace(state, turns=turns), correct=False, word=word,
            reply_zh=f"「{word}」很棒，不過我們現在在找{zh_topic}喔！再找找看。",
        )

    found = state.found + (word,)
    done = len(found) >= state.target_count
    new_state = replace(state, found=found, turns=turns, done=done)

    if done:
        return GameTurn(
            state=new_state, correct=True, word=word, done=True,
            target_en=info["sent"],
            reply_zh=f"找到「{word}」了，恭喜你完成這一關！"
                     f"你總共找到 {len(found)} 個{zh_topic}，好厲害！",
        )
    left = state.target_count - len(found)
    return GameTurn(
        state=new_state, correct=True, word=word, target_en=info["sent"],
        reply_zh=f"對！找到「{word}」了。還差 {left} 個，繼續找！",
    )


# ---------------------------------------------------------------------------
# 遊戲 B：猜猜我是誰 20 Questions
#
# 這是雲端價值的展示台。離線版只能回答「屬性表答得出來」的問題，雲端版
# 任何問題都答得出來——斷網那一刻的落差是看得見的。
#
# 屬性全部**從既有資料推導**，不新造資料集：
#   cat        → 是不是動物／食物／學校用品
#   en 首字母  → 「是不是 D 開頭？」
#   np 冠詞    → a / an（母音開頭）
# ---------------------------------------------------------------------------

# 一局最多問幾次。20 Questions 的原型是 20 次，但國小生的專注力撐不住，
# 而且離線能答的問題種類有限，問太多只會一直撞到 unknown。
DEFAULT_MAX_QUESTIONS = 8

# 前端拿這份清單做提示按鈕：孩子不知道能問什麼就玩不下去。
GUESS_WHO_SUPPORTED = (
    {"zh": "問類別", "en": "Is it an animal?"},
    {"zh": "問能不能吃", "en": "Is it food?"},
    {"zh": "問開頭字母", "en": "Does it start with D?"},
    {"zh": "直接猜", "en": "Is it a dog?"},
)

# 類別關鍵詞 → VOCAB 的 cat。離線問句理解就靠這張表，刻意做小而準：
# 答不出來會明說，不會硬湊。
#
# ⚠️ 英文必須用**詞邊界**比對，不能用子字串。第一版用 `"do" in text` 的結果是
# 「Does it start with B?」被判成動作類問句、「Does it live in Africa?」被答成
# 「不是」——兩個都是瞎猜，正是這個遊戲最不該犯的錯。中文沒有詞邊界，
# 維持子字串比對。
_CAT_KEYWORDS_EN = {
    "animal": ("animal", "animals"),
    "food":   ("food", "eat", "drink", "fruit"),
    "school": ("school", "classroom", "class"),
    "family": ("family", "people", "person"),
    "color":  ("color", "colour"),
    "action": ("action", "verb"),
}
_CAT_KEYWORDS_ZH = {
    "animal": ("動物",),
    "food":   ("吃", "喝", "食物", "水果"),
    "school": ("學校", "教室", "上課"),
    "family": ("家人", "家庭"),
    "color":  ("顏色",),
    "action": ("動作",),
}
_CAT_PATTERNS_EN = {
    cat: re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b",
                    re.IGNORECASE)
    for cat, words in _CAT_KEYWORDS_EN.items()
}


def _match_category(text: str) -> str | None:
    """問句問的是哪一個類別；問不出來回 None（→ 誠實說做不到）。"""
    for cat, pattern in _CAT_PATTERNS_EN.items():
        if pattern.search(text):
            return cat
    for cat, words in _CAT_KEYWORDS_ZH.items():
        if any(w in text for w in words):
            return cat
    return None

_FIRST_LETTER_RE = re.compile(
    r"(?:start|begin)s?\s+with\s+(?:the\s+letter\s+)?['\"]?([a-z])",
    re.IGNORECASE,
)
_ZH_FIRST_LETTER_RE = re.compile(r"([a-zA-Z])\s*開頭")


def start_guess_who(topic: str = "animal", *, seed: str = "",
                    student_id: str | None = None,
                    max_questions: int = DEFAULT_MAX_QUESTIONS) -> GameState:
    """開一局猜猜我是誰。

    謎底優先挑「這孩子答錯過又到期」的詞（遊戲即複習）；沒有到期詞時，
    用 seed 的雜湊決定，**不是隨機**——現場要可重現，測試也才驗得動。
    """
    if topic not in I_SPY_TOPICS:
        topic = I_SPY_TOPICS[0]
    pool = _words_in_cat(topic)
    if not pool:
        pool = list(scaffold.VOCAB.keys())

    secret = ""
    if student_id:
        try:
            from server import srs
            due = [w for w in srs.due_words(student_id, limit=10) if w in pool]
            if due:
                secret = due[0]
        except Exception:
            _log.warning("讀取到期詞失敗，謎底改用 seed 決定", exc_info=True)
    if not secret:
        digest = hashlib.sha256(str(seed).encode("utf-8")).hexdigest()
        secret = pool[int(digest[:8], 16) % len(pool)]

    try:
        budget = max(1, int(max_questions))
    except (TypeError, ValueError):
        budget = DEFAULT_MAX_QUESTIONS
    return GameState(game="guess_who", topic=topic, secret=secret,
                     target_count=budget)


def guess_who_prompt(state: GameState) -> Line:
    """開場白。**必須示範句型**——孩子不會問問句就玩不下去。"""
    return Line(
        zh=f"我想好一個{_CAT_ZH.get(state.topic, state.topic)}了，你來問我！"
           f"可以問「是不是動物？」「是不是 D 開頭？」，最多問 {state.target_count} 次。",
        en="Is it an animal?",
    )


def judge_guess_who(state: GameState, student_text) -> GameTurn:
    """回答一個 Yes/No 問句，或判定一次猜測。純規則、離線、不拋。"""
    try:
        return _judge_guess_who(state, student_text)
    except Exception:
        _log.exception("judge_guess_who 失敗，回中性提示")
        return GameTurn(state=state, correct=False, answer="unknown",
                        reply_zh="我沒有聽清楚，再問一次好嗎？")


def _judge_guess_who(state: GameState, student_text) -> GameTurn:
    if state.done:
        return GameTurn(state=state, correct=False, answer="unknown",
                        reply_zh="這一局結束囉！要不要再玩一次？")

    text = _safe_text(student_text)
    lowered = text.lower()
    info = scaffold.VOCAB.get(state.secret, {})
    secret_en = str(info.get("en", ""))
    asked = state.asked + (lowered[:40],)
    turns = state.turns + 1
    used_up = turns >= state.target_count

    def _finish(reply_zh: str, reply_en: str = "", correct: bool = False) -> GameTurn:
        return GameTurn(
            state=replace(state, asked=asked, turns=turns, done=True),
            correct=correct, answer="", reply_zh=reply_zh, reply_en=reply_en,
            word=state.secret, target_en=info.get("sent"), done=True,
        )

    def _reveal_suffix() -> str:
        return f"答案是「{state.secret}」（{secret_en}）。"

    # --- 1. 直接猜某個詞 ---------------------------------------------------
    guessed = _detect_vocab(text)
    if guessed is not None:
        if guessed == state.secret:
            return _finish(
                reply_zh=f"答對了！就是「{state.secret}」，你好厲害！",
                reply_en=str(info.get("sent", "")), correct=True,
            )
        # 猜錯：這也是一次「不是」，不是失敗
        if used_up:
            return _finish(reply_zh=f"問完囉！{_reveal_suffix()}",
                           reply_en=f"It is {info.get('np', secret_en)}.")
        left = state.target_count - turns
        return GameTurn(
            state=replace(state, asked=asked, turns=turns),
            correct=False, answer="no",
            reply_zh=f"不是「{guessed}」喔！還可以問 {left} 次。",
            reply_en="No, it isn't.",
        )

    # --- 2. 類別問句 -------------------------------------------------------
    cat = _match_category(text)
    if cat is not None:
        hit = info.get("cat") == cat
        if used_up:
            return _finish(reply_zh=f"問完囉！{_reveal_suffix()}",
                           reply_en=f"It is {info.get('np', secret_en)}.")
        left = state.target_count - turns
        return GameTurn(
            state=replace(state, asked=asked, turns=turns),
            correct=False, answer="yes" if hit else "no",
            reply_zh=("對！" if hit else "不是喔！") + f"還可以問 {left} 次。",
            reply_en="Yes, it is." if hit else "No, it isn't.",
        )

    # --- 3. 開頭字母問句 ---------------------------------------------------
    m = _FIRST_LETTER_RE.search(text) or _ZH_FIRST_LETTER_RE.search(text)
    if m and secret_en:
        hit = secret_en[0].lower() == m.group(1).lower()
        if used_up:
            return _finish(reply_zh=f"問完囉！{_reveal_suffix()}",
                           reply_en=f"It is {info.get('np', secret_en)}.")
        left = state.target_count - turns
        return GameTurn(
            state=replace(state, asked=asked, turns=turns),
            correct=False, answer="yes" if hit else "no",
            reply_zh=("對！" if hit else "不是喔！") + f"還可以問 {left} 次。",
            reply_en="Yes, it is." if hit else "No, it isn't.",
        )

    # --- 4. 答不出來：**誠實說做不到，不瞎猜** ------------------------------
    # 這一支是離線版的能力邊界。瞎猜 Yes/No 會讓孩子學到錯的東西，
    # 比承認做不到更糟；而同樣的問題雲端答得出來，落差就是雲端的價值。
    # 這一題不計入額度——孩子沒問錯，是我們答不了。
    hints = "、".join(k["en"] for k in GUESS_WHO_SUPPORTED[:3])
    return GameTurn(
        state=replace(state, asked=asked), correct=False, answer="unknown",
        reply_zh=f"這個問題我還答不出來耶！可以試試看問：{hints}",
        reply_en="I can't answer that yet.",
    )


# ---------------------------------------------------------------------------
# 遊戲 C：點餐時間 Restaurant
#
# 課綱附錄四明列 `Ordering food & drinks`、附錄三主題 `Eating out`。
# 三個遊戲裡最貼近真實情境的一個——孩子在餐廳真的會用到。
#
# 冠詞一律取自 VOCAB 的 np 欄位，**不自己拼**：麵包是不可數 → some bread，
# 不是 a bread。這種錯誤會直接教錯孩子，而詞條裡本來就寫對了。
# ---------------------------------------------------------------------------

# 一局最多點幾樣。點太多結帳句會變得又臭又長，也超出一局的長度。
DEFAULT_MAX_ITEMS = 4

# 「不用了」的各種說法。孩子不會照腳本講，中英文都要接得住。
_NO_MORE_EN = re.compile(
    r"\b(?:no|nope|nothing|that'?s all|that is all|done|finish(?:ed)?)\b",
    re.IGNORECASE,
)
_NO_MORE_ZH = ("不用", "沒有了", "夠了", "好了", "不要了", "這樣就好")


def start_restaurant(*, student_id: str | None = None,
                     max_items: int = DEFAULT_MAX_ITEMS) -> GameState:
    """開一局點餐時間。菜單提示優先給間隔重複到期的食物詞。"""
    try:
        limit = max(1, int(max_items))
    except (TypeError, ValueError):
        limit = DEFAULT_MAX_ITEMS
    return GameState(
        game="restaurant", topic="food", step="greet", target_count=limit,
        hints=_due_first("food", student_id),
    )


def restaurant_prompt(state: GameState) -> Line:
    """店員的招呼語，就是課綱 Ordering food & drinks 的標準句型。"""
    menu = "、".join(state.hints) if state.hints else "漢堡、果汁"
    return Line(
        zh=f"歡迎光臨！今天想吃什麼呢？可以說 I want a …（例如 {menu}）",
        en="What would you like?",
    )


def judge_restaurant(state: GameState, student_text) -> GameTurn:
    """處理一次點餐。純規則、離線、不拋。"""
    try:
        return _judge_restaurant(state, student_text)
    except Exception:
        _log.exception("judge_restaurant 失敗，回中性提示")
        return GameTurn(state=state, correct=False,
                        reply_zh="不好意思，我沒聽清楚，可以再說一次嗎？")


def _checkout(state: GameState, turns: int) -> GameTurn:
    """結帳：把點過的東西唸一遍。一樣都沒點也要說得通。"""
    done_state = replace(state, turns=turns, done=True, step="done")
    if not state.order:
        return GameTurn(
            state=done_state, correct=True, done=True,
            reply_zh="好的，那今天先這樣！下次想吃什麼再告訴我喔。",
            reply_en="OK! See you next time.",
        )
    items = "、".join(state.order)
    en_items = " and ".join(
        str(scaffold.VOCAB[w].get("np", scaffold.VOCAB[w]["en"])) for w in state.order
    )
    return GameTurn(
        state=done_state, correct=True, done=True,
        reply_zh=f"好的！您點了：{items}，馬上為您準備！",
        reply_en=f"You want {en_items}. Coming right up!",
    )


def _judge_restaurant(state: GameState, student_text) -> GameTurn:
    if state.done:
        return GameTurn(state=state, correct=False,
                        reply_zh="今天的點餐已經結束囉！要不要再玩一次？")

    text = _safe_text(student_text)
    turns = state.turns + 1

    # 先看是不是「不用了」——它會出現在任何階段
    if text and (_NO_MORE_EN.search(text) or any(w in text for w in _NO_MORE_ZH)):
        return _checkout(state, turns)

    word = _detect_vocab(text)

    if word is None:
        return GameTurn(
            state=replace(state, turns=turns), correct=False,
            reply_zh="不好意思，可以再說一次嗎？說說看 I want a …",
        )

    info = scaffold.VOCAB[word]

    if info["cat"] != "food":
        return GameTurn(
            state=replace(state, turns=turns), correct=False, word=word,
            reply_zh=f"「{word}」不是可以吃的東西喔！我們這裡有食物和飲料，再想想看？",
        )

    if word in state.order:
        return GameTurn(
            state=replace(state, turns=turns), correct=False, word=word,
            reply_zh=f"「{word}」已經點過了，還想要別的嗎？",
        )

    order = state.order + (word,)
    np = info.get("np", info["en"])
    # 點餐句型由 np 組出來，**不用詞條的 sent**。
    # sent 是通用例句（「I want to eat some bread.」），點餐時要教的是課綱
    # Ordering food & drinks 的標準句型「I want some bread.」。
    # 冠詞仍然來自 np，所以不可數詞不會變成 "a bread"。
    target = f"I want {np}."

    if len(order) >= state.target_count:
        # 點滿了直接結帳，不要讓孩子一直加點
        return _checkout(replace(state, order=order), turns)

    return GameTurn(
        state=replace(state, order=order, turns=turns, step="more"),
        correct=True, word=word, target_en=target,
        reply_zh=f"好的，一份「{word}」！還要別的嗎？",
        reply_en=f"OK, {np}. Anything else?",
    )


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


# ---------------------------------------------------------------------------
# 對外目錄：前端拿它畫按鈕，pipeline 拿它分派
#
# 放在這裡而不是前端硬編：兩份清單遲早不同步，而不同步的症狀是
# 「按鈕按下去沒反應」——現場最難查的那種。
# ---------------------------------------------------------------------------

GAMES = (
    {
        "kind": "i_spy",
        "zh": "火眼金睛",
        "en": "I Spy",
        "en_pattern": "I see a ___.",
        "function": "Naming common toys and household objects",
        "desc": "看到什麼就說出來，一關找五個",
    },
    {
        "kind": "guess_who",
        "zh": "猜猜我是誰",
        "en": "20 Questions",
        "en_pattern": "Is it ___?",
        "function": "Asking about abilities",
        "desc": "我想一個東西，你用 Yes/No 問句猜",
    },
    {
        "kind": "restaurant",
        "zh": "點餐時間",
        "en": "Restaurant",
        "en_pattern": "I want a ___.",
        "function": "Ordering food & drinks",
        "desc": "我當店員，你來點餐",
    },
    {
        "kind": "spell_along",
        "zh": "背單字",
        "en": "Spell Along",
        "en_pattern": "A, P, P, L, E,",
        "function": "Spelling and reading aloud familiar words",
        "desc": "我念單字、拼字母、說例句，你跟著念",
    },
)

GAME_KINDS = tuple(g["kind"] for g in GAMES)

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
_JUDGES = {
    "i_spy": judge_i_spy,
    "guess_who": judge_guess_who,
    "restaurant": judge_restaurant,
}


def start(kind: str, **kwargs) -> GameState:
    """依種類開一局。未知種類拋 ValueError——打錯字要當場失敗。"""
    if kind not in _STARTERS:
        raise ValueError(f"未知的遊戲：{kind!r}（可用：{GAME_KINDS}）")
    return _STARTERS[kind](**kwargs)


def prompt(state: GameState) -> Line:
    """該局的開場白。"""
    fn = _PROMPTS.get(getattr(state, "game", ""))
    return fn(state) if fn else Line(zh="我們來玩個遊戲吧！")


def judge(state: GameState, student_text) -> GameTurn:
    """把一句話交給該局的判定函式。"""
    fn = _JUDGES.get(getattr(state, "game", ""))
    if fn is None:
        return GameTurn(state=state, correct=False,
                        reply_zh="這個遊戲我還不會玩，換一個好嗎？")
    return fn(state, student_text)
