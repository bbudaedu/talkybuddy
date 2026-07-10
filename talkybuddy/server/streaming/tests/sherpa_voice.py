"""測試用最小 sherpa-onnx zh voice 載入（指向現成 _sherpa_cache 快取）。

從 spike/a2_pipecat/sherpa_voice.py 搬入。刻意不 import server/tts.py，
避開 onnx/piper 依賴，沿用現成 patched onnx + tokens.txt。
"""
from __future__ import annotations

from pathlib import Path

# server/streaming/tests/ 往上三層 = talkybuddy/
_TB_ROOT = Path(__file__).resolve().parents[3]
_CACHE = _TB_ROOT / "models" / "_sherpa_cache"
_ONNX = _CACHE / "zh_CN-huayan-medium.onnx"
_TOKENS = _CACHE / "zh_CN-huayan-medium.tokens.txt"


def _espeak_data_dir() -> str | None:
    """venv-無關定位 espeak-ng-data：先問已安裝的 piper 套件，再退回主 .venv 舊路徑。"""
    try:
        import piper  # piper-tts 套件內含 espeak-ng-data
        cand = Path(piper.__file__).parent / "espeak-ng-data"
        if cand.is_dir():
            return str(cand)
    except Exception:
        pass
    legacy = _TB_ROOT / ".venv" / "lib" / "python3.12" / "site-packages" / "piper" / "espeak-ng-data"
    return str(legacy) if legacy.is_dir() else None


def load_zh_voice():
    """載入 huayan 中文 sherpa OfflineTts；任何檔缺則 raise FileNotFoundError。"""
    import sherpa_onnx

    espeak = _espeak_data_dir()
    if espeak is None:
        raise FileNotFoundError("espeak-ng-data not found (install piper-tts)")
    for p in (_ONNX, _TOKENS, Path(espeak)):
        if not p.exists():
            raise FileNotFoundError(f"sherpa asset missing: {p}")

    vits = sherpa_onnx.OfflineTtsVitsModelConfig(
        model=str(_ONNX), tokens=str(_TOKENS), data_dir=espeak, lexicon=""
    )
    model_cfg = sherpa_onnx.OfflineTtsModelConfig(vits=vits, num_threads=1, provider="cpu")
    tts_cfg = sherpa_onnx.OfflineTtsConfig(model=model_cfg)
    if not tts_cfg.validate():
        raise RuntimeError("invalid sherpa-onnx tts config")
    return sherpa_onnx.OfflineTts(tts_cfg)
