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


# ---------------------------------------------------------------------------
# 觸發鍵改用 KEY_POWER(116)
#
# 2026-07-30 真機實測（繞過 Python、直接 dd 讀 evdev，並以耳機孔插拔事件作為
# 觀測方法有效性的對照組）：
#   - 自訂鍵（KEY_HOME/102）：按數十次、跨重開機，一律 0 bytes → **不可用**
#   - KEY_POWER(116)：短按 → 48 bytes = `EV_KEY code=116 value=1` + `EV_SYN`
# 所以 2026-07-29 記錄的「按自訂鍵三次都收到 KEY_HOME」是錯的：kernel 位元圖
# 確實註冊了 102（已獨立驗證兩次），但**註冊不等於那顆實體鍵接得上**。
# ---------------------------------------------------------------------------

def test_default_key_code_is_the_one_that_actually_works():
    """預設鍵碼必須是實測可用的 116，不是已證實收不到事件的 102。

    留著 102 當預設不只是「沒反應」——`_key_device_usable()` 只驗節點存在且可讀，
    event1 兩者都成立，於是會進入無限阻塞、連 Enter 降級都走不到（死鎖）。
    """
    assert audio_io._KEY_CODE == 116


def test_a_power_key_press_is_decoded():
    """真機抓到的那兩筆 bytes 必須被解析成一次按下。"""
    real_capture = _event(EV_KEY, KEY_POWER, 1) + _event(EV_SYN, 0, 0)
    assert audio_io._decode_key_press(real_capture, 116) is True


# ---------------------------------------------------------------------------
# 用 KEY_POWER 當觸發鍵的前提：logind 必須放手，否則按下去是關機
# ---------------------------------------------------------------------------

def test_handle_power_key_is_read_from_config():
    """要能判斷 logind 會不會把 power 鍵吃掉並關機。"""
    conf = '[Login]\nHandlePowerKey=ignore\nHandlePowerKeyLongPress=ignore\n'
    assert audio_io._parse_handle_power_key(conf) == "ignore"


def test_the_dangerous_default_is_detected():
    """logind 預設 `poweroff`——這時按下去會關機，必須偵測得出來。"""
    assert audio_io._parse_handle_power_key("[Login]\nHandlePowerKey=poweroff\n") == "poweroff"


def test_commented_out_setting_is_not_taken_as_effective():
    """註解掉的設定不算生效——Yocto 的 logind.conf 預設整份都是註解。"""
    conf = '[Login]\n#HandlePowerKey=ignore\n'
    assert audio_io._parse_handle_power_key(conf) is None


def test_last_setting_wins():
    """同一份檔案內重複設定時，以最後一筆為準（systemd 語意）。"""
    conf = '[Login]\nHandlePowerKey=poweroff\nHandlePowerKey=ignore\n'
    assert audio_io._parse_handle_power_key(conf) == "ignore"


def test_power_key_guard_warns_when_logind_would_power_off(monkeypatch, capsys):
    """守門：鍵碼是 116 但 logind 沒放手時，必須大聲警告而不是靜默讓人按下去關機。"""
    monkeypatch.setattr(audio_io, "_KEY_CODE", 116)
    monkeypatch.setattr(audio_io, "_effective_handle_power_key", lambda: "poweroff")

    assert audio_io._power_key_guard_ok() is False
    out = capsys.readouterr().out
    assert "關機" in out, f"警告訊息沒提到關機風險：{out!r}"


def test_power_key_guard_passes_when_logind_ignores(monkeypatch):
    monkeypatch.setattr(audio_io, "_KEY_CODE", 116)
    monkeypatch.setattr(audio_io, "_effective_handle_power_key", lambda: "ignore")
    assert audio_io._power_key_guard_ok() is True


def test_guard_is_irrelevant_for_non_power_keys(monkeypatch):
    """用別的鍵碼時不該被 logind 設定影響。"""
    monkeypatch.setattr(audio_io, "_KEY_CODE", 102)
    monkeypatch.setattr(audio_io, "_effective_handle_power_key", lambda: "poweroff")
    assert audio_io._power_key_guard_ok() is True


# ---------------------------------------------------------------------------
# 死鎖：按鍵不送事件時必須還能脫身
# ---------------------------------------------------------------------------

class _FakeTty:
    def isatty(self):
        return True

    def readline(self):
        return "\n"


class _FakePipe:
    def isatty(self):
        return False


