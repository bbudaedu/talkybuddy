# -*- coding: utf-8 -*-
"""local_client 必須能長時間待機：閒置不死、斷線自己接回來。

**為什麼要有這個**：2026-07-30 真機上，玩偶完成一輪對話後閒置一段時間，
行程就以 `BrokenPipeError` → `ConnectionClosedError: no close frame received
or sent` 崩潰退出。使用者回來按鍵、講話，完全沒反應——而從外面看跟按鍵故障
一模一樣（`dump_recent_turns` 也證實那幾輪一輪都沒完成）。

根因有兩層，兩層都得修：

1. `audio_io.wait_for_trigger()` 是**同步阻塞**呼叫（等人按實體鍵，可能好幾
   分鐘），卻被直接放在 async 函式裡 → 凍結整個 asyncio event loop。
   `websockets` 靠背景 task 定期送 keepalive ping，event loop 一凍就送不出去，
   連線於是被判定死亡而關閉。**閒置越久越必死。**
2. 斷線後 `raise` 讓行程直接退出，沒有重連。

決賽情境下這是致命的：玩偶放桌上等評審過來，閒置十幾分鐘後行程已經死了。
"""

import asyncio

import pytest
import websockets

from edge.runtime import local_client


class _FakeWS:
    """websockets.connect() 的 async context manager 替身。"""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def _wire_up(monkeypatch, *, on_turn):
    """把 run_loop 的外部相依全部換掉，只留控制流。"""
    counters = {"connect": 0}

    def _fake_connect(_url):
        counters["connect"] += 1
        return _FakeWS()

    monkeypatch.setattr(local_client.websockets, "connect", _fake_connect)
    monkeypatch.setattr(local_client, "wait_for_server_ready", lambda: None)
    monkeypatch.setattr(local_client, "fetch_token", lambda: "fake-token")
    monkeypatch.setattr(local_client.audio_io, "wait_for_trigger", lambda: None)
    monkeypatch.setattr(local_client, "_handle_turn", on_turn)

    # 重連退避不要真的等，否則測試變慢
    async def _no_wait(_s):
        return None

    monkeypatch.setattr(local_client.asyncio, "sleep", _no_wait)
    return counters


@pytest.mark.asyncio
async def test_connection_loss_reconnects_instead_of_killing_the_process(monkeypatch):
    """連線斷掉必須重連，不是讓行程死掉。

    用 KeyboardInterrupt 收尾而非一般 Exception：主迴圈刻意用
    `except Exception` 吞掉單輪失敗（一輪講壞了不該讓玩偶罷工），
    所以一般例外跳不出無限迴圈。
    """
    turns = {"n": 0}

    async def _on_turn(_ws):
        turns["n"] += 1
        if turns["n"] == 1:
            raise websockets.exceptions.ConnectionClosedError(None, None)
        raise KeyboardInterrupt  # 第二輪：確認已重連，收工

    counters = _wire_up(monkeypatch, on_turn=_on_turn)

    with pytest.raises(KeyboardInterrupt):
        await local_client.run_loop()

    assert counters["connect"] == 2, (
        f"斷線後沒有重連（connect 只被呼叫 {counters['connect']} 次）"
    )


@pytest.mark.asyncio
async def test_a_single_bad_turn_does_not_stop_the_toy(monkeypatch):
    """單輪對話失敗（非連線問題）只記 log，繼續等下一次觸發。

    一輪沒聽清楚就讓玩偶罷工，現場等於掛掉。
    """
    turns = {"n": 0}

    async def _on_turn(_ws):
        turns["n"] += 1
        if turns["n"] == 1:
            raise RuntimeError("這一輪壞掉")
        raise KeyboardInterrupt

    counters = _wire_up(monkeypatch, on_turn=_on_turn)

    with pytest.raises(KeyboardInterrupt):
        await local_client.run_loop()

    assert turns["n"] == 2, "單輪失敗後沒有繼續等下一次觸發"
    assert counters["connect"] == 1, "單輪失敗不該重建連線"


@pytest.mark.asyncio
async def test_trigger_wait_runs_off_the_event_loop(monkeypatch):
    """等按鍵必須丟到執行緒，不能凍結 event loop。

    這是閒置後斷線的根因：event loop 被同步的 wait_for_trigger 凍住，
    websockets 的 keepalive ping 送不出去，連線被判定死亡。
    """
    seen = {"to_thread": [], "direct": 0}

    def _sync_wait():
        seen["direct"] += 1

    real_to_thread = asyncio.to_thread

    async def _spy_to_thread(fn, *args, **kwargs):
        seen["to_thread"].append(getattr(fn, "__name__", repr(fn)))
        return await real_to_thread(fn, *args, **kwargs)

    async def _on_turn(_ws):
        raise KeyboardInterrupt

    _wire_up(monkeypatch, on_turn=_on_turn)
    monkeypatch.setattr(local_client.audio_io, "wait_for_trigger", _sync_wait)
    monkeypatch.setattr(local_client.asyncio, "to_thread", _spy_to_thread)

    with pytest.raises(KeyboardInterrupt):
        await local_client.run_loop()

    assert seen["to_thread"], (
        "wait_for_trigger 沒有經過 asyncio.to_thread——直接呼叫會凍結 event loop，"
        "keepalive ping 送不出去，閒置後連線必死"
    )
    assert seen["direct"] == 1, "wait_for_trigger 應該仍被實際執行一次"
