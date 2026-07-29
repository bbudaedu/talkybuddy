# -*- coding: utf-8 -*-
"""audio_io.py — 邊緣裝置 ALSA 擷取/播放統一介面（D-04，ELOOP-01）。

預設路徑：arecord/aplay 子行程（零 pip 編譯風險，符合裝置無 gcc/cmake 現況，
見 edge/runtime/provision_device.sh）。sounddevice 僅作為授權升級路徑：只有
在裝置上已安裝且可用時才會自動採用；import 失敗（未安裝）或缺 PortAudio
（OSError）一律靜默降級回 arecord/aplay、不拋例外，比照
server/cloud_llm.py::CloudLLM.available() 的 try/except Exception 降級
idiom。預設不安裝任何新套件；若確需 sounddevice，須先過 blocking-human
套件核可（見 08-03-PLAN.md threat_model T-08-SC）。

輸出格式契約：capture_16k_mono_wav() 必須回傳 16kHz mono S16_LE、帶
RIFF/WAVE header 的 WAV bytes，直接命中 server/pipeline.py 的 RIFF-sniff
fast path（soundfile 直讀，不經任何外部轉檔子行程）；規格不符會在 edge
profile 觸發 WavSpecMismatchError（見 server/pipeline.py::_webm_to_wav）。
此模組刻意不呼叫任何外部音訊轉檔工具（見 edge/runtime/provision_device.sh
「不裝轉檔工具」一節，裝置端亦未安裝）。

子行程一律以固定 argv 串列呼叫，絕不使用 shell 字串插值模式，避免命令注入
（見 08-03-PLAN.md threat_model T-08-03）。
"""

from __future__ import annotations

import logging
import os
import struct
import subprocess
import tempfile
import time

_log = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_CHANNELS = 1
_SAMPLE_FMT = "S16_LE"

# ALSA 裝置名稱可覆寫（多音效卡/測試環境）；預設 "default"。
_ARECORD_DEVICE = os.environ.get("TALKYBUDDY_EDGE_ALSA_DEVICE", "default")


def _import_sounddevice():
    """嘗試載入 sounddevice；不可用（未安裝/缺 PortAudio）一律回 None，不拋。

    sounddevice 僅作為授權升級路徑（見模組 docstring），本函式是唯一的載入
    邊界——任何呼叫端都不得直接 `import sounddevice`，一律經此函式降級。
    """
    try:
        import sounddevice as sd

        return sd
    except Exception:
        # ImportError（未安裝）或 OSError（缺 libportaudio2）皆視為不可用
        return None


