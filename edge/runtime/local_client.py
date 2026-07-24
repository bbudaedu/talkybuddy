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
    """裝置端離線對話主迴圈：等觸發 → 錄音 → 送 /ws/talk → 播放回覆。"""
    wait_for_server_ready()
    token = fetch_token()

    async with websockets.connect(_ws_url(token)) as ws:
        while True:
            audio_io.wait_for_trigger()
            try:
                await _handle_turn(ws)
            except websockets.ConnectionClosed:
                raise
            except Exception:
                _log.exception("local_client 本輪對話失敗，繼續等待下一次觸發")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_loop())
