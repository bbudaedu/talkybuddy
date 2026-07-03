"""TTS 引擎（piper-tts，zh/en 雙 voice）。

契約（CONTRACTS.md）：
- class TTSEngine
  - available() -> bool
  - synth(segments: list[tuple[str, str]]) -> bytes | None  # 完整 WAV (16-bit PCM mono)

規格：
- piper-tts Python API：PiperVoice.load(onnx_path)，zh 段用 PIPER_ZH、en 段用 PIPER_EN
- 兩個 voice 各自懶載入；任一 voice 缺 → 跳過該語言段；全部失敗 → None
- 各段合成後以線性插值重取樣到 22050Hz，串接成單一 WAV bytes（16-bit mono）
- 段落間插 150ms 靜音
- import 期不可炸：piper / numpy 走 lazy import + try/except
"""

from __future__ import annotations

import io
import threading
import wave

TARGET_RATE = 22050        # 輸出取樣率
GAP_MS = 150               # 段落間靜音毫秒數


class TTSEngine:
    """piper 語音合成引擎（zh/en 雙 voice 懶載入、降級安全）。"""

    def __init__(self) -> None:
        # 各語言 voice 快取；值為 voice 實例、"failed" 表示載入失敗過
        self._voices: dict[str, object] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def available(self) -> bool:
        """回傳引擎是否可用：piper 可 import 且至少一個 voice 模型檔存在。"""
        try:
            from piper import PiperVoice  # noqa: F401  # lazy import 探測
        except Exception:
            return False
        try:
            from server.config import PIPER_ZH, PIPER_EN
        except Exception:
            return False
        try:
            return PIPER_ZH.exists() or PIPER_EN.exists()
        except Exception:
            return False

    # ------------------------------------------------------------------
    def _get_voice(self, lang: str):
        """懶載入指定語言（"zh"/"en"）的 PiperVoice；失敗回 None 並快取失敗狀態。"""
        cached = self._voices.get(lang)
        if cached == "failed":
            return None
        if cached is not None:
            return cached
        with self._lock:
            cached = self._voices.get(lang)
            if cached == "failed":
                return None
            if cached is not None:
                return cached
            try:
                from piper import PiperVoice  # lazy import
                from server.config import PIPER_ZH, PIPER_EN

                path = PIPER_ZH if lang == "zh" else PIPER_EN
                if not path.exists():
                    raise FileNotFoundError(str(path))
                voice = PiperVoice.load(str(path))
                self._voices[lang] = voice
                return voice
            except Exception:
                self._voices[lang] = "failed"
                return None

    # ------------------------------------------------------------------
    @staticmethod
    def _resample_linear(samples, src_rate: int, dst_rate: int):
        """以線性插值把 int16 樣本陣列從 src_rate 重取樣到 dst_rate。"""
        import numpy as np

        if src_rate == dst_rate or samples.size == 0:
            return samples
        n_src = samples.size
        n_dst = max(1, int(round(n_src * dst_rate / src_rate)))
        # 以樣本索引位置做線性插值
        x_src = np.arange(n_src, dtype=np.float64)
        x_dst = np.linspace(0.0, n_src - 1, n_dst)
        resampled = np.interp(x_dst, x_src, samples.astype(np.float64))
        return np.clip(np.rint(resampled), -32768, 32767).astype(np.int16)

    # ------------------------------------------------------------------
    def _synth_one(self, voice, text: str):
        """用單一 voice 合成一段文字，回傳 22050Hz int16 numpy 陣列；失敗回 None。"""
        import numpy as np

        try:
            chunks = list(voice.synthesize(text))
        except Exception:
            return None
        parts = []
        for chunk in chunks:
            try:
                arr = chunk.audio_int16_array
                rate = int(chunk.sample_rate)
            except Exception:
                continue
            if arr is None or getattr(arr, "size", 0) == 0:
                continue
            # 保險：多聲道時取單聲道
            if arr.ndim > 1:
                arr = arr.reshape(-1)
            parts.append(self._resample_linear(arr, rate, TARGET_RATE))
        if not parts:
            return None
        return np.concatenate(parts)

    # ------------------------------------------------------------------
    def synth(self, segments: list[tuple[str, str]]) -> bytes | None:
        """逐段合成並串接成單一 WAV bytes（22050Hz、16-bit、mono）。

        - segments：[("zh", "你說得很棒！"), ("en", "I want an apple.")]
        - 任一 voice 缺 → 跳過該語言段；全部段落都失敗 → None
        - 段落之間插入 150ms 靜音
        """
        if not segments:
            return None
        try:
            import numpy as np
        except Exception:
            return None

        gap = np.zeros(int(TARGET_RATE * GAP_MS / 1000), dtype=np.int16)
        pieces = []
        for lang, text in segments:
            text = (text or "").strip()
            if not text:
                continue
            voice = self._get_voice(lang)
            if voice is None:
                continue  # 該語言 voice 缺 → 跳過此段
            audio = self._synth_one(voice, text)
            if audio is None or audio.size == 0:
                continue
            if pieces:
                pieces.append(gap)
            pieces.append(audio)

        if not pieces:
            return None

        pcm = np.concatenate(pieces)
        buf = io.BytesIO()
        try:
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)          # mono
                wf.setsampwidth(2)          # 16-bit
                wf.setframerate(TARGET_RATE)
                wf.writeframes(pcm.tobytes())
        except Exception:
            return None
        return buf.getvalue()
