# -*- coding: utf-8 -*-
"""test_pipeline_wav_fastpath.py — RIFF-sniff fast path 驗證（EDGE-01）。

涵蓋 CONTRACTS.md / 07-01-PLAN.md behavior：
- 原生 16kHz mono WAV bytes（Genio 520 ALSA 擷取）→ soundfile fast path，
  全程不呼叫 subprocess.run（無 ffmpeg）。
- 非 WAV bytes（無 RIFF/WAVE magic，模擬瀏覽器 WebM/Opus）→ 仍走既有 ffmpeg
  subprocess 分支，PC 原型行為不破壞。
- WAV 但取樣率/聲道不符（非 16k mono）在 edge profile 下明確 raise，不靜默
  偽成功、不自作 resample。
"""

from __future__ import annotations

import io
import os

import numpy as np
import pytest
import soundfile as sf

from server import config, pipeline as pipeline_mod


def _make_wav_bytes(sample_rate: int, channels: int, duration_s: float = 0.1) -> bytes:
    """用 soundfile 即時產生合法 WAV bytes（記憶體內，不落地暫存檔）。"""
    n_samples = int(sample_rate * duration_s)
    if channels == 1:
        samples = np.zeros(n_samples, dtype="float32")
    else:
        samples = np.zeros((n_samples, channels), dtype="float32")
    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def test_wav_16k_mono_fast_path_skips_subprocess(monkeypatch):
    """WAV bytes 為 16kHz mono → fast path 命中，subprocess.run 完全不被呼叫。"""
    called = {"count": 0}

    def _spy_run(*args, **kwargs):
        called["count"] += 1
        raise AssertionError("subprocess.run 不應被呼叫（應走 fast path）")

    monkeypatch.setattr(pipeline_mod.subprocess, "run", _spy_run)

    wav_bytes = _make_wav_bytes(16000, 1)
    wav_path = pipeline_mod._webm_to_wav(wav_bytes)

    try:
        assert called["count"] == 0
        assert wav_path is not None
        assert os.path.exists(wav_path)
        samples, sr = sf.read(wav_path)
        assert sr == 16000
    finally:
        if wav_path and os.path.exists(wav_path):
            os.unlink(wav_path)


def test_non_wav_bytes_falls_back_to_ffmpeg_subprocess(monkeypatch):
    """非 WAV bytes（無 RIFF/WAVE magic）→ 走既有 ffmpeg subprocess 分支。"""
    called = {"count": 0}

    class _FakeProc:
        returncode = 0
        stderr = b""

    def _spy_run(cmd, capture_output=False, timeout=None):
        called["count"] += 1
        out_path = cmd[-1]
        silence = np.zeros(int(16000 * 0.05), dtype="float32")
        sf.write(out_path, silence, 16000, subtype="PCM_16")
        return _FakeProc()

    monkeypatch.setattr(pipeline_mod.subprocess, "run", _spy_run)

    fake_webm_bytes = b"\x1aE\xdf\xa3" + b"not-a-wav-container-payload"
    wav_path = pipeline_mod._webm_to_wav(fake_webm_bytes)

    try:
        assert called["count"] == 1
        assert wav_path is not None
    finally:
        if wav_path and os.path.exists(wav_path):
            os.unlink(wav_path)


def test_wav_spec_mismatch_raises_on_edge_profile(monkeypatch):
    """WAV 但非 16k mono（8k stereo）在 edge profile 下明確 raise，不靜默偽成功。"""
    monkeypatch.setattr(config, "PIPELINE_PROFILE", "edge")

    def _spy_run(*args, **kwargs):
        raise AssertionError("edge 規格不符應直接 raise，不應嘗試 ffmpeg subprocess")

    monkeypatch.setattr(pipeline_mod.subprocess, "run", _spy_run)

    wav_bytes = _make_wav_bytes(8000, 2)

    with pytest.raises(Exception):
        pipeline_mod._webm_to_wav(wav_bytes)


def test_wav_spec_mismatch_message_has_no_tempfile_path(monkeypatch):
    """例外訊息只點名取樣率/聲道不符語意，不得嵌入暫存檔完整路徑（T-07-03）。"""
    monkeypatch.setattr(config, "PIPELINE_PROFILE", "edge")
    wav_bytes = _make_wav_bytes(8000, 2)

    with pytest.raises(Exception) as exc_info:
        pipeline_mod._webm_to_wav(wav_bytes)

    assert "/tmp" not in str(exc_info.value)
    assert ".wav" not in str(exc_info.value)
