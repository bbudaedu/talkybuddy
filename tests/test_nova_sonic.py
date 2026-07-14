# -*- coding: utf-8 -*-
"""NovaSonicSession + module available() 單元測試（全程 mock SDK，不真打雲端）。"""
from __future__ import annotations

import base64

import pytest

from server import nova_sonic


def test_available_false_without_creds(monkeypatch):
    """缺 SigV4 憑證 → available() False（bidi 不吃 bearer token）。"""
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    assert nova_sonic.available() is False


def test_available_true_with_creds(monkeypatch):
    """有 SigV4 憑證且 SDK 可 import → available() True。"""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_TEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret_test")
    assert nova_sonic.available() is True


def test_session_init_stores_params():
    s = nova_sonic.NovaSonicSession(model_id="m", voice="v", region="r")
    assert (s.model_id, s.voice, s.region) == ("m", "v", "r")


# ---- 共用 fake SDK ----
class _FakeInputStream:
    def __init__(self):
        self.sent = []            # list[dict] 已送出的 event payload
        self.closed = False

    async def send(self, chunk):
        # chunk 是我們的 _mk_event(...) 產物；直接記錄
        self.sent.append(chunk)

    async def close(self):
        self.closed = True


class _FakeOutputStream:
    def __init__(self, events):
        self._events = list(events)   # list[bytes]（每個是 json）

    async def receive(self):
        if self._events:
            raw = self._events.pop(0)
            return type("Ev", (), {"value": type("V", (), {"bytes_": raw})()})()
        return None


class _FakeStream:
    def __init__(self, out_events):
        self.input_stream = _FakeInputStream()
        self._out = _FakeOutputStream(out_events)

    async def await_output(self):
        return (None, self._out)

    async def close(self):
        pass


class _FakeClient:
    def __init__(self, out_events=()):
        self._out_events = out_events
        self.op_input = None

    async def invoke_model_with_bidirectional_stream(self, op_input):
        self.op_input = op_input
        return _FakeStream(self._out_events)


def _payloads(fake_input_stream):
    """把 fake input stream 收到的 chunk 還原成 event-key list。"""
    keys = []
    for c in fake_input_stream.sent:
        d = json.loads(c.value.bytes_)
        keys.extend(d.get("event", {}).keys())
    return keys


import json  # noqa: E402


@pytest.mark.asyncio
async def test_start_sends_session_and_prompt_sequence(monkeypatch):
    fake_client = _FakeClient()
    # 註：_build_client 是 instance method，須 monkeypatch class（非 module）才會生效。
    monkeypatch.setattr(nova_sonic.NovaSonicSession, "_build_client",
                        lambda self: fake_client)
    s = nova_sonic.NovaSonicSession("m", "tiffany", "us-east-1")

    await s.start("你是說說學伴。")

    sent_keys = _payloads(s._stream.input_stream)
    # 開場事件序：sessionStart → promptStart → (system) contentStart → textInput → contentEnd
    assert sent_keys[:5] == [
        "sessionStart", "promptStart", "contentStart", "textInput", "contentEnd",
    ]
    # promptStart 帶 24kHz 語音輸出 + voiceId
    ps = [json.loads(c.value.bytes_)["event"]["promptStart"]
          for c in s._stream.input_stream.sent
          if "promptStart" in json.loads(c.value.bytes_)["event"]][0]
    aoc = ps["audioOutputConfiguration"]
    assert aoc["sampleRateHertz"] == 24000 and aoc["voiceId"] == "tiffany"
    await s.close()


@pytest.mark.asyncio
async def test_send_audio_opens_audio_content_once_then_end(monkeypatch):
    fake_client = _FakeClient()
    monkeypatch.setattr(nova_sonic.NovaSonicSession, "_build_client",
                        lambda self: fake_client)
    s = nova_sonic.NovaSonicSession("m", "tiffany", "us-east-1")
    await s.start("sys")
    base = len(s._stream.input_stream.sent)

    await s.send_audio(b"\x01\x02" * 10)
    await s.send_audio(b"\x03\x04" * 10)
    await s.end_user_turn()

    new_keys = [k for c in s._stream.input_stream.sent[base:]
                for k in json.loads(c.value.bytes_)["event"].keys()]
    # 首塊觸發一次 AUDIO contentStart，之後 audioInput；end_user_turn 於 contentEnd
    # 前補一塊尾端靜音 audioInput（助 Nova VAD 收尾，否則不回覆並 55s 逾時）
    assert new_keys == [
        "contentStart", "audioInput", "audioInput", "audioInput", "contentEnd",
    ]
    # 收尾前最後一塊 audioInput 必為全零靜音，長度＝設定的尾靜音秒數
    last_audio = [json.loads(c.value.bytes_)["event"]["audioInput"]["content"]
                  for c in s._stream.input_stream.sent[base:]
                  if "audioInput" in json.loads(c.value.bytes_)["event"]][-1]
    tail_pcm = base64.b64decode(last_audio)
    assert tail_pcm == nova_sonic._TAIL_SILENCE_PCM
    assert set(tail_pcm) == {0} and len(tail_pcm) == int(0.8 * 16000) * 2
    # end_user_turn 不得送 promptEnd
    assert "promptEnd" not in new_keys
    # AUDIO contentStart 為 16kHz USER
    cs = [json.loads(c.value.bytes_)["event"]["contentStart"]
          for c in s._stream.input_stream.sent[base:]
          if "contentStart" in json.loads(c.value.bytes_)["event"]][0]
    assert cs["type"] == "AUDIO" and cs["role"] == "USER"
    assert cs["audioInputConfiguration"]["sampleRateHertz"] == 16000
    await s.close()


