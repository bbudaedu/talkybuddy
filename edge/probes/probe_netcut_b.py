"""型態 B 的真實版：回合進行中讓雲端「真的斷掉」，而不是切 network_mode。

前一輪實測發現的方法論問題：`airplaneSwitch`（切 network_mode）只決定
**下一回合要不要嘗試雲端**，不會取消已發出的請求（D-03 鎖定不做 asyncio
取消）。所以當雲端健康（1.7–2.3s 就回應）時，回合中途切換的那一回合仍會
跑完雲端、完全不降級——M1 是負的，測不到 NETCUT-03 要的降級行為。

真正對應「現場拔網路線」的做法是讓雲端**無回應**：回合進行中切斷反向隧道，
雲端請求就會卡到 CLOUD_LLM_TIMEOUT_S=4 到期才降級 edge。

預期：該回合 llm_ms ≈ 4s（雲端逾時）+ edge 生成時間，
      M1 = 該回合 llm_ms − 純 edge llm_ms ≈ 4s（本次逾時採用值）。
"""
import asyncio, json, subprocess, time, urllib.request

HOST = "192.168.31.78:8787"
TUNNEL_PAT = "8317:192.168.100.200:8317"


def http(path, data=None, token=None):
    req = urllib.request.Request(f"http://{HOST}{path}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("content-type", "application/json")
        req.data = json.dumps(data).encode()
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


async def turn(ws, text):
    await ws.send(json.dumps({"type": "text_input", "text": text}))
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=120)
        if isinstance(raw, bytes):
            continue
        msg = json.loads(raw)
        if msg.get("type") == "reply":
            return msg


async def main():
    import websockets
    tok = http("/api/login", {"email": "tutor@demo", "password": "demo1234"})["token"]
    http("/api/network_mode", {"mode": "cloud"}, tok)

    async with websockets.connect(f"ws://{HOST}/ws/talk?token={tok}", open_timeout=15) as ws:
        print("=== 暖場 + 基準（cloud 正常）===")
        m = await turn(ws, "蘋果")
        print(f"    暖場 llm={m['latency_ms']['llm']}ms")
        base = await turn(ws, "香蕉")
        print(f"    cloud 正常回合 llm={base['latency_ms']['llm']}ms "
              f"round={base['latency_ms']['round_total']}ms\n")

        print("=== 型態 B（真實版）：回合進行中切斷雲端路徑 ===")
        t0 = time.monotonic()
        task = asyncio.create_task(turn(ws, "麵包"))
        await asyncio.sleep(0.4)
        subprocess.run(["pkill", "-f", TUNNEL_PAT], check=False)
        t_cut = (time.monotonic() - t0) * 1000
        c = await task
        print(f"    於回合開始後 {t_cut:.0f}ms 切斷隧道")
        print(f"    該回合 llm={c['latency_ms']['llm']}ms "
              f"round={c['latency_ms']['round_total']}ms")
        print(f"    回覆：{c['text'][:60]}")

        print("\n=== 切斷後的下一回合（應為純 edge）===")
        d = await turn(ws, "牛奶")
        print(f"    llm={d['latency_ms']['llm']}ms round={d['latency_ms']['round_total']}ms")
        print(f"\n    M1（型態 B）= 該回合 {c['latency_ms']['llm']}ms "
              f"− 純 edge {d['latency_ms']['llm']}ms = {c['latency_ms']['llm'] - d['latency_ms']['llm']}ms")


asyncio.run(main())
