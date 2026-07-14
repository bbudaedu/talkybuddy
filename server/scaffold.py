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


def _polish_english(text: str) -> tuple[str, int]:
    """對英文句做簡單修正，回傳（修正後句子, 實質修正數）。

    實質修正：a/an 錯用、數詞後漏複數、獨立小寫 i。
    非實質（不計數）：首字大寫、句尾補句點、空白整理。
    """
    fixes = 0
    s = re.sub(r"\s+", " ", text).strip()

    # 獨立小寫 i → I
    s2 = re.sub(r"\bi\b", "I", s)
    if s2 != s:
        fixes += 1
    s = s2

    # a/an 修正（依下一個單字的母音發音）
    def _fix_article(m: re.Match) -> str:
        nonlocal fixes
        art, nxt = m.group(1), m.group(2)
        want = _article_for(nxt)
        if art.lower() != want:
            fixes += 1
            return (want.capitalize() if art[0].isupper() else want) + " " + nxt
        return m.group(0)

    s = re.sub(r"\b([Aa]n?)\s+([A-Za-z]+)", _fix_article, s)

    # 數詞/量詞後的已知名詞補複數
    def _fix_plural(m: re.Match) -> str:
        nonlocal fixes
        num, noun = m.group(1), m.group(2)
        if noun.lower() in _EN_NOUNS:
            fixes += 1
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
    return s, fixes


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


def _pick(lines: list[str], seed_text: str) -> str:
    """依輸入內容做確定性輪替，避免每次都同一句。"""
    return lines[sum(ord(c) for c in seed_text) % len(lines)]


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


def _respond_mixed(text: str) -> tuple[str, str]:
    """中英夾雜：替換中文詞產完整英文句。回（鼓勵語, 英文句）。"""
    replaced = _substitute_zh(text)
    replaced = _strip_zh(replaced)  # 清掉詞庫外的殘餘中文
    sentence, _ = _polish_english(replaced)
    if len(re.findall(r"[A-Za-z]+", sentence)) < 2:
        # 替換後太破碎，退回純中文路徑的邏輯
        return _respond_pure_zh(text)
    return _pick(_PRAISE_MIXED, text), sentence


def _respond_pure_zh(text: str) -> tuple[str, str]:
    """純中文：找詞庫詞給鼓勵＋整句英文；找不到給通用引導句。"""
    matches = _find_zh_vocab(text)
    if matches:
        # 優先選最長的詞（名詞多為雙字，資訊量較高）
        key = max(matches, key=len)
        entry = VOCAB[key]
        praise = f"你說到「{key}」！它的英文是 {entry['en']} 喔。" + _pick(_PRAISE_ZH, text)
        return praise, entry["sent"]
    return "我聽到了！我們一起用英文說說看，跟我唸：", "How are you today?"


def _respond_pure_en(text: str) -> tuple[str, str]:
    """純英文：簡單文法修正或稱讚＋延伸問句。回（鼓勵語, 目標英文句）。"""
    sentence, fixes = _polish_english(text)
    if fixes > 0:
        return _pick(_PRAISE_EN_FIX, text), sentence
    # 無誤：稱讚＋依詞彙分類給延伸問句
    low_words = {w.lower() for w in re.findall(r"[A-Za-z]+", text)}
    question = _EXTENSION_DEFAULT
    for v in VOCAB.values():
        if v["en"] in low_words:
            question = _EXTENSION_QUESTIONS.get(v["cat"], _EXTENSION_DEFAULT)
            break
    return _pick(_PRAISE_EN_GOOD, text) + "再回答我一個問題：", question


def respond(student_text: str) -> ScaffoldResult:
    """鷹架引擎主入口：安全檢查 → 路徑分流 → 組回應。"""
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
        )

    has_zh, has_en = _has_zh(text), _has_en(text)
    if has_zh and has_en:
        praise, target = _respond_mixed(text)
    elif has_zh:
        praise, target = _respond_pure_zh(text)
    else:
        praise, target = _respond_pure_en(text)

    reply = praise + " " + target
    return ScaffoldResult(
        reply_text=reply,
        tts_segments=split_tts_segments(reply),
        scores=compute_scores(text),
        safety_triggered=False,
        target_sentence=target,
    )


# ---------------------------------------------------------------------------
# 即時 S2S（Nova Sonic）鷹架 system prompt 組裝
# 靜態框架寫死 + 動態今日目標句/directive 折入；重用 guardrails 兒童安全護欄。
# ---------------------------------------------------------------------------

_LIVE_STATIC_FRAME = (
    "你是陪台灣國小學生練習說話的企鵝學伴「說說學伴」，溫暖、有耐心、像朋友。"
    "一、主要用繁體中文（台灣用語）回覆，語氣自然、每次回覆不超過兩句話。"
    "二、每一輪自然帶出一個簡單的英語單字或短句，鼓勵孩子跟著開口說一次（帶讀）。"
    "三、用鷹架學習法：先示範再邀請、給比孩子程度略高一點的內容（i+1）、"
    "孩子說對給具體正向回饋、說錯溫和重述不指責、循序漸進不催促。"
    "四、不使用 markdown 符號或 emoji。"
    "五、放慢說話速度、咬字清楚、一字一句慢慢說，像對小小孩講話一樣，不要講太快。"
)


def build_live_system_prompt(target_sentence, directive) -> str:
    """組裝即時陪聊 system prompt。

    - target_sentence：今日重點英文句（可 None）→ 提示學伴優先帶讀。
    - directive：B 軸 companion_directive 注入字串（可 None，見
      diagnose.format_directive_for_prompt）→ 折入本輪教學策略。
    """
    from server import guardrails

    parts = [_LIVE_STATIC_FRAME, guardrails.CHILD_SAFETY_CLAUSE]
    if target_sentence and str(target_sentence).strip():
        parts.append(f"今日想邀請孩子開口說的英文句是：「{str(target_sentence).strip()}」，"
                     "找機會自然帶讀，不必每句都用。")
    if directive and str(directive).strip():
        parts.append(str(directive).strip())
    return "".join(parts)
