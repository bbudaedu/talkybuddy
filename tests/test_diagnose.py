# -*- coding: utf-8 -*-
"""test_diagnose.py — B1 雙 Agent 閉環：companion_directive 產生與正規化。

涵蓋 research/b_axis/B1_雙Agent閉環.md §2：
- 規則式產生 directive（difficulty 由趨勢決定、goal/topic 由旗標決定）
- 軟性正規化（合法→dict、不合法→None）
- dict→prompt 文字化
- generate_diagnosis 收斂保底（不論路徑，最終一定有合法 directive）
"""

from __future__ import annotations

from server import diagnose


def _flags(article=False, chinese=False, short=False):
    """組出 _detect_patterns 形狀的旗標 dict。"""
    return {
        "article_correction": article,
        "high_chinese_ratio": chinese,
        "short_sentences": short,
        "article_hits": 1 if article else 0,
    }


def _scores(v: int) -> dict:
    """四維同分的 scores dict。"""
    return {"pronunciation": v, "fluency": v, "vocabulary": v, "grammar": v}


# ---------------------------------------------------------------------------
# 1. difficulty 由趨勢 delta 決定（重用 _build_emotional_status 邏輯）
# ---------------------------------------------------------------------------

def test_directive_difficulty_up_on_rising_trend():
    """avg 較 prev 上升 >= 1.5 且 avg >= 60 → difficulty=up。"""
    prev = {"scores": _scores(60)}
    cd = diagnose._build_companion_directive(_flags(), _scores(70), prev)
    assert cd["difficulty"] == "up"


def test_directive_difficulty_down_on_falling_trend():
    """avg 較 prev 下滑 <= -1.5 → difficulty=down。"""
    prev = {"scores": _scores(70)}
    cd = diagnose._build_companion_directive(_flags(), _scores(50), prev)
    assert cd["difficulty"] == "down"


def test_directive_difficulty_hold_when_flat():
    """趨勢平穩（|delta| < 1.5）→ difficulty=hold。"""
    prev = {"scores": _scores(60)}
    cd = diagnose._build_companion_directive(_flags(), _scores(61), prev)
    assert cd["difficulty"] == "hold"


def test_directive_difficulty_hold_when_no_prev():
    """無 prev（冷啟動）→ delta=0 → hold。"""
    cd = diagnose._build_companion_directive(_flags(), _scores(70), None)
    assert cd["difficulty"] == "hold"


# ---------------------------------------------------------------------------
# 2. goal/topic/questions 由最高優先旗標決定
# ---------------------------------------------------------------------------

def test_directive_article_flag_drives_topic():
    """article_correction 命中 → 目標鎖定冠詞、話題為食物。"""
    cd = diagnose._build_companion_directive(_flags(article=True), _scores(60), None)
    assert "冠詞" in cd["next_goal"]
    assert "食物" in cd["topic"]


def test_directive_chinese_flag_drives_topic():
    """high_chinese_ratio 命中 → 目標鎖定整句英文輸出。"""
    cd = diagnose._build_companion_directive(_flags(chinese=True), _scores(60), None)
    assert "英文" in cd["next_goal"]


def test_directive_article_flag_wins_over_chinese():
    """優先序：article_correction > high_chinese_ratio。"""
    cd = diagnose._build_companion_directive(
        _flags(article=True, chinese=True), _scores(60), None
    )
    assert "冠詞" in cd["next_goal"]


# ---------------------------------------------------------------------------
# 3. schema 完整性
# ---------------------------------------------------------------------------

def test_directive_has_all_fields():
    """directive 六欄齊備、型別正確、example_questions 為 1-3 句字串。"""
    cd = diagnose._build_companion_directive(_flags(short=True), _scores(55), None)
    assert set(cd.keys()) == {
        "level", "difficulty", "next_goal", "topic",
        "example_questions", "fallback_hint",
    }
    assert cd["difficulty"] in ("up", "hold", "down")
    assert isinstance(cd["example_questions"], list)
    assert 1 <= len(cd["example_questions"]) <= 3
    assert all(isinstance(q, str) and q for q in cd["example_questions"])
    assert cd["next_goal"] and cd["topic"] and cd["fallback_hint"]


# ---------------------------------------------------------------------------
# 4. _normalize_directive 軟性正規化
# ---------------------------------------------------------------------------

