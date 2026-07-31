# -*- coding: utf-8 -*-
"""SenseVoiceSTTService 在**真實 pipecat pipeline 驅動下**的行為測試。

`test_pipecat_stt_tts.py` 直接呼叫 `run_stt()`，那繞過了 pipeline 的分派機制——
所以它抓不到「繼承錯基底類別」這種結構性錯誤（實際上真的漏抓了一次：本 adapter
一度繼承 `STTService`，那會對每一個 20ms 音訊 frame 觸發一次 147ms 的離線推論，
單元測試全綠但上板必爆）。

這裡改用 pipecat 官方的 `pipecat.tests.utils.run_test`，讓 frame 真的流過 processor，
驗證的是**分段行為**而不是單次辨識邏輯。
"""

from __future__ import annotations

import threading

import pytest
from pipecat.frames.frames import (
    InputAudioRawFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.tests.utils import run_test

from edge.runtime.pipecat_adapters.sensevoice_stt import SenseVoiceSTTService

SAMPLE_RATE = 16000


class _RecordingStream:
    def __init__(self, text: str, calls: list):
        self._text = text
        self._calls = calls

    def accept_waveform(self, rate, samples):
        self._calls.append((rate, len(samples)))

    @property
    def result(self):
        return type("R", (), {"text": self._text})()


class _RecordingRecognizer:
    """記下每一次辨識呼叫，用來驗證「一句話只辨識一次」。"""

    def __init__(self, text: str = "我想要蘋果"):
        self.calls: list[tuple[int, int]] = []
        self._text = text

    def create_stream(self):
        return _RecordingStream(self._text, self.calls)

    def decode_stream(self, stream):
        pass


class _Engine:
    def __init__(self, recognizer):
        self._recognizer = recognizer
        self._opencc = None
        self._lock = threading.Lock()

    def _ensure_model(self):
        return self._recognizer

    def _ensure_opencc(self):
        return None


def _audio(n_bytes: int) -> InputAudioRawFrame:
    return InputAudioRawFrame(
        audio=b"\x01\x00" * (n_bytes // 2), sample_rate=SAMPLE_RATE, num_channels=1
    )


@pytest.mark.asyncio
async def test_one_utterance_triggers_exactly_one_recognition():
    """一整句話只能辨識一次。

    這是繼承 `SegmentedSTTService` 的全部理由。若退回 `STTService`，
    下面每一個 audio frame 都會觸發一次辨識，這個斷言會變成 3 或更多。
    """
    rec = _RecordingRecognizer()
    svc = SenseVoiceSTTService(engine=_Engine(rec), sample_rate=SAMPLE_RATE)

    frames_to_send = [
        VADUserStartedSpeakingFrame(),
        _audio(640),
        _audio(640),
        _audio(640),
        VADUserStoppedSpeakingFrame(),
    ]

    # 刻意不用 expected_down_frames 硬比對整串型別：pipeline 會原樣轉發
    # VAD／音訊 frame，還會插 STTMetadataFrame，那串會隨 pipecat 版本變動。
    # 這裡只斷言我們真正在乎的行為。
    down, _ = await run_test(svc, frames_to_send=frames_to_send, expected_down_frames=None)

    assert len(rec.calls) == 1, f"一句話應只辨識一次，實際 {len(rec.calls)} 次"
    assert sum(isinstance(f, TranscriptionFrame) for f in down) == 1


@pytest.mark.asyncio
async def test_pipeline_sets_sample_rate_and_feeds_raw_pcm():
    """pipeline 啟動後取樣率要正確，且模型拿到的是 raw PCM 不是 WAV。

    兩件事一起驗：
    - `sample_rate` 建構後是 0，要靠 `StartFrame` 補上（pipecat 通用陷阱）
    - `wants_wav_segments=False` 必須生效，否則模型會吃到 44 bytes 的 RIFF header
    """
    rec = _RecordingRecognizer()
    svc = SenseVoiceSTTService(engine=_Engine(rec), sample_rate=SAMPLE_RATE)
    assert svc.sample_rate == 0, "建構後應仍是 0（pipecat 的既定行為）"

    await run_test(
        svc,
        frames_to_send=[
            VADUserStartedSpeakingFrame(),
            _audio(640),
            _audio(640),
            VADUserStoppedSpeakingFrame(),
        ],
        expected_down_frames=None,
    )

    assert svc.sample_rate == SAMPLE_RATE, "StartFrame 後取樣率應已補上"

    rate, n_samples = rec.calls[0]
    assert rate == SAMPLE_RATE
    # 送了 2×640 bytes = 1280 bytes int16 = 640 samples。
    # 若 wants_wav_segments 沒生效，會多出 44 bytes header（22 samples）。
    assert n_samples == 640, f"應收到純 PCM 的 640 samples，實際 {n_samples}"


@pytest.mark.asyncio
async def test_speech_with_no_audio_does_not_call_model():
    """VAD 誤觸（開始又立刻結束、中間沒有音訊）不該驚動模型。

    決賽會場很吵，這種空段落會很多；每次都跑一輪 147ms 推論是浪費。
    """
    rec = _RecordingRecognizer()
    svc = SenseVoiceSTTService(engine=_Engine(rec), sample_rate=SAMPLE_RATE)

    down, _ = await run_test(
        svc,
        frames_to_send=[VADUserStartedSpeakingFrame(), VADUserStoppedSpeakingFrame()],
        expected_down_frames=None,
    )

    assert rec.calls == [], "空語段不該呼叫模型"
    assert not any(isinstance(f, TranscriptionFrame) for f in down), "空語段不該產出逐字稿"
