# -*- coding: utf-8 -*-
"""seed_units.py — Unit 3~6 教材種子（material agent 的實際產出）。

為什麼要固化成程式碼而不是靠上傳：
    Fargate 沒有持久儲存，容器每次重啟 SQLite 就重置，靠 /api/material 手動
    上傳的教材會消失。決賽現場不能依賴「上台前記得先傳一次教材」。

資料來源：hackathon/課程Unit 3~6.md，逐單元丟進
    server.agents.material.extract_vocab()（走 AgentCore）產出，
    2026-08-01 重跑。**不是手抄的**——topic 與詞條都是 agent 寫的。
    要重新產生就重跑一次，不要在這裡手改個別欄位：這份資料的價值在於它
    真的是 agent 對課本的產出，手改過一次就再也沒辦法主張這件事。

    Unit 5 只有 5 條是正常的：breakfast/lunch/dinner 早就在課綱 VOCAB 裡，
    被 register_material_vocab 的「zh 已存在一律拒絕」正確擋掉（教材只能新增、
    不能覆蓋既有詞條）。thirty／late 之前也不在裡面，那不正常——它們被
    ``_article_is_consistent`` 對單字 np 的誤殺擋掉了（形容詞與數詞寫不出名詞
    片語）。根因修掉後這次重跑就收得到，Unit 3~6 的分類也不再硬塞
    （天氣→weather、房間→place、時刻→time，見 scaffold._MATERIAL_CATS）。
"""
from __future__ import annotations

# 課程進度：一週一個單元，本週是第 4 週（Unit 6）。單元定義（編號、週次、標題、
# 核心句型、單字表）取自 hackathon/課程Unit 3~6.md。
#
# 為什麼放在這裡而不是 demo_class.py：這是**教材事實**（老師教到哪一課、那一課
# 的核心句型是什麼），不是展示用的模擬資料。原本它跟十個化名學生一起放在
# demo_class.py，導致 server/lesson.py 想依單元選帶讀句時，只能去 import 一個
# 明確標示「概念展示層、不要當正式起點」的模組。demo_class.py 現在改成從這裡
# 匯入，學生名單那些真的是編造的資料才留在它那邊。
UNITS = [
    {"no": 3, "week": 1, "title": "How's the Weather?", "zh": "天氣如何？",
     "pattern": "How's the weather today?",
     "words": ["sunny", "rainy", "cloudy", "windy", "snowy", "cold", "hot", "warm"]},
    {"no": 4, "week": 2, "title": "Where Are You?", "zh": "你在哪裡？",
     "pattern": "Where are you?",
     "words": ["living room", "dining room", "bedroom", "kitchen", "bathroom",
               "in", "on", "under"]},
    {"no": 5, "week": 3, "title": "What Time Is It?", "zh": "現在幾點了？",
     "pattern": "What time is it?",
     "words": ["time", "thirty", "get up", "breakfast", "lunch", "dinner", "late"]},
    {"no": 6, "week": 4, "title": "What Are You Doing?", "zh": "你在做什麼？",
     "pattern": "What are you doing?",
     "words": ["eating", "drinking", "reading", "writing", "sleeping",
               "singing", "dancing"]},
]
CURRENT_UNIT_NO = 6          # 老師本週在教的單元
_UNIT_BY_NO = {u["no"]: u for u in UNITS}


def unit(no: int) -> dict:
    """取單元定義；查無一律回第一單元（呼叫端不必處理 None）。"""
    return _UNIT_BY_NO.get(no, UNITS[0])


def current_unit() -> dict:
    """老師本週在教的那一課。"""
    return unit(CURRENT_UNIT_NO)


UNIT_MATERIALS = {
    3: {
        "topic": "天氣描述與戶外活動",
        "entries": [
            {"zh": "晴朗的", "en": "sunny", "cat": "weather", "np": "a sunny day", "sent": "It is sunny today."},
            {"zh": "下雨的", "en": "rainy", "cat": "weather", "np": "a rainy day", "sent": "It is rainy outside."},
            {"zh": "多雲的", "en": "cloudy", "cat": "weather", "np": "a cloudy day", "sent": "It is cloudy now."},
            {"zh": "風大的", "en": "windy", "cat": "weather", "np": "a windy day", "sent": "It is too windy today."},
            {"zh": "下雪的", "en": "snowy", "cat": "weather", "np": "a snowy day", "sent": "We like snowy days."},
            {"zh": "寒冷的", "en": "cold", "cat": "weather", "np": "a cold day", "sent": "It is cold outside."},
            {"zh": "熱的", "en": "hot", "cat": "weather", "np": "a hot day", "sent": "It is very hot today."},
            {"zh": "溫暖的", "en": "warm", "cat": "weather", "np": "a warm day", "sent": "It is sunny and warm."},
        ],
    },
    4: {
        "topic": "家中各房間與位置介系詞",
        "entries": [
            {"zh": "客廳", "en": "living room", "cat": "place", "np": "the living room", "sent": "Dad is in the living room."},
            {"zh": "餐廳", "en": "dining room", "cat": "place", "np": "the dining room", "sent": "We eat dinner in the dining room."},
            {"zh": "臥室", "en": "bedroom", "cat": "place", "np": "the bedroom", "sent": "I am in the bedroom."},
            {"zh": "廚房", "en": "kitchen", "cat": "place", "np": "the kitchen", "sent": "Mom is cooking in the kitchen."},
            {"zh": "浴室", "en": "bathroom", "cat": "place", "np": "the bathroom", "sent": "Wash your hands in the bathroom."},
            {"zh": "在……裡面", "en": "in", "cat": "place", "np": "in", "sent": "The cat is in the box."},
            {"zh": "在……上面", "en": "on", "cat": "place", "np": "on", "sent": "The book is on the desk."},
            {"zh": "在……下面", "en": "under", "cat": "place", "np": "under", "sent": "The ball is under the chair."},
        ],
    },
    5: {
        "topic": "認識時間與一日三餐",
        "entries": [
            {"zh": "時間", "en": "time", "cat": "time", "np": "the time", "sent": "What time is it?"},
            {"zh": "點鐘", "en": "o'clock", "cat": "time", "np": "o'clock", "sent": "It is seven o'clock."},
            {"zh": "三十分", "en": "thirty", "cat": "time", "np": "thirty", "sent": "It is seven thirty."},
            {"zh": "起床", "en": "get up", "cat": "action", "np": "get up", "sent": "I get up at six o'clock."},
            {"zh": "遲到的", "en": "late", "cat": "time", "np": "late", "sent": "You are late."},
        ],
    },
    6: {
        "topic": "進行式動作表達（你在做什麼？）",
        "entries": [
            {"zh": "正在吃", "en": "eating", "cat": "action", "np": "eating", "sent": "He is eating an apple."},
            {"zh": "正在喝", "en": "drinking", "cat": "action", "np": "drinking", "sent": "She is drinking milk."},
            {"zh": "正在閱讀", "en": "reading", "cat": "action", "np": "reading", "sent": "I am reading a storybook."},
            {"zh": "正在書寫", "en": "writing", "cat": "action", "np": "writing", "sent": "The boy is writing his homework."},
            {"zh": "正在睡覺", "en": "sleeping", "cat": "action", "np": "sleeping", "sent": "The dog is sleeping."},
            {"zh": "正在唱歌", "en": "singing", "cat": "action", "np": "singing", "sent": "They are singing a song."},
            {"zh": "正在跳舞", "en": "dancing", "cat": "action", "np": "dancing", "sent": "She is dancing now."},
        ],
    },
}
