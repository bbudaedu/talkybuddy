# -*- coding: utf-8 -*-
"""AlsaTransport 單元測試。

這些測試刻意**不啟動真的 arecord/aplay**：板子上 `talkybuddy-live-client.service`
正持有麥克風，測試若真的開 arecord 會變成第二個搶麥克風的行程——那個症狀
（2026-07-30 事故）長得跟麥克風壞掉一模一樣，極難定位。所以一律注入假的
子行程物件，只驗證本模組自己的邏輯。
"""

from __future__ import annotations

import asyncio

import pytest
from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame

from edge.runtime.pipecat_adapters.alsa_transport import (
    AlsaInputTransport,
    AlsaOutputTransport,
    AlsaTransportParams,
)


class _FakeStdout:
    """模擬 asyncio StreamReader.readexactly：吐完既定 chunk 後宣告串流結束。"""

    def __init__(self, chunks: list[bytes], tail: bytes = b""):
        self._chunks = list(chunks)
        self._tail = tail

    async def readexactly(self, n: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        raise asyncio.IncompleteReadError(partial=self._tail, expected=n)


class _FakeProc:
    """模擬 asyncio.subprocess.Process 的最小介面。"""

    def __init__(self, stdout=None, stdin=None, returncode=None):
        self.stdout = stdout
        self.stdin = stdin
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        return self.returncode


class _FakeStdin:
    """模擬 StreamWriter：記下寫入內容，可設定為斷管。"""

    def __init__(self, broken: bool = False):
        self.written = bytearray()
        self.closed = False
        self._broken = broken

    def is_closing(self) -> bool:
        return self.closed

    def close(self):
        self.closed = True

    def write(self, data: bytes):
        if self._broken:
            raise BrokenPipeError("aplay 已結束")
        self.written.extend(data)

    async def drain(self):
        if self._broken:
            raise BrokenPipeError("aplay 已結束")


def _in_params(**kw) -> AlsaTransportParams:
    return AlsaTransportParams(
        audio_in_enabled=True, audio_in_sample_rate=16000, read_chunk_bytes=4, **kw
    )


@pytest.mark.asyncio
async def test_read_handler_pushes_each_chunk_as_frame():
    """每個讀到的 chunk 都變成一個 InputAudioRawFrame，取樣率沿用上行設定。"""
    t = AlsaInputTransport(_in_params())
    t._sample_rate = 16000
    t._proc = _FakeProc(stdout=_FakeStdout([b"aabb", b"ccdd"]))

    pushed: list[InputAudioRawFrame] = []

    async def _capture(frame):
        pushed.append(frame)

    t.push_audio_frame = _capture

    await t._read_handler()

    assert [f.audio for f in pushed] == [b"aabb", b"ccdd"]
    assert all(f.sample_rate == 16000 for f in pushed)
    assert all(f.num_channels == 1 for f in pushed)


@pytest.mark.asyncio
async def test_read_handler_flushes_partial_tail_before_stopping():
    """arecord 被收掉時，最後不足一個 chunk 的殘餘音訊不能靜靜丟掉。"""
    t = AlsaInputTransport(_in_params())
    t._sample_rate = 16000
    t._proc = _FakeProc(stdout=_FakeStdout([b"aabb"], tail=b"xy"))

    pushed = []

    async def _capture(frame):
        pushed.append(frame)

    t.push_audio_frame = _capture

    await t._read_handler()

    assert [f.audio for f in pushed] == [b"aabb", b"xy"]


@pytest.mark.asyncio
async def test_read_handler_no_empty_frame_when_tail_is_empty():
    """沒有殘餘時不該推一個空 frame 出去。"""
    t = AlsaInputTransport(_in_params())
    t._sample_rate = 16000
    t._proc = _FakeProc(stdout=_FakeStdout([b"aabb"], tail=b""))

    pushed = []

    async def _capture(frame):
        pushed.append(frame)

    t.push_audio_frame = _capture

    await t._read_handler()

    assert [f.audio for f in pushed] == [b"aabb"]


@pytest.mark.asyncio
async def test_input_teardown_terminates_arecord_and_releases_mic():
    """teardown 必須真的收掉 arecord——不然麥克風不釋放。"""
    t = AlsaInputTransport(_in_params())
    proc = _FakeProc(stdout=_FakeStdout([]), returncode=None)
    t._proc = proc

    await t._teardown()

    assert proc.terminated is True
    assert t._proc is None


@pytest.mark.asyncio
async def test_input_teardown_is_reentrant():
    """stop() 與 cleanup() 都會呼叫 teardown，第二次不能炸。"""
    t = AlsaInputTransport(_in_params())
    t._proc = _FakeProc(stdout=_FakeStdout([]), returncode=None)

    await t._teardown()
    await t._teardown()  # 不應拋


@pytest.mark.asyncio
async def test_input_teardown_kills_when_terminate_ignored():
    """terminate 收不掉就得 kill，否則麥克風永遠不還。"""

    class _Stubborn(_FakeProc):
        def terminate(self):
            self.terminated = True  # 故意不改 returncode

        async def wait(self):
            if not self.killed:
                await asyncio.sleep(10)  # 讓 wait_for 逾時
            return -9

    t = AlsaInputTransport(_in_params())
    proc = _Stubborn(stdout=_FakeStdout([]), returncode=None)
    t._proc = proc

    await t._teardown()

    assert proc.killed is True


@pytest.mark.asyncio
async def test_write_audio_frame_without_process_returns_false():
    """aplay 沒起來時回 False，不拋——喇叭壞掉不該終結整場對話。"""
    t = AlsaOutputTransport(AlsaTransportParams(audio_out_enabled=True))
    frame = OutputAudioRawFrame(audio=b"\x00\x01", sample_rate=24000, num_channels=1)

    assert await t.write_audio_frame(frame) is False


@pytest.mark.asyncio
async def test_write_audio_frame_writes_to_stdin():
    """正常路徑：音訊 bytes 原封不動寫進 aplay stdin。"""
    t = AlsaOutputTransport(AlsaTransportParams(audio_out_enabled=True))
    stdin = _FakeStdin()
    t._proc = _FakeProc(stdin=stdin)

    frame = OutputAudioRawFrame(audio=b"\x01\x02\x03\x04", sample_rate=24000, num_channels=1)
    assert await t.write_audio_frame(frame) is True
    assert bytes(stdin.written) == b"\x01\x02\x03\x04"


@pytest.mark.asyncio
async def test_write_audio_frame_survives_broken_pipe():
    """aplay 中途死掉（裝置被拔／被佔用）回 False，不得讓例外炸穿 pipeline。"""
    t = AlsaOutputTransport(AlsaTransportParams(audio_out_enabled=True))
    t._proc = _FakeProc(stdin=_FakeStdin(broken=True))

    frame = OutputAudioRawFrame(audio=b"\x00\x00", sample_rate=24000, num_channels=1)
    assert await t.write_audio_frame(frame) is False


@pytest.mark.asyncio
async def test_output_teardown_closes_stdin_so_buffer_drains():
    """關 stdin 讓 aplay 播完緩衝再結束，而不是直接砍掉吞掉尾音。"""
    t = AlsaOutputTransport(AlsaTransportParams(audio_out_enabled=True))
    stdin = _FakeStdin()
    proc = _FakeProc(stdin=stdin, returncode=0)
    t._proc = proc

    await t._teardown()

    assert stdin.closed is True
    assert proc.killed is False
    assert t._proc is None
