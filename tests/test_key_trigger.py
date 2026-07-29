# -*- coding: utf-8 -*-
"""實體按鍵觸發：按一下玩偶就開始講話。

**為什麼要有這個**：裝置沒有螢幕也沒有鍵盤，而 `wait_for_trigger()` 原本是
`input("按 Enter…")`——需要有人 SSH 進去按 Enter。那不是能上台的觸發方式，
而原生喚醒詞（`wake_listener.py`）真人辨識率已判 NO-GO（11 次最佳命中 1 次）。

2026-07-29 真機探測（`edge/runtime/key_probe.py`）：板上 `mtk-pmic-keys`
註冊為 `/dev/input/event1`，使用者按「自訂鍵」三次，三次都乾淨收到
**KEY_HOME（102）**的按下/放開。音量鍵沒有事件（未接到 PMIC）。

所以觸發改讀 KEY_HOME。這讓「無螢幕實體伴讀裝置」的宣稱站得住——
不再需要一台筆電開著頁面對它講話。

evdev 解析刻意抽成純函式：真機阻塞式讀取沒辦法在 CI 測，但**解析錯了一樣會
讓按鍵失效**，那部分必須有測試守著。
"""

import struct

import pytest

from edge.runtime import audio_io

EV_KEY = 0x01
EV_SYN = 0x00
KEY_HOME = 102
KEY_POWER = 116


def _event(etype: int, code: int, value: int) -> bytes:
    """組一個 evdev input_event（64-bit：sec, usec, type, code, value）。"""
    return struct.pack("llHHi", 0, 0, etype, code, value)


def test_a_press_of_the_configured_key_is_detected():
    assert audio_io._decode_key_press(_event(EV_KEY, KEY_HOME, 1), KEY_HOME) is True


def test_a_release_is_not_a_press():
    """放開（value=0）不算——否則按一下會觸發兩次。"""
    assert audio_io._decode_key_press(_event(EV_KEY, KEY_HOME, 0), KEY_HOME) is False


def test_autorepeat_is_not_a_press():
    """長按重複（value=2）不算，否則按著不放會連續開錄音。"""
    assert audio_io._decode_key_press(_event(EV_KEY, KEY_HOME, 2), KEY_HOME) is False


def test_a_different_key_is_ignored():
    """電源鍵不得觸發錄音。"""
    assert audio_io._decode_key_press(_event(EV_KEY, KEY_POWER, 1), KEY_HOME) is False


def test_non_key_events_are_ignored():
    """EV_SYN 等同步事件每次按鍵都會夾帶，不能被當成按下。"""
    assert audio_io._decode_key_press(_event(EV_SYN, 0, 1), KEY_HOME) is False


def test_a_press_is_found_among_several_events():
    """一次讀到多筆時要挑得出按下那筆（驅動常一次送 KEY + SYN）。"""
    buf = _event(EV_SYN, 0, 0) + _event(EV_KEY, KEY_HOME, 1) + _event(EV_SYN, 0, 0)
    assert audio_io._decode_key_press(buf, KEY_HOME) is True


def test_truncated_data_does_not_raise():
    """讀到半筆不得炸——炸了整條對話迴圈就停了。"""
    assert audio_io._decode_key_press(b"\x00\x01\x02", KEY_HOME) is False


@pytest.mark.parametrize("data", [b"", None])
def test_empty_data_is_safe(data):
    assert audio_io._decode_key_press(data, KEY_HOME) is False


def test_falls_back_when_no_key_device(monkeypatch):
    """開發機沒有 /dev/input/event1 → 退回原本的 Enter 觸發，不得掛死。"""
    monkeypatch.setattr(audio_io, "_key_device_usable", lambda: False)
    called = {"input": 0}

    def _fake_input(prompt=""):
        called["input"] += 1
        return ""

    monkeypatch.setattr("builtins.input", _fake_input)

    audio_io.wait_for_trigger()

    assert called["input"] == 1, "沒有退回 Enter 觸發"


def test_key_device_is_preferred_when_available(monkeypatch):
    """有實體按鍵時就不該再等 Enter——裝置上根本沒有鍵盤。"""
    monkeypatch.setattr(audio_io, "_key_device_usable", lambda: True)
    monkeypatch.setattr(audio_io, "_block_until_key_press", lambda: True)

    def _must_not_be_called(prompt=""):
        raise AssertionError("有實體按鍵卻還在等 Enter")

    monkeypatch.setattr("builtins.input", _must_not_be_called)

    audio_io.wait_for_trigger()
