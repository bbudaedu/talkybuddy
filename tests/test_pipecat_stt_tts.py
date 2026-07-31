# -*- coding: utf-8 -*-
"""SenseVoiceSTTService 與 EdgeVitsTTSService 單元測試。

一律注入假引擎：真的模型只在板子上，而且載入要 1.9s、吃 664MB RSS，
不適合放進單元測試。這裡只驗證 adapter 自己的邏輯——型別轉換、降級路徑、
WAV header 有沒有剝掉。
"""

from __future__ import annotations

import io
import threading
import wave

import numpy as np
import pytest
from pipecat.frames.frames import ErrorFrame, TranscriptionFrame, TTSAudioRawFrame

from edge.runtime.pipecat_adapters.edge_tts import EdgeVitsTTSService, _has_cjk, _wav_to_pcm
from edge.runtime.pipecat_adapters.sensevoice_stt import SenseVoiceSTTService


# --------------------------------------------------------------------------
# STT
# --------------------------------------------------------------------------
class _FakeStream:
    def __init__(self, text: str):
        self._text = text
        self.accepted: tuple[int, np.ndarray] | None = None

    def accept_waveform(self, rate, samples):
        self.accepted = (rate, samples)

    @property
    def result(self):
        return type("R", (), {"text": self._text})()


class _FakeRecognizer:
    def __init__(self, text: str):
        self._text = text
        self.last_stream: _FakeStream | None = None

    def create_stream(self):
        self.last_stream = _FakeStream(self._text)
        return self.last_stream

    def decode_stream(self, stream):
        pass


class _FakeEngine:
    """模擬 SenseVoiceASREngine 的公開契約（available/_ensure_model/_ensure_opencc）。"""

    def __init__(self, recognizer=None, opencc=None):
        self._recognizer = recognizer
        self._opencc = opencc
        self._lock = threading.Lock()

    def _ensure_model(self):
        return self._recognizer

    def _ensure_opencc(self):
        return self._opencc


async def _collect(agen):
    return [f async for f in agen]


def _with_rate(svc, rate: int):
    """補上 pipeline 才會做的取樣率設定。

    pipecat 所有 service 的 `sample_rate` 建構後都是 **0**——傳進建構子的值只存到
    `_init_sample_rate`，要等 pipeline 送 `StartFrame` 才會生效
    （`stt_service.py:315`、`tts_service.py:549`：
    `self._sample_rate = self._init_sample_rate or frame.audio_in_sample_rate`）。
    `VADAnalyzer` 也是同一個形狀。單元測試不跑完整 pipeline，所以要自己補這一步，
    否則會拿 0 去餵模型。
    """
    svc._sample_rate = rate
    return svc


def test_sample_rate_is_zero_until_pipeline_starts():
    """釘住 pipecat 的陷阱：建構子的 sample_rate 不會立刻生效。

    這不是我們的 bug，是 pipecat 的設計——但它咬過兩次（VADAnalyzer 也一樣），
    所以用測試釘住。若哪天 pipecat 改成建構即生效，這個測試會紅，那是好事：
    代表 `_with_rate()` 那層補丁可以拿掉了。
    """
    assert SenseVoiceSTTService(engine=_FakeEngine(None), sample_rate=16000).sample_rate == 0
    assert EdgeVitsTTSService(engine=_FakeTTSEngine(None), sample_rate=22050).sample_rate == 0


@pytest.mark.asyncio
async def test_stt_empty_audio_yields_nothing():
    """沒有音訊就不該產生任何 frame。"""
    svc = SenseVoiceSTTService(engine=_FakeEngine(_FakeRecognizer("哈囉")), sample_rate=16000)
    assert await _collect(svc.run_stt(b"")) == []


