# -*- coding: utf-8 -*-
"""alsa_transport.py — 用 arecord/aplay 子行程實作的 pipecat transport。

取代官方 `LocalAudioTransport`（需要 pyaudio，本板無 gcc 裝不了，見套件 docstring）。

## 沿用而非重寫 ALSA 參數

上下行的 argv 一律向 `live_client` 借（`build_arecord_argv` / `build_aplay_argv`），
不在這裡另寫一份。那裡的 `--buffer-time` 是 2026-07-30 的實機教訓（預設緩衝
吸收不了下行抖動，聽感就是斷斷續續）；兩邊都必須 `-t raw`，因為 WAV header
只在檔案開頭出現一次，串流送出去會讓對端把 header bytes 當成音訊取樣。

## 取樣率一定要用 pipeline 協商的值

`live_client` 的預設是 Nova Sonic 的 16k/24k，**但這裡不能吃那個預設**。
2026-07-31 真機實測：邊緣 TTS 輸出 22050Hz，而 aplay 被以 24000 啟動，
播放速度快 8.8%、音調偏高。**aplay 不會因為取樣率對不上而報錯**——
單元測試也看不出來（argv 組得「成功」），只有真的聽到怪腔怪調才會發現。

所以 `start()` 一律把 `self._sample_rate`（來自 `StartFrame` 協商）傳進 argv builder。

## stderr 一律不吞

arecord 起不來（裝置被佔用、名稱打錯）時，唯一的線索就在 stderr。2026-07-30
的事故裡，吞掉 stderr 讓「麥克風被佔用」看起來跟「麥克風壞掉」一模一樣。

## 為什麼用 asyncio subprocess 而非 Popen + 執行緒

`live_client` 用 `Popen` 是因為它自己管迴圈；pipecat 是 asyncio 世界，
`asyncio.create_subprocess_exec` 讓讀 stdout 直接是 awaitable，不必再包一層
執行緒與 `run_coroutine_threadsafe`。argv 完全相同，ALSA 行為不受影響。
"""

from __future__ import annotations

import asyncio
import time

from loguru import logger
from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame, StartFrame
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import BaseTransport, TransportParams

from edge.runtime.live_client import build_aplay_argv, build_arecord_argv

# 每次從 arecord stdout 讀出的位元組數。16k×2 bytes = 32000 B/s，
# 640 bytes = 20ms，與 pipecat 內部 VAD 的分析窗大小同量級。
_READ_CHUNK_BYTES = 640


class AlsaTransportParams(TransportParams):
    """ALSA transport 參數。

    Parameters:
        input_device: arecord 的 `-D` 值；空字串表示不帶 `-D`（用 ALSA 預設）。
        output_device: aplay 的 `-D` 值。Genio 520 上播放必須是 `plughw:0,0`
            （3.5mm Lineout）——USB 麥克風沒有播放能力，見 `audio_io.py`。
        read_chunk_bytes: 每次自 arecord 讀取的位元組數。
    """

    input_device: str = ""
    output_device: str = ""
    read_chunk_bytes: int = _READ_CHUNK_BYTES
    keepalive_interval_s: float = 0.1
    keepalive_enabled: bool = True


class AlsaInputTransport(BaseInputTransport):
    """arecord 子行程 → `InputAudioRawFrame`。"""

    _params: AlsaTransportParams

    def __init__(self, params: AlsaTransportParams, **kwargs):
        """Initialize the ALSA input transport.

        Args:
            params: Transport configuration parameters.
        """
        super().__init__(params, **kwargs)
        self._proc: asyncio.subprocess.Process | None = None
        self._read_task: asyncio.Task | None = None
        self._sample_rate = 0

    async def start(self, frame: StartFrame):
        """Spawn arecord and begin pushing audio frames.

        Args:
            frame: The start frame carrying negotiated sample rates.
        """
        await super().start(frame)

        if self._proc:
            return

        self._sample_rate = self._params.audio_in_sample_rate or frame.audio_in_sample_rate

        # 取樣率一律用 pipeline 協商的值，不吃 live_client 的預設——
        # 餵錯取樣率 arecord 不會報錯，只會讓音調與速度跑掉。
        argv = build_arecord_argv(self._params.input_device, self._sample_rate)
        logger.debug(f"AlsaInputTransport 啟動：{' '.join(argv)}")
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            # stderr 不吞：arecord 起不來時唯一的線索在這裡（見模組 docstring）。
            stderr=None,
        )
        self._read_task = self.create_task(self._read_handler())

        await self.set_transport_ready(frame)

    async def stop(self, frame):
        """Stop reading and terminate arecord.

        Args:
            frame: The end frame.
        """
        await super().stop(frame)
        await self._teardown()

    async def cleanup(self):
        """Terminate arecord and release the microphone."""
        await super().cleanup()
        await self._teardown()

    async def _teardown(self):
        """終止讀取 task 與 arecord 子行程（可重入）。"""
        if self._read_task:
            await self.cancel_task(self._read_task)
            self._read_task = None
        if self._proc:
            if self._proc.returncode is None:
                self._proc.terminate()
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    # terminate 沒收掉就 kill——麥克風不釋放，下一個 client
                    # 會看到「裝置被佔用」，症狀跟麥克風壞掉一樣（2026-07-30 事故）。
                    self._proc.kill()
                    await self._proc.wait()
            self._proc = None

    async def _read_handler(self):
        """持續自 arecord stdout 讀 raw PCM 並推進 pipeline。"""
        assert self._proc is not None and self._proc.stdout is not None
        chunk_size = self._params.read_chunk_bytes
        while True:
            # readexactly 在子行程結束時拋 IncompleteReadError，正是停止訊號。
            try:
                data = await self._proc.stdout.readexactly(chunk_size)
            except asyncio.IncompleteReadError as e:
                if e.partial:
                    await self.push_audio_frame(
                        InputAudioRawFrame(
                            audio=e.partial,
                            sample_rate=self._sample_rate,
                            num_channels=self._params.audio_in_channels,
                        )
                    )
                logger.warning("arecord stdout 結束——麥克風串流中斷")
                break
            await self.push_audio_frame(
                InputAudioRawFrame(
                    audio=data,
                    sample_rate=self._sample_rate,
                    num_channels=self._params.audio_in_channels,
                )
            )


