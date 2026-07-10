"""StreamingTurnManager 整合測試（真 sherpa + StubReplySource，免真 LLM）。"""
import pytest

from server.streaming.harness import run_once
from server.streaming.reply_source import StubReplySource

_FOUR = ["你好呀，我是企鵝。", "今天天氣真好。", "我們一起來玩遊戲吧。", "你想先做什麼呢？"]


@pytest.mark.asyncio
async def test_no_bargein_synthesizes_all_sentences():
    # criterion #1：pipeline 不 crash；無 barge-in → N 句全合成
    sink, manager = await run_once(StubReplySource(_FOUR), barge_in=False)
    assert sink.frame_count >= len(_FOUR)  # 每句至少 1 個 TTSAudioRawFrame
    # criterion #4：無 barge-in 輪 TurnResult 欄位非空
    assert manager.result.asr_text != ""
    assert manager.result.reply_text != ""
    assert "barge_in" not in manager.result.state_events


@pytest.mark.asyncio
async def test_bargein_at_second_sentence_stops_clean():
    # criterion #2：第 2 句期間注入開口 → sink 句數 ≤ 2（句界乾淨停），比照 spike 4→1
    sink, manager = await run_once(StubReplySource(_FOUR), barge_in=True)
    # criterion #2 的淨判證據＝實際「合成」句數 ≤2（句界乾淨停）。
    # 串流語意（2026-07-08 使用者裁定）：manager 會瞬間預排全部句子成 TTSSpeakFrame，
    # reply_text 記錄的是「已排入 TTS 的句子」，非「實際合成/播放的半段」；真正被
    # barge-in 中止的證據是下游 sink 實際合成的 TTSAudioRawFrame 數 ≤2，而非 reply_text 長度。
    assert sink.frame_count <= 2
    # criterion #4：barge-in 輪 state_events 含 barge_in；有回覆內容產出
    assert "barge_in" in manager.result.state_events
    assert manager.result.reply_text != ""


from pipecat.frames.frames import VADUserStartedSpeakingFrame


@pytest.mark.asyncio
async def test_transport_vad_frame_no_longer_triggers_bargein():
    # 判準 D：舊耦合已移除——bot 說話中收 transport VADUserStartedSpeakingFrame → 不 barge-in
    sink, manager = await run_once(
        StubReplySource(_FOUR),
        barge_in=True,
        inject_frame_factory=lambda: VADUserStartedSpeakingFrame(start_secs=0.2),
    )
    assert sink.frame_count >= len(_FOUR)  # 4 句全合成
    assert "barge_in" not in manager.result.state_events