def _capture_with_arecord(seconds: float) -> bytes:
    """呼叫 arecord 子行程錄音，回傳 WAV bytes（固定 argv 串列，非 shell 字串）。"""
    fd, out_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        argv = [
            "arecord",
            "-D", _ARECORD_DEVICE,
            "-f", _SAMPLE_FMT,
            "-r", str(_SAMPLE_RATE),
            "-c", str(_CHANNELS),
            "-d", str(max(1, int(round(seconds)))),
            out_path,
        ]
        subprocess.run(argv, check=True, capture_output=True, timeout=seconds + 10)
        with open(out_path, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass


def _capture_with_sounddevice(sd, seconds: float) -> bytes:
    """以 sounddevice 錄音並組成 16k mono S16_LE WAV bytes（授權升級路徑）。"""
    import io

    import soundfile as sf

    frames = int(round(seconds * _SAMPLE_RATE))
    audio = sd.rec(frames, samplerate=_SAMPLE_RATE, channels=_CHANNELS, dtype="int16")
    sd.wait()
    buf = io.BytesIO()
    sf.write(buf, audio, _SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def capture_16k_mono_wav(seconds: float = 4.0) -> bytes:
    """擷取 16kHz mono S16_LE WAV bytes（arecord 主路徑，sounddevice 為升級路徑）。

    回傳格式必須直接命中 server/pipeline.py 的 RIFF-sniff fast path。
    sounddevice 可用但錄音本身失敗時，降級回 arecord（不拋）；arecord 路徑
    本身失敗（真實硬體錯誤）則如實拋出，不靜默偽成功。
    """
    sd = _import_sounddevice()
    if sd is not None:
        try:
            return _capture_with_sounddevice(sd, seconds)
        except Exception:
            _log.exception("sounddevice 擷取失敗，降級回 arecord")
    return _capture_with_arecord(seconds)


def _play_with_aplay(wav: bytes) -> None:
    """呼叫 aplay 子行程播放 WAV bytes（固定 argv 串列，非 shell 字串）。"""
    fd, wav_path = tempfile.mkstemp(suffix=".wav")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(wav)
        subprocess.run(["aplay", wav_path], check=True, capture_output=True, timeout=30)
    finally:
        try:
            os.remove(wav_path)
        except OSError:
            pass


def _play_with_sounddevice(sd, wav: bytes) -> None:
    """以 sounddevice 播放 WAV bytes（授權升級路徑）。"""
    import io

    import soundfile as sf

    samples, sample_rate = sf.read(io.BytesIO(wav), dtype="int16", always_2d=False)
    sd.play(samples, samplerate=sample_rate)
    sd.wait()


def play_wav_bytes(wav: bytes) -> None:
    """播放 WAV bytes（aplay 主路徑，sounddevice 為升級路徑）。

    子行程/播放失敗一律不拋、只記 log——播放失敗不應讓 local_client 主迴圈
    掛掉（比照 server/cloud_llm.py 的降級 idiom）。
    """
    sd = _import_sounddevice()
    if sd is not None:
        try:
            _play_with_sounddevice(sd, wav)
            return
        except Exception:
            _log.exception("sounddevice 播放失敗，降級回 aplay")
    try:
        _play_with_aplay(wav)
    except Exception:
        _log.exception("aplay 播放失敗")


# ---------------------------------------------------------------------------
# 實體按鍵觸發（按一下玩偶就開始講話）
#
# 裝置沒有螢幕也沒有鍵盤，原本的 `input("按 Enter…")` 需要有人 SSH 進去按，
# 不是能上台的觸發方式；原生喚醒詞真人辨識率已判 NO-GO。
#
# 2026-07-29 真機探測（edge/runtime/key_probe.py）：板上 `mtk-pmic-keys` 註冊為
# /dev/input/event1，按「自訂鍵」三次都收到 **KEY_HOME(102)**。音量鍵無事件。
# 電源鍵(116)刻意不用——可能觸發關機。
# ---------------------------------------------------------------------------

_KEY_DEVICE: str = os.environ.get("TALKYBUDDY_EDGE_KEY_DEVICE", "/dev/input/event1")
_KEY_CODE: int = int(os.environ.get("TALKYBUDDY_EDGE_KEY_CODE", "102"))  # KEY_HOME

# struct input_event（64-bit）：sec, usec, type, code, value
_EV_FMT = "llHHi"
_EV_SIZE = struct.calcsize(_EV_FMT)
_EV_KEY = 0x01


def _decode_key_press(data, code: int) -> bool:
    """這批 evdev bytes 裡有沒有 `code` 的「按下」事件（value==1）。

    純函式，因為真機的阻塞式讀取沒辦法在 CI 測，但**解析錯了一樣會讓按鍵失效**。
    只認 value==1：放開(0)會讓一次按鍵觸發兩次，長按重複(2)會連續開錄音。
    半筆資料回 False 不拋——炸了整條對話迴圈就停了。
    """
    if not data:
        return False
    for off in range(0, len(data) - _EV_SIZE + 1, _EV_SIZE):
        try:
            _s, _us, etype, ecode, value = struct.unpack(
                _EV_FMT, data[off:off + _EV_SIZE]
            )
        except struct.error:
            return False
        if etype == _EV_KEY and ecode == code and value == 1:
            return True
    return False


def _key_device_usable() -> bool:
    """實體按鍵讀得到嗎（開發機沒有，要能安全退回）。"""
    return os.path.exists(_KEY_DEVICE) and os.access(_KEY_DEVICE, os.R_OK)


def _block_until_key_press() -> bool:
    """擋住直到按下觸發鍵；讀取失敗回 False 讓呼叫端退回其他觸發方式。"""
    try:
        with open(_KEY_DEVICE, "rb", buffering=0) as f:
            while True:
                data = f.read(_EV_SIZE)
                if not data:
                    return False
                if _decode_key_press(data, _KEY_CODE):
                    return True
    except Exception:
        _log.warning("讀實體按鍵失敗，退回 Enter 觸發", exc_info=True)
        return False


def wait_for_trigger() -> None:
    """擋住直到使用者要求開始錄音。

    優先用**實體按鍵**（裝置上唯一可行的方式）；讀不到時退回 Enter
    （開發機互動測試用）；連 stdin 都沒有就短暫 sleep，避免背景行程掛死。
    """
    if _key_device_usable():
        print("按一下按鍵開始錄音...", flush=True)
        if _block_until_key_press():
            return
    try:
        input("按 Enter 開始錄音...")
    except EOFError:
        time.sleep(0.1)
