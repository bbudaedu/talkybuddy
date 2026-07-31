# -*- coding: utf-8 -*-
"""press_to_talk.py — 按鍵觸發：沒按之前把上行換成靜音。

## 為什麼需要（2026-08-01 決賽日）

local-client 是 press-to-talk（power 鍵），對環境噪音**天生免疫**；pipecat 走
VAD 連續聽。決賽會場很吵，而噪音誤觸不是假設——`PIPECAT_HANDOFF.md` 第二節記著
「寶貝多米」被辨識成「コび」。

近場門檻那條備案在這塊板子上**走不通**，不要再試：記憶 `project-edge-s2s-tuning`
記著 `TALKYBUDDY_EDGE_NEAR_FIELD_PEAK` 必須是 0，否則玩偶完全不回話（預設 0.06
會讓它全聾）。所以只剩按鍵這一條。

## 為什麼是兩個 processor

要擋噪音就必須攔在 **VAD 之前**（讓 VAD 根本不觸發），但「孩子講完了」這個訊號
（`UserStoppedSpeakingFrame`）是 `VADProcessor` 往**下游**推的——擺在 VAD 前面的
processor 永遠看不到它。

```
transport.input() → PressToTalkFilter(g) → PlaybackGateFilter → VAD → PressToTalkDisarmer(g) → STT → …
```

跟 `PlaybackGateFilter`/`PlaybackGateSink` 同一個理由、同一個已驗證過的形狀：
兩個 processor 共享一個 state 物件。

## 為什麼送靜音而不是丟棄

沿用 `playback_gate.py` 的通則（記憶 `project-edge-s2s-tuning`）：**不要在串流中
挖洞**。VAD 的狀態機依賴連續音訊，直接不送會留下斷口。

## 失效方向刻意選「開」

按鍵讀不到（裝置節點不見、權限不對）時 `PressToTalkGate` 會**永久 armed**，退回
現行的 VAD 連續聽。理由是不對稱的：玩偶變吵，現場的人看得出來也還能講話；玩偶
全聾的症狀跟壞掉一模一樣，沒有人救得回來，而那是決賽最貴的失敗。

## 為什麼用自己的執行緒而不是 `asyncio.to_thread`

`wait_for_trigger()` 是同步阻塞，可能擋好幾分鐘——直接在 async 裡呼叫會**凍結整個
event loop**（2026-07-30 local-client 閒置後崩潰的根因，見 `local_client.py:124`）。
`asyncio.to_thread` 也解得掉，但它會把預設 executor 的一個名額**永久佔住**，而這裡
的等待正是永不返回的那種。專屬 daemon 執行緒沒有這個問題，也不必跟著 pipecat 的
setup/cleanup 生命週期走。
"""

from __future__ import annotations

import asyncio
import math
import struct
import threading
import time
from typing import Awaitable, Callable

from loguru import logger
from pipecat.frames.frames import Frame, InputAudioRawFrame, UserStoppedSpeakingFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from edge.runtime import audio_io

# 按了鍵卻沒開口就走人時要自己關掉，否則閘門一直開著收會場噪音。
DEFAULT_IDLE_TIMEOUT_S = 15.0

# 提示音最多等這麼久。喇叭卡住時不該把背景工作永遠掛著。
CUE_TIMEOUT_S = 2.0

# 等待「孩子講完 → 閘門關上」的輪詢間隔。20Hz 對背景執行緒是免費的，
# 換來不必在兩個執行緒之間拉一條 Event。
_REARM_POLL_S = 0.05


