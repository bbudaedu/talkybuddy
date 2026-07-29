"""補驗另外兩個遊戲在 /ws/talk 上的觸發（i_spy 已由 probe_game_trigger.py 驗過）。

guess_who 的離線能力邊界是刻意的：屬性表答不出來的問題回 unknown，不瞎猜。
這裡順帶看它在真機上是不是真的這樣回。
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
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


async def play(token, lines):
    import websockets
    out = []
    async with websockets.connect(f"ws://{HOST}/ws/talk?token={token}", open_timeout=15) as ws:
        for line in lines:
            await ws.send(json.dumps({"type": "text_input", "text": line}))
            reply = None
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=45)
                except asyncio.TimeoutError:
                    break
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                if msg.get("type") == "reply":
                    reply = (msg.get("text", ""), msg.get("latency_ms", {}).get("llm"))
                    break
            out.append((line, reply))
    return out


async def main():
    tok = http("/api/login", {"email": "tutor@demo", "password": "demo1234"})["token"]

    for kind, lines in [
        ("guess_who", ["Is it an animal?", "Does it start with D?", "Is it a dog?"]),
        ("restaurant", ["I want an apple.", "I want some bread."]),
    ]:
        print(f"=== {kind} ===")
        st = http("/api/game", {"game": kind}, tok)
        print(f"    開場：{st['prompt_zh']}")
        for line, reply in await play(tok, lines):
            text, llm_ms = reply if reply else ("<無回覆>", None)
            print(f"    說: {line}")
            print(f"    回: {text}   [llm={llm_ms}ms]")
        after = http("/api/game", token=tok)
        print(f"    進度：game={after['game']} turns={after.get('turns')} found={after.get('found')}")
        http("/api/game", {"game": "none"}, tok)
        print()


asyncio.run(main())