def test_normalize_directive_valid_passes_through():
    """合法 directive → 正規化後保留內容。"""
    raw = {
        "level": "L2",
        "difficulty": "up",
        "next_goal": "升級句型",
        "topic": "農場動物",
        "example_questions": ["What color is the pig?", "Do you see a cat?"],
        "fallback_hint": "退回二選一",
    }
    out = diagnose._normalize_directive(raw)
    assert out is not None
    assert out["difficulty"] == "up"
    assert out["next_goal"] == "升級句型"
    assert out["example_questions"] == ["What color is the pig?", "Do you see a cat?"]


def test_normalize_directive_non_dict_returns_none():
    """非 dict → None（呼叫端補規則式）。"""
    assert diagnose._normalize_directive("nope") is None
    assert diagnose._normalize_directive(None) is None


def test_normalize_directive_missing_goal_or_topic_returns_none():
    """缺 next_goal 或 topic → None。"""
    assert diagnose._normalize_directive({"difficulty": "up"}) is None
    assert diagnose._normalize_directive(
        {"next_goal": "x", "topic": ""}
    ) is None


def test_normalize_directive_bad_difficulty_defaults_hold():
    """difficulty 非法值 → 收斂為 hold；example_questions 過長截到 3 句。"""
    raw = {
        "difficulty": "sideways",
        "next_goal": "goal",
        "topic": "topic",
        "example_questions": ["a", "b", "c", "d", "e"],
    }
    out = diagnose._normalize_directive(raw)
    assert out is not None
    assert out["difficulty"] == "hold"
    assert len(out["example_questions"]) == 3


# ---------------------------------------------------------------------------
# 5. format_directive_for_prompt 文字化
# ---------------------------------------------------------------------------

def test_format_directive_none_returns_none():
    """None → None（冷啟動時 prompt 不注入）。"""
    assert diagnose.format_directive_for_prompt(None) is None


def test_format_directive_contains_strategy_block():
    """輸出含策略區塊標題、目標與話題內容。"""
    cd = diagnose._build_companion_directive(_flags(article=True), _scores(60), None)
    s = diagnose.format_directive_for_prompt(cd)
    assert s is not None
    assert "【本輪教學策略】" in s
    assert cd["next_goal"] in s
    assert cd["topic"] in s
    # 護欄提醒：帶讀句不可改寫
    assert "跟我說一遍" in s


# ---------------------------------------------------------------------------
# 5b. B3 接法 A：把 level_state 的 CEFR 語言形式折進 directive 文字
# ---------------------------------------------------------------------------

def _level_state(target_form="完整句＋冠詞/複數/形容詞（I see a big dog.）",
                 cefr="A2", level="3-2"):
    """組出 curriculum.compute_level_state 形狀（僅取 format 需要的欄位）。"""
    return {"level": level, "cefr": cefr, "target_form": target_form,
            "prompt_strength": "maintain"}


def test_format_directive_appends_cefr_form_from_level_state():
    """給 level_state → 追加 CEFR 難度句，含目標語言形式與程度標籤。"""
    cd = diagnose._build_companion_directive(_flags(article=True), _scores(60), None)
    ls = _level_state()
    s = diagnose.format_directive_for_prompt(cd, ls)
    assert s is not None
    assert "【本輪教學策略】" in s           # 原策略區塊仍在
    assert "【CEFR 難度】" in s              # 新增 CEFR 難度區塊
    assert ls["target_form"] in s           # 帶入本輪語言形式
    assert ls["cefr"] in s                  # 帶入 CEFR 程度標籤
    assert ls["level"] in s                 # 帶入 band-step
    # 護欄提醒仍為最後一句、不可改寫
    assert "跟我說一遍" in s
    assert s.rstrip().endswith("不可改寫。")


def test_format_directive_level_state_none_is_backward_compatible():
    """level_state=None → 輸出與不傳完全一致（向後相容）。"""
    cd = diagnose._build_companion_directive(_flags(short=True), _scores(55), None)
    assert diagnose.format_directive_for_prompt(cd, None) == \
        diagnose.format_directive_for_prompt(cd)


def test_format_directive_level_state_without_target_form_no_append():
    """level_state 缺 target_form → 不追加 CEFR 句（等同無 level_state）。"""
    cd = diagnose._build_companion_directive(_flags(), _scores(60), None)
    empty_ls = {"level": "1-1", "cefr": "pre-A1"}  # 無 target_form
    assert diagnose.format_directive_for_prompt(cd, empty_ls) == \
        diagnose.format_directive_for_prompt(cd)


