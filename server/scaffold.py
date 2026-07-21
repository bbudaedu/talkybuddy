"""規則式雙語鷹架引擎（確定性主幹，永遠可用）。

依 CONTRACTS.md 契約實作：
- ScaffoldResult / respond / split_tts_segments / compute_scores / safety_check / FALLBACK_LINES
- 詞庫 ≥30 個國小常用詞（食物/學校/動物/家庭/動作/顏色，中→英）
- 三種輸入路徑：中英夾雜 / 純中文 / 純英文，皆產出鼓勵＋目標英文句
- 禁詞 ≥10 條（暴力/自傷/成人/個資），命中回安撫話術
- 僅使用標準函式庫，零第三方依賴，可獨立測試
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 資料結構
# ---------------------------------------------------------------------------

@dataclass
class ScaffoldResult:
    reply_text: str                          # 顯示用完整回應（繁中+英文）
    tts_segments: list = field(default_factory=list)  # [("zh", "..."), ("en", "...")]
    scores: dict = field(default_factory=dict)         # {"fluency","vocabulary","grammar"} 各 0-100
    safety_triggered: bool = False
    target_sentence: str | None = None       # 要學生跟讀的英文句
    matched: bool = True                     # 這輪是否真的命中詞庫/正確英文（供 pipeline 追蹤連續卡關）


# ---------------------------------------------------------------------------
# 兜底話術（pipeline 於 ASR 低信心時輪替使用）
# ---------------------------------------------------------------------------

FALLBACK_LINES: list[str] = [
    "我沒有聽清楚，可以再說一次嗎？",
    "嗯嗯，再靠近一點、慢慢說，我在聽喔！",
    "可以再大聲一點點嗎？我很想聽你說！",
    "沒關係，我們再試一次，你一定可以的！",
]


# ---------------------------------------------------------------------------
# 詞庫：中文 → (英文, 分類, 名詞片語, 目標英文句)
# 名詞片語（np）用於中英夾雜替換；目標句（sent）用於純中文引導。
# 分類：food / school / animal / family / action / color
# ---------------------------------------------------------------------------

VOCAB: dict[str, dict] = {
    # 食物（8）
    "蘋果":   {"en": "apple",     "cat": "food",   "np": "an apple",      "sent": "I want to eat an apple."},
    "香蕉":   {"en": "banana",    "cat": "food",   "np": "a banana",      "sent": "I want to eat a banana."},
    "麵包":   {"en": "bread",     "cat": "food",   "np": "some bread",    "sent": "I want to eat some bread."},
    "牛奶":   {"en": "milk",      "cat": "food",   "np": "some milk",     "sent": "I want to drink some milk."},
    "雞蛋":   {"en": "egg",       "cat": "food",   "np": "an egg",        "sent": "I want to eat an egg."},
    "米飯":   {"en": "rice",      "cat": "food",   "np": "some rice",     "sent": "I want to eat some rice."},
    "水":     {"en": "water",     "cat": "food",   "np": "some water",    "sent": "I want to drink some water."},
    "橘子":   {"en": "orange",    "cat": "food",   "np": "an orange",     "sent": "I want to eat an orange."},
    # 學校（8）
    "學校":   {"en": "school",    "cat": "school", "np": "school",        "sent": "I want to go to school."},
    "書包":   {"en": "backpack",  "cat": "school", "np": "a backpack",    "sent": "This is my backpack."},
    "書":     {"en": "book",      "cat": "school", "np": "a book",        "sent": "This is my book."},
    "鉛筆":   {"en": "pencil",    "cat": "school", "np": "a pencil",      "sent": "This is my pencil."},
    "橡皮擦": {"en": "eraser",    "cat": "school", "np": "an eraser",     "sent": "This is my eraser."},
    "老師":   {"en": "teacher",   "cat": "school", "np": "my teacher",    "sent": "I like my teacher."},
    "同學":   {"en": "classmate", "cat": "school", "np": "my classmate",  "sent": "I play with my classmates."},
    "教室":   {"en": "classroom", "cat": "school", "np": "the classroom", "sent": "I am in the classroom."},
    # 動物（7）
    "狗":     {"en": "dog",       "cat": "animal", "np": "a dog",         "sent": "I see a dog."},
    "貓":     {"en": "cat",       "cat": "animal", "np": "a cat",         "sent": "I see a cat."},
    "兔子":   {"en": "rabbit",    "cat": "animal", "np": "a rabbit",      "sent": "I see a rabbit."},
    "大象":   {"en": "elephant",  "cat": "animal", "np": "an elephant",   "sent": "I see an elephant."},
    "鳥":     {"en": "bird",      "cat": "animal", "np": "a bird",        "sent": "I see a bird."},
    "魚":     {"en": "fish",      "cat": "animal", "np": "a fish",        "sent": "I see a fish."},
    "獅子":   {"en": "lion",      "cat": "animal", "np": "a lion",        "sent": "I see a lion."},
    # 家庭（7）
    "媽媽":   {"en": "mom",       "cat": "family", "np": "my mom",        "sent": "I love my mom."},
    "爸爸":   {"en": "dad",       "cat": "family", "np": "my dad",        "sent": "I love my dad."},
    "哥哥":   {"en": "brother",   "cat": "family", "np": "my brother",    "sent": "I love my brother."},
    "姊姊":   {"en": "sister",    "cat": "family", "np": "my sister",     "sent": "I love my sister."},
    "爺爺":   {"en": "grandpa",   "cat": "family", "np": "my grandpa",    "sent": "I love my grandpa."},
    "奶奶":   {"en": "grandma",   "cat": "family", "np": "my grandma",    "sent": "I love my grandma."},
    "家":     {"en": "home",      "cat": "family", "np": "home",          "sent": "I want to go home."},
    # 動作（8）
    "吃":     {"en": "eat",       "cat": "action", "np": "eat",           "sent": "I want to eat."},
    "喝":     {"en": "drink",     "cat": "action", "np": "drink",         "sent": "I want to drink some water."},
    "去":     {"en": "go",        "cat": "action", "np": "go",            "sent": "I want to go to school."},
    "玩":     {"en": "play",      "cat": "action", "np": "play",          "sent": "I like to play."},
    "睡覺":   {"en": "sleep",     "cat": "action", "np": "sleep",         "sent": "I want to sleep."},
    "跑步":   {"en": "run",       "cat": "action", "np": "run",           "sent": "I like to run."},
    "畫畫":   {"en": "draw",      "cat": "action", "np": "draw",          "sent": "I like to draw."},
    "唱歌":   {"en": "sing",      "cat": "action", "np": "sing",          "sent": "I like to sing."},
    # 顏色（6）
    "紅色":   {"en": "red",       "cat": "color",  "np": "red",           "sent": "My favorite color is red."},
    "藍色":   {"en": "blue",      "cat": "color",  "np": "blue",          "sent": "My favorite color is blue."},
    "黃色":   {"en": "yellow",    "cat": "color",  "np": "yellow",        "sent": "My favorite color is yellow."},
    "綠色":   {"en": "green",     "cat": "color",  "np": "green",         "sent": "My favorite color is green."},
    "黑色":   {"en": "black",     "cat": "color",  "np": "black",         "sent": "My favorite color is black."},
    "白色":   {"en": "white",     "cat": "color",  "np": "white",         "sent": "My favorite color is white."},
}

# 中文詞依長度遞減排序（先匹配長詞，避免「書包」被「書」搶先）
_ZH_KEYS_BY_LEN = sorted(VOCAB.keys(), key=len, reverse=True)

# 英文單數名詞集合（供複數修正判斷）
_EN_NOUNS = {v["en"] for v in VOCAB.values() if v["cat"] in ("food", "school", "animal", "family")}


# ---------------------------------------------------------------------------
# 禁詞表（暴力/自傷/成人/個資），中文子字串比對＋英文詞邊界比對
# ---------------------------------------------------------------------------

FORBIDDEN_ZH: list[str] = [
    "殺", "自殺", "去死", "打死", "打人", "流血", "刀子", "槍", "炸彈",
    "毒品", "色情", "裸體", "脫衣服", "住址", "電話號碼", "身分證", "密碼",
]

FORBIDDEN_EN: list[str] = [
    "kill", "suicide", "die", "gun", "knife", "bomb",
    "drug", "sex", "naked", "porn", "password",
]

_SAFETY_REPLY_ZH = (
    "沒關係，我在這裡陪你。我們一起深呼吸，聊聊今天開心的事，好嗎？"
    "如果心裡有煩惱，記得告訴老師或家人喔。"
)
_SAFETY_REPLY_EN = "Let's take a deep breath together."

# 各路徑的繁中鼓勵話術（依輸入長度決定索引，確定性輪替）
_PRAISE_MIXED = [
    "你會把中文和英文放在一起說，好棒！整句英文可以這樣說：",
    "哇，你用了英文耶！我們把整句都變成英文說說看：",
    "很好喔！這句話全部用英文說是：",
]
_PRAISE_ZH = [
    "你說得很清楚！我們用英文說說看：",
    "好耶！這句話的英文是這樣說的，跟我唸：",
    "很棒的想法！用英文可以這樣說：",
]
_PRAISE_EN_FIX = [
    "說得很好！有一個小地方修一下會更棒，跟我唸：",
    "好厲害，幾乎全對了！更棒的說法是：",
    "很接近了！我們一起唸正確的句子：",
]
_PRAISE_EN_GOOD = [
    "太棒了，你說得很標準！",
    "哇，完全正確，好厲害！",
    "說得真好，給你一個讚！",
]

# 純英文有修正時的糾錯型態輪替（依 turn_index，呼應 Lyster & Ranta 糾錯回饋分類）：
# recast（示範正確講法）/ metalinguistic（用好奇/小秘密的口吻點出規則再帶讀）/
# clarification（肯定聽懂了、一起升級說法），比起永遠只用同一種「直接改好」更
# 像真人老師。三種型態的措辭都刻意避開「訂正錯誤」的口吻（不說「文法小提醒」
# 「你是說…嗎」這類聽起來像被抓錯的說法），改用好奇心／一起玩／升級的框架，
# 讓學生感受到的是「我們一起發現新東西」而不是「你講錯了」。
_FEEDBACK_STYLES = ["recast", "metalinguistic", "clarification"]

_METALINGUISTIC_HINTS = {
    "article": "偷偷告訴你一個小秘密：這個字聽起來像母音開頭，前面要換成 an 才會更順喔！",
    "plural": "偷偷告訴你一個小秘密：講到兩個以上，英文單字後面要加 s 才夠力喔！",
}
_CLARIFICATION_LEADS = [
    "我聽懂你想說什麼了！我們再多學一個更厲害的說法：",
    "你的意思我完全懂！偷偷教你一個更酷的講法：",
    "說得通喔！我們再加一點點，會更厲害：",
]

# 學生連續卡關（同一目標連續答不出來）時的開場，取代一直重複同一句完整目標句。
# 刻意不用「沒關係／別擔心」這類需要被安慰的措辭——那預設了學生做錯了什麼；
# 改成「換個好玩的方式」，讓切換成二選一/單字提示感覺像主動邀請玩遊戲，
# 而不是因為答不出來被降級處理。
_PRAISE_STUCK = [
    "來，我們換個好玩的方式：",
    "咦，換我當出題王，你選選看：",
    "我們來玩個小遊戲，你選一個：",
]

# 純英文無誤時的延伸問句（依分類）
_EXTENSION_QUESTIONS = {
    "food":   "What is your favorite food?",
    "school": "What is in your backpack?",
    "animal": "What animal do you like?",
    "family": "Do you love your family?",
    "action": "What do you like to play?",
    "color":  "What color do you like?",
}
_EXTENSION_DEFAULT = "Can you tell me more?"


# ---------------------------------------------------------------------------
# 基礎工具
# ---------------------------------------------------------------------------

def _is_zh_char(ch: str) -> bool:
    """以 unicode 範圍判斷是否為中文字元（含 CJK 標點與全形符號）。"""
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF      # CJK 統一漢字
        or 0x3400 <= code <= 0x4DBF   # CJK 擴充 A
        or 0x3000 <= code <= 0x303F   # CJK 標點
        or 0xFF00 <= code <= 0xFFEF   # 全形符號
    )


def _has_zh(text: str) -> bool:
    return any(_is_zh_char(ch) for ch in text)


def _has_en(text: str) -> bool:
    return re.search(r"[A-Za-z]", text) is not None


# a/an 特例：拼字開頭是母音但發音是子音（用 a），或相反（用 an）
_A_EXCEPTIONS = ("uni", "use", "user", "one", "euro")
_AN_EXCEPTIONS = ("hour", "honest")


def _article_for(word: str) -> str:
    """依（近似的）母音發音規則決定 a 或 an。"""
    w = word.lower()
    if any(w.startswith(p) for p in _AN_EXCEPTIONS):
        return "an"
    if any(w.startswith(p) for p in _A_EXCEPTIONS):
        return "a"
    return "an" if w[:1] in "aeiou" else "a"


def _pluralize(word: str) -> str:
    """簡單英文複數規則。"""
    if re.search(r"(s|x|z|ch|sh)$", word):
        return word + "es"
    if re.search(r"[^aeiou]y$", word):
        return word[:-1] + "ies"
    return word + "s"


def _polish_english(text: str) -> tuple[str, int, list[str]]:
    """對英文句做簡單修正，回傳（修正後句子, 實質修正數, 修正類型標籤）。

    實質修正：a/an 錯用（標籤 "article"）、數詞後漏複數（標籤 "plural"）、
    獨立小寫 i（標籤 "cap_i"）。非實質（不計數、不標籤）：首字大寫、句尾補句點、
    空白整理。標籤供 _respond_pure_en 挑選對應的文法提示（metalinguistic 回饋）。
    """
    fixes = 0
    tags: list[str] = []
    s = re.sub(r"\s+", " ", text).strip()

    # 獨立小寫 i → I
    s2 = re.sub(r"\bi\b", "I", s)
    if s2 != s:
        fixes += 1
        tags.append("cap_i")
    s = s2

    # a/an 修正（依下一個單字的母音發音）
    def _fix_article(m: re.Match) -> str:
        nonlocal fixes
        art, nxt = m.group(1), m.group(2)
        want = _article_for(nxt)
        if art.lower() != want:
            fixes += 1
            tags.append("article")
            return (want.capitalize() if art[0].isupper() else want) + " " + nxt
        return m.group(0)

    s = re.sub(r"\b([Aa]n?)\s+([A-Za-z]+)", _fix_article, s)

    # 數詞/量詞後的已知名詞補複數
    def _fix_plural(m: re.Match) -> str:
        nonlocal fixes
        num, noun = m.group(1), m.group(2)
        if noun.lower() in _EN_NOUNS:
            fixes += 1
            tags.append("plural")
            return num + " " + _pluralize(noun)
        return m.group(0)

    s = re.sub(
        r"\b(two|three|four|five|six|seven|eight|nine|ten|many)\s+([a-z]+)\b",
        _fix_plural, s,
    )

    # 首字大寫＋句尾標點（不計入實質修正）
    if s and s[0].islower():
        s = s[0].upper() + s[1:]
    if s and s[-1] not in ".!?":
        s += "."
    return s, fixes, tags


def _find_zh_vocab(text: str) -> list[str]:
    """找出文字中出現的中文詞庫詞，依出現位置排序（長詞優先卡位）。"""
    found: list[tuple[int, str]] = []
    taken: set[int] = set()
    for key in _ZH_KEYS_BY_LEN:
        start = 0
        while True:
            idx = text.find(key, start)
            if idx < 0:
                break
            span = set(range(idx, idx + len(key)))
            if not (span & taken):
                found.append((idx, key))
                taken |= span
            start = idx + 1
    found.sort()
    return [k for _, k in found]


def _strip_zh(text: str) -> str:
    """移除殘餘中文字元並整理空白。"""
    s = "".join(" " if _is_zh_char(ch) else ch for ch in text)
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# 契約函式
# ---------------------------------------------------------------------------

def safety_check(text: str) -> bool:
    """禁詞檢查，True = 命中（中文子字串、英文詞邊界）。"""
    if any(w in text for w in FORBIDDEN_ZH):
        return True
    low = text.lower()
    return any(re.search(r"\b" + re.escape(w) + r"\b", low) for w in FORBIDDEN_EN)


def split_tts_segments(text: str) -> list[tuple[str, str]]:
    """把中英混句切成 [("zh", "..."), ("en", "...")] 段落（供 LLM 回覆重用）。

    以字元的 unicode 範圍分類，中性字元（空白等）依附目前段落。
    """
    segments: list[tuple[str, str]] = []
    cur_lang: str | None = None
    buf: list[str] = []

    def _flush() -> None:
        nonlocal buf
        seg = "".join(buf).strip()
        if seg and re.search(r"[0-9A-Za-z㐀-䶿一-鿿]", seg):
            segments.append((cur_lang, seg))
        buf = []

    for ch in text:
        if _is_zh_char(ch):
            lang = "zh"
        elif ch.isascii() and (ch.isalnum() or ch in string.punctuation):
            lang = "en"
        else:
            lang = None  # 空白等中性字元，依附目前段落
        if lang is not None and lang != cur_lang:
            if cur_lang is not None:
                _flush()
            cur_lang = lang
        buf.append(ch)
    if cur_lang is not None:
        _flush()
    return segments


def compute_scores(student_text: str) -> dict:
    """依規則計算三維分數（0-100，確定性）。"""
    text = student_text.strip()
    en_words = re.findall(r"[A-Za-z]+", text)
    zh_chars = [ch for ch in text if 0x4E00 <= ord(ch) <= 0x9FFF]
    total_units = len(en_words) + len(zh_chars)
    en_ratio = (len(en_words) / total_units) if total_units else 0.0

    # 流暢度：英文比例＋句長
    fluency = 30 + int(en_ratio * 45) + min(20, len(en_words) * 4)

    # 詞彙量：命中詞庫（中文詞＋英文詞）
    hits = len(_find_zh_vocab(text))
    low_words = {w.lower() for w in en_words}
    hits += sum(1 for v in VOCAB.values() if v["en"] in low_words)
    vocabulary = 35 + min(50, hits * 15) + min(10, len(en_words))

    # 文法：以簡單錯誤扣分（a/an 錯用、獨立小寫 i、首字小寫）
    grammar = 45 + int(en_ratio * 35)
    for m in re.finditer(r"\b([Aa]n?)\s+([A-Za-z]+)", text):
        if m.group(1).lower() != _article_for(m.group(2)):
            grammar -= 15
    if re.search(r"\bi\b", text):
        grammar -= 5
    if text and text[0].isalpha() and text[0].islower():
        grammar -= 5
    if en_words and text[-1] in ".!?":
        grammar += 5

    clamp = lambda v: max(0, min(100, v))
    return {
        "fluency": clamp(fluency),
        "vocabulary": clamp(vocabulary),
        "grammar": clamp(grammar),
    }


def _pick(lines: list[str], turn_index: int) -> str:
    """依對話輪次做確定性輪替，避免學生講很像的話時鼓勵語卡在同一句。"""
    return lines[turn_index % len(lines)]


_DETERMINERS = r"(?:a|an|the|my|your|his|her|our|their|this|that|some)"


def _substitute_zh(text: str) -> str:
    """把中英夾雜句中的中文詞換成英文名詞片語；前面已有限定詞則用裸字。"""
    s = text
    for key in _ZH_KEYS_BY_LEN:
        if key not in s:
            continue
        entry = VOCAB[key]
        pattern = re.compile(
            r"(\b" + _DETERMINERS + r"\s+)?" + re.escape(key), re.IGNORECASE
        )

        def _rep(m: re.Match) -> str:
            if m.group(1):  # 前面已有 a/my/the... → 用裸字，交給 a/an 修正
                return m.group(1) + entry["en"]
            return entry["np"]

        s = pattern.sub(_rep, s)
    return s


def _respond_mixed(
    text: str,
    turn_index: int,
    lesson_target_sentence: str | None,
    stuck_hint: str | None = None,
) -> tuple[str, str, bool]:
    """中英夾雜：替換中文詞產完整英文句。回（鼓勵語, 英文句, 是否命中）。"""
    replaced = _substitute_zh(text)
    replaced = _strip_zh(replaced)  # 清掉詞庫外的殘餘中文
    sentence, _, _ = _polish_english(replaced)
    if len(re.findall(r"[A-Za-z]+", sentence)) < 2:
        # 替換後太破碎，退回純中文路徑的邏輯
        return _respond_pure_zh(text, turn_index, lesson_target_sentence, stuck_hint)
    return _pick(_PRAISE_MIXED, turn_index), sentence, True


def _respond_pure_zh(
    text: str,
    turn_index: int,
    lesson_target_sentence: str | None,
    stuck_hint: str | None = None,
) -> tuple[str, str, bool]:
    """純中文：找詞庫詞給鼓勵＋整句英文；找不到時優先用今日課程目標句引導，
    完全沒有課程資訊（lesson_target_sentence 為 None）才退回通用引導句。

    stuck_hint（可選）：pipeline 偵測到學生連續兩輪都沒命中詞庫時傳入的簡化
    提示（見 diagnose.py 的 fallback_prompt）。有給時優先用它取代常態的
    lesson_target_sentence／通用句，並換一組「我們簡單一點」的開場，
    避免學生一直卡住還被塞同一句完整目標句。回傳的 matched 一律 False
    （這個分支代表這一輪學生沒有真正命中任何詞庫內容）。
    """
    matches = _find_zh_vocab(text)
    if matches:
        # 優先選最長的詞（名詞多為雙字，資訊量較高）
        key = max(matches, key=len)
        entry = VOCAB[key]
        praise = f"你說到「{key}」！它的英文是 {entry['en']} 喔。" + _pick(_PRAISE_ZH, turn_index)
        return praise, entry["sent"], True
    if stuck_hint:
        return _pick(_PRAISE_STUCK, turn_index), stuck_hint, False
    fallback_sentence = lesson_target_sentence or "How are you today?"
    return "我聽到了！我們一起用英文說說看，跟我唸：", fallback_sentence, False


def _respond_pure_en(
    text: str, turn_index: int, lesson_topic: str | None
) -> tuple[str, str, bool]:
    """純英文：簡單文法修正或稱讚＋延伸問句。回（鼓勵語, 目標英文句, 是否命中）。

    延伸問句優先依這句話裡命中的詞彙分類；完全沒命中時改用今日課程主題
    （lesson_topic），比起固定的 _EXTENSION_DEFAULT 更貼近今天在練的內容。
    有實質修正時依 turn_index 輪替糾錯型態（recast/metalinguistic/
    clarification），而不是每次都用同一種「直接改好」的說法。
    純英文一律視為 matched=True——學生本來就在嘗試用英文表達，即使還有錯，
    這不算「卡關」（卡關特指完全沒有用英文/詞庫內容回應的情況）。
    """
    sentence, fixes, tags = _polish_english(text)
    if fixes > 0:
        style = _FEEDBACK_STYLES[turn_index % len(_FEEDBACK_STYLES)]
        if style == "metalinguistic":
            hint = next((_METALINGUISTIC_HINTS[t] for t in tags if t in _METALINGUISTIC_HINTS), None)
            lead = hint if hint else _pick(_PRAISE_EN_FIX, turn_index)
        elif style == "clarification":
            lead = _pick(_CLARIFICATION_LEADS, turn_index)
        else:
            lead = _pick(_PRAISE_EN_FIX, turn_index)
        return lead, sentence, True
    # 無誤：稱讚＋依詞彙分類給延伸問句
    low_words = {w.lower() for w in re.findall(r"[A-Za-z]+", text)}
    question = None
    for v in VOCAB.values():
        if v["en"] in low_words:
            question = _EXTENSION_QUESTIONS.get(v["cat"], _EXTENSION_DEFAULT)
            break
    if question is None:
        question = _EXTENSION_QUESTIONS.get(lesson_topic, _EXTENSION_DEFAULT)
    return _pick(_PRAISE_EN_GOOD, turn_index) + "再回答我一個問題：", question, True


def respond(
    student_text: str,
    turn_index: int = 0,
    lesson_topic: str | None = None,
    lesson_target_sentence: str | None = None,
    stuck_hint: str | None = None,
) -> ScaffoldResult:
    """鷹架引擎主入口：安全檢查 → 路徑分流 → 組回應。

    turn_index：本次連線內的對話輪次（由 pipeline 傳入），供鼓勵語輪替，
    避免學生講很像的話時鼓勵語卡在同一句；預設 0（向後相容）。
    lesson_topic / lesson_target_sentence：由 server/lesson.py 依當前診斷/
    profile 選出的「今日主題」與「今日目標句」；只在學生輸入完全無詞庫命中
    時作為引導依據，不覆蓋既有規則。兩者皆為 None 時行為與現況完全一致。
    stuck_hint：pipeline 偵測到學生連續兩輪都沒命中（見 ScaffoldResult.matched）
    時傳入的簡化提示句，只在純中文無詞庫命中的分支生效；None 時行為與現況
    完全一致（向後相容）。
    """
    text = (student_text or "").strip()

    # 空輸入：直接兜底
    if not text:
        line = FALLBACK_LINES[0]
        return ScaffoldResult(
            reply_text=line,
            tts_segments=[("zh", line)],
            scores={"fluency": 0, "vocabulary": 0, "grammar": 0},
            safety_triggered=False,
            target_sentence=None,
            matched=False,
        )

    # 禁詞：回安撫話術，不給學習目標句
    if safety_check(text):
        reply = _SAFETY_REPLY_ZH + " " + _SAFETY_REPLY_EN
        return ScaffoldResult(
            reply_text=reply,
            tts_segments=[("zh", _SAFETY_REPLY_ZH), ("en", _SAFETY_REPLY_EN)],
            scores=compute_scores(text),
            safety_triggered=True,
            target_sentence=None,
            matched=True,  # 安撫話術不算「卡關」，不應觸發降階提示
        )

    has_zh, has_en = _has_zh(text), _has_en(text)
    if has_zh and has_en:
        praise, target, matched = _respond_mixed(text, turn_index, lesson_target_sentence, stuck_hint)
    elif has_zh:
        praise, target, matched = _respond_pure_zh(text, turn_index, lesson_target_sentence, stuck_hint)
    else:
        praise, target, matched = _respond_pure_en(text, turn_index, lesson_topic)

    reply = praise + " " + target
    return ScaffoldResult(
        reply_text=reply,
        tts_segments=split_tts_segments(reply),
        scores=compute_scores(text),
        safety_triggered=False,
        target_sentence=target,
        matched=matched,
    )


# ---------------------------------------------------------------------------
# 即時 S2S（Nova Sonic）鷹架 system prompt 組裝
# 靜態框架寫死 + 動態今日目標句/directive 折入；重用 guardrails 兒童安全護欄。
# ---------------------------------------------------------------------------

_LIVE_STATIC_FRAME = (
    "你是陪台灣國小學生練習說話的英文教練企鵝「說說學伴」，溫暖、有耐心、像朋友。"
    "一、主要用繁體中文（台灣用語）回覆，語氣自然、每次回覆不超過兩句話。"
    "二、這一場的任務：帶孩子練「今天的主題」和「今天的目標句」，用「跟讀」的方式"
    "一句一句練，不要漫無目的閒聊。"
    "三、跟讀迴圈，每一輪照這個節奏：先用中文自然引出情境；再清楚放慢說出一句短英文"
    "（今天的目標句或它的小變化）；邀請孩子跟著說一次；孩子說完先具體誇獎，"
    "再溫和修正一兩個發音或用詞並示範一次；孩子跟上就換下一句或延伸一點，"
    "卡住就把句子拆更短、放更慢、再帶一次，用好奇、邀請玩遊戲的語氣，"
    "不要讓孩子覺得自己退步或講錯了。"
    "四、鷹架原則：先示範再邀請、給比孩子程度略高一點的內容（i+1）、"
    "說對給具體正向回饋、說錯溫和重述不指責、循序漸進不催促。"
    "五、不要每次都用同一句開場白，一次只給一句，不要長篇，不使用 markdown 符號或 emoji。"
    "六、務必明顯放慢說話速度，比平常慢很多：一個字一個字清楚地說，"
    "字與字、詞與詞之間都稍微停頓，像對三、四歲小小孩慢慢講話一樣。寧可太慢也不要快。"
)


def build_live_system_prompt(target_sentence, directive, topic=None) -> str:
    """組裝即時陪聊 system prompt（教練角色 + 跟讀迴圈）。

    - target_sentence：今日目標句（可 None）→ 提示教練帶讀、請孩子跟讀。
    - directive：B 軸 companion_directive 注入字串（可 None，見
      diagnose.format_directive_for_prompt）→ 折入本輪教學策略。
    - topic：今日主題（可 None）→ 提示先引出主題情境。
    None/空欄位一律略過，向後相容。
    """
    from server import guardrails

    parts = [_LIVE_STATIC_FRAME, guardrails.CHILD_SAFETY_CLAUSE]
    if topic and str(topic).strip():
        parts.append(f"今天的主題是「{str(topic).strip()}」，先用中文自然帶出這個主題的情境。")
    if target_sentence and str(target_sentence).strip():
        parts.append(f"今天想邀請孩子開口說的英文句是：「{str(target_sentence).strip()}」，"
                     "帶讀時放慢、一次一句，請孩子跟著說一次，再給回饋。")
    if directive and str(directive).strip():
        parts.append(str(directive).strip())
    return "".join(parts)