def _out_event(**ev):
    return json.dumps({"event": ev}).encode("utf-8")


@pytest.mark.asyncio
async def test_events_converge_multi_completion(monkeypatch):
    """先 USER-ASR completionEnd 不收尾，等 ASSISTANT 內容後才發 turn_end。"""
    asst_audio = base64.b64encode(b"\x00\x00" * 5).decode("ascii")
    out = [
        _out_event(textOutput={"role": "USER", "content": "我想吃蘋果"}),
        _out_event(completionEnd={}),  # user-ASR 段結束 → 不可收尾
        _out_event(textOutput={"role": "ASSISTANT", "content": "好棒！apple。"}),
        _out_event(audioOutput={"content": asst_audio}),
        _out_event(completionEnd={}),  # assistant 段結束 → turn_end
    ]
    fake_client = _FakeClient(out_events=out)
    monkeypatch.setattr(nova_sonic.NovaSonicSession, "_build_client",
                        lambda self: fake_client)
    s = nova_sonic.NovaSonicSession("m", "tiffany", "us-east-1")
    await s.start("sys")

    got = []
    async for ev in s.events():
        got.append((ev.kind, ev.role, ev.text, len(ev.audio)))

    assert ("transcript", "USER", "我想吃蘋果", 0) in got
    assert ("transcript", "ASSISTANT", "好棒！apple。", 0) in got
    assert any(k == "audio" and n > 0 for (k, _, _, n) in got)
    assert got[-1][0] == "turn_end"
    await s.close()
    assert s._stream.input_stream.closed is True


@pytest.mark.asyncio
async def test_events_continuous_yields_across_multiple_turns(monkeypatch):
    """events_continuous 不因 turn_end return，跨多輪吐到 None 哨兵才止。"""
    fake_client = _FakeClient()
    monkeypatch.setattr(nova_sonic.NovaSonicSession, "_build_client",
                        lambda self: fake_client)
    s = nova_sonic.NovaSonicSession("m", "tiffany", "us-east-1")
    await s.start("sys")
    # 直接灌 queue 模擬兩輪（繞過真 receive_loop）
    from server.nova_sonic import NovaEvent
    s._recv_task.cancel()  # 停背景 loop，改手動灌
    for ev in [NovaEvent("transcript", role="ASSISTANT", text="第一輪"),
               NovaEvent("turn_end"),
               NovaEvent("transcript", role="ASSISTANT", text="第二輪"),
               NovaEvent("turn_end"),
               None]:
        s._queue.put_nowait(ev)
    got = []
    async for e in s.events_continuous():
        got.append((e.kind, e.text))
    assert got == [("transcript", "第一輪"), ("turn_end", ""),
                   ("transcript", "第二輪"), ("turn_end", "")]


@pytest.mark.asyncio
async def test_receive_loop_emits_interrupt_on_user_speech_start(monkeypatch):
    """Nova barge-in 事件 userSpeechStart → queue 出現 NovaEvent(kind='interrupt')。

    實測（spec 附錄 A）：barge-in 時 Nova 送獨立 userSpeechStart 事件，而非
    contentEnd.stopReason=='INTERRUPTED'（contentEnd 一律 PARTIAL_TURN）。
    """
    out = [
        _out_event(textOutput={"role": "ASSISTANT", "content": "我正在說"}),
        _out_event(userSpeechStart={"inputAudioOffsetMs": 1000,
                                    "promptName": "p", "sessionId": "s"}),
    ]
    fake_client = _FakeClient(out_events=out)
    monkeypatch.setattr(nova_sonic.NovaSonicSession, "_build_client",
                        lambda self: fake_client)
    s = nova_sonic.NovaSonicSession("m", "tiffany", "us-east-1")
    await s.start("sys")
    kinds = []
    async for e in s.events_continuous():
        kinds.append(e.kind)
        if e.kind == "interrupt":
            break
    assert "interrupt" in kinds
    await s.close()


@pytest.mark.asyncio
async def test_close_sends_prompt_and_session_end(monkeypatch):
    fake_client = _FakeClient()
    monkeypatch.setattr(nova_sonic.NovaSonicSession, "_build_client",
                        lambda self: fake_client)
    s = nova_sonic.NovaSonicSession("m", "tiffany", "us-east-1")
    await s.start("sys")
    await s.close()
    keys = [k for c in s._stream.input_stream.sent
            for k in json.loads(c.value.bytes_)["event"].keys()]
    assert "promptEnd" in keys and "sessionEnd" in keys
