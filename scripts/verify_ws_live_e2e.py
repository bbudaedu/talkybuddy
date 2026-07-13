# -*- coding: utf-8 -*-
"""隔離 smoke test：對「正在運行的 server」/ws/live 送合成音訊，驗證 server→Nova 橋接。

目的：把「Timed out waiting for audio bytes」的根因二分——
    - 若本腳本能拿到 live_transcript / audio / turn_end → server 橋接 + NovaSonicSession 正常，
      真機卡點純在瀏覽器端（AudioContext suspended / worklet 沒送音）。
    - 若本腳本也 timeout / live_error → 卡點在 server 橋接或 NovaSonicSession。

繞過瀏覽器：直接用 websockets 連 wss://HOST:8000/ws/live，把 WAV 轉 16k/mono/PCM16
後當「binary frame」上行（等同瀏覽器 worklet 應該送的東西），再送 {"type":"user_end"}。

用法：
    cd talkybuddy
    .venv/bin/python scripts/verify_ws_live_e2e.py [wss://127.0.0.1:8000/ws/live] [音檔.wav]
"""
import asyncio
import json
import ssl
import sys
import wave

try:
    import audioop
except ImportError:  # pragma: no cover
    audioop = None

import websockets

DEFAULT_URI = "wss://127.0.0.1:8000/ws/live"
DEFAULT_WAV = "docs/voice-reference/alice_v3_baseline.wav"
import os
CHUNK_SAMPLES = int(os.environ.get("CHUNK_SAMPLES", "1024"))  # 每 binary frame 樣本數
FRAME_SLEEP = float(os.environ.get("FRAME_SLEEP", "0.01"))    # 每 frame 間隔秒
TAIL_SILENCE = float(os.environ.get("TAIL_SILENCE", "0.8"))   # 尾端補靜音秒數（助 VAD 收尾）


def load_pcm16k(path: str) -> bytes:
    """讀 wav → 16kHz / mono / 16-bit little-endian PCM bytes（尾補 0.8s 靜音助 VAD）。"""
    if audioop is None:
        raise RuntimeError("此 Python 無 audioop；請用 3.12。")
    w = wave.open(path, "rb")
    ch, sw, fr = w.getnchannels(), w.getsampwidth(), w.getframerate()
    pcm = w.readframes(w.getnframes())
    w.close()
    if sw != 2:
        pcm = audioop.lin2lin(pcm, sw, 2)
    if ch == 2:
        pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
    if fr != 16000:
        pcm, _ = audioop.ratecv(pcm, 2, 1, fr, 16000, None)
    pcm += b"\x00\x00" * int(TAIL_SILENCE * 16000)
    return pcm


async def run(uri: str, wav: str) -> int:
    pcm = load_pcm16k(wav)
    print("=" * 64)
    print("WS /ws/live 隔離 smoke test（繞過瀏覽器）")
    print("=" * 64)
    print(f"uri = {uri}")
    print(f"wav = {wav} → 16k/mono/16bit，送 {len(pcm)/2/16000:.2f}s，共 {len(pcm)} bytes")
    print("-" * 64)

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    collected = {"transcripts": [], "audio_bytes": 0, "audio_frames": 0,
                 "turn_end": False, "errors": [], "other": []}

    try:
        async with websockets.connect(uri, ssl=ssl_ctx, max_size=None,
                                      open_timeout=15) as ws:
            print("[ws] 已連線，立刻上行音訊（避免 55s Nova 逾時）…", flush=True)

            async def send_audio():
                step = CHUNK_SAMPLES * 2
                n = 0
                for i in range(0, len(pcm), step):
                    await ws.send(pcm[i:i + step])  # binary frame = raw PCM16
                    n += 1
                    await asyncio.sleep(FRAME_SLEEP)  # 輕度節流，貼近即時
                await ws.send(json.dumps({"type": "user_end"}))
                print(f"[ws] 上行完成：{n} 個 binary frame + user_end 已送。", flush=True)

            async def recv_loop():
                while True:
                    msg = await ws.recv()
                    if isinstance(msg, (bytes, bytearray)):
                        collected["audio_bytes"] += len(msg)
                        collected["audio_frames"] += 1
                        continue
                    try:
                        data = json.loads(msg)
                    except Exception:
                        collected["other"].append(str(msg)[:120])
                        continue
                    t = data.get("type")
                    if t == "live_transcript":
                        role = data.get("role", "?")
                        text = data.get("text", "")
                        collected["transcripts"].append((role, text))
                        print(f"[recv] transcript {role}: {text!r}", flush=True)
                    elif t == "turn_end":
                        collected["turn_end"] = True
                        print("[recv] turn_end", flush=True)
                        return
                    elif t == "live_error":
                        collected["errors"].append(data.get("reason", "?"))
                        print(f"[recv] live_error: {data.get('reason')}", flush=True)
                        return
                    else:
                        collected["other"].append(str(data)[:120])

            send_task = asyncio.create_task(send_audio())
            try:
                await asyncio.wait_for(recv_loop(), timeout=90)
            except asyncio.TimeoutError:
                collected["errors"].append("recv 逾時（90s 未見 turn_end/live_error）")
            send_task.cancel()
    except Exception as e:  # noqa: BLE001
        collected["errors"].append(f"連線/傳輸例外：{type(e).__name__}: {e}")

    print("\n--- 結果 ---")
    print(f"transcript 段數 = {len(collected['transcripts'])}")
    for role, text in collected["transcripts"]:
        print(f"    {role}: {text!r}")
    print(f"audio frames = {collected['audio_frames']}，"
          f"audio bytes = {collected['audio_bytes']} "
          f"(~{collected['audio_bytes']/2/24000:.2f}s @24kHz)")
    print(f"turn_end = {collected['turn_end']}")
    if collected["other"]:
        print(f"其他訊息 = {collected['other'][:5]}")
    if collected["errors"]:
        print("錯誤：")
        for e in collected["errors"]:
            print(f"    ⚠️ {e}")

    asst = [tx for role, tx in collected["transcripts"] if role == "ASSISTANT"]
    ok = bool(asst) or collected["audio_bytes"] > 0
    print("\n" + "=" * 64)
    print(f"server→Nova live 可調用 = {'✅ 是' if ok else '❌ 否'}")
    print("=" * 64)
    return 0 if ok else 1


if __name__ == "__main__":
    uri = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URI
    wav = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_WAV
    sys.exit(asyncio.run(run(uri, wav)))