def test_enter_still_triggers_so_a_dead_key_cannot_deadlock(monkeypatch, tmp_path):
    """按鍵永遠不送事件時，Enter 必須還能觸發。

    這是 2026-07-30「卡在自訂按鍵」的根因：`_key_device_usable()` 只驗節點存在
    且可讀（event1 兩者都成立），於是 `wait_for_trigger()` 阻塞在
    `_block_until_key_press()`，下面寫好的 `input()` 降級**永遠走不到**——
    不是「按鍵沒反應」而已，是整條對話迴圈死鎖。
    """
    dev = tmp_path / "event_fake"
    dev.write_bytes(b"")
    monkeypatch.setattr(audio_io, "_resolve_key_device", lambda: str(dev))
    fake_stdin = _FakeTty()
    monkeypatch.setattr(audio_io.sys, "stdin", fake_stdin)
    # 模擬：按鍵那邊永遠沒事件，使用者改按 Enter
    monkeypatch.setattr(audio_io.select, "select",
                        lambda r, w, x, *a: ([fake_stdin], [], []))

    assert audio_io._block_until_key_press() is True


def test_stdin_is_not_watched_when_not_a_tty(monkeypatch, tmp_path):
    """背景行程（stdin 非 TTY）不得把 stdin 加進 select。

    非 TTY 的 stdin 會一直是 ready，加進去會讓迴圈空轉燒 CPU，
    而 local_client 在裝置上正是以背景行程執行。
    """
    dev = tmp_path / "event_fake"
    dev.write_bytes(b"")
    monkeypatch.setattr(audio_io, "_resolve_key_device", lambda: str(dev))
    monkeypatch.setattr(audio_io.sys, "stdin", _FakePipe())

    watched = {}

    def _fake_select(r, w, x, *a):
        watched["n"] = len(r)
        raise KeyboardInterrupt  # 停掉迴圈，只驗證監看清單

    monkeypatch.setattr(audio_io.select, "select", _fake_select)
    try:
        audio_io._block_until_key_press()
    except KeyboardInterrupt:
        pass

    assert watched["n"] == 1, "非 TTY 的 stdin 不該被監看"


# ---------------------------------------------------------------------------
# 播放音量：只能在軟體做，ALSA mixer 對本板 3.5mm 輸出無效
# ---------------------------------------------------------------------------

def _pcm16(*values) -> bytes:
    return struct.pack(f"<{len(values)}h", *values)


def test_scaling_halves_the_amplitude():
    """2026-07-30 實測：Lineout（-4dB）與 ADDA_DL_GAIN（97%→13%，約 -18dB）
    調了都毫無效果，唯一有效的是直接改 PCM 樣本值。"""
    out = audio_io.scale_pcm16(_pcm16(1000, -2000, 4000), 0.5)
    assert struct.unpack("<3h", out) == (500, -1000, 2000)


def test_scaling_preserves_length():
    data = _pcm16(1, 2, 3, 4)
    assert len(audio_io.scale_pcm16(data, 0.3)) == len(data)


def test_factor_one_returns_the_data_untouched():
    """預設不改音量——只有明確設定才介入。"""
    data = _pcm16(1000, -1000)
    assert audio_io.scale_pcm16(data, 1.0) is data


def test_gain_above_one_is_refused():
    """不放大：超過 1.0 會削波（clipping），寧可維持原樣。"""
    data = _pcm16(30000, -30000)
    assert audio_io.scale_pcm16(data, 2.0) is data


def test_zero_factor_gives_silence():
    out = audio_io.scale_pcm16(_pcm16(9999, -9999), 0.0)
    assert struct.unpack("<2h", out) == (0, 0)


def test_empty_and_odd_length_input_do_not_crash():
    """半個 sample 的殘料不得讓播放整個炸掉。"""
    assert audio_io.scale_pcm16(b"", 0.5) == b""
    assert len(audio_io.scale_pcm16(b"\x01", 0.5)) == 0


def test_scaling_matches_the_pure_python_fallback(monkeypatch):
    """numpy 不可用時的降級路徑要給出相同結果（裝置上不保證有 numpy）。"""
    data = _pcm16(1000, -2000, 4000, -8000)
    with_numpy = audio_io.scale_pcm16(data, 0.25)

    import builtins
    real_import = builtins.__import__

    def _no_numpy(name, *a, **kw):
        if name == "numpy":
            raise ImportError("模擬沒有 numpy")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _no_numpy)
    without_numpy = audio_io.scale_pcm16(data, 0.25)

    assert with_numpy == without_numpy


