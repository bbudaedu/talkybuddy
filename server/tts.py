"""TTS 引擎（sherpa-onnx，zh/en 雙 voice）。

契約（CONTRACTS.md，逐字不變）：
- class TTSEngine
  - available() -> bool
  - synth(segments: list[tuple[str, str]]) -> bytes | None  # 完整 WAV (16-bit PCM mono)

規格：
- 改用 sherpa-onnx（Apache-2.0）取代 piper-tts（GPL-3.0）當合成後端，聲音檔仍沿用
  既有的 zh_CN-huayan-medium.onnx / en_US-lessac-medium.onnx 兩個 Piper=VITS 模型
  （config.PIPER_ZH / PIPER_EN），完全不需重新下載模型。
- sherpa-onnx 的 vits 模型載入需要：
  1) onnx 檔本身要有 model_type/sample_rate/voice 等 custom metadata（既有 Piper
     onnx 只把這些欄位存在旁的 .onnx.json，onnx 檔內沒有），
  2) tokens.txt（由 .onnx.json 的 phoneme_id_map 依 id 排序產生）。
  因此首次使用某語言時，會用 onnx 套件（Apache-2.0）讀取原始 onnx + 對應
  .onnx.json，補寫 metadata 後另存到 config.SHERPA_CACHE_DIR，並在同目錄產生
  tokens.txt；之後只要來源檔沒變就重用快取，不會每次都重算。
- espeak-ng-data（音素化資料）路徑見 config.ESPEAK_NG_DATA_DIR；其授權是 espeak-ng
  上游的 GPL-3.0，仍是待確認的殘留風險（spike 已記錄），本檔不處理。
- zh 段用 zh voice（config.PIPER_ZH）、en 段用 en voice（config.PIPER_EN），兩個
  各自獨立的 sherpa_onnx.OfflineTts 懶載入實例；任一 voice/模型缺 → 跳過該語言段；
  全部失敗 → available()=False、synth 回 None。
- 各段合成後以線性插值重取樣到 22050Hz，串接成單一 WAV bytes（16-bit mono）
- 段落間插 150ms 靜音
- import 期不可炸：sherpa_onnx / onnx / numpy 一律走 lazy import + try/except
"""

from __future__ import annotations

import io
import json
import threading
import wave
from pathlib import Path

TARGET_RATE = 22050        # 輸出取樣率
GAP_MS = 150               # 段落間靜音毫秒數