@pytest.mark.asyncio
async def test_stt_unavailable_model_yields_error_frame():
    """模型載不起來要明講，不能靜靜吞掉——那個症狀跟麥克風壞掉一樣難查。"""
    svc = SenseVoiceSTTService(engine=_FakeEngine(recognizer=None), sample_rate=16000)
    frames = await _collect(svc.run_stt(b"\x00\x01" * 100))
    assert len(frames) == 1
    assert isinstance(frames[0], ErrorFrame)


@pytest.mark.asyncio
async def test_stt_emits_transcription_frame():
    """正常辨識產出一個 TranscriptionFrame。"""
    svc = SenseVoiceSTTService(engine=_FakeEngine(_FakeRecognizer("我想要蘋果")), sample_rate=16000)
    frames = await _collect(svc.run_stt(b"\x00\x01" * 100))
    assert len(frames) == 1
    assert isinstance(frames[0], TranscriptionFrame)
    assert frames[0].text == "我想要蘋果"


@pytest.mark.asyncio
async def test_stt_empty_result_is_treated_as_noise():
    """SenseVoice 回空字串 = 雜音兜底，不要把噪音送進 LLM（決賽會場很吵）。"""
    svc = SenseVoiceSTTService(engine=_FakeEngine(_FakeRecognizer("   ")), sample_rate=16000)
    assert await _collect(svc.run_stt(b"\x00\x01" * 100)) == []


@pytest.mark.asyncio
async def test_stt_converts_int16_pcm_to_normalised_float32():
    """餵給模型的必須是 [-1,1] 的 float32，與 soundfile 讀出的值域一致。"""
    rec = _FakeRecognizer("測試")
    svc = _with_rate(SenseVoiceSTTService(engine=_FakeEngine(rec), sample_rate=16000), 16000)
    pcm = np.array([0, 32767, -32768, 16384], dtype=np.int16).tobytes()

    await _collect(svc.run_stt(pcm))

    rate, samples = rec.last_stream.accepted
    assert rate == 16000
    assert samples.dtype == np.float32
    assert samples.max() <= 1.0 and samples.min() >= -1.0
    np.testing.assert_allclose(samples, [0.0, 32767 / 32768, -1.0, 0.5], rtol=1e-6)


@pytest.mark.asyncio
async def test_stt_applies_opencc_conversion():
    """簡轉繁要生效（模型輸出是簡體）。"""

    class _CC:
        def convert(self, t):
            return "蘋果"

    svc = SenseVoiceSTTService(
        engine=_FakeEngine(_FakeRecognizer("苹果"), opencc=_CC()), sample_rate=16000
    )
    frames = await _collect(svc.run_stt(b"\x00\x01" * 100))
    assert frames[0].text == "蘋果"


@pytest.mark.asyncio
async def test_stt_survives_opencc_failure():
    """簡轉繁失敗就出簡體，總比整輪對話掉了好（沿用既有降級策略）。"""

    class _BadCC:
        def convert(self, t):
            raise RuntimeError("opencc 壞了")

    svc = SenseVoiceSTTService(
        engine=_FakeEngine(_FakeRecognizer("苹果"), opencc=_BadCC()), sample_rate=16000
    )
    frames = await _collect(svc.run_stt(b"\x00\x01" * 100))
    assert frames[0].text == "苹果"


@pytest.mark.asyncio
async def test_stt_recognizer_exception_is_not_fatal():
    """辨識爆炸不該讓 pipeline 掛掉，當作沒聽到就好。"""

    class _Boom(_FakeRecognizer):
        def decode_stream(self, stream):
            raise RuntimeError("native crash")

    svc = SenseVoiceSTTService(engine=_FakeEngine(_Boom("x")), sample_rate=16000)
    assert await _collect(svc.run_stt(b"\x00\x01" * 100)) == []


# --------------------------------------------------------------------------
# TTS
# --------------------------------------------------------------------------
def _make_wav(pcm: bytes, rate: int = 22050) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    return buf.getvalue()


class _FakeTTSEngine:
    def __init__(self, wav: bytes | None):
        self._wav = wav
        self.called_with: list = []

    def synth(self, segments):
        self.called_with.append(segments)
        return self._wav


