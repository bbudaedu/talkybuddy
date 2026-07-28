"""A2 real-wiring 真麥 entrypoint：把 StreamingTurnManager barge-in 迴路接上
LocalAudioTransport（裸麥/喇叭）＋真 Silero VAD，端到端跑一次真的插話。

鏈路：transport.input() → BargeInGate → STT → StreamingTurnManager → TTS → transport.output()
BargeInGate 排在 STT **之前**：直接吃 transport 的原始麥克風音訊，不依賴 STT 是否
轉發 InputAudioRawFrame（2026-07-29 實測 FunASR 目前原封轉發，但那是實作細節非契約）。

手動驗收（spec 2026-07-10-a2-realwire）：
  1. 講一句 → 聽到回覆（二元：有/無）。
  2. 回覆播到一半插話 → 回覆在句界停下、系統轉去聽新輸入（二元：乾淨停/沒停）。
     ← 2026-07-29 起真的成立：BargeInGate 已接上鏈（此前漏接，第 2 條必然失敗）。
        自動化證據：tests/test_run_realwire.py::test_realwire_chain_bargein_end_to_end

前置：pyaudio（`pipecat-ai[local]`，需系統 portaudio：sudo apt install portaudio19-dev；
      再 .venv/bin/pip install pyaudio）、SenseVoiceSmall cache、sherpa 資產。
無 AEC（留 A2-3）：外放時 TTS 可能被麥收回誤觸發 barge-in → 用耳機或 PTT-lite 規避。
"""
from __future__ import annotations

import sys
from pathlib import Path

from server.streaming.barge_in_gate import BargeInGate
from server.streaming.turn_manager import StreamingTurnManager
from server.streaming.batch_reply_source import BatchReplySource
from server.streaming.interruptible_tts import SherpaInterruptibleTTSService
from server.streaming.tests import sherpa_voice

_TB_ROOT = Path(__file__).resolve().parents[2]
_SENSEVOICE_CACHE = Path.home() / ".cache" / "modelscope" / "models" / "iic--SenseVoiceSmall"


def _has_audio_input_device() -> bool | None:
    """有沒有可錄音的裝置。True/False＝判定結果；None＝pyaudio 不在，無從判定。

    只看 PortAudio 有沒有列出任何 maxInputChannels > 0 的裝置，不試開串流
    （試開會動到硬體，前置檢查不該有副作用）。任何例外一律吞掉回 False：
    這支函式的職責是回報缺項，不是炸掉。
    """
    try:
        import pyaudio
    except Exception:
        return None
    try:
        pa = pyaudio.PyAudio()
    except Exception:
        return False
    try:
        return any(
            (pa.get_device_info_by_index(i).get("maxInputChannels") or 0) > 0
            for i in range(pa.get_device_count())
        )
    except Exception:
        return False
    finally:
        try:
            pa.terminate()
        except Exception:
            pass


def check_prerequisites() -> list[str]:
    """回傳缺項的可讀訊息（空 list＝就緒）。不 raise。"""
    missing: list[str] = []
    try:
        import pyaudio  # noqa: F401
    except Exception:
        missing.append(
            "缺 pyaudio（真麥/喇叭需要）：sudo apt install portaudio19-dev；"
            "再 .venv/bin/pip install pyaudio"
        )
    else:
        # pyaudio 裝了不等於有硬體。少了這一關，本函式會回報「全就緒」，卻要等
        # pipeline 起來才撞 PortAudio [Errno -9996]——而那是 non-fatal ErrorFrame，
        # 行程不退出、只是無聲掛住（2026-07-29 於無音效卡的開發機實測 200s 不退出）。
        if _has_audio_input_device() is False:
            missing.append(
                "無可用的錄音裝置（PortAudio 找不到任何 maxInputChannels>0 的裝置）："
                "請接上麥克風/USB 耳麥後重試；用 `ls /dev/snd/` 應看到 pcmC*D*c 節點"
            )
    if not _SENSEVOICE_CACHE.is_dir():
        missing.append(f"缺 SenseVoiceSmall cache：{_SENSEVOICE_CACHE}（需先備妥）")
    if sherpa_voice._espeak_data_dir() is None:
        missing.append("缺 espeak-ng-data（install piper-tts）")
    if not sherpa_voice._ONNX.exists():
        missing.append(f"缺 sherpa zh onnx：{sherpa_voice._ONNX}")
    return missing


def build_processors(transport, reply_source=None) -> list:
    """組裝 processor 列表（不啟動裝置）。transport 需提供 input()/output()。

    reply_source：可注入的回覆句流（預設 BatchReplySource＝真大腦）。沿用
    StreamingTurnManager / BatchReplySource 既有的 DI 風格，讓端到端測試能用
    固定句數的 StubReplySource 做確定性的「句界乾淨停」判定。
    """
    from pipecat.services.funasr.stt import FunASRSTTService

    stt = FunASRSTTService()
    gate = BargeInGate()
    manager = StreamingTurnManager(reply_source if reply_source is not None else BatchReplySource())
    tts = SherpaInterruptibleTTSService()
    return [transport.input(), gate, stt, manager, tts, transport.output()]


def _build_transport():
    """建 LocalAudioTransport（真裝置）；import/裝置失敗會 raise，由 main() 捕捉。"""
    from pipecat.transports.local.audio import (
        LocalAudioTransport,
        LocalAudioTransportParams,
    )
    from pipecat.audio.vad.silero import SileroVADAnalyzer

    params = LocalAudioTransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        vad_analyzer=SileroVADAnalyzer(),
    )
    return LocalAudioTransport(params)


async def _run() -> int:
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.task import PipelineTask
    from pipecat.pipeline.runner import PipelineRunner

    transport = _build_transport()
    task = PipelineTask(Pipeline(build_processors(transport)))
    print("[run_realwire] 就緒：對麥克風講話；回覆播放中再開口即測 barge-in。Ctrl-C 結束。")
    await PipelineRunner().run(task)
    return 0


def main() -> int:
    missing = check_prerequisites()
    if missing:
        print("[run_realwire] 無法啟動，缺少前置：", file=sys.stderr)
        for m in missing:
            print("  - " + m, file=sys.stderr)
        return 1
    import asyncio

    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        print("\n[run_realwire] 已結束。")
        return 0
    except Exception as exc:  # 真裝置/pyaudio 執行期錯誤 → 明確訊息、非 traceback
        print(f"[run_realwire] 啟動音訊裝置失敗：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
