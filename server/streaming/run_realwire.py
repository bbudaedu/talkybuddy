"""A2 real-wiring 真麥 entrypoint：把 StreamingTurnManager barge-in 迴路接上
LocalAudioTransport（裸麥/喇叭）＋真 Silero VAD，端到端跑一次真的插話。

手動驗收（spec 2026-07-10-a2-realwire）：
  1. 講一句 → 聽到回覆（二元：有/無）。
  2. 回覆播到一半插話 → 回覆在句界停下、系統轉去聽新輸入（二元：乾淨停/沒停）。

前置：pyaudio（`pipecat-ai[local]`，需系統 portaudio：sudo apt install portaudio19-dev；
      再 .venv/bin/pip install pyaudio）、SenseVoiceSmall cache、sherpa 資產。
無 AEC（留 A2-3）：外放時 TTS 可能被麥收回誤觸發 barge-in → 用耳機或 PTT-lite 規避。
"""
from __future__ import annotations

import sys
from pathlib import Path

from server.streaming.turn_manager import StreamingTurnManager
from server.streaming.batch_reply_source import BatchReplySource
from server.streaming.interruptible_tts import SherpaInterruptibleTTSService
from server.streaming.tests import sherpa_voice

_TB_ROOT = Path(__file__).resolve().parents[2]
_SENSEVOICE_CACHE = Path.home() / ".cache" / "modelscope" / "models" / "iic--SenseVoiceSmall"


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
    if not _SENSEVOICE_CACHE.is_dir():
        missing.append(f"缺 SenseVoiceSmall cache：{_SENSEVOICE_CACHE}（需先備妥）")
    if sherpa_voice._espeak_data_dir() is None:
        missing.append("缺 espeak-ng-data（install piper-tts）")
    if not sherpa_voice._ONNX.exists():
        missing.append(f"缺 sherpa zh onnx：{sherpa_voice._ONNX}")
    return missing


def build_processors(transport) -> list:
    """組裝 processor 列表（不啟動裝置）。transport 需提供 input()/output()。"""
    from pipecat.services.funasr.stt import FunASRSTTService

    stt = FunASRSTTService()
    manager = StreamingTurnManager(BatchReplySource())
    tts = SherpaInterruptibleTTSService()
    return [transport.input(), stt, manager, tts, transport.output()]


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
