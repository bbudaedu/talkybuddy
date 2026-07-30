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
# 連線位址：?mode=continuous 決定 server 走哪條路
# ---------------------------------------------------------------------------

def test_ws_url_must_request_continuous_mode():
    """少了 ?mode=continuous，server 走回合式、會等 user_end，兩邊互等。

    server/app.py::ws_live 以這個 query 參數分流：有它才啟動上下行雙 Task 常駐、
    turn 邊界交給 Nova VAD；沒有它則等 {"type":"user_end"} 才 end_user_turn 並
    開始迭代模型事件。而持續串流的 client 從不送 user_end（那正是連續模式的語意）。

    2026-07-30 實機實錄的症狀：上行 825600 bytes（25.8s）、下行 0 bytes、0 則事件。
    音訊確實送達，但 server 從未開始產出——查了三輪才發現是少一個 query 參數。
    """
    assert "mode=continuous" in live_client._ws_url()


def test_ws_url_points_at_the_live_endpoint():
    url = live_client._ws_url()
    assert url.startswith("ws://")
    assert "/ws/live" in url


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


def test_aplay_has_a_large_enough_buffer_to_absorb_jitter():
    """必須明示 --buffer-time，否則聲音會斷斷續續。

    Nova Sonic 的音訊成批到達、中間有生成空檔，aplay 預設緩衝吸收不了。
    2026-07-30 實機一場對話出現 16 次 `underrun!!!`，每次 0.8–1.9 秒。
    這是裝置本機的播放緩衝問題，不是網路——client 與 server 都在同一台走 loopback。
    """
    argv = live_client.build_aplay_argv("plughw:0,0")
    assert "--buffer-time" in argv, "沒設緩衝，播放會 underrun"
    buffer_us = int(argv[argv.index("--buffer-time") + 1])
    assert buffer_us >= 1_900_000, (
        f"緩衝 {buffer_us}us 吸收不了實測到的最大空檔（1.9s）"
    )


def test_device_can_be_omitted():
    """沒指定裝置時不帶 -D，維持與 audio_io 一致的行為。"""
    assert "-D" not in live_client.build_arecord_argv("")
    assert "-D" not in live_client.build_aplay_argv("")


# ---------------------------------------------------------------------------
# 自我打斷：玩偶講話時必須關閉上行
# ---------------------------------------------------------------------------

class _Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_gate_is_open_before_any_playback():
    """還沒播過任何東西時，上行當然要通。"""
    assert live_client.PlaybackGate(tail_s=0.6, now=_Clock()).is_open() is True


# 24kHz、16-bit、mono → 每秒 48000 bytes
_ONE_SECOND = 48000


def test_gate_closes_while_the_toy_is_speaking():
    """玩偶正在講話 → 關閉上行。

    2026-07-30 第一次成功跑通 S2S 時的現場症狀是「會自己打斷」：喇叭與 USB
    麥克風距離很近，玩偶自己的語音被收進去，Nova 的 server VAD 判定成使用者
    插話 → 發 interrupt → 打斷自己正在講的話，對話完全無法進行。
    """
    clock = _Clock()
    gate = live_client.PlaybackGate(tail_s=0.6, now=clock)
    gate.note_audio(_ONE_SECOND)
    assert gate.is_open() is False


def test_gate_stays_closed_for_the_whole_playback_duration():
    """關鍵：閘門要涵蓋**整段播放時間**，不是只到「收完資料」。

    下行音訊成批到達，aplay 收下後還要花對應時長才播完。第一版只看「最後收到
    資料的時刻 + tail」，實機結果是丟棄 74.2s 卻播了 85.3s——中間約 11 秒的
    空窗讓玩偶收到自己的聲音，逐字稿出現 `[USER] 你跟我一起说`。
    """
    clock = _Clock()
    # buffer_delay_s=0 隔離變因：這支測的是「涵蓋整段播放時長」，
    # 緩衝延遲另有專屬測試（test_gate_accounts_for_the_playback_buffer_delay）
    gate = live_client.PlaybackGate(tail_s=0.6, buffer_delay_s=0, now=clock)
    gate.note_audio(_ONE_SECOND * 5)      # 一次收到 5 秒的音訊

    clock.t += 3.0                        # 資料早就收完了，但還在播
    assert gate.is_open() is False, "只看『收完資料』會在播放中途就開閘"

    clock.t += 2.0                        # 播完（共 5s）但 tail 還沒滿
    assert gate.is_open() is False
    clock.t += 0.7
    assert gate.is_open() is True


