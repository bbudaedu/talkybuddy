# -*- coding: utf-8 -*-
"""lesson.py — 每場 live 對話開始時「選教材」：由最新診斷 + profile
決定今日主題、目標句與 B1 策略字串。與 WS/Nova 無耦合，離線可測。
"""

from __future__ import annotations

from dataclasses import dataclass

_DEFAULT_SENTENCE = "How are you today?"


@dataclass
class Lesson:
    topic: str
    target_sentence: str
    target_form: str | None
    directive: str | None


def pick_target_sentence(topic, profile=None) -> str:
    """挑本場跟讀的英文目標句。

    從 scaffold.VOCAB 篩 cat==topic 且有 sent 的詞；優先挑 profile 正在學
    （learning_vocab）對應的詞的例句；缺就取該類第一句；完全無 → 通用預設。
    任何例外都退化為預設，不炸。
    """
    from server import scaffold
    try:
        cands = [info for info in scaffold.VOCAB.values()
                 if info.get("cat") == topic and info.get("sent")]
        if not cands:
            return _DEFAULT_SENTENCE
        learning = set()
        for v in (profile or {}).get("learning_vocab", []) or []:
            en = v.get("en") if isinstance(v, dict) else v
            if en:
                learning.add(str(en))
        for info in cands:
            if info.get("en") in learning:
                return info["sent"]
        return cands[0]["sent"]
    except Exception:
        return _DEFAULT_SENTENCE


def topic_sentences(topic, profile=None, limit: int = 5) -> list[str]:
    """取同主題的多個例句，今日目標句排第一。

    `pick_target_sentence` 一次只給一句，於是即時陪聊的教練 prompt 只知道
    一句可以練。2026-07-31 十輪模擬對話的結果是：孩子第 1 輪就唸對了，玩偶
    仍然十輪都教同一句，孩子抗議「你怎麼一直叫我唸一樣的啦」。

    教練 prompt 本來就寫著「孩子跟上就換下一句或延伸一點」——缺的不是指令，
    是**材料**。`scaffold.VOCAB` 的 animal 類其實有 29 句。

    任何例外都回空 list，不炸（與本模組其他函式一致）。
    """
    from server import scaffold

    try:
        first = pick_target_sentence(topic, profile)
        out = [first]
        for info in scaffold.VOCAB.values():
            if info.get("cat") != topic or not info.get("sent"):
                continue
            sent = info["sent"]
            if sent not in out:
                out.append(sent)
            if len(out) >= limit:
                break
        # 主題不存在時 pick_target_sentence 會回通用預設，那不算這個主題的句子
        if len(out) == 1 and not any(
            i.get("cat") == topic and i.get("sent") for i in scaffold.VOCAB.values()
        ):
            return []
        return out[:limit]
    except Exception:
        return []


def build_lesson(diagnoses, profile=None) -> Lesson:
    """由最新診斷 + profile 組本場教材。全程安全退化，永不擋 live。"""
    from server import curriculum, diagnose
    default_topic = curriculum.TOPIC_ORDER[0]
    default_form = curriculum._TARGET_FORM[1]
    try:
        latest = (diagnoses or [])[-1] if (diagnoses or []) else None
        directive = None
        topic = default_topic
        target_form = default_form
        if latest:
            ls = latest.get("level_state") or {}
            cd = latest.get("companion_directive")
            if cd:
                directive = diagnose.format_directive_for_prompt(cd, ls) or None
            topic = ls.get("topic") or default_topic
            target_form = ls.get("target_form") or default_form
        return Lesson(topic, pick_target_sentence(topic, profile),
                      target_form, directive)
    except Exception:
        return Lesson(default_topic, pick_target_sentence(default_topic, profile),
                      default_form, None)