class AlsaOutputTransport(BaseOutputTransport):
    """`OutputAudioRawFrame` → aplay 子行程 stdin。"""

    _params: AlsaTransportParams

    def __init__(self, params: AlsaTransportParams, **kwargs):
        """Initialize the ALSA output transport.

        Args:
            params: Transport configuration parameters.
        """
        super().__init__(params, **kwargs)
        self._proc: asyncio.subprocess.Process | None = None
        self._sample_rate = 0
        self._keepalive_task: asyncio.Task | None = None
        self._last_write: float = 0.0

    async def start(self, frame: StartFrame):
        """Spawn aplay ready to receive raw PCM on stdin.

        Args:
            frame: The start frame carrying negotiated sample rates.
        """
        await super().start(frame)

        if self._proc:
            return

        self._sample_rate = self._params.audio_out_sample_rate or frame.audio_out_sample_rate

        # 同上：2026-07-31 真機實測，寫死 24000 播 22050Hz 的 TTS 音訊，
        # 速度會快 8.8%、音調偏高，而 aplay 一句警告都不會給。
        argv = build_aplay_argv(self._params.output_device, self._sample_rate)
        logger.debug(f"AlsaOutputTransport 啟動：{' '.join(argv)}")
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stderr=None,
        )

        self._last_write = time.monotonic()
        if self._params.keepalive_enabled:
            self._keepalive_task = self.create_task(self._keepalive_handler())

        await self.set_transport_ready(frame)

    async def _keepalive_handler(self):
        """空檔餵靜音，避免 aplay 緩衝空轉。

        **為什麼需要**：玩偶等 LLM 的那幾秒沒有音訊寫入，ALSA 環形緩衝就會
        underrun。2026-07-31 真人實測量到 8.9s / 17.0s / **41.3s** 三次。

        **真正的代價不只是聽感**：`--buffer-time` 之所以調到 2 秒就是為了吸收
        這種空檔，而那 2 秒直接變成 `PlaybackGate` 的死區——玩偶講完後上行要
        再聾 2.6 秒（2.0 緩衝 + 0.6 tail），孩子話音剛落就講會被吃掉開頭
        （實測：辨識從「三句全對」變成全錯）。

        有了 keepalive 撐住空檔，緩衝就能調小，死區才跟著縮短。
        這是記憶 `project-edge-s2s-tuning` 早就寫下的「未做的優化」。
        """
        interval = self._params.keepalive_interval_s
        silence = b"\x00" * int(self._sample_rate * 2 * interval)
        while True:
            await asyncio.sleep(interval)
            if not self._proc or not self._proc.stdin:
                continue
            # 只在真的沒人寫入時補：正常播放期間不要插靜音進去。
            if time.monotonic() - self._last_write < interval:
                continue
            try:
                self._proc.stdin.write(silence)
                await self._proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                return

    async def stop(self, frame):
        """Flush and terminate aplay.

        Args:
            frame: The end frame.
        """
        await super().stop(frame)
        await self._teardown()

    async def cleanup(self):
        """Terminate aplay and release the playback device."""
        await super().cleanup()
        await self._teardown()

    async def _teardown(self):
        """關閉 stdin 讓 aplay 播完緩衝後自然結束；逾時才強制收掉。"""
        if self._keepalive_task:
            await self.cancel_task(self._keepalive_task)
            self._keepalive_task = None
        if not self._proc:
            return
        if self._proc.stdin and not self._proc.stdin.is_closing():
            self._proc.stdin.close()
        if self._proc.returncode is None:
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        self._proc = None

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        """Write raw PCM to aplay's stdin.

        Args:
            frame: The audio frame to play.

        Returns:
            True if the bytes were handed to aplay, False if aplay is not running.
        """
        if not self._proc or not self._proc.stdin:
            return False
        try:
            self._last_write = time.monotonic()
            self._proc.stdin.write(frame.audio)
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            # aplay 掛了（裝置被拔、被佔用）。回 False 讓上層知道，不拋，
            # 對話不該因為喇叭出問題而整個炸掉。
            logger.warning("aplay stdin 已斷——播放子行程可能已結束")
            return False
        return True


class AlsaTransport(BaseTransport):
    """組合 arecord 輸入與 aplay 輸出的完整 transport。"""

    def __init__(self, params: AlsaTransportParams, **kwargs):
        """Initialize the ALSA transport.

        Args:
            params: Transport configuration parameters.
        """
        super().__init__(**kwargs)
        self._params = params
        self._input: AlsaInputTransport | None = None
        self._output: AlsaOutputTransport | None = None

    def input(self) -> FrameProcessor:
        """Get the input processor (lazily constructed).

        Returns:
            The arecord-backed input transport.
        """
        if not self._input:
            self._input = AlsaInputTransport(self._params, name=self._input_name)
        return self._input

    def output(self) -> FrameProcessor:
        """Get the output processor (lazily constructed).

        Returns:
            The aplay-backed output transport.
        """
        if not self._output:
            self._output = AlsaOutputTransport(self._params, name=self._output_name)
        return self._output
