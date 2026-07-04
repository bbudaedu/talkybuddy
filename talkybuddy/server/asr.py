"""ASR 引擎對外入口（薄 shim）。

維持 `from server.asr import ASREngine` 相容：ASREngine 綁定為工廠依
config.ASR_BACKEND 選定的引擎類別。實際實作見 asr_whisper.py / asr_sensevoice.py。
"""

from __future__ import annotations

from server.asr_base import get_asr_engine_class

# 於 import 時依 config.ASR_BACKEND 綁定選定類別；app.py 以 ASREngine() 實例化。
ASREngine = get_asr_engine_class()
