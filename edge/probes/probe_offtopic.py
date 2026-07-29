"""HANDOFF 待確認第 2 項：雲端模式下同一句話回什麼？

若雲端回覆正常（跟得上「動物」這個請求），PR #7 只影響斷網後的 edge 橋段，
必要性下降；若雲端也被固定尾巴綁住，那就與 network_mode 無關，PR #7 的
lesson_target_sentence 是唯一的修法。

同一句話在兩個模式各問一次，直接對照。
"""
import asyncio, json, sys, urllib.request

HOST = "192.168.31.78:8787"
QUESTION = "可以教我一些動物用法嗎"


def http(path, data=None, token=None):
    req = urllib.request.Request(f"http://{HOST}{path}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("content-type", "application/json")
        req.data = json.dumps(data).encode()
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


async def ask(token, text):
    import websockets
    async with websockets.connect(f"ws://{HOST}/ws/talk?token={token}", open_timeout=15) as ws:
        await ws.send(json.dumps({"type": "text_input", "text": text}))
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
            except asyncio.TimeoutError:
                return None
            if isinstance(raw, bytes):
                continue
            msg = json.loads(raw)
            if msg.get("type") == "reply":
                return msg


async def main():
    tok = http("/api/login", {"email": "tutor@demo", "password": "demo1234"})["token"]

    for mode in ("cloud", "edge"):
        http("/api/network_mode", {"mode": mode}, tok)
        status = http("/api/status")
        print(f"=== network_mode = {status['network_mode']} ===")
        msg = await ask(tok, QUESTION)
        if msg is None:
            print("    <逾時無回覆>")
            continue
        print(f"    問: {QUESTION}")
        print(f"    回: {msg['text']}")
        print(f"    latency: {msg['latency_ms']}  fallback={msg['fallback']}")
        print()


asyncio.run(main())
