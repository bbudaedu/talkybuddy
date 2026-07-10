"""雲端情緒 TTS（ElevenLabs）。

契約與邊緣 TTSEngine（server/tts.py）一致：
- class CloudTTS
  - available() -> bool
  - synth(segments: list[tuple[str, str]]) -> bytes | None  # 完整 WAV (22050Hz/16-bit/mono)

設計（見 docs/superpowers/specs/2026-07-08-cloud-emotional-tts-design.md）：
- network_mode=="cloud" 時由 pipeline 呼叫；任何失敗回 None → pipeline 靜默降級回邊緣 Piper。
- ElevenLabs 單一 voice 原生中英混讀 → segments 併成單一字串、單一 API 呼叫。
- output_format=pcm_22050 回傳 raw 16-bit LE mono PCM（headerless）→ 包成 WAV bytes
  （與邊緣同規格，前端零改動）；保險：若回傳已是 RIFF/WAV 則原樣通過。
- HTTP client 用 stdlib urllib.request（對齊 diagnose.py，不新增依賴、恆可用）。
- 逾時/非2xx/斷網/空 body/任何例外 → 回 None，不 raise。
"""

from __future__ import annotations

import io
import json
import urllib.request
import wave

TARGET_RATE = 22050  # 輸出取樣率（與 server/tts.py 一致）
_API_BASE = "https://api.elevenlabs.io/v1/text-to-speech"


class CloudTTS:
    """ElevenLabs 雲端合成引擎（與 TTSEngine 同契約、降級安全）。"""

    # ------------------------------------------------------------------
    def available(self) -> bool:
        """有金鑰且有 voice_id → 可用。urllib 為 stdlib 恆可 import，無需探測。"""
        try:
            from server.config import ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID
        except Exception:
            return False
        return bool(ELEVENLABS_API_KEY) and bool(ELEVENLABS_VOICE_ID)

    # ------------------------------------------------------------------
    def synth(self, segments: list[tuple[str, str]]) -> bytes | None:
        """把 segments 併成單一字串、呼叫 ElevenLabs、raw PCM 包成 WAV bytes。

        任何失敗（空輸入/無金鑰/逾時/非2xx/斷網/空 body/例外）→ 回 None，不 raise。
        """
        if not segments:
            return None
        try:
            text = " ".join(
                (t or "").strip() for _lang, t in segments if (t or "").strip()
            )
            if not text:
                return None

            try:
                from server.config import (
                    CLOUD_TTS_TIMEOUT_S,
                    ELEVENLABS_API_KEY,
                    ELEVENLABS_MODEL,
                    ELEVENLABS_SPEED,
                    ELEVENLABS_VOICE_ID,
                )
            except Exception:
                return None
            if not ELEVENLABS_API_KEY or not ELEVENLABS_VOICE_ID:
                return None

            url = f"{_API_BASE}/{ELEVENLABS_VOICE_ID}?output_format=pcm_22050"
            # voice_settings.speed 放慢語速（見 config.ELEVENLABS_SPEED）；較適合兒童聆聽。
            body = json.dumps(
                {
                    "text": text,
                    "model_id": ELEVENLABS_MODEL,
                    "voice_settings": {"speed": ELEVENLABS_SPEED},
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "xi-api-key": ELEVENLABS_API_KEY,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=CLOUD_TTS_TIMEOUT_S) as resp:
                    raw = resp.read()
                    content_type = resp.headers.get("Content-Type", "")
            except Exception:
                return None
            if not raw:
                return None
            # 黑名單（非白名單）：只擋「明顯不是音訊」的型別，避免真實 raw PCM
            # （Content-Type 可能是 audio/*、application/octet-stream 或空字串）被誤殺。
            ct = (content_type or "").strip().lower()
            if ct.startswith("text/") or ct.startswith("application/json"):
                return None
            return _pcm_to_wav(raw)
        except Exception:
            return None


# ----------------------------------------------------------------------
def _pcm_to_wav(raw: bytes) -> bytes | None:
    """raw 16-bit LE mono PCM(22050Hz) → WAV bytes；已是 RIFF 則原樣回傳；失敗回 None。"""
    if raw[:4] == b"RIFF":
        return raw
    if len(raw) % 2 != 0:
        # 16-bit LE mono PCM 必為偶數 bytes；奇數長度代表 body 不是合法 raw PCM。
        return None
    try:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)       # mono
            wf.setsampwidth(2)       # 16-bit
            wf.setframerate(TARGET_RATE)
            wf.writeframes(raw)
        return buf.getvalue()
    except Exception:
        return None
