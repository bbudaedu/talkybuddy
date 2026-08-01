# -*- coding: utf-8 -*-
"""polly_tts.py — Amazon Polly 童聲 TTS（只接英文段）。

為什麼只接英文：
    Polly 沒有 zh-TW。中文只有 ``cmn-CN`` Zhiyu（大陸腔、成人、不支援 generative）
    與 ``yue-CN`` Hiujin（粵語），對「台灣國小」這個場景比現在的本地 piper 沒有
    任何改善。英文則相反——Polly 有**兒童聲音** ``Ivy``(女童)／``Justin``／``Kevin``
    (男童)，而現在的 ``en_US-lessac`` 是成人男聲。一隻兒童英語學習玩偶用童聲講
    英文，這件事本身就是 demo 的畫面。

所以這個類別做的是**混合合成**：英文段送 Polly，中文段交回本地引擎，
再串成同一個 22050Hz WAV。專案的 TTS 本來就是分語言分段（scaffold.split_tts_segments），
這是接一個新後端，不是重寫。

合規（2026-08-01 查證，見 hackathon/黑客松競賽環境規範與限制_20260722.pdf 第 2 條）：
    Polly 送出去的是**文字**、收回來的才是音訊，全程不含任何生物識別資料，
    不受第 9 類限制。要守的是同條第 1 類「個人資料」——送去合成的文字不可
    含孩子姓名，這由上游的去識別化負責。

取樣率：pcm 只支援 8000/16000（mp3/ogg 才有 22050），這裡取 pcm 16000 再用
    TTSEngine 既有的線性重取樣拉到 22050，換掉 mp3 解碼的相依。

契約與 CloudTTS 對稱：
    available() -> bool
    synth(segments) -> bytes | None      # 任何失敗回 None，呼叫端降級回本地
"""
from __future__ import annotations

import io
import logging
import os
import wave

_log = logging.getLogger(__name__)

TARGET_RATE = 22050
_POLLY_RATE = 16000
_GAP_MS = 150

# 預設 Ivy（女童）。Justin／Kevin 是男童，Joanna／Danielle 是成人 generative。
_VOICE = os.environ.get("POLLY_VOICE_EN", "Ivy")
# Ivy 支援 neural；generative 只有部分成人聲有，童聲沒有。
_ENGINE = os.environ.get("POLLY_ENGINE", "neural")
_ENABLED_ENV = "TALKYBUDDY_POLLY_TTS"

# --- 中文（預設不啟用，維持本地 piper）--------------------------------
# 2026-08-01 實測這個帳號的中文只有 Zhiyu(cmn-CN) 與 Hiujin(yue-CN，粵語)，
# 41 個語言代碼裡**沒有任何 TW**。Zhiyu 與現用的 zh_CN-huayan 一樣是大陸腔，
# 所以換過去腔調不會變差，換的是自然度（neural vs piper medium）。
#
# 取捨（**Polly Neural 不支援 SSML prosody**，實測回 InvalidSsmlException）：
#   POLLY_VOICE_ZH=Zhiyu, POLLY_ENGINE_ZH=neural                → 最自然，成人女聲
#   POLLY_VOICE_ZH=Zhiyu, POLLY_ENGINE_ZH=standard, PITCH=+20%  → 童聲感，較機械
# 留空（預設）＝中文走本地 piper，行為與今天上線的版本完全一致。
_VOICE_ZH = (os.environ.get("POLLY_VOICE_ZH") or "").strip()
_ENGINE_ZH = os.environ.get("POLLY_ENGINE_ZH", "neural")
_PITCH_ZH = (os.environ.get("POLLY_ZH_PITCH") or "").strip()     # 例 "+20%"
_RATE_ZH = (os.environ.get("POLLY_ZH_RATE") or "").strip()       # 例 "95%"