def beep_pcm(
    sample_rate: int,
    *,
    freq_hz: float = 880.0,
    ms: int = 150,
    volume: float = 0.35,
) -> bytes:
    """產生「我在聽了」的提示音（16-bit mono PCM）。

    **為什麼是純音而不是說一句話**：玩偶只要講話，`PlaybackGate` 就得關上行
    （否則它把自己的話收回去——2026-07-31 逐字稿出現過玩偶自己剛講的句子），
    成本是每輪多 3.4 秒（語音 0.8s + aplay 緩衝 2.0s + tail 0.6s）。純音不會被
    SenseVoice 辨識成字，Silero VAD 是**語音**偵測器、對單一正弦波不敏感，
    所以不必關閘門。

    **淡入淡出是必要的**：突然開始／結束的邊緣是寬頻的「喀」聲，那反而像人聲
    的爆破音，可能觸發 VAD——正好毀掉上面那個前提。

    Args:
        sample_rate: 取樣率，要與 aplay 啟動時的一致（對不上會變調）。
        freq_hz: 音高。880Hz（A5）在玩偶的小喇叭上聽得清楚。
        ms: 長度。
        volume: 0–1，會留餘裕避免削波（削波產生諧波＝寬頻噪音）。

    Returns:
        little-endian 16-bit mono PCM bytes。
    """
    n = int(sample_rate * ms / 1000)
    fade = max(1, int(n * 0.15))
    peak = 32767 * min(volume, 0.95)
    out = []
    for i in range(n):
        env = min(1.0, i / fade, (n - 1 - i) / fade)
        out.append(int(peak * env * math.sin(2 * math.pi * freq_hz * i / sample_rate)))
    return struct.pack(f"<{n}h", *out)


class PressToTalkGate:
    """armed 與否的狀態機。時鐘可注入，所以測試不必真的等 15 秒。"""

    def __init__(
        self,
        *,
        idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S,
        now: Callable[[], float] = time.monotonic,
    ):
        """Initialize the press-to-talk gate.

        Args:
            idle_timeout_s: Auto-disarm this long after arming with no speech.
            now: Injectable clock, for tests.
        """
        self._idle_timeout_s = idle_timeout_s
        self._now = now
        self._armed_at: float | None = None
        self._always_armed = False
        self._cue_pending = False

    def arm(self) -> None:
        """Open the gate; it stays open until disarmed or idle-timed-out."""
        self._armed_at = self._now()
        self._cue_pending = True

    def take_cue(self) -> bool:
        """這次 arm 的提示音還沒發過嗎（發過就回 False）。

        一次按鍵一聲。重複發會變成連續嗶嗶，比沒有提示更糟。
        由 `arm()`（等按鍵的執行緒）設旗標、由 pipeline（event loop）取走，
        兩邊都是單一 bool 的讀寫，GIL 之下不需要鎖。

        Returns:
            True 代表現在該發提示音。
        """
        if not self._cue_pending:
            return False
        self._cue_pending = False
        return True

    def disarm(self) -> None:
        """Close the gate immediately."""
        self._armed_at = None

    def arm_permanently(self) -> None:
        """Fail open: the key can't be read, so fall back to continuous VAD.

        A noisy doll is recoverable on stage; a deaf one is not.
        """
        self._always_armed = True

    def is_armed(self) -> bool:
        """Whether uplink audio should reach the VAD right now.

        Returns:
            True when the key was pressed recently enough, or when the key
            device turned out to be unreadable.
        """
        if self._always_armed:
            return True
        if self._armed_at is None:
            return False
        return (self._now() - self._armed_at) < self._idle_timeout_s


