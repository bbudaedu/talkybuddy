# -*- coding: utf-8 -*-
"""test_pipeline.py — VoicePipeline 狀態機測試，注入自製 Stub ASR/LLM/TTS。

涵蓋 CONTRACTS.md pipeline.py 契約：
- 正常回合（scaffold + LLM 加值 + TTS + 寫 DB）
- 低信心（含空字串／conf < ASR_CONF_THRESHOLD）→ 兜底話術、不寫 DB
- LLM 回 None（或逾時）→ 降級用 scaffold.reply_text
- 半雙工：同一 session 併發重入 → 直接回 busy，不執行第二輪
"""

from __future__ import annotations

import asyncio
import time

import pytest

from server import config, pipeline as pipeline_mod, scaffold, store
from server.pipeline import VoicePipeline

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Stub 引擎（依 CONTRACTS.md 真實簽名：available() / transcribe() / generate() / synth()）
# ---------------------------------------------------------------------------

class StubASR:
    """假 ASR：回固定 (text, confidence)，不碰真實檔案。"""

    def __init__(self, text: str = "", conf: float = 0.9):
        self._text = text
        self._conf = conf

    def available(self) -> bool:
        return True

    def transcribe(self, wav_path: str) -> tuple[str, float]:
        return (self._text, self._conf)


class StubLLM:
    """假 LLM：可設定回覆字串（或 None）、是否 available、以及模擬耗時。"""

    def __init__(self, reply: str | None = None, available: bool = True, delay_s: float = 0.0):
        self._reply = reply
        self._available_flag = available
        self._delay_s = delay_s

    def available(self) -> bool:
        return self._available_flag

    def generate(self, student_text: str, scaffold_result, directive=None) -> str | None:
        if self._delay_s:
            time.sleep(self._delay_s)  # 模擬耗時生成（在 to_thread 中執行）
        return self._reply


class StubTTS:
    """假 TTS：回固定 wav bytes（或 None 代表不可用）。"""

    def __init__(self, wav: bytes | None = b"FAKEWAVBYTES", available: bool = True):
        self._wav = wav
        self._available_flag = available

    def available(self) -> bool:
        return self._available_flag

    def synth(self, segments: list[tuple[str, str]]) -> bytes | None:
        return self._wav


async def _collecting_emit(events: list[dict]):
    """回傳一個會把送出的事件記錄到 events 清單的 async emit callback。"""

    async def emit(payload: dict) -> None:
        events.append(payload)

    return emit


# ---------------------------------------------------------------------------
# 1. 正常回合
# ---------------------------------------------------------------------------

async def test_normal_turn_uses_llm_reply_and_writes_db():
    """正常回合：ASR 信心足夠 → scaffold → LLM 加值 → TTS → 寫 DB，seq 遞增。"""
    events: list[dict] = []
    emit = await _collecting_emit(events)
    vp = VoicePipeline(StubASR(), StubLLM(reply="LLM 加值回覆：Hi there."), StubTTS())

    result = await vp.run_turn_text("我要一個蘋果", emit)

    assert result is not None
    assert result.fallback is False
    assert result.reply_text == "LLM 加值回覆：Hi there."
    assert result.tts_wav == b"FAKEWAVBYTES"
    assert result.seq == 1
    assert result.asr_text == "我要一個蘋果"
    assert result.asr_conf == 1.0  # 文字輪固定 1.0
    assert set(result.scores.keys()) == {"fluency", "vocabulary", "grammar"}
    assert result.state_events == ["thinking", "tts", "idle"]
    assert {"asr", "llm", "tts_first", "round_total"} <= result.latency_ms.keys()

    # 寫入 DB：edge 模式（預設）→ synced=False → pending 應為 1
    assert store.pending_count() == 1
    rows = store.list_interactions(limit=10)
    assert len(rows) == 1
    assert rows[0]["ai_response_text"] == "LLM 加值回覆：Hi there."
    assert rows[0]["network_mode"] == "edge"
    assert rows[0]["synced"] is False


async def test_normal_turn_cloud_mode_marks_synced_true():
    """network_mode="cloud" 時寫入的紀錄 synced 應為 True。"""
    events: list[dict] = []
    emit = await _collecting_emit(events)
    vp = VoicePipeline(StubASR(), StubLLM(available=False), StubTTS())
    vp.network_mode = "cloud"

    result = await vp.run_turn_text("我要一個蘋果", emit)

    assert result.seq == 1
    rows = store.list_interactions(limit=10)
    assert rows[0]["synced"] is True
    assert rows[0]["network_mode"] == "cloud"


# ---------------------------------------------------------------------------
# 2. 低信心兜底（不寫 DB）
# ---------------------------------------------------------------------------

async def test_empty_text_triggers_fallback_and_no_db_write():
    """文字輪傳空字串 → asr_text 為空 → 走兜底，不寫 DB。"""
    events: list[dict] = []
    emit = await _collecting_emit(events)
    vp = VoicePipeline(StubASR(), StubLLM(), StubTTS())

    result = await vp.run_turn_text("", emit)

    assert result.fallback is True
    assert result.reply_text in scaffold.FALLBACK_LINES
    assert result.seq == 0
    assert store.pending_count() == 0
    assert store.list_interactions() == []


