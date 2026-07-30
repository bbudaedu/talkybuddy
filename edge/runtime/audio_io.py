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
import select
import struct
import subprocess
import sys
import tempfile
import time

_log = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_CHANNELS = 1
_SAMPLE_FMT = "S16_LE"

# ALSA 裝置名稱可覆寫（多音效卡/測試環境）；預設 "default"。
_ARECORD_DEVICE = os.environ.get("TALKYBUDDY_EDGE_ALSA_DEVICE", "default")
# 播放裝置。空字串＝不帶 -D（維持原行為）。
# Genio 520 上必須設 `plughw:0,0`（3.5mm Lineout）——USB 麥克風沒有播放能力，
# 而 `default` 由 /etc/asound.conf 決定、不保證是那顆。不設的話玩偶會
# 「回答了但聽不到」。
_PLAYBACK_DEVICE = os.environ.get("TALKYBUDDY_EDGE_ALSA_PLAYBACK", "")


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
        argv = ["aplay"]
        if _PLAYBACK_DEVICE:
            argv += ["-D", _PLAYBACK_DEVICE]
        argv.append(wav_path)
        subprocess.run(argv, check=True, capture_output=True, timeout=30)
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

# 節點編號不可寫死。USB 音效裝置（麥克風）插上去時也會註冊自己的 input 節點，
# 編號會位移——一旦 mtk-pmic-keys 不再是 event1，`_key_device_usable()` 的
# 「存在且可讀」對 USB 那顆一樣成立，於是迴圈會阻塞在一個永遠不會送出
# KEY_HOME 的裝置上。症狀是「印出提示、按了完全沒反應」，不是明確報錯，
# 極難從外部看出來。所以預設改成從 /proc/bus/input/devices 依**名稱**解析。
_KEY_DEVICE_ENV: str = os.environ.get("TALKYBUDDY_EDGE_KEY_DEVICE", "")
_KEY_DEVICE_FALLBACK = "/dev/input/event1"  # 2026-07-29 探測到的節點，僅作最後退路
_PROC_INPUT_DEVICES = "/proc/bus/input/devices"
_KEY_NAME_HINT: str = os.environ.get("TALKYBUDDY_EDGE_KEY_NAME", "pmic").lower()

# 觸發鍵＝KEY_POWER(116)，不是 KEY_HOME(102)。
#
# 2026-07-30 真機實測（繞過 Python、直接 `dd` 讀 evdev；並以耳機孔插拔事件當
# 對照組，證明觀測方法本身有效）：
#   - 自訂鍵 KEY_HOME(102)：按數十次、跨重開機，一律 0 bytes → 不可用
#   - KEY_POWER(116)：短按 → `EV_KEY code=116 value=1` + `EV_SYN`
# 2026-07-29 記錄的「按自訂鍵三次都收到 KEY_HOME」是錯的——kernel 位元圖確實
# 註冊了 102（已獨立驗證兩次），但註冊不等於那顆實體鍵接得上。
#
# ⚠️ 用 116 的前提：systemd-logind 必須設 `HandlePowerKey=ignore`，否則按下去
# 就是關機（見 `_power_key_guard_ok()` 與 provision_device.sh）。
_KEY_CODE: int = int(os.environ.get("TALKYBUDDY_EDGE_KEY_CODE", "116"))  # KEY_POWER

_KEY_POWER = 116
# logind 設定：主檔 + drop-in 目錄（後者優先，systemd 語意為後讀者覆寫）。
_LOGIND_CONF = "/etc/systemd/logind.conf"
_LOGIND_CONF_D = "/etc/systemd/logind.conf.d"

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


def _find_key_device_from_proc(text: str) -> str | None:
    """從 /proc/bus/input/devices 內容裡找出按鍵所在的 event 節點。

    純函式（真機的 /proc 沒辦法在 CI 造出來，但挑錯節點就等於按鍵完全失效）。
    格式每個裝置一段、段間空行，`N: Name="..."` 在 `H: Handlers=...` 之前：

        N: Name="mtk-pmic-keys"
        H: Handlers=kbd event2

    解析不出來一律回 None，讓呼叫端退回其他觸發方式，不拋。
    """
    if not text:
        return None
    name = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            name = ""  # 段落結束，別讓名稱殘留到下一段
            continue
        if line.startswith("N: Name="):
            name = line[len("N: Name="):].strip().strip('"').lower()
        elif line.startswith("H: Handlers=") and _KEY_NAME_HINT and _KEY_NAME_HINT in name:
            for token in line[len("H: Handlers="):].split():
                if token.startswith("event"):
                    return "/dev/input/" + token
    return None


