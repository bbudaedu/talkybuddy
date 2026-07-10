"""一次性驗證：ElevenLabs eleven_v3 + Alice + output_format=pcm_22050 的 voice_settings 相容性。

用法（金鑰只透過 env 傳入，不寫進檔案、不印出來）：
    ELEVENLABS_API_KEY=sk_新金鑰 python verify_v3_tts.py

會對三組 voice_settings 各打一次真 API，印出 HTTP status / body 長度 / 前 4 bytes 是否 RIFF /
Content-Type；失敗時印出錯誤 body（通常會指出哪個參數不被接受）。驗證後可刪除本檔。
"""

import json
import os
import urllib.error
import urllib.request

VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "Xb7hH8MSUJpSbSDYk0k2")  # Alice
API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_v3")
TEXT = "你好，我是你的智慧學伴。今天天氣不錯，我們來學習吧！"

URL = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}?output_format=pcm_22050"

CASES = {
    "A: 只 speed 0.75": {"speed": 0.75},
    "B: 使用者驗證組（無 speed）": {
        "stability": 0.5,
        "similarity_boost": 0.8,
        "style": 0.2,
        "use_speaker_boost": True,
    },
    "C: B + speed 0.75": {
        "stability": 0.5,
        "similarity_boost": 0.8,
        "style": 0.2,
        "use_speaker_boost": True,
        "speed": 0.75,
    },
}


def run_case(name: str, voice_settings: dict) -> None:
    body = json.dumps(
        {"text": TEXT, "model_id": MODEL, "voice_settings": voice_settings}
    ).encode("utf-8")
    req = urllib.request.Request(
        URL,
        data=body,
        headers={"Content-Type": "application/json", "xi-api-key": API_KEY},
        method="POST",
    )
    print(f"\n=== {name} ===")
    print(f"  voice_settings = {voice_settings}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            ct = resp.headers.get("Content-Type", "")
        is_riff = raw[:4] == b"RIFF"
        print(f"  HTTP 200  bytes={len(raw)}  Content-Type={ct!r}  RIFF={is_riff}  even_len={len(raw) % 2 == 0}")
        print("  -> OK：可合成，適合走我們的 pcm_22050 → WAV 路徑")
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", "replace")[:400]
        print(f"  HTTP {e.code} 錯誤  body={err}")
        print("  -> 此組不被接受（若含 speed 被拒，代表 v3 不吃 speed）")
    except Exception as e:  # noqa: BLE001
        print(f"  其他例外：{type(e).__name__}: {e}")


def main() -> None:
    if not API_KEY:
        print("請先設定 env ELEVENLABS_API_KEY（不要寫進檔案）後再執行。")
        return
    print(f"voice_id={VOICE_ID}  model={MODEL}")
    for name, vs in CASES.items():
        run_case(name, vs)


if __name__ == "__main__":
    main()
