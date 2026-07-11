"""BargeInGate：把 SpeechGate 接成 Pipecat 處理器，偵測 bot 說話中的插話。

觀察下行 InputAudioRawFrame（原封轉發給下游），把 PCM 餵給 SpeechGate；
SpeechGate 吐 "started" 時往 downstream 發 BargeInDetectedFrame，供 StreamingTurnManager
做 barge-in。門檻參數與 transport turn-taking VAD 分離、獨立可調。

門檻預設沿用 build_analyzer 預設；A2-3 將依真兒童錄音調高 confidence/min_volume 抗 double-talk。
BargeInDetectedFrame 繼承 SystemFrame（高優先即時傳遞，語意同 VADUserStartedSpeakingFrame），
確保能穿過中間處理器即時抵達 turn_manager。
"""
from __future__ import annotations

from dataclasses import dataclass

from pipecat.frames.frames import InputAudioRawFrame, SystemFrame
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

from server.streaming.vad import SpeechGate, build_analyzer


@dataclass
class BargeInDetectedFrame(SystemFrame):
    """VAD 偵測到使用者於 bot 播放期間開口（barge-in 訊號）。"""
    pass


class BargeInGate(FrameProcessor):
    """獨立可調 barge-in 偵測器：InputAudioRawFrame → SpeechGate → BargeInDetectedFrame。

    SpeechGate 於首個 InputAudioRawFrame 依其 sample_rate 惰性建立（build_analyzer 內部
    需 set_sample_rate 初始化位元緩衝）。門檻經 gate_params 傳入 build_analyzer。
    """

    def __init__(self, gate_params: dict | None = None, **kwargs):
        super().__init__(**kwargs)
        self._gate_params = gate_params or {}
        self._gate: SpeechGate | None = None

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InputAudioRawFrame):
            if self._gate is None:
                self._gate = SpeechGate(build_analyzer(frame.sample_rate, **self._gate_params))
            for event in self._gate.push(frame.audio):
                if event == "started":
                    await self.push_frame(BargeInDetectedFrame(), FrameDirection.DOWNSTREAM)

        await self.push_frame(frame, direction)