def test_has_cjk_distinguishes_languages():
    """語言判斷的啟發式：含 CJK 走中文聲音，純英文走英文聲音。"""
    assert _has_cjk("我想要蘋果") is True
    assert _has_cjk("I want an apple") is False
    assert _has_cjk("I want 蘋果") is True


def test_wav_to_pcm_strips_header():
    """header 必須真的被剝掉——留著會在開頭爆一聲。"""
    pcm = b"\x01\x02\x03\x04" * 10
    wav = _make_wav(pcm)
    assert len(wav) > len(pcm)  # 確認測試資料真的有 header
    assert _wav_to_pcm(wav) == pcm


@pytest.mark.asyncio
async def test_tts_empty_text_yields_nothing():
    """空字串不合成。"""
    svc = EdgeVitsTTSService(engine=_FakeTTSEngine(_make_wav(b"\x00" * 100)))
    assert await _collect(svc.run_tts("   ", "ctx")) == []


@pytest.mark.asyncio
async def test_tts_failure_yields_error_frame():
    """synth 回 None（所有 voice 都缺）要明講，不要讓玩偶靜靜變啞。"""
    svc = EdgeVitsTTSService(engine=_FakeTTSEngine(None))
    frames = await _collect(svc.run_tts("你好", "ctx"))
    assert len(frames) == 1
    assert isinstance(frames[0], ErrorFrame)


@pytest.mark.asyncio
async def test_tts_emits_header_free_pcm_chunks():
    """輸出必須是剝掉 header 的 raw PCM，且完整重組後與原 PCM 一致。"""
    pcm = bytes(range(256)) * 40  # 10240 bytes
    svc = _with_rate(
        EdgeVitsTTSService(engine=_FakeTTSEngine(_make_wav(pcm)), chunk_bytes=4410), 22050
    )

    frames = await _collect(svc.run_tts("你好", "ctx"))

    assert all(isinstance(f, TTSAudioRawFrame) for f in frames)
    assert b"".join(f.audio for f in frames) == pcm
    assert b"RIFF" not in frames[0].audio[:8]
    assert len(frames) == 3  # 10240 / 4410 無條件進位
    assert all(f.context_id == "ctx" for f in frames)
    assert all(f.sample_rate == 22050 for f in frames)


@pytest.mark.asyncio
async def test_tts_picks_language_by_script():
    """中文文字要送 zh 聲音，英文送 en。"""
    eng = _FakeTTSEngine(_make_wav(b"\x00" * 100))
    svc = EdgeVitsTTSService(engine=eng)

    await _collect(svc.run_tts("我想要蘋果", "ctx"))
    await _collect(svc.run_tts("I want an apple", "ctx"))

    assert eng.called_with[0] == [("zh", "我想要蘋果")]
    assert eng.called_with[1] == [("en", "I want an apple")]


@pytest.mark.asyncio
async def test_tts_segments_provider_overrides_heuristic():
    """上游若能正確分段中英，adapter 要讓它接手，而不是硬套啟發式。"""
    eng = _FakeTTSEngine(_make_wav(b"\x00" * 100))
    svc = EdgeVitsTTSService(
        engine=eng, segments_provider=lambda t: [("zh", "蘋果"), ("en", "apple")]
    )

    await _collect(svc.run_tts("蘋果 apple", "ctx"))

    assert eng.called_with[0] == [("zh", "蘋果"), ("en", "apple")]


@pytest.mark.asyncio
async def test_tts_malformed_wav_yields_error_frame():
    """引擎給了不是 WAV 的東西，要報錯而不是把垃圾當音訊播出去。"""
    svc = EdgeVitsTTSService(engine=_FakeTTSEngine(b"not a wav at all"))
    frames = await _collect(svc.run_tts("你好", "ctx"))
    assert len(frames) == 1
    assert isinstance(frames[0], ErrorFrame)
