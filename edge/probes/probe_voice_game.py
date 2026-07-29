"""驗收語音開局（A）與卡關主動邀請（B）—— `server/game_intent.py`，commit c63ca57。

用法（開發機上跑，打裝置）：
    ./.venv/bin/python edge/probes/probe_voice_game.py

判讀：每一項自己印 PASS/FAIL，最後一行印總結。全 PASS 才算邏輯就緒。

⚠️ **這支探針送的是 `text_input`，繞過 ASR。** 全 PASS 只代表邏輯層就緒。

### ASR 那一段（2026-07-29 已用真人聲音驗過，結論很重要）

| 講的 | SenseVoice 聽成 | |
|---|---|---|
| 我要玩**火眼金睛** | 「我要玩**佛火眼鏡**」 | ✗ |
| 我要玩**小遊戲** | 「我要玩小遊戲。」 | ✓ |

**意圖詞「我要玩」兩次都完全正確，壞的只有四字成語遊戲名。** 成語用字冷僻，
對 ASR 難、對國小孩子講也難——所以主要觸發語改成「小遊戲」（`A0`），
遊戲名降為進階用法（`A1`）。**現場請講「我要玩小遊戲」。**

另一個實測數字：兩次的 `rms` 分別是 744 與 325，**差一倍多**。325 已經接近
`mic_check.py` 的 300 門檻——**收音距離與音量會決定成敗**，現場要靠近講。

重驗 ASR：

    ssh -t root@192.168.31.78 'cd /root/talkybuddy && \
      ./.venv/bin/python edge/runtime/mic_check.py 6 "我要玩小遊戲"'

若哪天又聽錯，修法是照**逐字稿實際聽到的字**加別名到 `server/game_intent.py`，
**不要憑想像加**——這是 `NATIVE_KWS_PLAN.md` 那個教訓的形狀：合成音自檢通過、
真人卻失敗，因為量測條件比真實情境仁慈。
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

    print("=== A0. 主要觸發語：「我要玩小遊戲」 ===")
    async with connect(tok) as ws:
        msg = await say(ws, "我要玩小遊戲")
        print(f"    回: {msg.get('text')}")
        check("「我要玩小遊戲」有開起來", http("/api/game", token=tok).get("game") == "i_spy")
        check("開場白報出其他遊戲名", all(
            n in (msg.get("text") or "") for n in ("猜猜我是誰", "點餐時間")))
    http("/api/game", {"game": "none"}, tok)

    print("=== A1. 進階：直接講遊戲名 ===")
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
    print("註：本探針走 text_input、繞過 ASR。ASR 那段已於 2026-07-29 用真人聲音驗過")
    print("   （「我要玩小遊戲」✓ 逐字正確；「我要玩火眼金睛」✗ 聽成「佛火眼鏡」）。")
    print("   換過麥克風或環境後請重跑：edge/runtime/mic_check.py")
    sys.exit(1 if failed else 0)


asyncio.run(main())
