"""engine-agnostic 可中止逐句合成核心：sherpa callback 中止 + 句界 break。

與 Pipecat 完全解耦，故可獨立單元測試。Pipecat TTS service（interruptible_tts.py）
只是把本類包一層：非同步 wrapper 逐句以 asyncio.to_thread 呼叫 synth_one，
好讓 event loop 在句間仍能處理 InterruptionFrame。

來源：從 spike/a2_pipecat/interruptible_synth.py 畢業搬入（A2-1 spike 已 5 pytest 驗過），
邏輯一行未改，僅更換模組落點（A2-2 spec 2026-07-08）。
"""
from __future__ import annotations

import re
import threading
from typing import Iterator, Optional

_SENT_SPLIT = re.compile(r"[^。！？!?]*[。！？!?]")


def split_sentences(text: str) -> list[str]:
    """按中英標點切句，去除首尾空白，丟棄純空白句。"""
    out = []
    for m in _SENT_SPLIT.findall(text or ""):
        s = m.strip()
        if s and re.sub(r"[。！？!?\s]", "", s):
            out.append(s)
    return out


class InterruptibleSynth:
    """逐句呼叫 sherpa OfflineTts.generate(text, callback)，被打斷時句界乾淨停止。"""

    def __init__(self, voice) -> None:
        self._voice = voice
        self._interrupted = threading.Event()

    def interrupt(self) -> None:
        self._interrupted.set()

    def reset(self) -> None:
        self._interrupted.clear()

    def is_interrupted(self) -> bool:
        return self._interrupted.is_set()

    def _callback(self, samples, progress) -> int:  # sherpa 契約：回非 0 即中止當前 generate
        return 1 if self._interrupted.is_set() else 0

    def synth_one(self, sentence: str) -> Optional[object]:
        """合成單句；已中止則不開始（回 None），合成中途被打斷也回 None。"""
        if self._interrupted.is_set():
            return None
        audio = self._voice.generate(sentence, callback=self._callback)
        if self._interrupted.is_set():
            return None  # callback 在合成中途回非 0、於句內提早中止
        return audio

    def synth_sentences(self, sentences) -> Iterator[tuple[int, object]]:
        for idx, sentence in enumerate(sentences):
            if self._interrupted.is_set():
                break  # 句界：不再開始下一句
            audio = self.synth_one(sentence)
            if audio is None:
                break
            yield idx, audio