async def test_low_asr_confidence_triggers_fallback_and_no_db_write(monkeypatch):
    """ASR 信心 < ASR_CONF_THRESHOLD → 兜底話術，不寫 DB（走 run_turn_audio 路徑）。

    以 monkeypatch 取代 _webm_to_wav，跳過真實 ffmpeg 轉檔，直接進 ASR 階段。
    """
    monkeypatch.setattr(pipeline_mod, "_webm_to_wav", lambda webm_bytes: "/tmp/fake.wav")

    low_conf = config.ASR_CONF_THRESHOLD - 0.1
    assert low_conf < config.ASR_CONF_THRESHOLD
    asr = StubASR(text="嗯...", conf=low_conf)
    events: list[dict] = []
    emit = await _collecting_emit(events)
    vp = VoicePipeline(asr, StubLLM(), StubTTS())

    result = await vp.run_turn_audio(b"fake-webm-bytes", emit)

    assert result is not None
    assert result.fallback is True
    assert result.asr_conf == pytest.approx(low_conf)
    assert result.reply_text in scaffold.FALLBACK_LINES
    assert result.seq == 0
    assert result.state_events == ["asr", "tts", "idle"]
    # 兜底路徑不寫 DB
    assert store.pending_count() == 0
    assert store.list_interactions() == []


# ---------------------------------------------------------------------------
# 3. LLM 回 None / 逾時 → 用 scaffold 文字
# ---------------------------------------------------------------------------

async def test_llm_returns_none_falls_back_to_scaffold_text():
    """LLM available 但 generate() 回 None → reply_text 用 scaffold.respond() 的文字。"""
    text = "我要一個蘋果"
    expected_reply = scaffold.respond(text).reply_text

    events: list[dict] = []
    emit = await _collecting_emit(events)
    vp = VoicePipeline(StubASR(), StubLLM(reply=None, available=True), StubTTS())

    result = await vp.run_turn_text(text, emit)

    assert result.fallback is False
    assert result.reply_text == expected_reply


async def test_llm_unavailable_falls_back_to_scaffold_text():
    """LLM available()=False → 完全不呼叫 generate，reply_text 用 scaffold 文字。"""
    text = "我要一個蘋果"
    expected_reply = scaffold.respond(text).reply_text

    events: list[dict] = []
    emit = await _collecting_emit(events)
    vp = VoicePipeline(StubASR(), StubLLM(available=False), StubTTS())

    result = await vp.run_turn_text(text, emit)

    assert result.fallback is False
    assert result.reply_text == expected_reply


async def test_llm_timeout_falls_back_to_scaffold_text(monkeypatch):
    """LLM 生成逾時（> LLM_TIMEOUT_S）→ 降級用 scaffold 文字，不因逾時而拋例外。"""
    monkeypatch.setattr(pipeline_mod, "LLM_TIMEOUT_S", 0.05)
    text = "我要一個蘋果"
    expected_reply = scaffold.respond(text).reply_text

    events: list[dict] = []
    emit = await _collecting_emit(events)
    slow_llm = StubLLM(reply="太慢了，不該被採用", available=True, delay_s=0.3)
    vp = VoicePipeline(StubASR(), slow_llm, StubTTS())

    result = await vp.run_turn_text(text, emit)

    assert result.fallback is False
    assert result.reply_text == expected_reply


# ---------------------------------------------------------------------------
# 4. 半雙工：併發重入 → busy
# ---------------------------------------------------------------------------

async def test_reentrant_call_while_locked_returns_busy():
    """同一 VoicePipeline 實例：上一輪仍持有鎖時再呼叫 → 立即 emit busy 並回 None。"""
    events: list[dict] = []
    emit = await _collecting_emit(events)
    vp = VoicePipeline(StubASR(), StubLLM(), StubTTS())

    async with vp._lock:  # 模擬「上一輪還在跑」
        result = await vp.run_turn_text("hello", emit)

    assert result is None
    assert {"type": "busy"} in events
    # busy 不應寫入任何 DB 紀錄
    assert store.list_interactions() == []


async def test_concurrent_run_turn_text_second_call_gets_busy():
    """真實併發：同時觸發兩輪，其中一輪應收到 busy（半雙工單一 session 只跑一輪）。"""
    events_a: list[dict] = []
    events_b: list[dict] = []
    emit_a = await _collecting_emit(events_a)
    emit_b = await _collecting_emit(events_b)

    # 讓第一輪的 LLM 生成刻意變慢，確保第二輪有機會在鎖被持有時進來
    slow_llm = StubLLM(reply="慢慢回覆", available=True, delay_s=0.2)
    vp = VoicePipeline(StubASR(), slow_llm, StubTTS())

    async def first():
        return await vp.run_turn_text("first", emit_a)

    async def second():
        await asyncio.sleep(0.02)  # 確保 first() 已先取得鎖並進入耗時階段
        return await vp.run_turn_text("second", emit_b)

    result_a, result_b = await asyncio.gather(first(), second())

    assert result_a is not None
    assert result_b is None
    assert {"type": "busy"} in events_b
    # 只有 first 那輪成功寫入 DB
    assert len(store.list_interactions()) == 1
