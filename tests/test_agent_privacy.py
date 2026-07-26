# -*- coding: utf-8 -*-
"""test_agent_privacy.py — 三個 agent 上雲前的資料最小化（B4）。

這組測試驗**真實行為**，不驗 mock：
- 不 monkeypatch guardrails.deidentify（用真的），只攔截送上雲的 prompt 字串，
  斷言孩子的名字不在裡面。deidentify 遮不掉中文姓名（只遮個資詞／連續數字／
  非詞庫的 Title-case 英文專名），所以「有呼叫 deidentify」不等於「沒外洩」。
- companion_directive 與 instructions 是 LLM 依孩子講的話生成的自由文字，
  孩子說「我是王小明」名字就會落在裡面，再被原文送上雲並由 Memory 長期保存。

修法是白名單投影（server/agents/privacy.py）：只挑該送的欄位，
其餘一律不送——新增欄位預設不上雲，忘記更新遮罩清單不會變成外洩。
"""

from __future__ import annotations

import json

import pytest

# ---------------------------------------------------------------------------
# fixtures：帶「孩子講出來的名字」的診斷與 profile
# ---------------------------------------------------------------------------

# 這個名字必須是中文姓名：guardrails.deidentify 遮不掉，所以只要它出現在
# prompt 裡就是真外洩，而不是遮罩強度的問題。
_CHILD_NAME = "王小明"
_SIBLING = "陳大華"


def _diag_with_free_text(date: str = "2026-07-20", grammar: int = 42) -> dict:
    """一筆完整診斷：含 companion_directive 與 instructions 兩個自由文字欄位。"""
    return {
        "date": date,
        "scores": {
            "pronunciation": 70,
            "fluency": 68,
            "vocabulary": 65,
            "grammar": grammar,
        },
        "strengths": ["願意開口嘗試"],
        "weaknesses": ["冠詞 a/an 仍不穩定"],
        "emotional_status": "學習態度積極。",
        # 以下兩欄是 B4 的漏洞所在
        "companion_directive": f"學生自稱{_CHILD_NAME}，今天跟哥哥{_SIBLING}去動物園，"
                               f"可以多聊動物主題。",
        "instructions": {
            "classroom": f"老師可提醒{_CHILD_NAME}放慢語速。",
            "device": "麥克風收音正常。",
            "peer": f"與{_SIBLING}同組時容易分心。",
        },
    }


_PROFILE_WITH_NAME = {
    "student_id": "STUDENT-001",
    "name": _CHILD_NAME,
    "notes": f"{_CHILD_NAME}的媽媽說他喜歡動物。",
    "interests": [{"topic": "animal", "label": "動物", "hits": 5}],
    "difficulty": {"level": 2, "score_avg": 61, "avg_en_words": 4.2, "en_ratio": 0.6},
    "emotional_recent": "穩定",
}


_CLOUD_HOMEWORK = json.dumps({
    "focus": "文法",
    "items": [
        {"target_en": "I have a dog.", "prompt_zh": "說說你的狗", "why": "冠詞"},
        {"target_en": "She likes cats.", "prompt_zh": "你的朋友呢？", "why": "第三人稱"},
        {"target_en": "We eat an apple.", "prompt_zh": "說說你吃什麼", "why": "an"},
    ],
    "source": "cloud",
}, ensure_ascii=False)

_CLOUD_REPORT = json.dumps({
    "period": "最近 5 次練習",
    "summary": "整體表現穩定成長，發音與詞彙量皆有進步。",
    "highlights": ["發音清晰度提升"],
    "concerns": ["文法仍需加強"],
    "suggestions": ["睡前用英文聊今天做了什麼"],
    "source": "cloud",
}, ensure_ascii=False)

_CLOUD_DECISION = json.dumps({
    "actions": ["homework"],
    "reason": "文法面向偏弱，建議派發針對性作業加強練習。",
    "priority": "high",
    "source": "cloud",
}, ensure_ascii=False)


@pytest.fixture
def capture_prompt(monkeypatch):
    """攔截送上雲的 user prompt；回傳一個 list，測試結束後取 [0]。

    只 patch bedrock_converse，不 patch guardrails——去識別化要用真的。
    """
    captured: list[str] = []

    def _make(response: str):
        from server import bedrock_converse

        def _fake_converse(system, user, *, cfg, max_tokens=1024, timeout_s=12.0, **kw):
            captured.append(user)
            return response

        monkeypatch.setattr(bedrock_converse, "resolve_config",
                            lambda role=None: {"region": "ap-southeast-1", "model_id": "m"})
        monkeypatch.setattr(bedrock_converse, "converse_text", _fake_converse)
        return captured

    return _make


# ---------------------------------------------------------------------------
# 三個 agent：孩子的名字不得出現在上雲 prompt
# ---------------------------------------------------------------------------

def test_report_does_not_upload_companion_directive(capture_prompt):
    """週報 agent：companion_directive / instructions 不得上雲。"""
    from server.agents import report

    captured = capture_prompt(_CLOUD_REPORT)
    diags = [_diag_with_free_text(f"2026-07-1{i}") for i in range(5)]

    report.generate_report(_PROFILE_WITH_NAME, diags, allow_cloud=True)

    assert captured, "converse_text 應被呼叫"
    prompt = captured[0]
    assert _CHILD_NAME not in prompt, "孩子的名字不得上雲（companion_directive 外洩）"
    assert _SIBLING not in prompt, "家人的名字不得上雲"
    assert "companion_directive" not in prompt, "companion_directive 欄位整個不該送"
    assert "instructions" not in prompt, "instructions 欄位整個不該送"


