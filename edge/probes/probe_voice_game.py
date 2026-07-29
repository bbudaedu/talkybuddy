"""驗收語音開局（A）與卡關主動邀請（B）—— `server/game_intent.py`，commit c63ca57。

用法（開發機上跑，打裝置）：
    ./.venv/bin/python edge/probes/probe_voice_game.py

判讀：每一項自己印 PASS/FAIL，最後一行印總結。全 PASS 才算邏輯就緒。

⚠️ **這支探針驗不到 ASR，而 ASR 是這個功能的第一風險。**
它送的是 `text_input`（文字直接進 pipeline），**完全繞過語音辨識**。
真正的問題是裝置的 SenseVoice 聽不聽得出「火眼金睛」這個成語——同音誤差很可能。

這正是 `NATIVE_KWS_PLAN.md` 那個教訓的形狀：用自家 TTS 合成音驗 KWS 通過了、
真人卻失敗，因為量測條件比真實情境仁慈。**text_input 全 PASS 不等於對著玩偶講得動。**

所以真機驗收要做兩段：

  1. 本探針（邏輯層）—— 確認開局／判定／邀請／誤觸防護都對
  2. **對著裝置真的講一次**，然後讀逐字稿確認 ASR 聽到什麼：
         ssh root@192.168.31.78 'cd /root/talkybuddy && \
           ./.venv/bin/python -m edge.runtime.dump_recent_turns'
     若 ASR 把「火眼金睛」聽成別的字，修法是在 `server/game_intent.py` 加別名，
     **但要照逐字稿實際聽到的字加，不要憑想像加。**
"""
import asyncio, json, sys, urllib.request

HOST = "192.168.31.78:8787"

_results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    print(f"    [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def http(path, data=None, token=None):
    req = urllib.request.Request(f"http://{HOST}{path}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("content-type", "application/json")
        req.data = json.dumps(data).encode()
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


async def say(ws, text: str) -> dict:
    """送一句話，回傳該輪的 reply 事件（逾時回空 dict）。"""
    await ws.send(json.dumps({"type": "text_input", "text": text}))
    while True:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=60)
        except asyncio.TimeoutError:
            return {}
        if isinstance(raw, bytes):
            continue
        msg = json.loads(raw)
        if msg.get("type") in ("reply", "busy"):
            return msg


def connect(token):
    import websockets
    return websockets.connect(f"ws://{HOST}/ws/talk?token={token}", open_timeout=15)


async def main():
    tok = http("/api/login", {"email": "tutor@demo", "password": "demo1234"})["token"]
    http("/api/game", {"game": "none"}, tok)  # 乾淨起點

    print("=== A1. 用講的開局 ===")
    async with connect(tok) as ws:
        msg = await say(ws, "我要玩火眼金睛")
        print(f"    回: {msg.get('text')}")
        st = http("/api/game", token=tok)
        check("「我要玩火眼金睛」有開起來", st.get("game") == "i_spy", f"game={st.get('game')}")
        check("開局不花 LLM 時間", msg.get("latency_ms", {}).get("llm") == 0,
              f"llm={msg.get('latency_ms', {}).get('llm')}ms")

        print("=== A2. 開局後下一句要走遊戲判定 ===")
        msg = await say(ws, "I see a dog.")
        print(f"    回: {msg.get('text')}")
        st = http("/api/game", token=tok)
        check("這局有前進", st.get("turns") == 1, f"turns={st.get('turns')} found={st.get('found')}")

        print("=== A3. 用講的結束 ===")
        msg = await say(ws, "不玩了")
        print(f"    回: {msg.get('text')}")
        check("這局已結束", http("/api/game", token=tok).get("game") is None)

    print("=== A4. 誤觸防護（「點餐時間到了」是在聊餐廳，不該開局）===")
    async with connect(tok) as ws:
        msg = await say(ws, "點餐時間到了")
        print(f"    回: {msg.get('text')}")
        check("沒有誤開局", http("/api/game", token=tok).get("game") is None)

    print("=== B1. 連續卡關 → 主動邀請 ===")
    async with connect(tok) as ws:
        invited = None
        for i in range(4):
            msg = await say(ws, "我不知道")
            print(f"    第{i + 1}輪回: {msg.get('text')}")
            if "要不要" in (msg.get("text") or ""):
                invited = msg
                break
        check("玩偶主動開口邀請", invited is not None)
        check("邀請講得出遊戲名", "火眼金睛" in ((invited or {}).get("text") or ""))

        if invited:
            print("=== B2. 答應邀請 → 開局 ===")
            msg = await say(ws, "好")
            print(f"    回: {msg.get('text')}")
            check("答應後有開局", http("/api/game", token=tok).get("game") == "i_spy")

    http("/api/game", {"game": "none"}, tok)

    failed = [n for n, ok, _ in _results if not ok]
    print()
    print(f"=== 總結：{len(_results) - len(failed)}/{len(_results)} PASS ===")
    if failed:
        print("FAIL：" + "、".join(failed))
    print()
    print("⚠️ 全 PASS 只代表邏輯層就緒。ASR 沒被驗到——還要對著裝置真的講一次，")
    print("   再用 dump_recent_turns 讀逐字稿確認「火眼金睛」有被聽對。")
    sys.exit(1 if failed else 0)


asyncio.run(main())
