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
_ESPEAK = _TB_ROOT / ".venv" / "lib" / "python3.12" / "site-packages" / "piper" / "espeak-ng-data"


def load_zh_voice():
    """載入 huayan 中文 sherpa OfflineTts；任何檔缺則 raise FileNotFoundError。"""
    import sherpa_onnx

    for p in (_ONNX, _TOKENS, _ESPEAK):
        if not p.exists():
            raise FileNotFoundError(f"sherpa asset missing: {p}")

    vits = sherpa_onnx.OfflineTtsVitsModelConfig(
        model=str(_ONNX), tokens=str(_TOKENS), data_dir=str(_ESPEAK), lexicon=""
    )
    model_cfg = sherpa_onnx.OfflineTtsModelConfig(vits=vits, num_threads=1, provider="cpu")
    tts_cfg = sherpa_onnx.OfflineTtsConfig(model=model_cfg)
    if not tts_cfg.validate():
        raise RuntimeError("invalid sherpa-onnx tts config")
    return sherpa_onnx.OfflineTts(tts_cfg)
