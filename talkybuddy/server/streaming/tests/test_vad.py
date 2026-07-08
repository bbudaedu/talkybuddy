"""SpeechGate 單元測試：真 Silero VAD，canned wav 出現 started→stopped；靜音無事件。"""
import wave
from pathlib import Path

import pytest

from server.streaming.vad import SpeechGate, build_analyzer

_TB_ROOT = Path(__file__).resolve().parents[3]
_WAV = _TB_ROOT / "models" / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17" / "test_wavs" / "zh.wav"


def _load_pcm():
    with wave.open(str(_WAV), "rb") as wf:
        return wf.getframerate(), wf.readframes(wf.getnframes())


def test_speech_wav_emits_started_then_stopped():
    rate, pcm = _load_pcm()
    gate = SpeechGate(build_analyzer(rate))
    events = []
    step = 640  # 320 samples * 2 bytes；SpeechGate 內部會再依 num_frames_required 切塊
    for i in range(0, len(pcm), step):
        events += gate.push(pcm[i:i + step])
    # 尾端補一段靜音，逼 VAD 收斂到 stopped
    events += gate.push(b"\x00" * rate)  # 0.5s 靜音（int16）
    assert "started" in events
    assert events.index("started") < events.index("stopped")


def test_silence_emits_no_started():
    rate = 16000
    gate = SpeechGate(build_analyzer(rate))
    events = []
    for _ in range(20):
        events += gate.push(b"\x00" * 640)
    assert "started" not in events
