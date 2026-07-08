"""Silero VAD 薄封裝：連續音訊 bytes → started/stopped 說話邊緣事件。

實測 API（Pipecat 1.5.0，A2-2 執行期校正）：
- SileroVADAnalyzer(sample_rate=int, params=VADParams(confidence, start_secs, stop_secs, min_volume))
- **必須先呼叫 `set_sample_rate(sample_rate)`** 才會初始化內部位元緩衝
  （`_vad_frames_num_bytes` 等）；Pipecat pipeline 於 transport 啟動時自動呼叫，
  standalone 使用（本類）需自行呼叫，否則 `_run_analyzer` 會 AttributeError。
- 逐塊狀態判定核心 `_run_analyzer(buffer: bytes) -> VADState` 為**同步**方法；
  對外的 `analyze_audio(buffer)` 只是把它包進 executor 的 **async** 版
  （`await loop.run_in_executor(...)`，需 running event loop）。本類的 `push()`
  是同步介面（框架無關、可用 canned wav 單元測試），故直接驅動同步核心
  `_run_analyzer`，避免為每塊起一個 event loop。
- VADState ∈ {QUIET, STARTING, SPEAKING, STOPPING}；核心已內含 start_secs/stop_secs
  平滑（STARTING 累積達門檻→SPEAKING、STOPPING 累積達門檻→QUIET）。
- num_frames_required() -> int（每塊固定樣本數；buffer bytes = frames * 2, int16 mono）。
  Silero 僅支援 sample_rate ∈ {8000, 16000}。

參數集中此處便於日後上機調校（research/12：繁中兒童門檻待實測）。實測預設 confidence=0.7
於 canned zh.wav 即可乾淨產出 started→stopped，無須調低。
"""
from __future__ import annotations

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams, VADState


def build_analyzer(
    sample_rate: int,
    *,
    confidence: float = 0.7,
    start_secs: float = 0.2,
    stop_secs: float = 0.8,
    min_volume: float = 0.6,
) -> SileroVADAnalyzer:
    """建 Silero VAD analyzer；stop_secs 保守取值避免把停頓誤判為輪結束。

    回傳前呼叫 set_sample_rate 初始化內部緩衝，讓 standalone 同步驅動可用。
    """
    params = VADParams(
        confidence=confidence,
        start_secs=start_secs,
        stop_secs=stop_secs,
        min_volume=min_volume,
    )
    analyzer = SileroVADAnalyzer(sample_rate=sample_rate, params=params)
    analyzer.set_sample_rate(sample_rate)  # 初始化 _vad_frames_num_bytes 等內部狀態
    return analyzer


class SpeechGate:
    """把 SileroVADAnalyzer 的逐塊 VADState 折成 started/stopped 邊緣事件。

    分析器要求固定大小 buffer（num_frames_required），故本類自帶位元緩衝，
    湊滿一塊才 analyze，剩餘位元留待下次 push。
    """

    def __init__(self, analyzer: SileroVADAnalyzer) -> None:
        self._analyzer = analyzer
        self._chunk_bytes = analyzer.num_frames_required() * 2  # int16 mono
        self._buf = bytearray()
        self._speaking = False

    def reset(self) -> None:
        self._buf.clear()
        self._speaking = False

    def push(self, pcm: bytes) -> list[str]:
        """餵入任意長度音訊 bytes，回本次觸發的邊緣事件序列。"""
        self._buf.extend(pcm)
        events: list[str] = []
        while len(self._buf) >= self._chunk_bytes:
            chunk = bytes(self._buf[: self._chunk_bytes])
            del self._buf[: self._chunk_bytes]
            state = self._analyzer._run_analyzer(chunk)  # 同步核心（見模組 docstring）
            if state == VADState.SPEAKING and not self._speaking:
                self._speaking = True
                events.append("started")
            elif state == VADState.QUIET and self._speaking:
                self._speaking = False
                events.append("stopped")
        return events
