"""斷網彩排（API 路徑）：型態 A × 2 + 型態 B × 1。

⚠️ 這條路徑用 text_input + /api/network_mode，**跳過 USB 麥克風與瀏覽器**，
不等於 NETCUT_REHEARSAL_CHECKLIST 要求的完整演練（M2 牆鐘時間仍須人在現場
用碼錶記）。這裡量的是降級機制本身：切到 edge 之後該回合的 llm_ms 是否
真的不再等雲端。

型態 A：回合**之間**切換 → 預期 M1 ≈ 0（下一回合從頭就不試雲端）
型態 B：回合**進行中**切換 → 預期 M1 落在 0–4s（須等雲端內層逾時到期，
        本次逾時設 4s，故上界是 4s 而非文件原本的 3.0s）
"""
import asyncio, json, time, urllib.request

HOST = "192.168.31.78:8787"
WARM = "蘋果"
LINES = ["香蕉", "麵包", "牛奶", "雞蛋", "橘子", "西瓜"]


def http(path, data=None, token=None):
    req = urllib.request.Request(f"http://{HOST}{path}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("content-type", "application/json")
        req.data = json.dumps(data).encode()
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


async def turn(ws, text):
    await ws.send(json.dumps({"type": "text_input", "text": text}))
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=90)
        if isinstance(raw, bytes):
            continue
        msg = json.loads(raw)
        if msg.get("type") == "reply":
            return msg


async def main():
    import websockets
    tok = http("/api/login", {"email": "tutor@demo", "password": "demo1234"})["token"]
    it = iter(LINES)

    def switch(mode):
        http("/api/network_mode", {"mode": mode}, tok)
        return time.monotonic()

    switch("cloud")
    async with websockets.connect(f"ws://{HOST}/ws/talk?token={tok}", open_timeout=15) as ws:
        print("=== 步驟 1：暖場（焐熱 KV cache，冷啟動數字不得與穩態混算）===")
        m = await turn(ws, WARM)
        print(f"    暖場 llm={m['latency_ms']['llm']}ms round={m['latency_ms']['round_total']}ms\n")

        # ---------- 型態 A × 2 ----------
        for run in (1, 2):
            switch("cloud")
            a = await turn(ws, next(it))
            t_sw = switch("edge")          # 回合「之間」切換
            b = await turn(ws, next(it))
            m1 = b["latency_ms"]["llm"] - a["latency_ms"]["llm"]
            print(f"=== 型態 A 第 {run} 次（回合間切換）===")
            print(f"    cloud 回合 llm={a['latency_ms']['llm']}ms round={a['latency_ms']['round_total']}ms")
            print(f"    切換後 edge 回合 llm={b['latency_ms']['llm']}ms round={b['latency_ms']['round_total']}ms")
            print(f"    M1（edge−cloud 的 llm 差額）= {m1}ms\n")

        # ---------- 型態 B × 1 ----------
        switch("cloud")
        print("=== 型態 B（回合進行中切換）===")
        text = next(it)
        t0 = time.monotonic()
        task = asyncio.create_task(turn(ws, text))
        await asyncio.sleep(0.6)            # 讓回合先進到雲端請求階段
        switch("edge")
        t_sw = time.monotonic() - t0
        c = await task
        print(f"    切換於回合開始後 {t_sw*1000:.0f}ms")
        print(f"    該回合 llm={c['latency_ms']['llm']}ms round={c['latency_ms']['round_total']}ms")
        print("    ⚠️ 判讀陷阱：該回合寫入 DB 的 network_mode 仍是切換前的 cloud，")
        print("       要看 llm_ms 是否明顯短於雲端正常值來判斷是否降級。")


asyncio.run(main())
