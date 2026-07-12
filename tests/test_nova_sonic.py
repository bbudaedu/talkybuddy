# -*- coding: utf-8 -*-
"""NovaSonicSession + module available() 單元測試（全程 mock SDK，不真打雲端）。"""
from __future__ import annotations

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
