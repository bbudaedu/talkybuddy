# -*- coding: utf-8 -*-
"""test_guardrails.py — B4 內容安全與隱私最小化共用模組。

涵蓋 research/b_axis/B4_隱私與Guardrails.md：
- §1.2 L2：CHILD_SAFETY_CLAUSE 兒童安全明文護欄常數
- §1.2 L3：passes_guardrail() 輸出後置過濾共用 helper（edge/雲端共用）
- §3.2 ：deidentify() 上雲前去識別化（遮罩人名/電話/住址，保留學習詞）
"""

from __future__ import annotations

from server import guardrails


# ---------------------------------------------------------------------------
# 1. CHILD_SAFETY_CLAUSE：兒童安全明文護欄
# ---------------------------------------------------------------------------

def test_child_safety_clause_covers_core_topics():
    """護欄字串涵蓋暴力/成人/自傷/個資與情緒危機安撫指引。"""
    c = guardrails.CHILD_SAFETY_CLAUSE
    assert isinstance(c, str) and c.strip()
    for kw in ("暴力", "成人", "個人資料"):
        assert kw in c
    # 情緒危機：安撫 + 轉大人、不追問細節
    assert "安撫" in c
    assert ("老師" in c or "家人" in c)


# ---------------------------------------------------------------------------
# 2. passes_guardrail：輸出後置過濾（True = 安全可用）
# ---------------------------------------------------------------------------

def test_passes_guardrail_clean_text_true():
    """乾淨文字 → True（可採用）。"""
    assert guardrails.passes_guardrail("很棒！跟我說一遍：I see a dog.") is True


def test_passes_guardrail_forbidden_text_false():
    """命中禁詞 → False（須丟棄降級）。"""
    assert guardrails.passes_guardrail("我想要一把槍") is False
    assert guardrails.passes_guardrail("I have a gun") is False


def test_passes_guardrail_empty_false():
    """空字串/None → False（沒有可採用的內容）。"""
    assert guardrails.passes_guardrail("") is False
    assert guardrails.passes_guardrail(None) is False


# ---------------------------------------------------------------------------
# 3. deidentify：上雲前去識別化
# ---------------------------------------------------------------------------

def test_deidentify_masks_english_name():
    """詞庫外的英文專名（人名）→ [名字]。"""
    out = guardrails.deidentify("她叫 Mimi 很可愛")
    assert "Mimi" not in out
    assert "[名字]" in out


def test_deidentify_masks_phone_digits():
    """三位以上連續數字（電話/門牌）→ [數字]。"""
    out = guardrails.deidentify("我家電話 0912345678")
    assert "0912345678" not in out
    assert "[數字]" in out


def test_deidentify_masks_chinese_pii_words():
    """住址/電話號碼/身分證/密碼 等個資詞 → [個資]。"""
    out = guardrails.deidentify("這是我的住址和身分證")
    assert "住址" not in out and "身分證" not in out
    assert "[個資]" in out


def test_deidentify_keeps_vocab_learning_words():
    """食物/動物/顏色等詞庫學習詞不被遮罩（不影響診斷品質）。"""
    text = "I like apple and I see a big dog"
    out = guardrails.deidentify(text)
    assert "apple" in out
    assert "dog" in out
    assert "[名字]" not in out  # 學習詞與常用詞不誤判為人名


def test_deidentify_empty_is_safe():
    """空字串不炸。"""
    assert guardrails.deidentify("") == ""


# ---------------------------------------------------------------------------
# 4. 接線：llm system prompt 內嵌兒童安全護欄
# ---------------------------------------------------------------------------

def test_llm_system_prompt_embeds_safety_clause():
    """EdgeLLM._SYSTEM_PROMPT 尾端含 CHILD_SAFETY_CLAUSE（edge 側護欄）。"""
    from server.llm import EdgeLLM
    assert guardrails.CHILD_SAFETY_CLAUSE in EdgeLLM._SYSTEM_PROMPT
