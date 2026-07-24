# -*- coding: utf-8 -*-
"""test_audio_io.py — edge/runtime/audio_io.py 的擷取/播放單元測試。

不需真麥克風/喇叭：monkeypatch subprocess.run 攔截 arecord/aplay 呼叫，並用
soundfile 組出假 16k mono WAV bytes 模擬子行程寫出的錄音檔。驗收重點：

- arecord 子行程的 argv 含 S16_LE/16000/1（命中格式契約），且一律固定 argv
  串列（非 shell 字串）。
- capture_16k_mono_wav() 回傳的 bytes 命中 server/pipeline.py 的
  RIFF-sniff fast path（_is_wav_riff + soundfile 讀出 samplerate/channels）。
- sounddevice 不可用（本測試環境未安裝，或明確 monkeypatch 成不可用）時，
  自動降級呼叫 arecord，降級路徑不拋例外。
- play_wav_bytes() 呼叫 aplay 子行程；子行程失敗不拋例外（記 log 即可）。
"""

from __future__ import annotations

import io
import subprocess

import numpy as np
import soundfile as sf

from edge.runtime import audio_io
from server.pipeline import _is_wav_riff


def _fake_wav_bytes(seconds: float = 0.1, sample_rate: int = 16000) -> bytes:
    """組出一段真的 16k mono S16_LE WAV bytes，模擬 arecord 錄出的檔案內容。"""
    samples = np.zeros(int(seconds * sample_rate), dtype="int16")
    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def _fake_arecord_run(argv, **kwargs):
    """假 subprocess.run：把假 WAV bytes 寫進 argv 最後一個參數（輸出檔路徑）。"""
    out_path = argv[-1]
    with open(out_path, "wb") as f:
        f.write(_fake_wav_bytes())
    return subprocess.CompletedProcess(argv, 0)


def test_capture_arecord_argv_uses_fixed_list_with_16k_mono_s16le(monkeypatch):
    """arecord argv 含 S16_LE/16000/1，且以固定 argv 串列呼叫（非 shell 字串）。"""
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs
        return _fake_arecord_run(argv, **kwargs)

    monkeypatch.setattr(audio_io, "_import_sounddevice", lambda: None)
    monkeypatch.setattr(audio_io.subprocess, "run", fake_run)

    audio_io.capture_16k_mono_wav(seconds=1.0)

    argv = captured["argv"]
    assert isinstance(argv, list)
    assert argv[0] == "arecord"
    assert "S16_LE" in argv
    assert "16000" in argv
    assert "1" in argv
    # 不得用 shell=True（絕不字串插值），命令注入防線
    assert captured["kwargs"].get("shell") is not True


def test_capture_returns_bytes_that_hit_riff_fast_path(monkeypatch):
    """capture_16k_mono_wav() 回傳的 bytes 命中 _is_wav_riff，且 samplerate/channels 正確。"""
    monkeypatch.setattr(audio_io, "_import_sounddevice", lambda: None)
    monkeypatch.setattr(audio_io.subprocess, "run", _fake_arecord_run)

    wav = audio_io.capture_16k_mono_wav(seconds=1.0)

    assert isinstance(wav, bytes)
    assert _is_wav_riff(wav[:12])
    samples, sample_rate = sf.read(io.BytesIO(wav), dtype="int16", always_2d=False)
    assert sample_rate == 16000
    channels = 1 if getattr(samples, "ndim", 1) <= 1 else samples.shape[1]
    assert channels == 1


def test_sounddevice_unavailable_in_this_environment():
    """本測試環境（venv）未安裝 sounddevice：_import_sounddevice() 應回 None 不拋。"""
    assert audio_io._import_sounddevice() is None


def test_capture_degrades_to_arecord_when_sounddevice_unavailable(monkeypatch):
    """sounddevice 不可用時自動降級呼叫 arecord；降級路徑不拋例外。"""
    monkeypatch.setattr(audio_io, "_import_sounddevice", lambda: None)
    monkeypatch.setattr(audio_io.subprocess, "run", _fake_arecord_run)

    wav = audio_io.capture_16k_mono_wav(seconds=1.0)  # 不應拋例外

    assert isinstance(wav, bytes) and len(wav) > 0


def test_play_wav_bytes_uses_aplay_with_fixed_argv(monkeypatch):
    """play_wav_bytes() 呼叫 aplay，且以固定 argv 串列（非 shell 字串）。"""
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(audio_io, "_import_sounddevice", lambda: None)
    monkeypatch.setattr(audio_io.subprocess, "run", fake_run)

    audio_io.play_wav_bytes(_fake_wav_bytes())

    assert calls
    argv, kwargs = calls[0]
    assert argv[0] == "aplay"
    assert kwargs.get("shell") is not True


def test_play_wav_bytes_does_not_raise_on_subprocess_failure(monkeypatch):
    """aplay 子行程失敗（CalledProcessError）不拋例外，只記 log。"""

    def fake_run(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(audio_io, "_import_sounddevice", lambda: None)
    monkeypatch.setattr(audio_io.subprocess, "run", fake_run)

    audio_io.play_wav_bytes(_fake_wav_bytes())  # 不應拋例外


def test_no_shell_true_or_ffmpeg_in_module_source():
    """原始碼層級防線：audio_io.py 不得出現 shell=True 或 ffmpeg 字樣。"""
    import inspect

    source = inspect.getsource(audio_io)
    assert "shell=True" not in source
    assert "ffmpeg" not in source
