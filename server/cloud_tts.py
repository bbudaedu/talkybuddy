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
import time
import urllib.error
import urllib.request
import wave

from server.timestretch import stretch_pcm16

TARGET_RATE = 22050  # 輸出取樣率（與 server/tts.py 一致）
_API_BASE = "https://api.elevenlabs.io/v1/text-to-speech"

# 這些模型的 API 會忽略 voice_settings.speed（2026-07-30 真 API 實測：
# eleven_v3 給 speed=0.7 與 1.0 產出的語音長度是 4.16s vs 4.32s，無差別；
# 同一份文字在 eleven_turbo_v2_5 則是 5.94s vs 4.74s，明顯有效）。
# 對這些模型，放慢改在合成後用 WSOLA 補（見 synth）。
_MODELS_IGNORING_SPEED = frozenset({"eleven_v3"})


def _model_honours_speed(model: str) -> bool:
    """這個模型的 API 會照做 voice_settings.speed 嗎。

    未知模型一律當成「會照做」，因為猜錯的後果不對稱：
    - 猜「不會」但其實會 → API 放慢一次、WSOLA 再放慢一次，慢到不能聽，且不報錯
    - 猜「會」但其實不會 → 語速維持原速，只是沒放慢，仍然可用
    """
    return model not in _MODELS_IGNORING_SPEED


