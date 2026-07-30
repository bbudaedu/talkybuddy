# -*- coding: utf-8 -*-
"""裝置端 Nova Sonic S2S client（/ws/live）的控制流與事件分派。

**設計前提**（2026-07-30 決定）：Nova Sonic 的 turn 邊界由它自己的 VAD 判斷，
協定註明「連續模式：user_end 無意義」——它預期持續串流。但裝置若永遠在聽，
環境噪音會不斷誤觸（當天實測：旁邊播放兒童節目《寶貝多米》時，ASR 確實收到
過噪音誤判成韓文字符的紀錄），決賽會場人聲更吵。

所以觸發設計是 **按鍵開關一段 live session**：按一下開始串流、期間多輪自然
對話（含打斷），再按一下結束回待機。待機時完全不送音訊，零誤觸、零流量。

「按著講」不可行：按住 power 鍵 8–10 秒會觸發 PMIC 硬體斷電，軟體攔不住。
"""

import asyncio

import pytest

from edge.runtime import live_client


# ---------------------------------------------------------------------------
# 事件分派（純函式）
# ---------------------------------------------------------------------------

def test_audio_is_not_a_json_event_at_all():
    """音訊走 binary frame，不是 JSON 事件。

    /ws/live 實際只送四種 JSON：interrupt / live_error / live_transcript /
    turn_end；音訊一律經 server/app.py 的 emit_bytes。binary 與 JSON 的分流在
    pump_downlink 做掉（見 test_downlink_audio_reaches_the_speaker），
    所以 classify_live_event 不該有「播放」這個動作。
    """
    assert not hasattr(live_client, "PLAY")
    # 就算真的收到這種型別也只是未知事件，不得當成音訊
    assert live_client.classify_live_event(
        {"type": "live_audio"}) == live_client.CONTINUE


def test_transcript_is_shown_not_played():
    action = live_client.classify_live_event(
        {"type": "live_transcript", "role": "ASSISTANT", "text": "hi"})
    assert action == live_client.SHOW


def test_interrupt_stops_playback_immediately():
    """打斷是全雙工的重點：孩子插話時玩偶要立刻閉嘴。

    不清掉已緩衝的音訊的話，玩偶會把被打斷的那句講完，體感完全不是即時對話。
    """
    assert live_client.classify_live_event({"type": "interrupt"}) == live_client.FLUSH


def test_turn_end_is_a_boundary_not_a_session_end():
    """一輪結束不等於 session 結束——連續模式下還要繼續聽。"""
    assert live_client.classify_live_event({"type": "turn_end"}) == live_client.CONTINUE


def test_live_error_ends_the_session():
    assert live_client.classify_live_event(
        {"type": "live_error", "reason": "unavailable"}) == live_client.ABORT


def test_consent_required_also_ends_the_session():
    assert live_client.classify_live_event(
        {"type": "live_error", "reason": "consent_required"}) == live_client.ABORT


def test_unknown_events_are_ignored_not_fatal():
    """伺服器之後新增事件型別時，舊的裝置端不該整個掛掉。"""
    assert live_client.classify_live_event({"type": "something_new"}) == live_client.CONTINUE
    assert live_client.classify_live_event({}) == live_client.CONTINUE


# ---------------------------------------------------------------------------
# 音訊參數：上行 16k、下行 24k，兩者不同不能混用
# ---------------------------------------------------------------------------

def test_uplink_is_16k_and_downlink_is_24k():
    """Nova Sonic 收 16k、吐 24k。用錯取樣率播放會變成怪腔怪調。"""
    assert live_client.UPLINK_RATE == 16000
    assert live_client.DOWNLINK_RATE == 24000


def test_arecord_argv_streams_raw_at_16k():
    argv = live_client.build_arecord_argv("plughw:1,0")
    assert argv[0] == "arecord"
    assert "-D" in argv and argv[argv.index("-D") + 1] == "plughw:1,0"
    assert argv[argv.index("-r") + 1] == "16000"
    # raw 串流而非 WAV：WAV header 只在檔案開頭出現一次，串流送出去會讓
    # 伺服器把 header bytes 當成音訊取樣
    assert "raw" in argv
    assert argv[-1] == "-"


def test_aplay_argv_plays_raw_at_24k():
    argv = live_client.build_aplay_argv("plughw:0,0")
    assert argv[0] == "aplay"
    assert argv[argv.index("-D") + 1] == "plughw:0,0"
    assert argv[argv.index("-r") + 1] == "24000"
    assert "raw" in argv
    assert argv[-1] == "-"


def test_device_can_be_omitted():
    """沒指定裝置時不帶 -D，維持與 audio_io 一致的行為。"""
    assert "-D" not in live_client.build_arecord_argv("")
    assert "-D" not in live_client.build_aplay_argv("")


# ---------------------------------------------------------------------------
# session 控制流
# ---------------------------------------------------------------------------

