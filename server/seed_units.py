# -*- coding: utf-8 -*-
"""seed_units.py — Unit 3~6 教材種子（material agent 的實際產出）。

為什麼要固化成程式碼而不是靠上傳：
    Fargate 沒有持久儲存，容器每次重啟 SQLite 就重置，靠 /api/material 手動
    上傳的教材會消失。決賽現場不能依賴「上台前記得先傳一次教材」。

資料來源：hackathon/課程Unit 3~6.md，逐單元丟進
    server.agents.material.extract_vocab()（走 AgentCore）產出，
    2026-08-01 執行。**不是手抄的**——topic 與詞條都是 agent 寫的，
    Unit 3 它還多抓到課本單字表沒有的 warm。

    Unit 5 只有 4 條是正常的：breakfast/lunch/dinner 早就在課綱 VOCAB 裡，
    被 register_material_vocab 的「zh 已存在一律拒絕」正確擋掉（教材只能新增、
    不能覆蓋既有詞條）。
"""
from __future__ import annotations

UNIT_MATERIALS = {
    3: {
        "topic": "天氣描述與戶外活動",
        "entries": [
            {"zh": "晴朗的", "en": "sunny", "cat": "color", "np": "a sunny day", "sent": "It is sunny today."},
            {"zh": "下雨的", "en": "rainy", "cat": "color", "np": "a rainy day", "sent": "It is rainy outside."},
            {"zh": "多雲的", "en": "cloudy", "cat": "color", "np": "a cloudy sky", "sent": "Look at the cloudy sky."},
            {"zh": "風大的", "en": "windy", "cat": "color", "np": "a windy day", "sent": "It is too windy to fly a kite."},
            {"zh": "下雪的", "en": "snowy", "cat": "color", "np": "a snowy day", "sent": "We like snowy days."},
            {"zh": "寒冷的", "en": "cold", "cat": "color", "np": "a cold day", "sent": "Put on your coat because it is cold."},
            {"zh": "熱的", "en": "hot", "cat": "color", "np": "a hot day", "sent": "I want some ice cream because it is hot."},
            {"zh": "溫暖的", "en": "warm", "cat": "color", "np": "a warm day", "sent": "It is sunny and warm today."},
        ],
    },
    4: {
        "topic": "家中各房間與位置介系詞",
        "entries": [
            {"zh": "客廳", "en": "living room", "cat": "school", "np": "the living room", "sent": "Dad is reading a book in the living room."},
            {"zh": "餐廳", "en": "dining room", "cat": "school", "np": "the dining room", "sent": "We eat dinner in the dining room."},
            {"zh": "臥室", "en": "bedroom", "cat": "school", "np": "the bedroom", "sent": "I sleep in my bedroom every night."},
            {"zh": "廚房", "en": "kitchen", "cat": "school", "np": "the kitchen", "sent": "Mom is cooking in the kitchen."},
            {"zh": "浴室", "en": "bathroom", "cat": "school", "np": "the bathroom", "sent": "Please wash your hands in the bathroom."},
            {"zh": "在……裡面", "en": "in", "cat": "action", "np": "in the box", "sent": "The cat is in the box."},
            {"zh": "在……上面", "en": "on", "cat": "action", "np": "on the desk", "sent": "The book is on the desk."},
            {"zh": "在……下面", "en": "under", "cat": "action", "np": "under the chair", "sent": "The ball is under the chair."},
        ],
    },
    5: {
        "topic": "現在幾點了？日常作息與時間表達",
        "entries": [
            {"zh": "時間", "en": "time", "cat": "school", "np": "the time", "sent": "What time is it?"},
            {"zh": "起床", "en": "get up", "cat": "action", "np": "get up", "sent": "I get up at six o'clock."},
            {"zh": "起來", "en": "wake up", "cat": "action", "np": "wake up", "sent": "Wake up! It's time for school."},
            {"zh": "時鐘", "en": "clock", "cat": "school", "np": "a clock", "sent": "Look at the clock on the wall."},
        ],
    },
    6: {
        "topic": "日常動作與現在進行式",
        "entries": [
            {"zh": "正在吃", "en": "eating", "cat": "action", "np": "eating", "sent": "He is eating an apple."},
            {"zh": "正在喝", "en": "drinking", "cat": "action", "np": "drinking", "sent": "She is drinking milk."},
            {"zh": "正在閱讀", "en": "reading", "cat": "action", "np": "reading", "sent": "I am reading a storybook."},
            {"zh": "正在書寫", "en": "writing", "cat": "action", "np": "writing", "sent": "The boy is writing his homework."},
            {"zh": "正在睡覺", "en": "sleeping", "cat": "action", "np": "sleeping", "sent": "The dog is sleeping under the table."},
            {"zh": "正在唱歌", "en": "singing", "cat": "action", "np": "singing", "sent": "They are singing a song."},
            {"zh": "正在跳舞", "en": "dancing", "cat": "action", "np": "dancing", "sent": "Look! She is dancing."},
        ],
    },
}