# ---------------------------------------------------------------------------
# 6. 掛進主流程 + generate_diagnosis 收斂保底
# ---------------------------------------------------------------------------

def test_rule_based_diagnosis_includes_directive():
    """_rule_based_diagnosis 回傳含合法 companion_directive。"""
    interactions = [
        {
            "student_text": "我想要 apple",
            "asr_confidence": 0.8,
            "ai_response_text": "很棒！記得母音開頭用 an：I want an apple.",
            "scores": {"fluency": 55, "vocabulary": 60, "grammar": 50},
        }
    ]
    d = diagnose._rule_based_diagnosis(interactions, None)
    cd = d["companion_directive"]
    assert cd["difficulty"] in ("up", "hold", "down")
    assert cd["next_goal"] and cd["topic"]


def test_generate_diagnosis_always_has_directive(monkeypatch):
    """無 API key → 規則式路徑，最終一定有合法 directive（含空互動極端情況）。"""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    d = diagnose.generate_diagnosis([], None)
    assert d.get("companion_directive")
    assert d["companion_directive"]["difficulty"] in ("up", "hold", "down")


def test_generate_diagnosis_routes_to_bedrock(monkeypatch):
    from server import diagnose, cloud_llm, config, guardrails
    monkeypatch.setattr(config, "LLM_CLOUD_PROVIDER", "bedrock", raising=False)
    monkeypatch.setattr(guardrails, "consent_granted", lambda: True)
    called = {"n": 0}

    def _fake_bedrock(interactions, prev):
        called["n"] += 1
        return {
            "date": diagnose._today(),
            "scores": {"pronunciation": 61, "fluency": 57, "vocabulary": 63, "grammar": 52},
            "strengths": ["願意開口"], "weaknesses": ["句長偏短"],
            "emotional_status": "平穩",
            "instructions": {"classroom": "a", "device": "b", "peer": "c"},
            "companion_directive": None,
        }

    monkeypatch.setattr(cloud_llm, "diagnose_via_bedrock", _fake_bedrock)
    out = diagnose.generate_diagnosis([{"student_text": "cat", "scores": {}}], None)
    assert called["n"] == 1
    assert out["scores"]["pronunciation"] == 61
    assert out.get("companion_directive")  # 收斂保底一定補上


def test_generate_diagnosis_bedrock_fail_falls_back_to_mock(monkeypatch):
    from server import diagnose, cloud_llm, config, guardrails
    monkeypatch.setattr(config, "LLM_CLOUD_PROVIDER", "bedrock", raising=False)
    monkeypatch.setattr(guardrails, "consent_granted", lambda: True)
    monkeypatch.setattr(cloud_llm, "diagnose_via_bedrock", lambda i, p: None)
    out = diagnose.generate_diagnosis([{"student_text": "貓", "scores": {}}], None)
    assert "scores" in out and out.get("companion_directive")  # mock 兜底


# ---------------------------------------------------------------------------
# _compute_scores：真發音分數（live 背景評測）優先，無則 fallback asr_confidence
# ---------------------------------------------------------------------------

def test_compute_scores_uses_real_pronunciation_when_present():
    """interaction scores 內有真 pronunciation → pronunciation 維度取真分平均，
    不再用 asr_confidence 映射（否則 0.3→30，真分應為 85）。"""
    interactions = [
        {"scores": {"pronunciation": 90}, "asr_confidence": 0.3},
        {"scores": {"pronunciation": 80}, "asr_confidence": 0.3},
    ]
    out = diagnose._compute_scores(interactions, None)
    assert out["pronunciation"] == 85


def test_compute_scores_falls_back_to_asr_conf_without_pronunciation():
    """無真 pronunciation → 維持 asr_confidence 映射（向後相容）。"""
    interactions = [
        {"scores": {}, "asr_confidence": 0.5},
        {"scores": {}, "asr_confidence": 0.5},
    ]
    out = diagnose._compute_scores(interactions, None)
    assert out["pronunciation"] == 50


def test_compute_scores_ignores_none_pronunciation_entries():
    """部分 interaction pron=None（評分逾時/降級）→ 只平均有值者，不當 0。"""
    interactions = [
        {"scores": {"pronunciation": 88}, "asr_confidence": 0.2},
        {"scores": {"pronunciation": None}, "asr_confidence": 0.2},
    ]
    out = diagnose._compute_scores(interactions, None)
    assert out["pronunciation"] == 88
