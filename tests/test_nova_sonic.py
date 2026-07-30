# -*- coding: utf-8 -*-
"""NovaSonicSession + module available() 單元測試（全程 mock SDK，不真打雲端）。"""
from __future__ import annotations

import base64

import pytest

from server import nova_sonic


def _clear_aws_env(monkeypatch):
    """清掉三個 AWS env（monkeypatch 會在測試結束還原，不污染其他測試）。"""
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def _fake_boto3_credentials(monkeypatch, access_key, secret_key, token=None):
    """讓 boto3 憑證鏈回傳指定憑證；access_key=None 代表「完全找不到憑證」。"""
    import boto3

    class _Frozen:
        def __init__(self):
            self.access_key = access_key
            self.secret_key = secret_key
            self.token = token

    class _Creds:
        def get_frozen_credentials(self):
            return _Frozen()

    class _Session:
        def get_credentials(self):
            return None if access_key is None else _Creds()

    monkeypatch.setattr(boto3.session, "Session", _Session)


def test_available_false_without_creds(monkeypatch):
    """env 與 boto3 憑證鏈都沒有憑證 → available() False（bidi 不吃 bearer token）。

    必須同時擋掉憑證鏈：開發機通常有 ~/.aws/credentials，只刪 env 是擋不住的。
    """
    _clear_aws_env(monkeypatch)
    _fake_boto3_credentials(monkeypatch, None, None)
    assert nova_sonic.available() is False


def test_available_true_with_creds(monkeypatch):
    """有 SigV4 憑證且 SDK 可 import → available() True。"""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_TEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret_test")
    assert nova_sonic.available() is True


# ---------------------------------------------------------------------------
# 憑證來源：Nova Sonic bidi 只吃環境變數，但憑證常在別處
# ---------------------------------------------------------------------------

def test_credentials_from_shared_file_are_promoted_into_env(monkeypatch):
    """~/.aws/credentials 的憑證要能啟用 live_s2s。

    2026-07-30：憑證有效（sts.get_caller_identity 成功、us-west-2、
    nova-2-sonic-v1:0 可列出）、SDK 也裝好了，但 live_s2s 永遠是 false——
    因為 _build_client 用 EnvironmentCredentialsResolver（已知踩雷後的刻意選擇），
    **只讀環境變數**，而 botocore 是從 shared-credentials-file 解析到憑證的。
    先前把這個落差誤記成「卡在沒有 AWS 憑證」，其實憑證一直都有。
    """
    _clear_aws_env(monkeypatch)
    _fake_boto3_credentials(monkeypatch, "AKIA_FROM_FILE", "secret_from_file")

    assert nova_sonic.available() is True
    import os
    assert os.environ["AWS_ACCESS_KEY_ID"] == "AKIA_FROM_FILE"
    assert os.environ["AWS_SECRET_ACCESS_KEY"] == "secret_from_file"


def test_temporary_credentials_carry_the_session_token(monkeypatch):
    """SSO／IAM role／STS 的臨時憑證少了 session token 會 SigV4 驗簽失敗。"""
    _clear_aws_env(monkeypatch)
    _fake_boto3_credentials(monkeypatch, "ASIA_TMP", "secret_tmp", token="tok123")

    assert nova_sonic.available() is True
    import os
    assert os.environ["AWS_SESSION_TOKEN"] == "tok123"


def test_explicit_env_credentials_are_not_overwritten(monkeypatch):
    """env 已顯式設定時不得被憑證鏈蓋掉——顯式設定優先。"""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_EXPLICIT")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret_explicit")
    _fake_boto3_credentials(monkeypatch, "AKIA_FROM_FILE", "secret_from_file")

    assert nova_sonic.available() is True
    import os
    assert os.environ["AWS_ACCESS_KEY_ID"] == "AKIA_EXPLICIT"


def test_incomplete_credentials_from_the_chain_are_rejected(monkeypatch):
    """憑證鏈回了物件但缺 secret → 視為不可用，不要注入半套憑證。"""
    _clear_aws_env(monkeypatch)
    _fake_boto3_credentials(monkeypatch, "AKIA_PARTIAL", "")
    assert nova_sonic.available() is False


def test_broken_credential_chain_does_not_raise(monkeypatch):
    """憑證鏈本身炸掉（設定檔壞、boto3 缺）只回 False，不得讓伺服器起不來。"""
    _clear_aws_env(monkeypatch)
    import boto3

    class _Boom:
        def get_credentials(self):
            raise RuntimeError("profile 壞了")

    monkeypatch.setattr(boto3.session, "Session", _Boom)
    assert nova_sonic.available() is False


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
