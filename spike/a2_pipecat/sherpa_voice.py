"""自帶最小 sherpa-onnx zh voice 載入（複製 server/tts.py 精神，指向現成快取）。

刻意不 import server/：spike 拋棄式、與正式碼解耦。沿用 _sherpa_cache 現成的
patched onnx + tokens.txt，故不需 onnx/piper 依賴，也不重算 metadata。
"""
from __future__ import annotations

from pathlib import Path

# spike/a2_pipecat/ 往上兩層 = talkybuddy/
_TB_ROOT = Path(__file__).resolve().parents[2]
_CACHE = _TB_ROOT / "models" / "_sherpa_cache"
_ONNX = _CACHE / "zh_CN-huayan-medium.onnx"
_TOKENS = _CACHE / "zh_CN-huayan-medium.tokens.txt"
_ESPEAK = _TB_ROOT / ".venv" / "lib" / "python3.12" / "site-packages" / "piper" / "espeak-ng-data"


def load_zh_voice():
    """載入 huayan 中文 sherpa OfflineTts；任何檔缺則 raise FileNotFoundError。"""
    import sherpa_onnx

    for p in (_ONNX, _TOKENS, _ESPEAK):
        if not p.exists():
            raise FileNotFoundError(f"spike sherpa asset missing: {p}")

    vits = sherpa_onnx.OfflineTtsVitsModelConfig(
        model=str(_ONNX), tokens=str(_TOKENS), data_dir=str(_ESPEAK), lexicon=""
    )
    model_cfg = sherpa_onnx.OfflineTtsModelConfig(vits=vits, num_threads=1, provider="cpu")
    tts_cfg = sherpa_onnx.OfflineTtsConfig(model=model_cfg)
    if not tts_cfg.validate():
        raise RuntimeError("invalid sherpa-onnx tts config")
    return sherpa_onnx.OfflineTts(tts_cfg)
