# -*- coding: utf-8 -*-
"""live_client.py — 裝置端 Nova Sonic S2S client（沿用既有 /ws/live 協定）。

與 `local_client.py`（回合式 /ws/talk：錄 4 秒 → ASR → LLM → TTS → 播放）並存，
但走的是完全不同的形狀：**持續雙向串流**，上行 PCM16(16k)、下行 24k 音訊，
turn 邊界由 Nova Sonic 自己的 VAD 判斷（協定註明「連續模式：user_end 無意義」）。
全雙工，孩子可以插話打斷。

## 觸發設計：按鍵開關一段 live session

Nova Sonic 預期持續串流，但**裝置若永遠在聽，環境噪音會不斷誤觸**——2026-07-30
實測，旁邊播放兒童節目時 ASR 收到過把噪音判成韓文字符的紀錄；決賽會場人聲更吵，
玩偶會自己跟電視聊起來。所以：

    按一下 power 鍵 → 開始串流 → 多輪自然對話（含打斷）→ 再按一下 → 回待機

待機時完全不送音訊，零誤觸、零雲端流量。**「按著講」不可行**：按住 power 鍵
8–10 秒會觸發 PMIC 硬體斷電，軟體攔不住（見 edge/runtime/README.md）。

## 為什麼用 arecord/aplay 子行程而非 Python 音訊套件

裝置無 gcc/cmake（見 provision_device.sh），且 `wake_listener.py` 已證實
`arecord ... -` + `Popen(stdout=PIPE)` 這條串流路徑在本板可用。零新相依。

**上行 16k、下行 24k 是兩個不同的取樣率**，混用會變成怪腔怪調。
兩邊都必須用 `-t raw`：WAV header 只在檔案開頭出現一次，串流送出去會讓對端
把 header bytes 當成音訊取樣。

用法（裝置上）：

    ./.venv/bin/python -m edge.runtime.live_client
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import struct
import subprocess
import time

import websockets

from edge.runtime import audio_io

_log = logging.getLogger(__name__)

# 回合式 client 的 unit 名稱。它與本程式搶同一支 USB 麥克風（見 assert_exclusive_mic）。
LOCAL_CLIENT_UNIT = "talkybuddy-local-client.service"

WS_HOST: str = os.environ.get("TALKYBUDDY_EDGE_WS_HOST", "127.0.0.1")
WS_PORT: int = int(os.environ.get("TALKYBUDDY_EDGE_WS_PORT", "8787"))

# Nova Sonic：收 16k、吐 24k。不是筆誤，也不能統一。
UPLINK_RATE = 16000
DOWNLINK_RATE = 24000

# 每次上行送出的位元組數。16k×2 bytes = 32000 B/s，3200 bytes ≈ 100ms。
# 太小會讓子行程讀寫與 WS 訊框過於頻繁，太大則增加對話延遲。
UPLINK_CHUNK_BYTES = 3200

_RECONNECT_DELAY_S = 2.0

# 播放結束後要再靜默多久才恢復上行。喇叭與 USB 麥克風距離很近，玩偶會把自己
# 的聲音收進去，Nova 的 VAD 判定成「使用者插話」→ 觸發 barge-in → **自己打斷
# 自己**。tail 是為了吃掉喇叭殘響與房間回音；太短仍會自我打斷，太長則孩子講話
# 的開頭會被吃掉。0.6s 為起始值，現場可用環境變數調。
_PLAYBACK_TAIL_S = float(os.environ.get("TALKYBUDDY_EDGE_PLAYBACK_TAIL_S", "0.6"))

# aplay 的 ALSA 環形緩衝大小（微秒）。預設值太小，吸收不了 Nova Sonic 音訊
# 成批到達的抖動——實機一場對話出現 16 次 underrun，每次 0.8–1.9 秒，聽感是
# 「斷斷續續」。2 秒足以吸收觀測到的最大空檔（1.9s），代價是聲音晚一點出來。
_PLAYBACK_BUFFER_US = int(os.environ.get("TALKYBUDDY_EDGE_PLAYBACK_BUFFER_US", "2000000"))

# 近場門檻（peak 0.0–1.0）：低於此值的音訊不上行，用來擋掉遠處的電視與旁人。
# 2026-07-30 實測 preflight 近距離人聲 peak≈0.135；遠場噪音明顯更低。
# 0.06 是保守起點——寧可讓一點噪音通過，也不要吃掉孩子講話的開頭。
# 設 0 可完全關閉過濾（安靜環境）。
_NEAR_FIELD_PEAK = float(os.environ.get("TALKYBUDDY_EDGE_NEAR_FIELD_PEAK", "0.06"))

# 播放音量（0.0–1.0）。**必須在軟體做**：這塊板子的 ALSA mixer 對 3.5mm 輸出
# 完全無效（Lineout -4dB 與 ADDA_DL_GAIN -18dB 實測都不影響音量，推測後方接了
# 硬體固定增益的功放）。詳見 audio_io.scale_pcm16。
# 喇叭小聲一點也直接減少自我迴音，對 S2S 的自我打斷有幫助。
_PLAYBACK_VOLUME = float(os.environ.get("TALKYBUDDY_EDGE_PLAYBACK_VOLUME", "1.0"))

# classify_live_event 的動作。
# 注意這裡沒有「播放」——**音訊不走 JSON**，一律是 binary frame（server/app.py
# 的 emit_bytes）。JSON 事件只有 interrupt / live_error / live_transcript /
# turn_end 四種，binary 與 JSON 的分流在 pump_downlink 就做掉了。
SHOW = "show"          # 逐字稿 → 印出來
FLUSH = "flush"        # 打斷 → 立刻清掉還沒播完的音訊
CONTINUE = "continue"  # 無事發生，繼續
ABORT = "abort"        # session 結束


def _ws_url() -> str:
    """/ws/live 的連線位址。

    **`?mode=continuous` 不可省。** server 端以這個 query 參數分流（見
    `server/app.py::ws_live` 的 `continuous = websocket.query_params.get(...)`）：

    - 有它 → 上下行雙 Task 常駐，turn 邊界交給 Nova VAD（本 client 要的）
    - 沒有 → 回合式，server 會**等 `{"type":"user_end"}`** 才 end_user_turn
      並開始迭代模型事件

    少了它會兩邊互等：server 等 user_end，client 等下行事件。2026-07-30 實機
    實錄的症狀是「上行 825600 bytes（25.8s）、下行 0 bytes、0 則事件」——
    音訊確實送到了，但 server 從未開始產出。
    """
    return f"ws://{WS_HOST}:{WS_PORT}/ws/live?mode=continuous"


def classify_live_event(payload: dict) -> str:
    """一則下行 JSON 事件該做什麼。純函式。

    只處理 JSON 事件；音訊是 binary frame，在 pump_downlink 就分流掉了。
    未知型別一律 CONTINUE：伺服器之後新增事件時，舊的裝置端不該整個掛掉。
    """
    etype = (payload or {}).get("type")
    if etype == "live_transcript":
        return SHOW
    if etype == "interrupt":
        return FLUSH
    if etype == "live_error":
        return ABORT
    return CONTINUE


class PlaybackGate:
    """玩偶在講話時關閉上行，避免它把自己的聲音當成使用者插話。

    **為什麼需要**：3.5mm 喇叭與 USB 麥克風距離很近，玩偶自己的語音會被收進去，
    Nova Sonic 的 server VAD 判定成使用者說話 → 發 interrupt → 打斷自己正在講的
    話。2026-07-30 第一次成功跑通 S2S 時就撞到，現場表現是「會自己打斷」。

    **代價**：播放期間孩子無法打斷玩偶（真正的 barge-in 需要回音消除 AEC，
    而裝置無 gcc/cmake、裝不了 AEC 套件）。權衡下「不會自我打斷」比「能被打斷」
    對 demo 重要得多——自我打斷會讓對話完全無法進行。

    **追蹤的是預估播完的時刻，不是「最後收到資料的時刻」**——這兩者差很多。
    下行音訊成批到達，`aplay` 收下後還要花對應的時長才播完（log 裡的
    `underrun` 就是緩衝積壓的證據）。第一版用「最後收到資料 + tail」，結果
    丟棄 74.2s 卻播了 85.3s，中間約 11 秒的空窗讓玩偶收到自己的聲音，
    逐字稿裡於是出現 `[USER] 你跟我一起说` 這種它自己剛講過的句子。

    **還要再扣掉 aplay 的緩衝延遲**。寫進 aplay 的音訊不是立刻從喇叭出來——
    `--buffer-time` 設多少，發聲就晚多少。2026-07-30 實測：buffer 為了壓下
    underrun 調成 2 秒，tail 卻還是 0.6 秒，於是

        閘門關閉 ： [寫入 ────────── 寫入+時長+0.6]
        喇叭實響 ： [寫入+2.0 ──────────── 寫入+2.0+時長]
                                  ↑ 閘門在這裡就開了，喇叭還在響

    中間約 1.4 秒的空窗讓玩偶收到自己的聲音，逐字稿出現 `[USER] 哎西`
    這種使用者確認沒講過的句子。緩衝延遲預設跟著 `_PLAYBACK_BUFFER_US` 走，
    不要求任何人記得同步調兩個數字——沒記錄的耦合正是當初出錯的原因。

    `now` 可注入以便測試（真機的時序沒辦法在 CI 重現）。
    """

    def __init__(self, tail_s: float = _PLAYBACK_TAIL_S,
                 buffer_delay_s: float | None = None, now=time.monotonic,
                 rate: int = DOWNLINK_RATE):
        # rate 預設 DOWNLINK_RATE（24k，Nova Sonic）。pipecat 那條路的邊緣 TTS
        # 是 22050Hz——**算錯取樣率就會算錯播放時長**，閘門會提早開，
        # 玩偶就收得到自己的尾音（2026-07-31 真人實測聽成「跟我說一定方」）。
        self._rate = rate
        self._tail = tail_s
        self._buffer_delay = (_PLAYBACK_BUFFER_US / 1_000_000
                              if buffer_delay_s is None else buffer_delay_s)
        self._now = now
        self._playing_until: float = 0.0
        # 打斷之後緩衝被清空，那些音訊永遠不會發聲——此時不該再扣緩衝延遲，
        # 否則每次打斷都白關 2 秒上行，孩子下一句的開頭會被吃掉。
        self._buffer_drained = True

    def note_audio(self, nbytes: int) -> None:
        """收到一塊下行音訊：依長度推算它會播到什麼時候。

        24kHz、16-bit、mono → 每秒 2×24000 bytes。若前一段還沒播完就接續累加，
        否則從現在起算。
        """
        duration = nbytes / 2 / self._rate
        start = max(self._now(), self._playing_until)
        self._playing_until = start + duration
        self._buffer_drained = False

    def note_flush(self) -> None:
        """播放緩衝被清掉了（barge-in）——玩偶立刻閉嘴，不必再等。

        `flush_pending()` 是 kill 掉 aplay 子行程再重啟，緩衝裡還沒發聲的
        音訊一起消失，所以連緩衝延遲都不必再等。
        """
        self._playing_until = self._now()
        self._buffer_drained = True

    def is_open(self) -> bool:
        """現在可以送上行嗎（喇叭已真的靜下來並滿 tail）。"""
        delay = 0.0 if self._buffer_drained else self._buffer_delay
        return self._now() >= (self._playing_until + delay + self._tail)


def chunk_peak(chunk: bytes) -> float:
    """這塊 PCM16 的峰值音量（0.0–1.0）。純函式。

    只取 peak 而非 RMS：計算便宜（每 100ms 算一次 1600 個樣本），而近場/遠場
    的差異在峰值上就很明顯，不需要更精細的量測。
    """
    n = len(chunk) // 2
    if n == 0:
        return 0.0
    peak = 0
    for value in struct.unpack(f"<{n}h", chunk[: n * 2]):
        if value < 0:
            value = -value
        if value > peak:
            peak = value
    return peak / 32768.0


def is_near_field(chunk: bytes, threshold: float = _NEAR_FIELD_PEAK) -> bool:
    """這塊音訊夠不夠大聲，值得送上雲端。

    **為什麼需要**：Nova Sonic 是持續串流、由它的 server VAD 判斷誰在說話，
    對「聲音從多遠來」毫無概念。2026-07-30 實測，旁邊播放的兒童節目《寶貝多米》
    講的「我明白了」被收進去、判定成使用者插話 → 打斷玩偶 → 重講，對話變得
    斷斷續續。使用者確認那句話不是他說的。決賽會場的人聲比電視吵得多。

    近場門檻用距離換取抗噪：孩子對著玩偶講話音量大，遠處的電視與旁人音量小。
    這不是完美的方案（大聲喊的旁人仍會穿透），但在無法做波束成形的硬體上，
    它是成本最低、效果最直接的一道防線。

    threshold=0 可完全關閉此過濾（安靜環境下想要最高靈敏度時）。
    """
    if threshold <= 0:
        return True
    return chunk_peak(chunk) >= threshold


def build_arecord_argv(device: str, rate: int = UPLINK_RATE) -> list[str]:
    """上行：mono S16_LE raw 串流到 stdout。裝置為空時不帶 -D。

    `rate` 預設 `UPLINK_RATE`（16k，Nova Sonic 的上行取樣率），既有呼叫端行為不變。
    另一個消費者是 pipecat 的 `AlsaInputTransport`，它會傳入 pipeline 協商出來的
    取樣率——**餵錯取樣率不會報錯，只會讓音調與速度跑掉**，所以不寫死。
    """
    argv = ["arecord"]
    if device:
        argv += ["-D", device]
    argv += ["-f", "S16_LE", "-r", str(rate), "-c", "1", "-t", "raw", "-"]
    return argv


def build_aplay_argv(device: str, rate: int = DOWNLINK_RATE) -> list[str]:
    """下行：從 stdin 讀 mono S16_LE raw 播放。裝置為空時不帶 -D。

    `rate` 預設 `DOWNLINK_RATE`（24k，Nova Sonic 的下行取樣率）。

    **這個參數是 2026-07-31 真機測試逼出來的**：邊緣 TTS 輸出 22050Hz，而當時
    寫死 24000 送給 aplay，播放會快 8.8%、音調偏高——正是本檔開頭警告的
    「兩個不同取樣率混用會變成怪腔怪調」，只是這次踩到的是我們自己。
    **aplay 不會因為取樣率對不上而報錯**，所以單元測試看不出來，要真機才會現形。

    `--buffer-time` 不可省：Nova Sonic 的音訊**成批到達、中間有生成空檔**，
    aplay 預設緩衝吸收不了這種抖動，聽感就是「斷斷續續」。2026-07-30 實機
    一場對話出現 16 次 `underrun!!!`，每次 0.8–1.9 秒。這是裝置本機的播放
    緩衝問題，與到 AWS 的網路無關（client 與 server 都在同一台走 loopback）。

    代價是回覆聲音會晚一點出來（緩衝要先填），所以不能無限加大。
    """
    argv = ["aplay"]
    if device:
        argv += ["-D", device]
    argv += ["-f", "S16_LE", "-r", str(rate), "-c", "1", "-t", "raw",
             "--buffer-time", str(_PLAYBACK_BUFFER_US), "-"]
    return argv


class MicSource:
    """arecord 子行程包裝：持續讀取 raw PCM。"""

    def __init__(self, device: str):
        self._argv = build_arecord_argv(device)
        # stderr 不吞：arecord 起不來時（裝置被佔用、名稱錯）唯一的線索就在這裡。
        # 第一次實機除錯時把它丟進 DEVNULL，結果只看得到「session 立刻結束」
        # 這個症狀，完全無法定位。arecord 正常時也只會印一行 "Recording raw data"。
        # preexec_fn：父行程被 pkill 掉時 arecord 必須跟著死，否則變孤兒
        # 繼續獨佔麥克風（見 audio_io.die_with_parent）。
        self._proc = subprocess.Popen(
            self._argv, stdout=subprocess.PIPE,
            preexec_fn=audio_io.die_with_parent,
        )
        _log.info("arecord 啟動：%s", " ".join(self._argv))

    def read(self, n: int) -> bytes:
        if self._proc.stdout is None:
            return b""
        data = self._proc.stdout.read(n)
        if not data:
            rc = self._proc.poll()
            _log.error("arecord 沒有資料了（returncode=%s）——上行中止", rc)
        return data

    def stop(self) -> None:
        try:
            self._proc.terminate()
            self._proc.wait(timeout=3)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass


class SpeakerSink:
    """aplay 子行程包裝。

    `flush_pending()` 用**重啟子行程**實作打斷：aplay 沒有「丟掉已寫入但還沒播完
    的緩衝」的介面，只寫入端停手的話，被打斷的那句仍會播完，體感就不是即時對話了。
    """

    def __init__(self, device: str):
        self._device = device
        self._proc = None
        self._start()

    def _start(self) -> None:
        # stderr 同樣不吞（見 MicSource 的理由）
        self._proc = subprocess.Popen(
            build_aplay_argv(self._device), stdin=subprocess.PIPE,
            preexec_fn=audio_io.die_with_parent,
        )

    def write(self, data: bytes) -> None:
        try:
            if self._proc and self._proc.stdin:
                # 音量只能在這裡做——ALSA mixer 對本板 3.5mm 輸出無效
                data = audio_io.scale_pcm16(data, _PLAYBACK_VOLUME)
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
        except Exception:
            # 播放失敗不該讓整場對話中斷（比照 audio_io.play_wav_bytes）
            _log.debug("寫入 aplay 失敗", exc_info=True)

    def flush_pending(self) -> None:
        self._kill()
        self._start()

    def _kill(self) -> None:
        try:
            if self._proc:
                self._proc.kill()
                self._proc.wait(timeout=2)
        except Exception:
            pass

    def stop(self) -> None:
        try:
            if self._proc and self._proc.stdin:
                self._proc.stdin.close()
            if self._proc:
                self._proc.wait(timeout=3)
        except Exception:
            self._kill()


async def pump_downlink(ws, sink, gate=None) -> None:
    """收下行：binary → 喇叭；JSON → 依 classify_live_event 分派。

    壞掉的 JSON 只跳過那一則，不中斷整場對話。
    """
    stats = {"audio_bytes": 0, "events": 0}
    try:
        await _pump_downlink_inner(ws, sink, stats, gate)
    finally:
        _log.info("下行統計：音訊 %d bytes（~%.1fs @24k）、JSON 事件 %d 則",
                  stats["audio_bytes"], stats["audio_bytes"] / 2 / DOWNLINK_RATE,
                  stats["events"])


async def _pump_downlink_inner(ws, sink, stats, gate=None) -> None:
    async for raw in ws:
        if isinstance(raw, (bytes, bytearray)):
            # 寫入 aplay 的 stdin 是**阻塞 I/O**：緩衝一滿就卡住整個 event loop，
            # websockets 的 keepalive ping 送不出去，連線被判定死亡——第一次實機跑
            # 就撞到 `1011 keepalive ping timeout`。這是同一類錯誤在本專案的第三次
            # （local_client 的 wait_for_trigger、preflight 的收音裝置），
            # 凡是阻塞呼叫都不能直接放進 async 迴圈。
            await asyncio.to_thread(sink.write, bytes(raw))
            stats["audio_bytes"] += len(raw)
            if gate is not None:
                # 依音訊長度推算播到何時 → 那之前都關閉上行
                gate.note_audio(len(raw))
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        stats["events"] += 1
        action = classify_live_event(payload)
        if action == FLUSH:
            sink.flush_pending()
            if gate is not None:
                gate.note_flush()
        elif action == SHOW:
            role = payload.get("role", "?")
            text = payload.get("text", "")
            print(f"  [{role}] {text}", flush=True)
        elif action == ABORT:
            _log.warning("live_error：%s", payload.get("reason"))
            return


async def pump_uplink(ws, mic, stop: asyncio.Event, gate=None) -> None:
    """送上行：持續把麥克風 PCM 推給伺服器，直到 stop 被設起來。

    `mic.read` 是阻塞的子行程讀取，必須丟到執行緒——直接在 async 函式裡呼叫會
    凍結 event loop，下行就收不到、keepalive 也送不出去（local_client 踩過這個
    坑：閒置後連線被判定死亡而崩潰）。
    """
    sent = 0
    dropped = 0
    quiet = 0
    while not stop.is_set():
        chunk = await asyncio.to_thread(mic.read, UPLINK_CHUNK_BYTES)
        if not chunk:
            _log.info("上行統計：已送 %d bytes（~%.1fs @16k）", sent, sent / 2 / UPLINK_RATE)
            return  # arecord 掛了，收手
        # 玩偶正在講話時，把這塊換成**靜音**再送——不是不送。
        #
        # 「什麼都不送」會在串流中挖出一個洞，而 Nova Sonic 是持續串流協定、
        # 由它的 server VAD 判 turn 邊界。2026-07-30 實測，同一個模式出事兩次：
        #   近場門檻丟棄 31s   → 下行音訊 0 bytes，玩偶全程沉默
        #   播放期間丟棄 34.5s → 玩偶自問自答、自己稱讚、繞回開頭重講
        # 送零值 PCM 的內容是誠實的（使用者當下確實沒說話），串流保持連續，
        # 而且不含迴音——送的不是麥克風收到的東西。
        muted = False
        if gate is not None and not gate.is_open():
            dropped += len(chunk)
            muted = True
        # 近場門檻：遠處的電視／旁人音量小，擋掉以免被當成插話（見 is_near_field）
        elif not is_near_field(chunk):
            quiet += len(chunk)
            muted = True
        if muted:
            chunk = bytes(len(chunk))
        try:
            await ws.send(chunk)
        except Exception as exc:
            # 這條路徑先前沒有 log，導致只看得到「session 由 uplink 結束」
            # 卻不知道為什麼——實機除錯時卡在這裡。
            _log.error("上行送出失敗（%s: %s）——連線可能已被伺服器關閉",
                       type(exc).__name__, exc)
            return
        sent += len(chunk)
    _log.info("上行統計：已送 %d bytes（~%.1fs）、播放期間丟棄 %.1fs、低於近場門檻丟棄 %.1fs——由按鍵結束",
              sent, sent / 2 / UPLINK_RATE, dropped / 2 / UPLINK_RATE,
              quiet / 2 / UPLINK_RATE)


async def run_session(ws, mic, sink, stop: asyncio.Event) -> None:
    """跑一場 live 對話：上行、下行並行，任一結束就收攤。"""
    gate = PlaybackGate()
    up = asyncio.create_task(pump_uplink(ws, mic, stop, gate), name="uplink")
    down = asyncio.create_task(pump_downlink(ws, sink, gate), name="downlink")
    done, pending = await asyncio.wait(
        {up, down}, return_when=asyncio.FIRST_COMPLETED
    )
    # 哪一邊先收工決定了「session 為什麼結束」——沒有這行就只看得到
    # 「session 立刻結束」這個症狀，分不出是麥克風起不來還是伺服器關了連線。
    for task in done:
        exc = task.exception() if not task.cancelled() else None
        _log.info("session 由 %s 結束%s", task.get_name(),
                  f"（例外：{type(exc).__name__}: {exc}）" if exc else "")
    stop.set()
    for task in pending:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


# 被「過期的」等待任務接到的按鍵先寄放在這裡，交給主迴圈消費。
#
# 為什麼需要：等按鍵是 asyncio.to_thread 包的阻塞讀取，**cancel 不會真的中斷
# 執行緒**。若這場 session 是因為連線問題結束（不是因為按鍵），那個執行緒仍在
# 等，於是會吃掉使用者的下一次按鍵——表現成「有時候要按兩次才有反應」，
# 在現場看起來就像按鍵不靈。寄放起來讓主迴圈直接取用，按鍵就不會被吞掉。
_pending_trigger = False


async def _wait_for_trigger() -> None:
    """等按鍵；若先前有被寄放的按鍵就直接消費掉，不再等一次。"""
    global _pending_trigger
    if _pending_trigger:
        _pending_trigger = False
        return
    await asyncio.to_thread(audio_io.wait_for_trigger)


async def _wait_for_stop_key(stop: asyncio.Event) -> None:
    """等「再按一次」結束這場對話。按鍵讀取是阻塞的，丟到執行緒。"""
    global _pending_trigger
    await asyncio.to_thread(audio_io.wait_for_trigger)
    if stop.is_set():
        # session 早就結束了，這次按鍵是使用者要開下一場——留給主迴圈
        _pending_trigger = True
    else:
        stop.set()


async def run_loop() -> None:
    """待機 → 按鍵開始一段 live 對話 → 再按一次結束 → 回待機。

    待機期間完全不連線、不送音訊：零誤觸、零雲端流量（見模組 docstring）。
    """
    assert_exclusive_mic()
    local_client_ready()

    while True:
        print("按一下按鍵開始即時對話（再按一下結束）...", flush=True)
        await _wait_for_trigger()

        mic = None
        sink = None
        stop = asyncio.Event()
        try:
            # max_size=None：對齊 scripts/verify_ws_live_e2e.py 那支已實證可行的
            # 腳本。預設有 1MB 接收上限，超過會直接關閉連線；下行是連續音訊，
            # 沒必要在這裡設限。
            async with websockets.connect(_ws_url(), max_size=None) as ws:
                print("  ● 連線中，開始說話（再按一次按鍵結束）", flush=True)
                mic = MicSource(audio_io._ARECORD_DEVICE)
                sink = SpeakerSink(audio_io._PLAYBACK_DEVICE)
                stopper = asyncio.create_task(_wait_for_stop_key(stop))
                try:
                    await run_session(ws, mic, sink, stop)
                finally:
                    stopper.cancel()
                    try:
                        await stopper
                    except (asyncio.CancelledError, Exception):
                        pass
                try:
                    await ws.send(json.dumps({"type": "bye"}))
                except Exception:
                    pass
        except (websockets.ConnectionClosed, OSError) as exc:
            _log.warning("連線問題（%s），%.1f 秒後回待機",
                         type(exc).__name__, _RECONNECT_DELAY_S)
            await asyncio.sleep(_RECONNECT_DELAY_S)
        except Exception:
            _log.exception("這場對話失敗，回待機等下一次觸發")
        finally:
            if mic is not None:
                mic.stop()
            if sink is not None:
                sink.stop()
            print("  ○ 已結束，回待機", flush=True)


def local_client_is_active(run=subprocess.run) -> bool:
    """回合式 client 是否正在跑（＝麥克風已被佔走）。

    systemctl 不存在（開發機、容器）或查詢失敗時回 False：守衛的職責是
    「發現已知衝突就明講」，不是「無法確認就一律拒絕」——後者會讓這支程式
    在沒有 systemd 的環境完全不能跑。
    """
    try:
        proc = run(
            ["systemctl", "is-active", LOCAL_CLIENT_UNIT],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return False
    return proc.stdout.strip() == "active"


def assert_exclusive_mic() -> None:
    """麥克風被回合式 client 佔著就拒絕啟動，並直接給出解法。

    `Conflicts=` 只在經 systemd 啟動時生效；手動 `python -m edge.runtime.
    live_client` 繞過了它，而這正是 2026-07-30 當天的啟動方式。少了這道守衛，
    症狀是上行 0 bytes、玩偶毫無反應，看起來跟按鍵故障一模一樣。

    真正浪費時間的不是被擋住，是**不知道被誰擋住**，所以訊息要能直接複製貼上。
    """
    if not local_client_is_active():
        return
    raise RuntimeError(
        f"{LOCAL_CLIENT_UNIT} 正在執行，它獨佔著同一支 USB 麥克風。\n"
        "兩個 client 不能共存（ALSA capture 是獨佔的），繼續下去上行會是 0 bytes。\n"
        "改用 systemd 啟動，Conflicts= 會自動幫你停掉回合式那邊：\n"
        "    systemctl start talkybuddy-live-client\n"
        "或先手動停掉再跑：\n"
        f"    systemctl stop {LOCAL_CLIENT_UNIT}"
    )


def local_client_ready() -> None:
    """沿用 local_client 的伺服器就緒探測，避免搶在 uvicorn 起來前連線。"""
    from edge.runtime.local_client import wait_for_server_ready
    wait_for_server_ready()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_loop())
