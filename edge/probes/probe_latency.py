"""PR #7 的 _ensure_lesson() 是否讓每一輪都變慢？

合併前 edge round_total=3460ms，合併後單輪量到 5031–7205ms。
關鍵區別：
  - 若只有「每條連線的第一輪」慢 → 是 _ensure_lesson 冷啟動，現場孩子用同一條
    連線講整場，只付一次，可接受
  - 若每一輪都慢 → 每輪多花約 2 秒，Phase 8 的 2.96s 數字就不再成立

所以在**同一條連線**上連送 4 輪，看第 2 輪之後有沒有回到基準。
"""
import asyncio, json, urllib.request

HOST = "192.168.31.78:8787"


def http(path, data=None, token=None):
    req = urllib.request.Request(f"http://{HOST}{path}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("content-type", "application/json")
        req.data = json.dumps(data).encode()
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


async def main():
    import websockets
    tok = http("/api/login", {"email": "tutor@demo", "password": "demo1234"})["token"]
    http("/api/network_mode", {"mode": "edge"}, tok)

    lines = ["蘋果", "香蕉", "麵包", "牛奶"]
    print("=== 同一條連線連續 4 輪（edge）===")
    async with websockets.connect(f"ws://{HOST}/ws/talk?token={tok}", open_timeout=15) as ws:
        for i, text in enumerate(lines, 1):
            await ws.send(json.dumps({"type": "text_input", "text": text}))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=90)
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                if msg.get("type") == "reply":
                    lat = msg["latency_ms"]
                    print(f"  第 {i} 輪 {text}: round_total={lat.get('round_total')}ms "
                          f"llm={lat.get('llm')}ms tts_first={lat.get('tts_first')}ms")
                    break


asyncio.run(main())