class CloudTTS:
    """ElevenLabs 雲端合成引擎（與 TTSEngine 同契約、降級安全）。

    `available()` 與 `verified()` 是兩件不同的事，不要混用：

    - `available()`：**設定**齊全嗎。每個回合都會被 `pipeline._synth_tts` 呼叫，
      必須便宜、不碰網路。
    - `verified()`：**實際**跑得動嗎。依最近一次 `synth()` 的結果回答，沒跑過就
      是 False。`/api/status` 用這個。

    分開的理由（2026-07-30 實測）：金鑰設好後 `available()` 為 True，但
    `CLOUD_TTS_TIMEOUT_S` 預設 1.5 秒、`eleven_v3` 暖機後仍要約 3 秒，於是每次
    合成都逾時、靜默降級回邊緣語音，而自檢一路顯示綠燈。**自檢說謊比自檢說
    沒設定更危險**，因為前者不會有人去查。
    """

    def __init__(self) -> None:
        # 最近一次 synth() 的結果：None＝還沒跑過（＝還沒有證據，不得當成功）
        self._last: dict | None = None

    # ------------------------------------------------------------------
    def available(self) -> bool:
        """有金鑰且有 voice_id → 設定齊全。urllib 為 stdlib 恆可 import，無需探測。

        ⚠️ 這只代表「設定齊全」，不代表跑得動。判斷能不能用請看 `verified()`。
        """
        try:
            from server.config import ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID
        except Exception:
            return False
        return bool(ELEVENLABS_API_KEY) and bool(ELEVENLABS_VOICE_ID)

    # ------------------------------------------------------------------
    def _record(self, ok: bool, reason: str, ms: int = 0) -> None:
        """記下最近一次 synth() 的實際結果（成功會覆蓋先前的失敗，反之亦然）。

        不做「連續 N 次失敗才算壞」這種平滑：網路抖一下就恢復是常態，而
        demo 前要看的是**此刻**能不能用，不是歷史平均。
        """
        self._last = {"ok": ok, "reason": reason, "ms": ms}

    def verified(self) -> bool:
        """最近一次合成真的成功了嗎。沒跑過 → False（沒有證據就不報綠燈）。"""
        return bool(self._last and self._last["ok"])

    def status_detail(self) -> str:
        """一句話講清楚現在是什麼狀態、以及依據是什麼。

        給 `/api/status` 與 `edge/runtime/preflight.py` 用。措辭要能直接回答
        「那我現在到底聽得到雲端語音嗎」，不要只回一個布林讓人自己猜。
        """
        if not self.available():
            return "未啟用：缺 ELEVENLABS_API_KEY 或 ELEVENLABS_VOICE_ID"
        if self._last is None:
            return "尚未驗證：設定齊全，但這次啟動後還沒實際合成過"
        if self._last["ok"]:
            return f"可用：上次合成成功（{self._last['ms']}ms）"
        return f"設定齊全但上次合成失敗 → 已靜默降級回邊緣語音（{self._last['reason']}）"

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
                    CLOUD_TTS_SPEED,
                    CLOUD_TTS_TIMEOUT_S,
                    ELEVENLABS_API_KEY,
                    ELEVENLABS_MODEL,
                    ELEVENLABS_SIMILARITY_BOOST,
                    ELEVENLABS_STABILITY,
                    ELEVENLABS_STYLE,
                    ELEVENLABS_USE_SPEAKER_BOOST,
                    ELEVENLABS_VOICE_ID,
                )
            except Exception:
                return None
            if not ELEVENLABS_API_KEY or not ELEVENLABS_VOICE_ID:
                self._record(False, "缺 ELEVENLABS_API_KEY 或 ELEVENLABS_VOICE_ID")
                return None

            url = f"{_API_BASE}/{ELEVENLABS_VOICE_ID}?output_format=pcm_22050"
            # 情緒參數（見 config）：stability/style 控制情緒起伏、
            # similarity_boost 貼近原聲。
            voice_settings = {
                "stability": ELEVENLABS_STABILITY,
                "similarity_boost": ELEVENLABS_SIMILARITY_BOOST,
                "style": ELEVENLABS_STYLE,
                "use_speaker_boost": ELEVENLABS_USE_SPEAKER_BOOST,
            }
            # 放慢語速：吃 speed 的模型交給 API（免一次 WSOLA 運算），
            # 不吃的留到合成後補。兩邊只能擇一，都做會變成放慢兩次。
            api_slows_it_down = (
                _model_honours_speed(ELEVENLABS_MODEL) and CLOUD_TTS_SPEED != 1.0
            )
            if api_slows_it_down:
                voice_settings["speed"] = CLOUD_TTS_SPEED
            body = json.dumps(
                {
                    "text": text,
                    "model_id": ELEVENLABS_MODEL,
                    "voice_settings": voice_settings,
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
            t0 = time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=CLOUD_TTS_TIMEOUT_S) as resp:
                    raw = resp.read()
                    content_type = resp.headers.get("Content-Type", "")
            except urllib.error.HTTPError as exc:
                # 401/403 金鑰、422 voice_id/model、429 額度——每種的處置完全不同，
                # 全部顯示成「失敗」等於逼人重查一輪。
                self._record(False, f"HTTP {exc.code}")
                return None
            except Exception as exc:
                # urlopen 逾時可能是 TimeoutError 或 socket.timeout（依 Python 版本
                # 與底層路徑而異），統一以「有沒有超過上限」判斷比對型別可靠。
                dt = time.monotonic() - t0
                if isinstance(exc, TimeoutError) or dt >= CLOUD_TTS_TIMEOUT_S * 0.9:
                    # 帶上上限值：只說「逾時」的話，下一個人還是得自己去翻 config
                    self._record(False, f"逾時 > {CLOUD_TTS_TIMEOUT_S}s 上限",
                                 int(dt * 1000))
                else:
                    self._record(False, f"{type(exc).__name__}", int(dt * 1000))
                return None
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            if not raw:
                self._record(False, "回應是空的", elapsed_ms)
                return None
            # 黑名單（非白名單）：只擋「明顯不是音訊」的型別，避免真實 raw PCM
            # （Content-Type 可能是 audio/*、application/octet-stream 或空字串）被誤殺。
            ct = (content_type or "").strip().lower()
            if ct.startswith("text/") or ct.startswith("application/json"):
                self._record(False, f"回應不是音訊（Content-Type: {ct}）", elapsed_ms)
                return None
            # 只有 API 沒幫忙放慢時才自己來（見上方 api_slows_it_down）。
            # 對 raw PCM 做保持音高的時間伸縮（WSOLA）；僅處理 raw PCM
            # （非 RIFF 容器）；放慢失敗（None/空）則沿用原音訊。
            if not api_slows_it_down and CLOUD_TTS_SPEED != 1.0 and raw[:4] != b"RIFF":
                slowed = stretch_pcm16(raw, CLOUD_TTS_SPEED, TARGET_RATE)
                if slowed:
                    raw = slowed
            self._record(True, "ok", elapsed_ms)
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