def _escape_ssml(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class PollyTTS:
    """英文走 Polly 童聲、中文回退本地引擎的混合 TTS。"""

    def __init__(self, local_engine=None) -> None:
        self._local = local_engine
        self._client = None
        self._last_error: str = ""
        self._verified = False

    # ------------------------------------------------------------------
    def enabled(self) -> bool:
        return (os.environ.get(_ENABLED_ENV) or "").strip().lower() in ("1", "true", "yes")

    def available(self) -> bool:
        """開關有開、boto3 可用、憑證解析得到就算可用（不觸網）。"""
        if not self.enabled():
            return False
        try:
            import boto3  # noqa: F401
        except Exception:
            self._last_error = "缺 boto3"
            return False
        return True

    def status_detail(self) -> str:
        if not self.enabled():
            return f"未啟用：需設 {_ENABLED_ENV}=1"
        if self._verified:
            return f"已驗證：voice={_VOICE} engine={_ENGINE}"
        return self._last_error or "尚未驗證：啟動後還沒實際合成過"

    def verified(self) -> bool:
        return self._verified

    # ------------------------------------------------------------------
    def _get_client(self):
        if self._client is None:
            import boto3
            region = (os.environ.get("POLLY_REGION")
                      or os.environ.get("AWS_REGION")
                      or os.environ.get("AWS_DEFAULT_REGION")
                      or "us-west-2")
            self._client = boto3.client("polly", region_name=region)
        return self._client

    @staticmethod
    def _voice_for(lang: str) -> tuple[str, str] | None:
        """該語言要用的 (voice, engine)；回 None 代表這個語言不走 Polly。"""
        if lang == "en":
            return (_VOICE, _ENGINE)
        if lang == "zh" and _VOICE_ZH:
            return (_VOICE_ZH, _ENGINE_ZH)
        return None

    def _polly_samples(self, text: str, lang: str = "en"):
        """文字 → int16 @22050 樣本；失敗回 None。"""
        import numpy as np

        picked = self._voice_for(lang)
        if picked is None:
            return None
        voice, engine = picked

        kwargs = dict(Text=text)
        # prosody 只有 standard engine 吃得下——neural 會回 InvalidSsmlException
        # （2026-08-01 實測）。所以「調音高做童聲感」與「neural 的自然度」是二選一。
        if lang == "zh" and engine != "neural" and (_PITCH_ZH or _RATE_ZH):
            attrs = ""
            if _PITCH_ZH:
                attrs += f' pitch="{_PITCH_ZH}"'
            if _RATE_ZH:
                attrs += f' rate="{_RATE_ZH}"'
            kwargs = dict(
                Text=f"<speak><prosody{attrs}>{_escape_ssml(text)}</prosody></speak>",
                TextType="ssml",
            )

        r = self._get_client().synthesize_speech(
            VoiceId=voice, Engine=engine,
            OutputFormat="pcm", SampleRate=str(_POLLY_RATE), **kwargs,
        )
        raw = r["AudioStream"].read()
        if not raw:
            return None
        samples = np.frombuffer(raw, dtype="<i2")
        if samples.size == 0:
            return None
        from server.tts import TTSEngine
        return TTSEngine._resample_linear(samples, _POLLY_RATE, TARGET_RATE)

    def _local_samples(self, lang: str, text: str):
        """交回本地引擎（中文，或 Polly 失敗時的英文）；失敗回 None。"""
        if self._local is None:
            return None
        try:
            voice = self._local._get_voice(lang)
            if voice is None:
                return None
            return self._local._synth_one(voice, text)
        except Exception:
            return None

    # ------------------------------------------------------------------
    def synth(self, segments: list[tuple[str, str]]) -> bytes | None:
        """逐段合成（en→Polly、zh→本地）並串成單一 WAV bytes。

        任一段失敗只影響該段（英文段失敗會就地回退本地聲音），全部段都失敗才回 None。
        """
        if not segments or not self.available():
            return None
        try:
            import numpy as np
        except Exception:
            return None

        gap = np.zeros(int(TARGET_RATE * _GAP_MS / 1000), dtype=np.int16)
        pieces = []
        used_polly = False
        for lang, text in segments:
            text = (text or "").strip()
            if not text:
                continue
            audio = None
            if self._voice_for(lang) is not None:
                try:
                    audio = self._polly_samples(text, lang)
                    if audio is not None and audio.size:
                        used_polly = True
                except Exception as exc:          # 逾時/權限/配額
                    self._last_error = f"Polly 失敗（{lang}）：{type(exc).__name__}"
                    _log.warning("Polly 合成失敗，該段回退本地：%s", exc)
                    audio = None
            if audio is None or getattr(audio, "size", 0) == 0:
                audio = self._local_samples(lang, text)
            if audio is None or audio.size == 0:
                continue
            if pieces:
                pieces.append(gap)
            pieces.append(audio)

        if not pieces:
            return None
        merged = np.concatenate(pieces).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(TARGET_RATE)
            wf.writeframes(merged.tobytes())
        if used_polly:
            self._verified = True
        return buf.getvalue()
