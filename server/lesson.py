# -*- coding: utf-8 -*-
"""lesson.py — 每場 live 對話開始時「選教材」：由最新診斷 + profile
決定今日主題、目標句與 B1 策略字串。與 WS/Nova 無耦合，離線可測。
"""

from __future__ import annotations

_DEFAULT_SENTENCE = "How are you today?"


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
