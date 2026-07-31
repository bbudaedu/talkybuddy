# -*- coding: utf-8 -*-
"""ensure_readalong 的 allow_variation：即時陪聊契約允許玩偶換句子。

2026-07-31 模擬對話實測抓到的 bug：

    孩子：我不想要說狗，我想說 I see a cat！
    玩偶：…跟我說一遍：I see a cat.跟我說一遍：I see a dog.

模型講的「跟我說一遍：I see a cat.」其實是**對的**——孩子想練貓。但護欄看不到
本輪 target（I see a dog.），就再補一句，孩子會聽到兩句帶讀。

回合式契約要這個嚴格行為（目標句由教材決定，不可被模型改掉），
即時陪聊契約不要。所以是加旗標，不是改預設。
"""
from __future__ import annotations

from server.guardrails import ensure_readalong

TARGET = "I see a dog."


def test_default_still_forces_the_exact_target():
    """預設行為一字不動——回合式契約靠它。"""
    out = ensure_readalong("你好棒！跟我說一遍：I see a cat.", TARGET)
    assert "跟我說一遍：I see a dog." in out


def test_variation_accepts_another_english_sentence():
    """允許變化時，模型自己帶讀的英文句要被留著、不再補第二句。"""
    text = "你真聰明！跟我說一遍：I see a cat."
    out = ensure_readalong(text, TARGET, allow_variation=True)
    assert out == text
    assert out.count("跟我說一遍") == 1


def test_variation_still_appends_when_no_readalong_at_all():
    """完全沒帶讀時仍要補——放寬的是「換句子」，不是「可以不帶讀」。"""
    out = ensure_readalong("你今天好棒喔！", TARGET, allow_variation=True)
    assert "跟我說一遍：I see a dog." in out


def test_variation_still_strips_chinese_readalong():
    """帶讀中文是真機出現過的 bug，放寬變化不代表放行這個。"""
    out = ensure_readalong(
        "很好！跟我說一遍：我看到一隻兔子。", TARGET, allow_variation=True
    )
    assert "我看到一隻兔子" not in out
    assert "跟我說一遍：I see a dog." in out


def test_variation_does_not_multiply_readalongs():
    """已有兩句英文帶讀時也不該再加第三句。"""
    text = "跟我說一遍：I see a cat. 跟我說一遍：I see a bird."
    out = ensure_readalong(text, TARGET, allow_variation=True)
    assert out.count("跟我說一遍") == 2
