# -*- coding: utf-8 -*-
"""server.timestretch（WSOLA 保持音高變速）單元測試。

驗證：放慢後長度變長、音高不變（FFT 主頻）、speed=1/短輸入 passthrough、
奇數長度不動、缺 numpy → None、大振幅不溢位。全程用純 numpy 產測試音，不連網。
"""

from __future__ import annotations

import math
import struct
import sys

import numpy as np

from server.timestretch import stretch_pcm16

RATE = 22050


def _sine_pcm(freq: float, seconds: float, amp: int = 12000, rate: int = RATE) -> bytes:
    """產生單聲道 16-bit LE 正弦波 PCM bytes。"""
    n = int(seconds * rate)
    t = np.arange(n) / rate
    samples = (amp * np.sin(2 * math.pi * freq * t)).astype("<i2")
    return samples.tobytes()


def _dominant_freq(pcm: bytes, rate: int = RATE) -> float:
    """回傳 PCM 的 FFT 主頻（Hz）。"""
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    x = x - x.mean()
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), 1.0 / rate)
    return float(freqs[int(np.argmax(spec))])


# --- 長度：放慢 → 變長 ---------------------------------------------------

def test_stretch_lengthens_output_when_slower():
    pcm = _sine_pcm(440, 0.5)          # 11025 樣本
    out = stretch_pcm16(pcm, 0.8, RATE)
    assert out is not None
    in_samples = len(pcm) // 2
    out_samples = len(out) // 2
    expected = in_samples / 0.8
    # 允許 ±8%（尾端未合成的殘框）
    assert abs(out_samples - expected) <= 0.08 * expected
    assert out_samples > in_samples   # 一定變長


# --- 音高不變（保持音高才算 time-stretch，而非 resample）----------------

def test_stretch_preserves_pitch():
    pcm = _sine_pcm(440, 0.5)
    out = stretch_pcm16(pcm, 0.8, RATE)
    assert out is not None
    freq = _dominant_freq(out)
    # WSOLA 保持音高 → 仍在 440Hz 附近；naive resample 會掉到 ~352Hz。
    assert 415 < freq < 465, f"主頻 {freq} 偏離 440，疑似變成 resample"


# --- passthrough：speed=1 / 短輸入 / 奇數長度 / speed<=0 -----------------

def test_speed_one_returns_original_bytes():
    pcm = _sine_pcm(440, 0.3)
    assert stretch_pcm16(pcm, 1.0, RATE) == pcm


def test_short_input_returned_unchanged():
    pcm = b"\x01\x00" * 100           # 100 樣本 < frame_len → 不處理
    assert stretch_pcm16(pcm, 0.8, RATE) == pcm


def test_odd_length_returned_unchanged():
    pcm = b"\x01\x02\x03"             # 非合法 16-bit PCM → 原樣回傳，不 raise
    assert stretch_pcm16(pcm, 0.8, RATE) == pcm


def test_non_positive_speed_returns_original():
    pcm = _sine_pcm(440, 0.3)
    assert stretch_pcm16(pcm, 0.0, RATE) == pcm


# --- 失敗設計：缺 numpy → None（呼叫端回退原音訊）----------------------

def test_returns_none_when_numpy_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "numpy", None)   # import numpy → ImportError
    pcm = _sine_pcm(440, 0.5)
    assert stretch_pcm16(pcm, 0.8, RATE) is None


# --- 大振幅不溢位、振幅保留（不塌陷也不整片削頂）------------------------

def test_no_overflow_amplitude_preserved():
    pcm = _sine_pcm(300, 0.5, amp=30000)
    out = stretch_pcm16(pcm, 0.85, RATE)
    assert out is not None
    y = np.frombuffer(out, dtype="<i2").astype(np.int64)
    peak = int(np.abs(y).max())
    assert peak <= 32767                 # 合法 int16
    assert 22000 < peak < 32767          # 振幅保留、非塌陷、非整片飽和
    # 飽和樣本佔比極低（若有 runaway 會大量削頂）
    saturated = int((np.abs(y) >= 32767).sum())
    assert saturated < 0.01 * len(y)
