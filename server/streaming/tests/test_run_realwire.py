"""run_realwire.py 純組裝/錯誤處理 smoke test（不起真實音訊裝置）。"""
from server.streaming import run_realwire
from server.streaming.barge_in_gate import BargeInGate
from server.streaming.turn_manager import StreamingTurnManager


class _FakeProc:
    def __init__(self, tag):
        self.tag = tag


class _FakeTransport:
    """假 transport：input()/output() 回傳可辨識 sentinel，免真裝置/pyaudio。"""

    def input(self):
        return _FakeProc("input")

    def output(self):
        return _FakeProc("output")


def _index_of(procs, cls):
    for i, p in enumerate(procs):
        if isinstance(p, cls):
            return i
    return None


def test_check_prerequisites_returns_list():
    missing = run_realwire.check_prerequisites()
    assert isinstance(missing, list)
    # 每個缺項需是可讀字串（非 traceback）
    assert all(isinstance(m, str) and m for m in missing)


def test_build_processors_shape():
    # 驗組裝不 crash、頭尾是 transport 的 input/output。
    # 刻意不斷言 len()——長度是脆弱斷言，鏈上多接一個處理器就假紅；改以型別/順序斷言。
    procs = run_realwire.build_processors(_FakeTransport())
    assert procs[0].tag == "input"
    assert procs[-1].tag == "output"
    assert all(p is not None for p in procs)


def test_build_processors_includes_barge_in_gate():
    # G2 缺口的結構判準：鏈上必須有 BargeInGate，且位於 transport.input() 之後、
    # StreamingTurnManager 之前（gate 往 DOWNSTREAM 發 BargeInDetectedFrame，
    # manager 在下游才收得到）。
    procs = run_realwire.build_processors(_FakeTransport())
    gate_idx = _index_of(procs, BargeInGate)
    mgr_idx = _index_of(procs, StreamingTurnManager)
    assert gate_idx is not None, "build_processors 未接上 BargeInGate → 真機不會 barge-in"
    assert mgr_idx is not None
    assert 0 < gate_idx < mgr_idx, (
        f"BargeInGate 位置錯誤：gate_idx={gate_idx}, mgr_idx={mgr_idx}"
    )


def test_barge_in_gate_sits_upstream_of_stt():
    # 位置決策（2026-07-29 實測）：gate 放 STT 前後都能動——實測 FunASRSTTService 會把
    # InputAudioRawFrame 原封轉發（28 frames / 178944 bytes，rate/ch/長度全等），故
    # 「gate 在 STT 之後」今天也收得到音訊。但那是第三方實作細節，不是契約：改版若
    # 改成消費/重採樣音訊，barge-in 會無聲失效。gate 排在 STT 之前 → 直接吃 transport
    # 的原始麥克風音訊，零成本移除這個相依。本測試把該決策釘住。
    from pipecat.services.funasr.stt import FunASRSTTService

    procs = run_realwire.build_processors(_FakeTransport())
    gate_idx = _index_of(procs, BargeInGate)
    stt_idx = _index_of(procs, FunASRSTTService)
    assert stt_idx is not None
    assert gate_idx < stt_idx, (
        f"BargeInGate 應排在 STT 之前，不得依賴 STT 轉發原始音訊："
        f"gate_idx={gate_idx}, stt_idx={stt_idx}"
    )


# --- 端到端 frame-level：證明「真的接通了」，而非只證明「物件在鏈上」 ---------------

import asyncio

import pytest
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.pipeline.runner import PipelineRunner
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.frames.frames import (
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
    EndFrame,
)

from server.streaming.harness import OutputSink, _speech_burst, _load_wav_frames
from server.streaming.reply_source import StubReplySource

_FOUR = ["你好呀，我是企鵝。", "今天天氣真好。", "我們一起來玩遊戲吧。", "你想先做什麼呢？"]


class _PassThrough(FrameProcessor):
    """假 transport.input()：原封轉發（真 LocalAudioTransport 的輸入端語意）。"""

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class _PipelineFakeTransport:
    """假 transport：input()＝pass-through、output()＝計數 sink，免 pyaudio/真裝置。"""

    def __init__(self, sink):
        self._sink = sink

    def input(self):
        return _PassThrough()

    def output(self):
        return self._sink


async def _run_realwire_chain(*, barge_in: bool):
    """用 build_processors 組出的**真實鏈**跑一輪，frame 一律由鏈頭往 DOWNSTREAM 灌。

    刻意不走 harness 的 UPSTREAM 回注：真機的麥克風音訊是從 transport.input() 往下游
    流的，只有同向灌才能檢出「BargeInGate 排在 STT 之後、拿不到 InputAudioRawFrame」
    這類接線錯誤。
    """
    sink = OutputSink()
    procs = run_realwire.build_processors(
        _PipelineFakeTransport(sink), reply_source=StubReplySource(_FOUR)
    )
    manager = procs[_index_of(procs, StreamingTurnManager)]
    task = PipelineTask(Pipeline(procs))

    async def feed():
        # 第一輪輸入：真語音經 VAD 段落 → 真 STT → TranscriptionFrame → manager 開始回覆
        await task.queue_frame(VADUserStartedSpeakingFrame(start_secs=0.2))
        for f in _load_wav_frames():
            await task.queue_frame(f)
        await task.queue_frame(VADUserStoppedSpeakingFrame(stop_secs=0.5))
        if barge_in:
            # 等 bot 真的開始出聲（第一個合成 frame）再插話，避免用固定 sleep 賭時序
            for _ in range(200):
                if sink.frame_count >= 1:
                    break
                await asyncio.sleep(0.05)
            await task.queue_frame(_speech_burst())
        await asyncio.sleep(12)
        await task.queue_frame(EndFrame())

    await asyncio.gather(PipelineRunner().run(task), feed())
    return sink, manager


@pytest.mark.asyncio
async def test_realwire_chain_bargein_end_to_end():
    # G2 的真判準：真實 build_processors 鏈上，回覆播放中灌入含人聲的音訊 →
    # state_events 出現 barge_in，且合成句數明顯少於完整回覆（句界乾淨停）。
    sink, manager = await _run_realwire_chain(barge_in=True)
    assert manager.result.asr_text.strip(), "STT 應產出非空 asr_text"
    assert "barge_in" in manager.result.state_events, (
        "真實鏈上灌入人聲未觸發 barge-in → BargeInGate 沒接到音訊"
    )
    assert sink.frame_count < len(_FOUR), (
        f"barge-in 後合成句數應少於完整 {len(_FOUR)} 句，實得 {sink.frame_count}"
    )


@pytest.mark.asyncio
async def test_realwire_chain_no_bargein_speaks_all():
    # 對照組：不插話 → 4 句全合成、無 barge_in（證明上面的差異來自插話而非鏈本身壞掉）
    sink, manager = await _run_realwire_chain(barge_in=False)
    assert sink.frame_count >= len(_FOUR)
    assert "barge_in" not in manager.result.state_events