# --- aplay 的緩衝延遲 -------------------------------------------------------

def test_gate_accounts_for_the_playback_buffer_delay():
    """寫進 aplay 的音訊不是立刻從喇叭出來——閘門要算進這段延遲。

    2026-07-30 實測踩到：`--buffer-time` 是 2 秒（為了壓下 underrun 才調大的），
    但 tail 只有 0.6 秒。閘門以「寫入時刻」推算播放區間，喇叭實際發聲卻晚了
    整整一個緩衝深度：

        閘門關閉 ： [寫入 ────────── 寫入+時長+0.6]
        喇叭實響 ： [寫入+2.0 ──────────── 寫入+2.0+時長]
                                  ↑ 閘門在這裡就開了，喇叭還在響

    中間約 1.4 秒的空窗讓玩偶收到自己的聲音，逐字稿於是出現 `[USER] 哎西`
    這種使用者確認沒講過的句子。

    這是「修一個問題時無聲弄壞另一個」的典型：把 buffer 調大解決了
    underrun（16 次 → 2 次），卻讓迴音防線破了個洞，而兩者之間的耦合
    當時沒有任何地方記錄。
    """
    clock = _Clock()
    gate = live_client.PlaybackGate(tail_s=0.6, buffer_delay_s=2.0, now=clock)
    gate.note_audio(_ONE_SECOND)          # 1 秒的音訊

    clock.t += 1.7                        # 若忽略緩衝，此時已「播完 1s + tail 0.6s」
    assert gate.is_open() is False, "喇叭要等緩衝排空才發聲，這時它還在響"

    clock.t += 2.0                        # 1s 播放 + 2s 緩衝 + 0.6s tail
    assert gate.is_open() is True


def test_the_buffer_delay_defaults_to_the_configured_playback_buffer():
    """預設值要跟著 PLAYBACK_BUFFER_US 走，不能讓人記得手動同步兩個數字。

    這正是當初出錯的原因：改了 buffer 卻沒有人想到要一起改 tail。
    綁在一起之後，調 buffer 時閘門自動跟上。
    """
    expected = live_client._PLAYBACK_BUFFER_US / 1_000_000
    assert live_client.PlaybackGate(now=_Clock())._buffer_delay == expected


def test_flush_also_clears_the_buffered_audio():
    """barge-in 時 aplay 子行程是被 kill 重啟的，緩衝裡的音訊一起消失。

    所以打斷之後不必再等緩衝排空——那些音訊永遠不會發聲了。
    若這裡還扣著緩衝延遲，每次打斷都會白白多關 2 秒上行，
    孩子講的下一句會被吃掉開頭。
    """
    clock = _Clock()
    gate = live_client.PlaybackGate(tail_s=0.6, buffer_delay_s=2.0, now=clock)
    gate.note_audio(_ONE_SECOND * 5)
    gate.note_flush()

    clock.t += 0.7                        # 只需等 tail，不需等緩衝
    assert gate.is_open() is True


def test_consecutive_chunks_accumulate_playback_time():
    """連續收到多塊音訊時，播放時間要累加而不是重設。"""
    clock = _Clock()
    gate = live_client.PlaybackGate(tail_s=0.0, buffer_delay_s=0, now=clock)
    gate.note_audio(_ONE_SECOND * 2)
    gate.note_audio(_ONE_SECOND * 2)      # 同一時刻又收到 2 秒
    clock.t += 3.0
    assert gate.is_open() is False, "兩塊各 2 秒應累加成 4 秒"
    clock.t += 1.1
    assert gate.is_open() is True


