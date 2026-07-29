"""PR #7 合併後的產品規則合規率檢查（edge/PROMPT_ORDERING_FINDING.md 要求的驗收）。

那份文件的教訓：prompt/scaffold 一改動，就必須逐條檢查三條核心產品規則，
因為快取面的改善可能悄悄破壞行為面（該次是稱讚整句消失，5/5 掉到 0/5）。

三條規則：
  一、回覆先有一句繁體中文稱讚鼓勵
  二、用「跟我說一遍：<英文句>」或「跟我唸：<英文句>」帶讀
  三、帶讀的英文句是完整句（非截斷）

自動判定是啟發式，原文全部印出供人工覆核——這是那份文件的做法。
"""
import asyncio, json, re, sys, urllib.request

HOST = "192.168.31.78:8787"

INPUTS = [
    "可以教我一些動物用法嗎",   # 本案主角：離題中文提問
    "蘋果",                     # 命中詞庫
    "I like apples",            # 純英文
    "我不會",                   # 卡關
    "今天天氣很好",             # 完全離題
]

_LEAD = re.compile(r"(跟我說一遍|跟我唸)\s*[:：]\s*(.+?)\s*$", re.S)
_CJK = re.compile(r"[一-鿿]")


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
                raw = await asyncio.wait_for(ws.recv(), timeout=90)
            except asyncio.TimeoutError:
                return None
            if isinstance(raw, bytes):
                continue
            msg = json.loads(raw)
            if msg.get("type") == "reply":
                return msg


def judge(reply: str):
    """回傳 (規則一, 規則二, 規則三) 的布林判定。"""
    m = _LEAD.search(reply)
    rule2 = m is not None
    head = reply[:m.start()] if m else reply
    # 規則一：帶讀之前要有中文稱讚（至少 4 個中文字）
    rule1 = len(_CJK.findall(head)) >= 4
    # 規則三：帶讀的英文句要像完整句（有字母、結尾有標點或夠長）
    rule3 = False
    if m:
        en = m.group(2).strip()
        rule3 = bool(re.search(r"[A-Za-z]", en)) and (en.endswith((".", "!", "?")) or len(en.split()) >= 3)
    return rule1, rule2, rule3


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "edge"
    tok = http("/api/login", {"email": "tutor@demo", "password": "demo1234"})["token"]
    http("/api/network_mode", {"mode": mode}, tok)
    print(f"### network_mode = {http('/api/status')['network_mode']}\n")

    tally = [0, 0, 0]
    for text in INPUTS:
        msg = await ask(tok, text)
        if msg is None:
            print(f"輸入：{text}\n  <逾時無回覆>\n")
            continue
        reply = msg["text"]
        r1, r2, r3 = judge(reply)
        for i, ok in enumerate((r1, r2, r3)):
            tally[i] += 1 if ok else 0
        flat = reply.replace("\n", " ⏎ ")
        print(f"輸入：{text}")
        print(f"  回覆：{flat}")
        print(f"  規則一中文稱讚={'✅' if r1 else '❌'}  "
              f"規則二帶讀格式={'✅' if r2 else '❌'}  "
              f"規則三目標句完整={'✅' if r3 else '❌'}   "
              f"[round={msg['latency_ms'].get('round_total')}ms]")
        print()

    n = len(INPUTS)
    print(f"合規率：規則一 {tally[0]}/{n}　規則二 {tally[1]}/{n}　規則三 {tally[2]}/{n}")


asyncio.run(main())
