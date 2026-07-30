# -*- coding: utf-8 -*-
"""verify_cloud_tts_live.py — 用真的 ElevenLabs 金鑰驗證 cloud_tts 這條路。

## 為什麼需要這支（而不是直接呼叫 CloudTTS.synth）

`server/cloud_tts.py::CloudTTS.synth()` 對**所有**失敗一律回 `None` 不 raise
（這在正式路徑是對的：TTS 掛掉不該讓整場對話中斷，降級回本機 Piper 就好）。
但拿它當診斷工具就變成災難——金鑰打錯、額度用完、voice_id 不存在、網路不通，
症狀全都是 `None`，完全無法定位。

所以這支**刻意把 HTTP 錯誤全部攤開**：狀態碼、錯誤 body、耗時都印出來。
（呼應 2026-07-30 的教訓：不要把診斷資訊丟進 DEVNULL。）

驗完之後才跑一次真正的正式路徑，確認 `available()` / `synth()` 也同意。

## 用法

金鑰放在 repo 根目錄的 `.env`（已被 .gitignore 涵蓋，不會進版控）：

    ELEVENLABS_API_KEY=sk_...

然後：

    set -a; . ./.env; set +a
    .venv/bin/python -m scripts.verify_cloud_tts_live

會在 /tmp 產出 WAV 讓你實際聽，並印出 `/api/status` 的 cloud_tts 會是什麼值。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

# demo 時真的會講的句子：中英混講的伴讀口吻，能同時聽出中文自然度與英語發音。
SAMPLE_TEXT = "好，我們一起唸一次喔。The cat is sleeping on the sofa. 你跟著我唸～"

# 步驟 2 刻意**不用** config.CLOUD_TTS_TIMEOUT_S（預設 1.5s）。
# eleven_v3 合成這種長度的句子往往要 1–3 秒，用正式逾時直打的話，「金鑰壞了」
# 與「逾時設太緊」會是同一個症狀，等於自己製造了一個分不開的變因。
# 這裡先用寬鬆逾時確認金鑰本身沒問題，再拿實測耗時去比對正式逾時夠不夠。
_DIAGNOSTIC_TIMEOUT_S = 30.0


def _mask(secret: str) -> str:
    """只露頭尾，讓人能認出是哪把金鑰，但不會把它印進 log 或截圖裡。"""
    if not secret:
        return "(空)"
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}…{secret[-4:]}（長度 {len(secret)}）"


def main() -> int:
    from server import config

    print("=== 1. 設定檢查 ===")
    print(f"  ELEVENLABS_API_KEY   : {_mask(config.ELEVENLABS_API_KEY)}")
    print(f"  ELEVENLABS_VOICE_ID  : {config.ELEVENLABS_VOICE_ID}")
    print(f"  ELEVENLABS_MODEL     : {config.ELEVENLABS_MODEL}")
    print(f"  CLOUD_TTS_SPEED      : {config.CLOUD_TTS_SPEED}")
    print(f"  CLOUD_TTS_TIMEOUT_S  : {config.CLOUD_TTS_TIMEOUT_S}")

    if not config.ELEVENLABS_API_KEY:
        print()
        print("✗ 沒讀到金鑰。config.py 只讀 os.environ，.env 不會自己生效：")
        print("    set -a; . ./.env; set +a")
        print("  然後重跑這支。")
        return 2

    print()
    print("=== 2. 直接打 API（錯誤全部攤開）===")
    url = (f"https://api.elevenlabs.io/v1/text-to-speech/"
           f"{config.ELEVENLABS_VOICE_ID}?output_format=pcm_22050")
    body = json.dumps({
        "text": SAMPLE_TEXT,
        "model_id": config.ELEVENLABS_MODEL,
        "voice_settings": {
            "stability": config.ELEVENLABS_STABILITY,
            "similarity_boost": config.ELEVENLABS_SIMILARITY_BOOST,
            "style": config.ELEVENLABS_STYLE,
            "use_speaker_boost": config.ELEVENLABS_USE_SPEAKER_BOOST,
        },
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "xi-api-key": config.ELEVENLABS_API_KEY},
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=_DIAGNOSTIC_TIMEOUT_S) as resp:
            raw = resp.read()
            ct = resp.headers.get("Content-Type", "")
        dt = time.monotonic() - t0
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:800]
        print(f"✗ HTTP {exc.code} {exc.reason}（{time.monotonic() - t0:.2f}s）")
        print(f"  body: {detail}")
        print()
        print("  401/403 → 金鑰錯或被停用；422 → voice_id/model 名稱不對；")
        print("  429     → 額度或速率上限。")
        return 1
    except Exception as exc:
        print(f"✗ {type(exc).__name__}: {exc}（{time.monotonic() - t0:.2f}s）")
        return 1

    # pcm_22050 是 raw PCM16，沒有 header：bytes ÷ 2 ÷ 22050 就是秒數
    secs = len(raw) / 2 / 22050
    print(f"✓ HTTP 200 · {len(raw)} bytes · 約 {secs:.1f} 秒語音 · "
          f"{dt:.2f}s · Content-Type={ct or '(空)'}")

    # 金鑰沒問題不代表正式路徑跑得完——逾時是獨立的一關，要分開講。
    if dt > config.CLOUD_TTS_TIMEOUT_S:
        print()
        print(f"⚠️ 但這次花了 {dt:.2f}s，超過 CLOUD_TTS_TIMEOUT_S="
              f"{config.CLOUD_TTS_TIMEOUT_S}s。")
        print("  → 正式路徑會逾時、靜默降級回本機 Piper，聽起來就像雲端 TTS 沒開，")
        print("    而 /api/status 仍然顯示 cloud_tts=true（available() 只看金鑰在不在）。")
        print("  → 下面步驟 3 的 synth() 若回 None，原因是這個，不是金鑰。")
        print(f"  → 調法：export CLOUD_TTS_TIMEOUT_S={max(4, int(dt) + 2)}")

    print()
    print("=== 3. 正式路徑（server/cloud_tts.py）===")
    from server.cloud_tts import CloudTTS

    tts = CloudTTS()
    print(f"  available()          : {tts.available()}")
    t0 = time.monotonic()
    wav = tts.synth([("zh", SAMPLE_TEXT)])
    dt = time.monotonic() - t0
    if not wav:
        print(f"✗ synth() 回 None（{dt:.2f}s）——但上面直打 API 是成功的，所以不是金鑰。")
        if dt >= config.CLOUD_TTS_TIMEOUT_S * 0.9:
            print(f"  耗時貼近 CLOUD_TTS_TIMEOUT_S={config.CLOUD_TTS_TIMEOUT_S}s，"
                  "幾乎確定是逾時。見上面的調法。")
        else:
            print("  且沒有貼近逾時 → 問題在 cloud_tts.py 內部"
                  "（WSOLA 放慢、Content-Type 判斷）。")
        return 1
    out = "/tmp/talkybuddy_cloud_tts_sample.wav"
    with open(out, "wb") as f:
        f.write(wav)
    # 放慢是誰做的要講清楚：說錯的話，下一個人查「語速不對」會找錯地方
    from server.cloud_tts import _model_honours_speed

    if config.CLOUD_TTS_SPEED == 1.0:
        how = "未放慢，CLOUD_TTS_SPEED=1.0"
    elif _model_honours_speed(config.ELEVENLABS_MODEL):
        how = f"放慢交給 API：voice_settings.speed={config.CLOUD_TTS_SPEED}"
    else:
        how = f"放慢用合成後 WSOLA：{config.ELEVENLABS_MODEL} 會忽略 API 的 speed"
    print(f"✓ synth() 回 {len(wav)} bytes WAV · {dt:.2f}s")
    print(f"  {how}")
    print(f"  已寫到 {out} — 聽聽看：  aplay {out}")

    print()
    print("=== 結論 ===")
    print("  /api/status 的 cloud_tts 會是 true。")
    print("  斷網降級 demo 現在聽得出對比了：雲端 ElevenLabs vs 邊緣 Piper。")
    print("  裝置上要生效，記得把同一行也加進裝置的 .env 再 restart：")
    print("    systemctl restart talkybuddy-server")
    return 0


if __name__ == "__main__":
    sys.exit(main())