def test_orchestrator_does_not_upload_companion_directive(capture_prompt):
    """決策 agent：最新診斷與歷史診斷的自由文字都不得上雲。"""
    from server.agents import orchestrator

    captured = capture_prompt(_CLOUD_DECISION)
    latest = _diag_with_free_text("2026-07-24")
    history = [_diag_with_free_text(f"2026-07-2{i}") for i in range(4)]

    orchestrator.decide_next_actions(
        profile=_PROFILE_WITH_NAME,
        diagnosis=latest,
        history=history,
        turn_count=10,
        allow_cloud=True,
    )

    assert captured, "converse_text 應被呼叫"
    prompt = captured[0]
    assert _CHILD_NAME not in prompt, "孩子的名字不得上雲（companion_directive 外洩）"
    assert _SIBLING not in prompt, "家人的名字不得上雲"
    assert "companion_directive" not in prompt, "companion_directive 欄位整個不該送"
    assert "instructions" not in prompt, "instructions 欄位整個不該送"


def test_homework_does_not_upload_companion_directive(capture_prompt):
    """派作業 agent：目前 prompt 沒 dump 整個 diagnosis，但投影仍須把自由文字擋掉。"""
    from server.agents import homework

    captured = capture_prompt(_CLOUD_HOMEWORK)

    homework.generate_homework(_PROFILE_WITH_NAME, _diag_with_free_text(), allow_cloud=True)

    assert captured, "converse_text 應被呼叫"
    prompt = captured[0]
    assert _CHILD_NAME not in prompt, "孩子的名字不得上雲"
    assert _SIBLING not in prompt, "家人的名字不得上雲"


# ---------------------------------------------------------------------------
# privacy 模組本身：白名單語意
# ---------------------------------------------------------------------------

def test_safe_diagnosis_drops_unknown_fields():
    """未列在白名單的欄位一律不進輸出——包含將來新增的欄位。"""
    from server.agents import privacy

    out = privacy.safe_diagnosis({
        **_diag_with_free_text(),
        "some_future_field": f"{_CHILD_NAME} 說了什麼",
        "raw_transcript": "孩子的完整逐字稿",
    })

    assert set(out.keys()) <= {"date", "scores", "strengths", "weaknesses", "emotional_status"}
    assert "some_future_field" not in out
    assert "raw_transcript" not in out
    assert "companion_directive" not in out
    assert "instructions" not in out
    assert _CHILD_NAME not in json.dumps(out, ensure_ascii=False)


def test_safe_diagnosis_keeps_what_cloud_needs():
    """該送的還是要送：沒有 scores / weaknesses，雲端產出品質會掉。"""
    from server.agents import privacy

    out = privacy.safe_diagnosis(_diag_with_free_text(grammar=42))

    assert out["date"] == "2026-07-20"
    assert out["scores"]["grammar"] == 42
    assert set(out["scores"]) == {"pronunciation", "fluency", "vocabulary", "grammar"}
    assert out["weaknesses"] == ["冠詞 a/an 仍不穩定"]
    assert out["strengths"] == ["願意開口嘗試"]
    assert out["emotional_status"].strip()


def test_safe_profile_drops_identifiers_and_free_text():
    """profile：student_id / name / notes 不上雲，難度與興趣等結構化欄位保留。"""
    from server.agents import privacy

    out = privacy.safe_profile(_PROFILE_WITH_NAME)

    assert "student_id" not in out, "student_id 由 AgentCore actor_id 攜帶，不必進 prompt"
    assert "name" not in out
    assert "notes" not in out
    assert _CHILD_NAME not in json.dumps(out, ensure_ascii=False)
    assert out["difficulty"]["level"] == 2
    assert out["interests"][0]["topic"] == "animal"


def test_safe_helpers_never_raise_on_garbage():
    """垃圾輸入不得拋例外（三個 agent 的『絕不拋』契約靠這層守住）。"""
    from server.agents import privacy

    for bad in (None, {}, {"scores": "not-a-dict"}, {"strengths": "字串不是 list"},
                {"date": 12345}, {"scores": {"grammar": "abc"}}):
        assert isinstance(privacy.safe_diagnosis(bad), dict)
        assert isinstance(privacy.safe_profile(bad), dict)
    assert privacy.safe_diagnoses(None) == []
    assert privacy.safe_diagnoses([None, {}]) == [{}, {}]


def test_safe_diagnosis_truncates_long_free_text():
    """自由文字仍需截斷，避免整段逐字稿塞進白名單欄位混上雲。"""
    from server.agents import privacy

    out = privacy.safe_diagnosis({
        "emotional_status": "很開心。" * 500,
        "strengths": ["願意開口。" * 500],
    })
    assert len(out["emotional_status"]) <= privacy.MAX_TEXT_LEN
    assert len(out["strengths"][0]) <= privacy.MAX_TEXT_LEN
