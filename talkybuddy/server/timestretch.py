"""保持音高的時間伸縮（WSOLA），用於放慢雲端 v3 語音。

背景：ElevenLabs eleven_v3 實測忽略 API 的 speed 參數（三種 speed byte 數相同），
所以「放慢語速」不能靠參數，只能在合成後對 raw PCM 做**保持音高的變速**。
本模組用 WSOLA（Waveform Similarity Overlap-Add）：以相似度搜尋對齊重疊區，
拉長/壓縮時間軸而**不改音高**（naive resample 會連音高一起變）。

依賴：numpy 為 faster-whisper / sherpa-onnx 的既有傳遞相依，部署恆有。為求穩健，
numpy 惰性 import 於函式內；缺 numpy 或任何例外 → 回傳 None，由呼叫端回退原始音訊
（放慢失敗絕不能讓 TTS 崩潰）。
"""

from __future__ import annotations

_SAMPLE_MIN = -32768
_SAMPLE_MAX = 32767

# WSOLA 參數（22050Hz 下：frame≈46ms、50% overlap、搜尋±256 樣本≈±11ms）
_FRAME_LEN = 1024
_SYN_HOP = 512
_TOL = 256


def stretch_pcm16(pcm: bytes, speed: float, rate: int = 22050) -> bytes | None:
    """對 raw 16-bit LE mono PCM 做保持音高的變速。

    - speed < 1 → 放慢（輸出變長）；speed > 1 → 加快；speed == 1 → 原樣回傳。
    - speed <= 0、輸入過短（< 一個 frame）、奇數 bytes（非合法 16-bit PCM）→ 原樣回傳。
    - 缺 numpy / 任何例外 → 回傳 None（呼叫端回退原始 pcm）。
    """
    try:
        import numpy as np
    except Exception:
        return None
    try:
        if speed <= 0 or abs(speed - 1.0) < 1e-3:
            return pcm
        if len(pcm) % 2 != 0:            # 非合法 16-bit LE mono PCM
            return pcm
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
        if len(x) < _FRAME_LEN:          # 太短、伸縮無意義
            return pcm
        y = _wsola(np, x, speed)
        y = np.clip(np.rint(y), _SAMPLE_MIN, _SAMPLE_MAX).astype("<i2")
        return y.tobytes()
    except Exception:
        return None


def _wsola(np, x, speed: float):
    """WSOLA 時間伸縮核心（保持音高）。輸入/輸出皆為 float64 ndarray。

    合成端固定以 _SYN_HOP 前進；分析端理想前進量 = _SYN_HOP*speed。每一框在理想
    分析位置附近 ±_TOL 內，用互相關搜尋與「前一框自然延續段」最相似的位置後再
    overlap-add，藉此對齊波形相位、避免破音。
    """
    win = np.hanning(_FRAME_LEN)
    ana_hop = _SYN_HOP * speed
    n = len(x)

    num_frames = int(np.floor((n - _FRAME_LEN) / ana_hop)) + 1
    if num_frames < 1:
        return x

    # 尾端補零，讓搜尋窗（理想位置 +_TOL +一框）永不越界。
    pad = _FRAME_LEN + _TOL + _SYN_HOP + 1
    xp = np.concatenate([x, np.zeros(pad)])

    out_len = (num_frames - 1) * _SYN_HOP + _FRAME_LEN
    y = np.zeros(out_len)
    ow = np.zeros(out_len)               # 視窗重疊權重，最後用來正規化

    natural = None                       # 前一框的「自然延續」參考段
    for k in range(num_frames):
        ideal = int(round(k * ana_hop))
        if natural is None:
            p = ideal                    # 首框：不搜尋
        else:
            lo = max(0, ideal - _TOL)
            hi = ideal + _TOL
            seg = xp[lo : hi + _FRAME_LEN]
            # 互相關：對每個位移取 dot(seg_shift, natural)，取最大者。
            scores = np.correlate(seg, natural, mode="valid")
            p = lo + int(np.argmax(scores))

        frame = xp[p : p + _FRAME_LEN] * win
        s = k * _SYN_HOP
        y[s : s + _FRAME_LEN] += frame
        ow[s : s + _FRAME_LEN] += win
        natural = xp[p + _SYN_HOP : p + _SYN_HOP + _FRAME_LEN]

    ow[ow < 1e-6] = 1.0                  # 避免除以 0（僅出現在無訊號尾端）
    return y / ow
