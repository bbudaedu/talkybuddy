# -*- coding: utf-8 -*-
"""local_client.py — 裝置端離線對話 WebSocket client（沿用既有 /ws/talk 協定）。

獨立行程：uvicorn（server.app:app）就緒後啟動，迴圈「等觸發 → 錄音 → 送
/ws/talk → 收 tts_audio → 播放」。這不是新協定，是既有 /ws/talk wire
protocol 換一個講話的人（08-PATTERNS.md Pattern 1）——server/app.py 的
routing/auth/WS 狀態機完全不動。

啟動：``python3 -m edge.runtime.local_client``（或直接執行本檔）。連線目標
固定 loopback，host/port 可經環境變數覆寫（TALKYBUDDY_EDGE_WS_HOST/PORT）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
import urllib.error
import urllib.request

import websockets

from edge.runtime import audio_io

_log = logging.getLogger(__name__)

# 連線目標：僅 loopback，host/port 可經環境變數覆寫（測試/多網卡環境）。
WS_HOST: str = os.environ.get("TALKYBUDDY_EDGE_WS_HOST", "127.0.0.1")
WS_PORT: int = int(os.environ.get("TALKYBUDDY_EDGE_WS_PORT", "8787"))

# /ws/talk 需要合法 token；沿用 server/auth.py 既有 device seed 帳號
# （見 _SEED = [..., ("device:GENIO-520-X992", "demo1234", ...)]）。可用環境
# 變數覆寫，避免帳密硬編在程式碼裡。
_DEVICE_EMAIL: str = os.environ.get("TALKYBUDDY_EDGE_DEVICE_EMAIL", "device:GENIO-520-X992")
_DEVICE_PASSWORD: str = os.environ.get("TALKYBUDDY_EDGE_DEVICE_PASSWORD", "demo1234")

_HEALTH_TIMEOUT_S = 2.0
_HEALTH_RETRIES = 30
_LOGIN_TIMEOUT_S = 5.0
# 斷線重連的退避。取小值：玩偶待機時斷線要盡快接回來，否則使用者按下去
# 那一刻若還沒重連，那一輪就白按了。
_RECONNECT_DELAY_S = 2.0


def _http_base_url() -> str:
    return f"http://{WS_HOST}:{WS_PORT}"


def _ws_url(token: str) -> str:
    return f"ws://{WS_HOST}:{WS_PORT}/ws/talk?token={token}"


def wait_for_server_ready() -> None:
    """輪詢 /api/status 直到 uvicorn 就緒，避免 local_client 搶在 server 起來前連線。

    server/app.py 目前沒有專用 /health 路由；/api/status 不需要 auth、啟動即
    可回應，拿來當就緒探測點（比照 08-PATTERNS.md 的 curl-loop idiom）。
    """
    url = f"{_http_base_url()}/api/status"
    for _ in range(_HEALTH_RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=_HEALTH_TIMEOUT_S) as resp:
                if resp.status == 200:
                    return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError(f"server 逾時未就緒：{url}")


def fetch_token() -> str:
    """以裝置 seed 帳號登入取得 /ws/talk 合法 token（見 server/auth.py _SEED）。"""
    body = json.dumps({"email": _DEVICE_EMAIL, "password": _DEVICE_PASSWORD}).encode("utf-8")
    req = urllib.request.Request(
        f"{_http_base_url()}/api/login",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_LOGIN_TIMEOUT_S) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload["token"]


async def _handle_turn(ws) -> None:
    """送出一輪錄音，接收訊息直到本輪結束（tts_audio/tts_unavailable）。

    wire protocol 見 server/app.py::ws_talk：binary WAV frame → {"type":
    "audio_end"} → 依序收 asr_result/reply/tts_audio(或 tts_unavailable)。
    tts_audio 的 payload key 為 wav_b64（server/app.py line ~404-406）。
    """
    wav = audio_io.capture_16k_mono_wav()
    await ws.send(wav)
    await ws.send(json.dumps({"type": "audio_end"}))

    async for raw in ws:
        try:
            event = json.loads(raw)
        except Exception:
            continue
        etype = event.get("type")
        if etype == "tts_audio":
            try:
                audio_io.play_wav_bytes(base64.b64decode(event.get("wav_b64", "")))
            except Exception:
                _log.exception("local_client 播放 tts_audio 失敗")
            break
        if etype == "tts_unavailable":
            break
        # asr_result / reply / state / busy：本輪中間事件，繼續等待回合結束訊號


async def run_loop() -> None:
    """裝置端離線對話主迴圈：等觸發 → 錄音 → 送 /ws/talk → 播放回覆。

    **玩偶必須能長時間待機**：放在桌上等人來按，閒置多久都不能死。
    2026-07-30 真機上它會在閒置後以 `BrokenPipeError` →
    `ConnectionClosedError: no close frame received or sent` 崩潰退出，
    使用者回來按鍵完全沒反應——從外面看跟按鍵故障一模一樣。兩層根因：

    1. `wait_for_trigger()` 是同步阻塞（等人按實體鍵，可能好幾分鐘），
       直接在 async 函式裡呼叫會**凍結整個 event loop**；`websockets` 的
       keepalive ping 因此送不出去，連線被判定死亡而關閉。閒置越久越必死。
       → 丟到執行緒（`asyncio.to_thread`），讓 event loop 繼續轉。
    2. 斷線後直接 `raise`，行程就結束了。→ 外層重連，不退出。

    單輪對話失敗（聽不清楚、TTS 出錯等）只記 log 就繼續等下一次觸發——
    一輪講壞了不該讓玩偶罷工。
    """
    wait_for_server_ready()
    token = fetch_token()

    while True:
        try:
            async with websockets.connect(_ws_url(token)) as ws:
                while True:
                    # 見上方 docstring 第 1 點：絕不可改回直接呼叫。
                    await asyncio.to_thread(audio_io.wait_for_trigger)
                    try:
                        await _handle_turn(ws)
                    except websockets.ConnectionClosed:
                        raise  # 交給外層重連
                    except Exception:
                        _log.exception("local_client 本輪對話失敗，繼續等待下一次觸發")
        except (websockets.ConnectionClosed, OSError) as exc:
            # OSError 涵蓋 server 還沒起來／重啟中的 ConnectionRefusedError。
            _log.warning(
                "連線中斷（%s），%.1f 秒後重連", type(exc).__name__, _RECONNECT_DELAY_S
            )
            await asyncio.sleep(_RECONNECT_DELAY_S)
            try:
                # token 可能已過期；取不到就沿用舊的再試，不讓行程死掉。
                token = await asyncio.to_thread(fetch_token)
            except Exception:
                _log.exception("重新取得 token 失敗，沿用舊 token 重試")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_loop())
