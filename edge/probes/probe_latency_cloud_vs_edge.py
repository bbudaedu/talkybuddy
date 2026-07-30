"""雲端 TTS 修好之後，cloud 模式的 round_total 差多少？

## 為什麼要分兩種模式量

`round_total` 由 llm + tts_first 主宰，而**只有 cloud 模式會碰雲端 TTS**。
2026-07-30 把 ELEVENLABS_MODEL 從 eleven_v3 換成 eleven_turbo_v2_5 之前，
cloud 模式的 TTS 階段實際上是：

    先花滿 CLOUD_TTS_TIMEOUT_S=1.5s 等 eleven_v3（它要約 3s，必逾時）
    → 靜默降級 → 再跑一次邊緣 sherpa-onnx 合成

也就是**白付 1.5 秒**，而且 /api/status 一路顯示 cloud_tts=true。換成 turbo
之後裝置實測 0.67s，這 1.5s 應該消失。edge 模式不受影響，拿來當對照組——
若兩邊都變快，那就不是 TTS 改動的功勞，是別的變因（例如剛重開機）。

## 量法

沿用 edge/probes/probe_latency.py 的形狀（同一條連線連送多輪、走 text_input
跳過 ASR），數字才與歷史紀錄可比。**第 1 輪一律丟棄**：KV cache 全空的冷啟動
會落在 4.5–5s，和穩態混算會得到沒有意義的平均（見 edge/BOOT_SOP.md）。

用法（在開發機，對著裝置跑）：

    .venv/bin/python -m edge.probes.probe_latency_cloud_vs_edge
"""
import asyncio
import json
import statistics
import urllib.request

HOST = "192.168.31.78:8787"
TURNS = ["蘋果", "香蕉", "麵包", "牛奶", "雞蛋"]


def http(path, data=None, token=None):
    req = urllib.request.Request(f"http://{HOST}{path}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("content-type", "application/json")
        req.data = json.dumps(data).encode()
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


async def run_mode(mode: str, token: str) -> list[dict]:
    import websockets

    http("/api/network_mode", {"mode": mode}, token)
    rows = []
    async with websockets.connect(
        f"ws://{HOST}/ws/talk?token={token}", open_timeout=15, max_size=None
    ) as ws:
        for i, text in enumerate(TURNS, 1):
            await ws.send(json.dumps({"type": "text_input", "text": text}))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=120)
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                if msg.get("type") == "reply":
                    lat = msg["latency_ms"]
                    rows.append({"n": i, **lat})
                    print(f"    第 {i} 輪 {text}: round_total="
                          f"{lat.get('round_total')}ms  llm={lat.get('llm')}ms  "
                          f"tts_first={lat.get('tts_first')}ms")
                    break
    return rows


def summarise(label: str, rows: list[dict]) -> None:
    warm = [r for r in rows if r["n"] > 1]  # 第 1 輪是冷啟動，不併入
    if not warm:
        print(f"  {label}: 沒有暖機後的資料")
        return
    for key in ("round_total", "llm", "tts_first"):
        vals = [r[key] for r in warm if r.get(key) is not None]
        if vals:
            print(f"  {label} {key:11s} 中位數 {int(statistics.median(vals)):5d}ms  "
                  f"（{min(vals)}–{max(vals)}ms，n={len(vals)}）")


async def main():
    tok = http("/api/login", {"email": "tutor@demo", "password": "demo1234"})["token"]
    results = {}
    try:
        for mode in ("edge", "cloud"):
            print(f"=== {mode} 模式（同一條連線 {len(TURNS)} 輪）===")
            results[mode] = await run_mode(mode, tok)
            print()
    finally:
        # 裝置預設是 edge（見 edge/BOOT_SOP.md），量完還原，別留下非預設狀態
        http("/api/network_mode", {"mode": "edge"}, tok)

    print("=== 暖機後彙總（已排除第 1 輪冷啟動）===")
    for mode, rows in results.items():
        summarise(mode, rows)

    status = http("/api/status")
    print()
    print(f"cloud_tts={status['cloud_tts']}  {status.get('cloud_tts_detail', '')}")
    print(f"cloud_provider={status['cloud_provider']}")


if __name__ == "__main__":
    asyncio.run(main())