class PressToTalkFilter(FrameProcessor):
    """沒按鍵之前把上行換成靜音。擺在 VAD **之前**。"""

    def __init__(
        self,
        gate: PressToTalkGate,
        *,
        trigger: Callable[[], object] | None = None,
        cue: Callable[[], Awaitable[None]] | None = None,
        **kwargs,
    ):
        """Initialize the press-to-talk filter.

        Args:
            gate: Shared gate instance, also held by the disarmer.
            trigger: Blocking call that returns when the key is pressed.
                Defaults to the device's physical-key wait.
            cue: Awaitable played once right after arming, so the child knows
                the doll is listening. `None` = 沒有提示音（原本的行為）。
        """
        super().__init__(**kwargs)
        self._gate = gate
        self._trigger = trigger or audio_io.wait_for_trigger
        self._cue = cue
        self._cue_task: asyncio.Task | None = None
        self._thread: threading.Thread | None = None
        self._gave_up = False
        self._muted_frames = 0
        self._was_armed: bool | None = None

    @property
    def muted_frames(self) -> int:
        """How many uplink frames were silenced (for diagnostics).

        Returns:
            Count of frames replaced with silence since start.
        """
        return self._muted_frames

    def _ensure_waiter(self) -> None:
        """確保等待按鍵的執行緒活著。每個音訊 frame 都會呼叫（50 次/秒）。

        **`_gave_up` 不可省。** 等待迴圈在讀不到按鍵時會結束（見 `_wait_loop`），
        少了這個旗標，下一個 frame 就會再開一條、再拋、再死——每秒 50 條執行緒
        與 50 行含 traceback 的 WARNING。板子的 journal 是 `Storage=volatile`
        （存在 RAM，只有 1.7GB 可用），現場撞到就是 log 洗爆加 CPU 燒光。
        """
        if self._gave_up:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._wait_loop, name="press-to-talk", daemon=True
        )
        self._thread.start()

    def _wait_loop(self) -> None:
        """等按鍵 → armed → 等閘門關上 → 再等按鍵。跑在自己的執行緒。"""
        while True:
            try:
                self._trigger()
            except Exception:
                # 讀不到按鍵就退回連續聽，並且大聲講——靜默地變成連續聽，
                # 現場會以為按鍵有效而百思不解為何噪音還是進得來。
                logger.warning(
                    "⚠️ 按鍵讀不到，press-to-talk 退回 VAD 連續聽（會收環境噪音）",
                    exc_info=True,
                )
                # 記住已經放棄，否則 _ensure_waiter 會每個 frame 重開一條執行緒。
                self._gave_up = True
                self._gate.arm_permanently()
                return
            logger.info("🔘 按鍵觸發，開始聽")
            self._gate.arm()
            while self._gate.is_armed():
                time.sleep(_REARM_POLL_S)

    async def _play_cue_safely(self) -> None:
        """在背景放提示音，逾時或出錯都不影響對話。"""
        try:
            await asyncio.wait_for(self._cue(), timeout=CUE_TIMEOUT_S)
        except Exception:
            # 喇叭出問題不該讓玩偶聾掉——提示音是加分項，聽孩子講話不是。
            logger.warning("提示音發不出來，對話照常進行", exc_info=True)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Replace uplink audio with silence until the key is pressed.

        Args:
            frame: The frame flowing through the pipeline.
            direction: Frame direction.
        """
        await super().process_frame(frame, direction)
        self._ensure_waiter()
        if self._cue is not None and self._gate.take_cue():
            # **不可以 await**：提示音走 aplay 的 stdin，緩衝滿時 `drain()` 會等，
            # 而這裡是**上行路徑**——擋住它就等於玩偶聾掉（音訊照樣被讀進來，
            # 但卡在這一層進不了 VAD，從外面看就是「按了、有嗶聲、講話沒反應」）。
            # 見 test_a_hanging_cue_does_not_block_the_uplink。
            self._cue_task = asyncio.create_task(self._play_cue_safely())
        if isinstance(frame, InputAudioRawFrame):
            armed = self._gate.is_armed()
            if armed != self._was_armed:
                logger.debug(f"PressToTalk {'開啟' if armed else '關閉'}上行")
                self._was_armed = armed
            if not armed:
                self._muted_frames += 1
                frame = InputAudioRawFrame(
                    audio=b"\x00" * len(frame.audio),
                    sample_rate=frame.sample_rate,
                    num_channels=frame.num_channels,
                )
        await self.push_frame(frame, direction)


class PressToTalkDisarmer(FrameProcessor):
    """孩子講完就關閘門。擺在 VAD **之後**（那是 `UserStoppedSpeakingFrame` 的來源）。"""

    def __init__(self, gate: PressToTalkGate, **kwargs):
        """Initialize the disarmer.

        Args:
            gate: Shared gate instance, also held by the filter.
        """
        super().__init__(**kwargs)
        self._gate = gate

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Close the gate once the child finishes an utterance.

        Args:
            frame: The frame flowing through the pipeline.
            direction: Frame direction.
        """
        await super().process_frame(frame, direction)
        if isinstance(frame, UserStoppedSpeakingFrame):
            logger.debug("PressToTalk 孩子講完，關閉上行，等下一次按鍵")
            self._gate.disarm()
        # 只是旁觀者：吃掉 frame 會讓下游 STT 收不到分段結束訊號。
        await self.push_frame(frame, direction)
