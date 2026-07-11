"""A2 real-wiring 自動化驗收：canned zh.wav 餵完整 VAD→STT→manager→TTS→sink 鏈。

判準 A（有回覆）：餵一段語音 → sink 收到 ≥1 個合成音訊 frame，且 STT 產非空 asr_text。
判準 B（barge-in 淨停）：回覆合成期間注入第二次 VAD 開口 → sink 實際合成句數 ≤2，
                         且明顯少於無插話時的完整句數。二元淨判，不量延遲。

不依賴真實音訊裝置（用 InputAudioRawFrame 餵 canned WAV，同 harness）。

USE_STT：本環境 SenseVoice 真 STT 已探查可用（Task 3 Step 1，asr_text 非空、frames=4），
故走完整鏈（no_stt=False）。若日後環境 SenseVoice 不可用，改 USE_STT=False 退回 spec
允許的妥協 — 在 STT 之後注入 TranscriptionFrame，barge-in 淨停仍有效。
"""
import pytest

from server.streaming import harness
from server.streaming.batch_reply_source import BatchReplySource

# Task 3 Step 1 探查：SenseVoice 真 STT 可用 → 走完整鏈。
USE_STT = True


@pytest.mark.asyncio
async def test_criterion_a_has_reply():
    sink, mgr = await harness.run_once(
        BatchReplySource(), barge_in=False, no_stt=not USE_STT
    )
    assert sink.frame_count >= 1, "sink 應收到至少一個合成音訊 frame"
    if USE_STT:
        assert mgr.result.asr_text.strip(), "STT 應產出非空 asr_text"


@pytest.mark.asyncio
async def test_criterion_b_barge_in_clean_stop():
    full, _ = await harness.run_once(
        BatchReplySource(), barge_in=False, no_stt=not USE_STT
    )
    cut, mgr = await harness.run_once(
        BatchReplySource(), barge_in=True, no_stt=not USE_STT
    )
    assert cut.frame_count <= 2, f"barge-in 後合成句數應 ≤2，實得 {cut.frame_count}"
    assert cut.frame_count < full.frame_count, (
        f"barge-in（{cut.frame_count}）應明顯少於完整（{full.frame_count}）"
    )
    assert "barge_in" in mgr.result.state_events