def _resolve_key_device() -> str:
    """決定要讀哪個 input 節點。

    優先序：環境變數明示 > 依名稱自動偵測 > 2026-07-29 探測到的 event1。
    自動偵測是為了吸收 USB 麥克風插拔造成的節點位移（見上方常數註解）。
    """
    if _KEY_DEVICE_ENV:
        return _KEY_DEVICE_ENV
    try:
        with open(_PROC_INPUT_DEVICES, "r", encoding="utf-8", errors="replace") as f:
            found = _find_key_device_from_proc(f.read())
    except OSError:
        found = None
    return found or _KEY_DEVICE_FALLBACK


def _parse_handle_power_key(text: str) -> str | None:
    """從 logind 設定內容取出 `HandlePowerKey` 的值；沒設定回 None。

    純函式。註解行不算生效（Yocto 的 logind.conf 預設整份都是註解，
    此時 logind 走內建預設 `poweroff`——也就是按下去會關機）。
    同檔重複設定以最後一筆為準（systemd 語意）。
    """
    value: str | None = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key.strip() == "HandlePowerKey":
            value = val.strip() or None
    return value


def _effective_handle_power_key() -> str:
    """logind 實際會怎麼處理 power 鍵。

    讀主檔與 drop-in（drop-in 後讀、覆寫主檔）。都沒設定時回 logind 的內建
    預設 `poweroff`——**不是** `ignore`，這個差別就是「按下去會不會關機」。
    """
    texts: list[str] = []
    try:
        with open(_LOGIND_CONF, "r", encoding="utf-8", errors="replace") as f:
            texts.append(f.read())
    except OSError:
        pass
    try:
        for name in sorted(os.listdir(_LOGIND_CONF_D)):
            if not name.endswith(".conf"):
                continue
            try:
                with open(os.path.join(_LOGIND_CONF_D, name), "r",
                          encoding="utf-8", errors="replace") as f:
                    texts.append(f.read())
            except OSError:
                continue
    except OSError:
        pass

    effective = None
    for text in texts:
        found = _parse_handle_power_key(text)
        if found is not None:
            effective = found
    return effective or "poweroff"  # logind 內建預設


def _power_key_guard_ok() -> bool:
    """用 KEY_POWER 當觸發鍵時，確認 logind 已放手；否則大聲警告。

    不阻止啟動（設定方式不只一種，也可能在別處被覆寫），但**絕不靜默**——
    靜默的後果是有人按下玩偶就直接斷電。
    """
    if _KEY_CODE != _KEY_POWER:
        return True
    handling = _effective_handle_power_key()
    if handling == "ignore":
        return True
    print(
        f"⚠️ 觸發鍵是 KEY_POWER(116)，但 systemd-logind 的 HandlePowerKey="
        f"{handling!r}——現在按下去會**關機**，不是開始錄音。\n"
        f"   修法（撐得過重開機）：\n"
        f"     mkdir -p {_LOGIND_CONF_D}\n"
        f"     printf '[Login]\\nHandlePowerKey=ignore\\n"
        f"HandlePowerKeyLongPress=ignore\\n' > "
        f"{_LOGIND_CONF_D}/10-talkybuddy-powerkey.conf\n"
        f"     systemctl restart systemd-logind",
        flush=True,
    )
    return False


def _key_device_usable() -> bool:
    """實體按鍵讀得到嗎（開發機沒有，要能安全退回）。"""
    path = _resolve_key_device()
    return os.path.exists(path) and os.access(path, os.R_OK)


def _block_until_key_press() -> bool:
    """擋住直到按下觸發鍵**或**（互動時）使用者按下 Enter。

    同時監看 stdin 是為了防死鎖：實體按鍵若因硬體或節點問題永遠不送事件，
    只讀按鍵會無限阻塞，呼叫端寫好的 Enter 降級永遠走不到——2026-07-30
    「卡在自訂按鍵」就是這個死鎖，而不是單純「按鍵沒反應」。

    只在 stdin 是 TTY 時才監看它：非 TTY 的 stdin 恆為 ready，加進 select
    會讓迴圈空轉燒 CPU，而裝置上 local_client 正是以背景行程執行。
    """
    try:
        with open(_resolve_key_device(), "rb", buffering=0) as f:
            watch = [f]
            stdin = sys.stdin
            try:
                watch_stdin = stdin is not None and stdin.isatty()
            except Exception:
                watch_stdin = False
            if watch_stdin:
                watch.append(stdin)

            while True:
                ready, _, _ = select.select(watch, [], [])
                if watch_stdin and stdin in ready:
                    stdin.readline()  # 吃掉那一行，Enter 也算觸發
                    return True
                if f in ready:
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
        # 用 KEY_POWER 當觸發鍵時先確認 logind 已放手，否則按下去是關機而非錄音。
        _power_key_guard_ok()
        # 印出實際挑到的節點與鍵碼：挑錯了按下去就沒反應，而從外面看只是「卡住」。
        print(f"按一下按鍵開始錄音...（讀 {_resolve_key_device()}，鍵碼 {_KEY_CODE}）", flush=True)
        if _block_until_key_press():
            return
    try:
        input("按 Enter 開始錄音...")
    except EOFError:
        time.sleep(0.1)
