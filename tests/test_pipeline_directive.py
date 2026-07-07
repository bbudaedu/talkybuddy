# -*- coding: utf-8 -*-
"""test_pipeline_directive.py — B1：pipeline 的 directive 快取與異步刷新。

涵蓋 research/b_axis/B1_雙Agent閉環.md §4：
- 即時路徑：呼叫 llm.generate 時把 self._directive 當第三參數傳入（None 走現況）。
- 每 N 輪（DIRECTIVE_REFRESH_EVERY）成功回合觸發背景 _refresh_directive（不 await、不擋）。
- _refresh_directive：讀 DB→產診斷→存 DB→更新記憶體字串快取。
- 防重入：_directive_refreshing=True 時直接返回，不重複產診斷。
"""

from __future__ import annotations

import asyncio

import pytest

from server import diagnose, pipeline as pipeline_mod
from server.pipeline import VoicePipeline

pytestmark = pytest.mark.anyio


class CapturingLLM:
    """記錄 generate 收到的 directive 參數；回固定字串。"""

    def __init__(self):
        self.captured_directive = "__unset__"

    def available(self) -> bool:
        return True

    def generate(self, student_text, scaffold_result, directive=None):
        self.captured_directive = directive
        return "加值回覆：Hi."


class StubTTS:
    def available(self) -> bool:
        return True

    def synth(self, segments):
        return b"WAV"


async def _emit(events):
    async def emit(payload):
        events.append(payload)
    return emit


# ---------------------------------------------------------------------------
# 1. 即時路徑：把 self._directive 傳入 generate
# ---------------------------------------------------------------------------

async def test_directive_none_by_default_passed_to_generate():
    """冷啟動：_directive 預設 None → generate 收到 None（行為同現況）。"""
    llm = CapturingLLM()
    vp = VoicePipeline(asr=None, llm=llm, tts=StubTTS())

    await vp.run_turn_text("我要一個蘋果", await _emit([]))

    assert llm.captured_directive is None


async def test_cached_directive_passed_to_generate():
    """設定 _directive 後 → generate 收到同一字串。"""
    llm = CapturingLLM()
    vp = VoicePipeline(asr=None, llm=llm, tts=StubTTS())
    vp._directive = "【本輪教學策略】目標：升級句型。"

    await vp.run_turn_text("我要一個蘋果", await _emit([]))

    assert llm.captured_directive == "【本輪教學策略】目標：升級句型。"


# ---------------------------------------------------------------------------
# 2. 每 N 輪觸發背景刷新（不擋即時路徑）
# ---------------------------------------------------------------------------

async def test_refresh_triggered_every_n_turns(monkeypatch):
    """成功回合每 DIRECTIVE_REFRESH_EVERY 輪排一次 _refresh_directive。"""
    monkeypatch.setattr(pipeline_mod, "DIRECTIVE_REFRESH_EVERY", 2)
    calls: list[int] = []

    async def _fake_refresh():
        calls.append(1)

    vp = VoicePipeline(asr=None, llm=CapturingLLM(), tts=StubTTS())
    monkeypatch.setattr(vp, "_refresh_directive", _fake_refresh)

    for _ in range(4):
        await vp.run_turn_text("我要一個蘋果", await _emit([]))
    await asyncio.sleep(0)  # 讓 create_task 排入的背景 task 有機會執行

    assert len(calls) == 2  # 第 2、4 輪各觸發一次


async def test_fallback_turn_does_not_count_toward_refresh(monkeypatch):
    """低信心兜底回合不計入輪數（不觸發刷新）。"""
    monkeypatch.setattr(pipeline_mod, "DIRECTIVE_REFRESH_EVERY", 1)
    calls: list[int] = []

    async def _fake_refresh():
        calls.append(1)

    vp = VoicePipeline(asr=None, llm=CapturingLLM(), tts=StubTTS())
    monkeypatch.setattr(vp, "_refresh_directive", _fake_refresh)

    await vp.run_turn_text("", await _emit([]))  # 空字串 → 兜底
    await asyncio.sleep(0)

    assert calls == []


# ---------------------------------------------------------------------------
# 3. _refresh_directive 內容：更新記憶體快取字串
# ---------------------------------------------------------------------------

async def test_refresh_directive_updates_cache(monkeypatch):
    """_refresh_directive 產診斷→存 DB→把格式化 directive 寫入 self._directive。"""
    fake_diag = {
        "date": "2026-07-07",
        "scores": {"pronunciation": 70, "fluency": 70, "vocabulary": 70, "grammar": 70},
        "strengths": ["s"],
        "weaknesses": ["w"],
        "emotional_status": "穩定",
        "instructions": {"classroom": "c", "device": "d", "peer": "p"},
        "companion_directive": {
            "level": "L3",
            "difficulty": "up",
            "next_goal": "升級句型",
            "topic": "喜歡的事物",
            "example_questions": ["What do you like?"],
            "fallback_hint": "退回單句",
        },
    }
    monkeypatch.setattr(diagnose, "generate_diagnosis", lambda recent, prev: fake_diag)

    vp = VoicePipeline(asr=None, llm=CapturingLLM(), tts=StubTTS())
    await vp._refresh_directive()

    assert vp._directive is not None
    assert "【本輪教學策略】" in vp._directive
    assert "升級句型" in vp._directive


async def test_refresh_directive_folds_level_state_cefr(monkeypatch):
    """診斷含 level_state（target_form）→ 快取 directive 帶 CEFR 難度區塊（B3 接法 A）。"""
    fake_diag = {
        "date": "2026-07-08",
        "scores": {"pronunciation": 60, "fluency": 60, "vocabulary": 60, "grammar": 60},
        "strengths": ["s"],
        "weaknesses": ["w"],
        "emotional_status": "穩定",
        "instructions": {"classroom": "c", "device": "d", "peer": "p"},
        "companion_directive": {
            "level": "L3", "difficulty": "hold", "next_goal": "鞏固",
            "topic": "動物", "example_questions": ["What is it?"],
            "fallback_hint": "退回二選一",
        },
        "level_state": {
            "level": "3-2", "cefr": "A2",
            "target_form": "完整句＋冠詞/複數/形容詞（I see a big dog.）",
            "prompt_strength": "maintain",
        },
    }
    monkeypatch.setattr(diagnose, "generate_diagnosis", lambda recent, prev: fake_diag)

    vp = VoicePipeline(asr=None, llm=CapturingLLM(), tts=StubTTS())
    await vp._refresh_directive()

    assert vp._directive is not None
    assert "【CEFR 難度】" in vp._directive
    assert fake_diag["level_state"]["target_form"] in vp._directive


async def test_refresh_directive_reentrancy_guard(monkeypatch):
    """_directive_refreshing=True 時直接返回，不重複產診斷。"""
    called: list[int] = []

    def _boom(recent, prev):
        called.append(1)
        return {}

    monkeypatch.setattr(diagnose, "generate_diagnosis", _boom)

    vp = VoicePipeline(asr=None, llm=CapturingLLM(), tts=StubTTS())
    vp._directive_refreshing = True
    await vp._refresh_directive()

    assert called == []  # 防重入：未呼叫 generate_diagnosis