# ---------------------------------------------------------------------------
# 播放裝置
# ---------------------------------------------------------------------------

def test_playback_device_can_be_configured(monkeypatch):
    """播放必須能指定 ALSA 裝置。

    錄音早就有 `TALKYBUDDY_EDGE_ALSA_DEVICE`（必須是 `plughw:1,0`，USB 麥克風），
    但播放寫死走 `default`——而已驗證可用的喇叭是 3.5mm Lineout `plughw:0,0`
    （USB 麥克風沒有播放能力）。`default` 由 /etc/asound.conf 決定，
    不一定是那顆，演練時玩偶可能「回答了但聽不到」。
    """
    calls = []
    monkeypatch.setattr(audio_io, "_PLAYBACK_DEVICE", "plughw:0,0")

    def _fake_run(argv, **kw):
        calls.append(argv)
        class _R:
            returncode = 0
        return _R()

    monkeypatch.setattr(audio_io.subprocess, "run", _fake_run)
    audio_io._play_with_aplay(b"RIFFfake")

    assert calls, "沒有呼叫 aplay"
    argv = calls[0]
    assert "-D" in argv, f"aplay 沒指定裝置：{argv}"
    assert argv[argv.index("-D") + 1] == "plughw:0,0"


# ---------------------------------------------------------------------------
# 找出按鍵在哪個 input 節點
# ---------------------------------------------------------------------------

# 真機 /proc/bus/input/devices 的形狀（節錄）。重點：mtk-pmic-keys 不一定是
# event1——USB 音效裝置（麥克風）自己也會註冊 input 節點，插拔就可能位移。
_PROC_INPUT_DEVICES = """\
I: Bus=0019 Vendor=0000 Product=0000 Version=0000
N: Name="mtk-pmic-keys"
P: Phys=
S: Sysfs=/devices/platform/100d0000.pwrap/mt6359-keys
H: Handlers=kbd event2\x20
B: EV=3

I: Bus=0003 Vendor=0d8c Product=0134 Version=0100
N: Name="C-Media USB Audio Device"
P: Phys=usb-11201000.usb-1.3/input3
S: Sysfs=/devices/platform/11201000.usb/usb1/1-1/1-1.3/1-1.3:1.3/0003:0d8c:0134.0003/input/input1
H: Handlers=kbd event1\x20
B: EV=3
"""


def test_the_pmic_key_device_is_found_by_name_not_by_number():
    """按鍵節點要靠名稱找，不能靠寫死的 event 編號。

    USB 麥克風插上去後也會註冊 input 節點（音量鍵之類），編號可能位移。
    寫死 event1 的話會阻塞在一個永遠不會有 KEY_HOME 的裝置上——症狀是
    「印出提示、按了沒反應」，而不是明確報錯。
    """
    found = audio_io._find_key_device_from_proc(_PROC_INPUT_DEVICES)
    assert found == "/dev/input/event2"


def test_the_usb_audio_input_node_is_not_mistaken_for_the_key():
    """USB 音效裝置的 input 節點不能被當成按鍵——它就是會位移的元凶。"""
    found = audio_io._find_key_device_from_proc(_PROC_INPUT_DEVICES)
    assert found != "/dev/input/event1"


def test_no_pmic_keys_present_returns_none():
    """板子上沒有 PMIC 按鍵時回 None，讓呼叫端退回其他觸發方式。"""
    only_usb = """\
I: Bus=0003 Vendor=0d8c Product=0134 Version=0100
N: Name="C-Media USB Audio Device"
H: Handlers=kbd event1\x20
"""
    assert audio_io._find_key_device_from_proc(only_usb) is None


def test_malformed_proc_content_returns_none_instead_of_raising():
    """/proc 內容不如預期時回 None，不要炸掉整條對話迴圈。"""
    assert audio_io._find_key_device_from_proc("") is None
    assert audio_io._find_key_device_from_proc("garbage\nlines\n") is None


def test_playback_without_configuration_keeps_the_old_behaviour(monkeypatch):
    """沒設定時維持原行為（不帶 -D），避免在別的機器上改壞。"""
    calls = []
    monkeypatch.setattr(audio_io, "_PLAYBACK_DEVICE", "")

    def _fake_run(argv, **kw):
        calls.append(argv)
        class _R:
            returncode = 0
        return _R()

    monkeypatch.setattr(audio_io.subprocess, "run", _fake_run)
    audio_io._play_with_aplay(b"RIFFfake")

    assert "-D" not in calls[0]