class _FakeWS:
    """websockets 連線替身：記錄送出的東西，依腳本回傳收到的東西。"""

    def __init__(self, inbound=None):
        self.sent_bytes: list[bytes] = []
        self.sent_json: list[str] = []
        self._inbound = list(inbound or [])
        self.closed = False

    async def send(self, data):
        if isinstance(data, (bytes, bytearray)):
            self.sent_bytes.append(bytes(data))
        else:
            self.sent_json.append(data)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._inbound:
            raise StopAsyncIteration
        item = self._inbound.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeSink:
    def __init__(self):
        self.written: list[bytes] = []
        self.flushes = 0
        self.stopped = False

    def write(self, data):
        self.written.append(data)

    def flush_pending(self):
        self.flushes += 1

    def stop(self):
        self.stopped = True


@pytest.mark.asyncio
async def test_downlink_audio_reaches_the_speaker():
    ws = _FakeWS(inbound=[b"\x01\x02", b"\x03\x04"])
    sink = _FakeSink()

    await live_client.pump_downlink(ws, sink)

    assert sink.written == [b"\x01\x02", b"\x03\x04"]


@pytest.mark.asyncio
async def test_interrupt_flushes_the_speaker_buffer():
    """收到 interrupt 要清掉還沒播完的音訊，否則玩偶會講完被打斷的話。"""
    ws = _FakeWS(inbound=[b"\x01", '{"type": "interrupt"}', b"\x02"])
    sink = _FakeSink()

    await live_client.pump_downlink(ws, sink)

    assert sink.flushes == 1
    assert sink.written == [b"\x01", b"\x02"]


@pytest.mark.asyncio
async def test_live_error_aborts_the_session_early():
    """live_error 之後的音訊不該再播——session 已經無效了。"""
    ws = _FakeWS(inbound=['{"type": "live_error", "reason": "unavailable"}', b"\x99"])
    sink = _FakeSink()

    await live_client.pump_downlink(ws, sink)

    assert sink.written == [], "live_error 之後還在播音訊"


@pytest.mark.asyncio
async def test_malformed_json_does_not_kill_the_session():
    """半筆/壞掉的 JSON 不得中斷整場對話。"""
    ws = _FakeWS(inbound=["{壞掉的 json", b"\x07"])
    sink = _FakeSink()

    await live_client.pump_downlink(ws, sink)

    assert sink.written == [b"\x07"]


@pytest.mark.asyncio
async def test_uplink_stops_when_the_session_is_told_to_stop():
    """再按一次按鍵 → stop event → 上行串流必須停下來，不能繼續送音訊。"""
    ws = _FakeWS()
    stop = asyncio.Event()

    class _Mic:
        def __init__(self):
            self.reads = 0
            self.stopped = False

        def read(self, _n):
            self.reads += 1
            if self.reads >= 3:
                stop.set()
            return b"\x00" * 8

        def stop(self):
            self.stopped = True

    mic = _Mic()
    await live_client.pump_uplink(ws, mic, stop)

    assert len(ws.sent_bytes) >= 1
    assert stop.is_set()


@pytest.mark.asyncio
async def test_a_keypress_after_the_session_ended_is_not_swallowed(monkeypatch):
    """session 已結束後才接到的按鍵，要留給下一場、不能被吞掉。

    等按鍵是 asyncio.to_thread 包的阻塞讀取，cancel 不會真的中斷執行緒。
    若這場 session 因連線問題結束（不是因為按鍵），那個執行緒仍在等，
    會吃掉使用者的下一次按鍵——表現成「有時候要按兩次」，現場看起來像按鍵不靈。
    """
    monkeypatch.setattr(live_client, "_pending_trigger", False)
    monkeypatch.setattr(live_client.audio_io, "wait_for_trigger", lambda: None)

    stop = asyncio.Event()
    stop.set()  # session 已經結束了

    await live_client._wait_for_stop_key(stop)
    assert live_client._pending_trigger is True, "按鍵被吞掉了"

    # 主迴圈下一次等待應直接消費掉它，不再阻塞
    await asyncio.wait_for(live_client._wait_for_trigger(), timeout=1)
    assert live_client._pending_trigger is False, "寄放的按鍵沒有被消費"


@pytest.mark.asyncio
async def test_a_keypress_during_the_session_stops_it(monkeypatch):
    """session 進行中按鍵 → 結束這場，而不是寄放起來。"""
    monkeypatch.setattr(live_client, "_pending_trigger", False)
    monkeypatch.setattr(live_client.audio_io, "wait_for_trigger", lambda: None)

    stop = asyncio.Event()
    await live_client._wait_for_stop_key(stop)

    assert stop.is_set(), "按鍵沒有結束這場對話"
    assert live_client._pending_trigger is False, "不該寄放"


@pytest.mark.asyncio
async def test_empty_mic_read_ends_the_uplink():
    """arecord 掛掉（讀到 EOF）時上行要收手，不要空轉。"""
    ws = _FakeWS()
    stop = asyncio.Event()

    class _DeadMic:
        def read(self, _n):
            return b""

        def stop(self):
            pass

    await live_client.pump_uplink(ws, _DeadMic(), stop)
    assert ws.sent_bytes == []
