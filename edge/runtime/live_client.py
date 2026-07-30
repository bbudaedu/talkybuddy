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
import subprocess

import websockets

from edge.runtime import audio_io

_log = logging.getLogger(__name__)

WS_HOST: str = os.environ.get("TALKYBUDDY_EDGE_WS_HOST", "127.0.0.1")
WS_PORT: int = int(os.environ.get("TALKYBUDDY_EDGE_WS_PORT", "8787"))

# Nova Sonic：收 16k、吐 24k。不是筆誤，也不能統一。
UPLINK_RATE = 16000
DOWNLINK_RATE = 24000

# 每次上行送出的位元組數。16k×2 bytes = 32000 B/s，3200 bytes ≈ 100ms。
# 太小會讓子行程讀寫與 WS 訊框過於頻繁，太大則增加對話延遲。
UPLINK_CHUNK_BYTES = 3200

_RECONNECT_DELAY_S = 2.0

# classify_live_event 的動作。
# 注意這裡沒有「播放」——**音訊不走 JSON**，一律是 binary frame（server/app.py
# 的 emit_bytes）。JSON 事件只有 interrupt / live_error / live_transcript /
# turn_end 四種，binary 與 JSON 的分流在 pump_downlink 就做掉了。
SHOW = "show"          # 逐字稿 → 印出來
FLUSH = "flush"        # 打斷 → 立刻清掉還沒播完的音訊
CONTINUE = "continue"  # 無事發生，繼續
ABORT = "abort"        # session 結束


def _ws_url() -> str:
    return f"ws://{WS_HOST}:{WS_PORT}/ws/live"


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


def build_arecord_argv(device: str) -> list[str]:
    """上行：16k mono S16_LE raw 串流到 stdout。裝置為空時不帶 -D。"""
    argv = ["arecord"]
    if device:
        argv += ["-D", device]
    argv += ["-f", "S16_LE", "-r", str(UPLINK_RATE), "-c", "1", "-t", "raw", "-"]
    return argv


def build_aplay_argv(device: str) -> list[str]:
    """下行：從 stdin 讀 24k mono S16_LE raw 播放。裝置為空時不帶 -D。"""
    argv = ["aplay"]
    if device:
        argv += ["-D", device]
    argv += ["-f", "S16_LE", "-r", str(DOWNLINK_RATE), "-c", "1", "-t", "raw", "-"]
    return argv


class MicSource:
    """arecord 子行程包裝：持續讀取 raw PCM。"""

    def __init__(self, device: str):
        self._argv = build_arecord_argv(device)
        self._proc = subprocess.Popen(
            self._argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )

    def read(self, n: int) -> bytes:
        if self._proc.stdout is None:
            return b""
        return self._proc.stdout.read(n)

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
        self._proc = subprocess.Popen(
            build_aplay_argv(self._device),
            stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )

    def write(self, data: bytes) -> None:
        try:
            if self._proc and self._proc.stdin:
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


async def pump_downlink(ws, sink) -> None:
    """收下行：binary → 喇叭；JSON → 依 classify_live_event 分派。

    壞掉的 JSON 只跳過那一則，不中斷整場對話。
    """
    async for raw in ws:
        if isinstance(raw, (bytes, bytearray)):
            sink.write(bytes(raw))
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        action = classify_live_event(payload)
        if action == FLUSH:
            sink.flush_pending()
        elif action == SHOW:
            role = payload.get("role", "?")
            text = payload.get("text", "")
            print(f"  [{role}] {text}", flush=True)
        elif action == ABORT:
            _log.warning("live_error：%s", payload.get("reason"))
            return


async def pump_uplink(ws, mic, stop: asyncio.Event) -> None:
    """送上行：持續把麥克風 PCM 推給伺服器，直到 stop 被設起來。

    `mic.read` 是阻塞的子行程讀取，必須丟到執行緒——直接在 async 函式裡呼叫會
    凍結 event loop，下行就收不到、keepalive 也送不出去（local_client 踩過這個
    坑：閒置後連線被判定死亡而崩潰）。
    """
    while not stop.is_set():
        chunk = await asyncio.to_thread(mic.read, UPLINK_CHUNK_BYTES)
        if not chunk:
            return  # arecord 掛了，收手
        try:
            await ws.send(chunk)
        except Exception:
            return


async def run_session(ws, mic, sink, stop: asyncio.Event) -> None:
    """跑一場 live 對話：上行、下行並行，任一結束就收攤。"""
    up = asyncio.create_task(pump_uplink(ws, mic, stop))
    down = asyncio.create_task(pump_downlink(ws, sink))
    done, pending = await asyncio.wait(
        {up, down}, return_when=asyncio.FIRST_COMPLETED
    )
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
    local_client_ready()

    while True:
        print("按一下按鍵開始即時對話（再按一下結束）...", flush=True)
        await _wait_for_trigger()

        mic = None
        sink = None
        stop = asyncio.Event()
        try:
            async with websockets.connect(_ws_url()) as ws:
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


def local_client_ready() -> None:
    """沿用 local_client 的伺服器就緒探測，避免搶在 uvicorn 起來前連線。"""
    from edge.runtime.local_client import wait_for_server_ready
    wait_for_server_ready()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_loop())