class TTSEngine:
    """sherpa-onnx 語音合成引擎（zh/en 雙 voice 懶載入、降級安全）。"""

    def __init__(self) -> None:
        # 各語言 voice 快取；值為 sherpa_onnx.OfflineTts 實例、"failed" 表示載入失敗過
        self._voices: dict[str, object] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def available(self) -> bool:
        """回傳引擎是否可用：sherpa_onnx 可 import 且至少一個 voice 模型檔存在。"""
        try:
            import sherpa_onnx  # noqa: F401  # lazy import 探測
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
    @staticmethod
    def _resolve_espeak_data_dir() -> str | None:
        """找 espeak-ng-data 目錄：優先專案內建路徑，找不到 fallback 到 piper 套件內附版本。"""
        try:
            from server.config import ESPEAK_NG_DATA_DIR

            if ESPEAK_NG_DATA_DIR.exists():
                return str(ESPEAK_NG_DATA_DIR)
        except Exception:
            pass
        try:
            import piper

            fallback = Path(piper.__file__).resolve().parent / "espeak-ng-data"
            if fallback.exists():
                return str(fallback)
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    @staticmethod
    def _patch_onnx_metadata(onnx_path: Path, voice_cfg: dict, cache_onnx: Path) -> None:
        """讀原始 onnx + 對應 .onnx.json，補上 sherpa-onnx 需要的 metadata 後另存快取。"""
        import onnx  # lazy import

        model = onnx.load(str(onnx_path))
        del model.metadata_props[:]  # 避免重複疊加

        def add(key: str, value) -> None:
            kv = model.metadata_props.add()
            kv.key = key
            kv.value = str(value)

        audio_cfg = voice_cfg.get("audio", {}) or {}
        espeak_cfg = voice_cfg.get("espeak", {}) or {}
        lang_cfg = voice_cfg.get("language", {}) or {}

        add("model_type", "vits")
        add("comment", "piper")
        add("language", lang_cfg.get("name_english", ""))
        add("voice", espeak_cfg.get("voice", ""))
        add("n_speakers", voice_cfg.get("num_speakers", 1))
        add("sample_rate", audio_cfg.get("sample_rate", TARGET_RATE))
        add("phoneme_type", "espeak")
        add("has_espeak", 1)
        add("punctuation", ", . ! ? ; :")

        cache_onnx.parent.mkdir(parents=True, exist_ok=True)
        onnx.save(model, str(cache_onnx))

    # ------------------------------------------------------------------
    @staticmethod
    def _write_tokens_txt(voice_cfg: dict, tokens_path: Path) -> None:
        """依 phoneme_id_map（symbol -> [id]）依 id 排序寫出 sherpa-onnx 用的 tokens.txt。"""
        phoneme_id_map: dict = voice_cfg["phoneme_id_map"]
        items = sorted(phoneme_id_map.items(), key=lambda kv: kv[1][0])
        tokens_path.parent.mkdir(parents=True, exist_ok=True)
        with open(tokens_path, "w", encoding="utf-8") as f:
            for symbol, ids in items:
                f.write(f"{symbol} {ids[0]}\n")

    # ------------------------------------------------------------------
    def _ensure_patched_model(self, onnx_path: Path) -> tuple[Path, Path]:
        """確保 onnx_path 對應的 patched onnx + tokens.txt 存在於快取目錄，回傳兩者路徑。

        來源 onnx / .onnx.json 沒有比快取新時直接重用快取，不重算。
        """
        from server.config import SHERPA_CACHE_DIR

        json_path = Path(str(onnx_path) + ".json")
        cache_onnx = SHERPA_CACHE_DIR / onnx_path.name
        tokens_path = SHERPA_CACHE_DIR / (onnx_path.stem + ".tokens.txt")

        src_mtime = max(onnx_path.stat().st_mtime, json_path.stat().st_mtime)
        cache_fresh = (
            cache_onnx.exists()
            and tokens_path.exists()
            and cache_onnx.stat().st_mtime >= src_mtime
            and tokens_path.stat().st_mtime >= src_mtime
        )
        if cache_fresh:
            return cache_onnx, tokens_path

        with open(json_path, "r", encoding="utf-8") as f:
            voice_cfg = json.load(f)
        self._patch_onnx_metadata(onnx_path, voice_cfg, cache_onnx)
        self._write_tokens_txt(voice_cfg, tokens_path)
        return cache_onnx, tokens_path

    # ------------------------------------------------------------------
    def _get_voice(self, lang: str):
        """懶載入指定語言（"zh"/"en"）的 sherpa_onnx.OfflineTts；失敗回 None 並快取失敗狀態。"""
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
                import sherpa_onnx  # lazy import
                from server.config import PIPER_ZH, PIPER_EN

                onnx_path = PIPER_ZH if lang == "zh" else PIPER_EN
                if not onnx_path.exists():
                    raise FileNotFoundError(str(onnx_path))

                data_dir = self._resolve_espeak_data_dir()
                if not data_dir:
                    raise FileNotFoundError("espeak-ng-data not found")

                cache_onnx, tokens_path = self._ensure_patched_model(onnx_path)

                vits_cfg = sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=str(cache_onnx),
                    tokens=str(tokens_path),
                    data_dir=data_dir,
                    lexicon="",
                )
                model_cfg = sherpa_onnx.OfflineTtsModelConfig(
                    vits=vits_cfg, num_threads=1, provider="cpu"
                )
                tts_cfg = sherpa_onnx.OfflineTtsConfig(model=model_cfg)
                if not tts_cfg.validate():
                    raise RuntimeError(f"invalid sherpa-onnx tts config for lang={lang}")

                voice = sherpa_onnx.OfflineTts(tts_cfg)
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
        """用單一 sherpa_onnx.OfflineTts 合成一段文字，回傳 22050Hz int16 numpy 陣列；失敗回 None。"""
        import numpy as np

        try:
            audio = voice.generate(text)
        except Exception:
            return None
        try:
            float_samples = np.asarray(audio.samples, dtype=np.float64)
            rate = int(audio.sample_rate)
        except Exception:
            return None
        if float_samples is None or float_samples.size == 0:
            return None
        if float_samples.ndim > 1:
            float_samples = float_samples.reshape(-1)  # 保險：多聲道時取單聲道
        # sherpa-onnx 回傳 [-1, 1] 浮點樣本，先轉 int16 再走既有重取樣邏輯
        int16_samples = np.clip(np.rint(float_samples * 32767.0), -32768, 32767).astype(np.int16)
        return self._resample_linear(int16_samples, rate, TARGET_RATE)

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
