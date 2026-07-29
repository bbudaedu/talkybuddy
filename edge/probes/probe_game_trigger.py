"""決定性測試：tutor 用 /api/game 開局後，學生在 /ws/talk 講話會不會走遊戲判定。

假設：/api/game 動的是全域 pipeline 單例，/ws/talk 每連線新建 conn_pipe 且未承接
game 狀態 → 遊戲判定不會觸發，回合走自由對話。

判讀：
  遊戲有觸發 → 回覆會是 games.py 的判定語（如「找到了！」「還差 N 個」）
  沒觸發     → 回覆是自由對話（很可能以「跟我說一遍：How are you today?」收尾）
"""
import asyncio, json, sys, urllib.request

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


async def talk(token, lines):
    import websockets
    url = f"ws://{HOST}/ws/talk?token={token}"
    out = []
    async with websockets.connect(url, open_timeout=15) as ws:
        for line in lines:
            await ws.send(json.dumps({"type": "text_input", "text": line}))
            reply = None
            # 收到 reply 就停；其餘 state/tts 事件略過
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=45)
                except asyncio.TimeoutError:
                    break
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                if msg.get("type") == "reply":
                    reply = msg.get("text", "")
                    break
                if msg.get("type") == "busy":
                    reply = "<busy>"
                    break
            out.append((line, reply))
    return out


async def main():
    tok = http("/api/login", {"email": "tutor@demo", "password": "demo1234"})["token"]

    print("=== 1. 開局 i_spy/animal ===")
    st = http("/api/game", {"game": "i_spy", "topic": "animal"}, tok)
    print(f"    prompt_zh: {st['prompt_zh']}")
    print(f"    hints: {st['hints']}")

    print("=== 2. 確認全域 pipeline 確實有這局 ===")
    print(f"    GET /api/game -> game={http('/api/game', token=tok)['game']}")

    print("=== 3. 學生在 /ws/talk 講遊戲句 ===")
    for line, reply in await talk(tok, ["I see a dog.", "I see a cat."]):
        print(f"    說: {line}")
        print(f"    回: {reply}")

    print("=== 4. 講完後全域這局的進度 ===")
    after = http("/api/game", token=tok)
    print(f"    game={after['game']} turns={after.get('turns')} found={after.get('found')}")

    http("/api/game", {"game": "none"}, tok)
    print("=== 已結束該局 ===")


asyncio.run(main())