def test_flush_reopens_the_gate_immediately():
    """barge-in 清掉播放緩衝後，玩偶立刻閉嘴，不該再等剩餘播放時間。"""
    clock = _Clock()
    gate = live_client.PlaybackGate(tail_s=0.0, now=clock)
    gate.note_audio(_ONE_SECOND * 10)
    assert gate.is_open() is False
    gate.note_flush()
    assert gate.is_open() is True


def test_gate_tail_is_configurable():
    """現場要能調：太短仍自我打斷，太長會吃掉孩子講話的開頭。"""
    clock = _Clock()
    gate = live_client.PlaybackGate(tail_s=2.0, buffer_delay_s=0, now=clock)
    gate.note_audio(_ONE_SECOND // 100)   # 極短的一塊，主要驗 tail
    clock.t += 1.0
    assert gate.is_open() is False
    clock.t += 1.1
    assert gate.is_open() is True


@pytest.mark.asyncio
async def test_uplink_sends_silence_instead_of_going_dark_while_the_toy_speaks():
    """閘門關閉時要送**靜音**，不是什麼都不送。

    2026-07-30 實測，「什麼都不送」會在串流中挖出一個洞，而 Nova Sonic 是
    持續串流協定、由它的 server VAD 判斷 turn 邊界。同一天已經證實過兩次
    這個模式會出事：

    - 近場門檻丟棄 31s → 下行音訊 0 bytes，玩偶全程沉默
    - 播放期間丟棄 34.5s → 玩偶自問自答、自己稱讚、繞回開頭重講

    送零值 PCM 的內容是**誠實的**——使用者當下確實沒在說話——而且串流保持
    連續，VAD 讀得到「這段是靜默」而不是「訊號不見了」。同時不含迴音，
    因為送的不是麥克風收到的東西。
    """
    ws = _FakeWS()
    stop = asyncio.Event()

    class _ClosedGate:
        def is_open(self):
            return False

    class _LoudMic:
        """一直收到很大聲的東西——就是玩偶自己的聲音。"""

        def __init__(self):
            self.reads = 0

        def read(self, n):
            self.reads += 1
            if self.reads >= 3:
                stop.set()
            return b"\x7f\x7f" * (n // 2)

        def stop(self):
            pass

    await live_client.pump_uplink(ws, _LoudMic(), stop, _ClosedGate())

    assert ws.sent_bytes, "閘門關著也要維持串流，不能整段斷掉"
    assert all(c == bytes(len(c)) for c in ws.sent_bytes), \
        "送出去的必須是靜音，不能是麥克風收到的內容（那就是迴音）"


@pytest.mark.asyncio
async def test_uplink_keeps_reading_the_mic_while_the_gate_is_closed():
    """閘門關閉時仍要**繼續讀麥克風**，否則 arecord 的緩衝會爆掉。"""
    ws = _FakeWS()
    stop = asyncio.Event()

    class _ClosedGate:
        def is_open(self):
            return False

    class _Mic:
        def __init__(self):
            self.reads = 0

        def read(self, _n):
            self.reads += 1
            if self.reads >= 3:
                stop.set()
            return b"\x00" * 8

        def stop(self):
            pass

    mic = _Mic()
    await live_client.pump_uplink(ws, mic, stop, _ClosedGate())

    assert mic.reads >= 3, "沒有繼續讀麥克風，arecord 緩衝會爆"
    assert all(c == bytes(len(c)) for c in ws.sent_bytes), \
        "玩偶講話時把麥克風內容送上去 → 會自我打斷（該送靜音）"


@pytest.mark.asyncio
async def test_downlink_audio_notifies_the_gate():
    """收到下行音訊要通知閘門，否則閘門永遠不會關。"""
    ws = _FakeWS(inbound=[b"\x01\x02"])
    sink = _FakeSink()
    noted = {"n": 0}

    class _Gate:
        def note_audio(self, nbytes):
            noted["n"] += 1
            noted["bytes"] = nbytes

    await live_client.pump_downlink(ws, sink, _Gate())
    assert noted["n"] == 1
    assert noted["bytes"] == 2, "要把實際 byte 數傳給閘門才能推算播放時長"


# ---------------------------------------------------------------------------
# 近場門檻：擋掉遠處的電視與旁人
# ---------------------------------------------------------------------------

def _pcm(peak_value: int, n: int = 1600) -> bytes:
    """造一塊 PCM16，峰值為 peak_value。"""
    import struct as _s
    samples = [0] * n
    samples[n // 2] = peak_value
    return _s.pack(f"<{n}h", *samples)


def test_peak_of_silence_is_zero():
    assert live_client.chunk_peak(_pcm(0)) == 0.0


def test_peak_scales_to_unit_range():
    assert live_client.chunk_peak(_pcm(32767)) == pytest.approx(1.0, abs=0.001)
    assert live_client.chunk_peak(_pcm(16384)) == pytest.approx(0.5, abs=0.001)


def test_peak_handles_negative_samples():
    """負向峰值一樣算數——只看正半邊會低估一半的音量。"""
    assert live_client.chunk_peak(_pcm(-16384)) == pytest.approx(0.5, abs=0.001)


def test_peak_of_empty_chunk_does_not_crash():
    assert live_client.chunk_peak(b"") == 0.0
    assert live_client.chunk_peak(b"\x00") == 0.0  # 半個 sample


def test_distant_television_is_filtered_out():
    """遠處電視的音量低於門檻 → 不上行。

    2026-07-30 實測：旁邊播放的《寶貝多米》講的「我明白了」被送上雲端、
    判定成使用者插話 → 打斷玩偶 → 重講。使用者確認那句不是他說的。
    """
    quiet = _pcm(int(0.02 * 32768))
    assert live_client.is_near_field(quiet, threshold=0.06) is False


def test_child_speaking_into_the_toy_passes():
    """近距離人聲（preflight 實測 peak≈0.135）必須通過。"""
    close = _pcm(int(0.135 * 32768))
    assert live_client.is_near_field(close, threshold=0.06) is True


def test_threshold_zero_disables_filtering():
    """安靜環境想要最高靈敏度時可完全關閉。"""
    assert live_client.is_near_field(_pcm(1), threshold=0.0) is True


@pytest.mark.asyncio
async def test_uplink_mutes_quiet_chunks_rather_than_dropping_them():
    """低於近場門檻的音訊要換成靜音，不是整塊不送。

    2026-07-30 之前這裡是 `continue`（完全不送），實測造成串流破洞、
    Nova Sonic 的 VAD 判不出 turn → 下行音訊 0 bytes。內容一樣被擋掉
    （遠場噪音不會上雲），但串流的連續性保住了。
    """
    ws = _FakeWS()
    stop = asyncio.Event()

    class _Mic:
        def __init__(self):
            self.reads = 0

        def read(self, _n):
            self.reads += 1
            if self.reads >= 3:
                stop.set()
            return _pcm(int(0.01 * 32768))  # 很小聲

        def stop(self):
            pass

    await live_client.pump_uplink(ws, _Mic(), stop)
    assert ws.sent_bytes, "串流不能整段斷掉（見上方 docstring）"
    assert all(c == bytes(len(c)) for c in ws.sent_bytes), \
        "遠場噪音被原樣送上去了，會觸發誤判插話"


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
async def test_speaker_writes_do_not_block_the_event_loop():
    """寫入 aplay 是阻塞 I/O，必須丟到執行緒。

    直接放在 async 迴圈裡的話，aplay 緩衝一滿就凍結 event loop，
    websockets 的 keepalive ping 送不出去 → 連線被判定死亡。
    2026-07-30 第一次實機跑就撞到 `1011 keepalive ping timeout`。
    """
    ws = _FakeWS(inbound=[b"\x01"])
    sink = _FakeSink()
    seen = {"to_thread": 0}

    real = asyncio.to_thread

    async def _spy(fn, *a, **kw):
        seen["to_thread"] += 1
        return await real(fn, *a, **kw)

    original = asyncio.to_thread
    asyncio.to_thread = _spy
    try:
        await live_client.pump_downlink(ws, sink)
    finally:
        asyncio.to_thread = original

    assert seen["to_thread"] >= 1, "音訊寫入沒有經過 asyncio.to_thread"
    assert sink.written == [b"\x01"]


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
            return _pcm(int(0.3 * 32768))   # 夠大聲，要能通過近場門檻

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
