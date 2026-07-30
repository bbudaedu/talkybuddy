# -*- coding: utf-8 -*-
"""preflight.py — 開機後一行跑完所有前置檢查，把「又 debug 半天」變成看一張表。

**為什麼要有這支**：2026-07-30 一整輪除錯下來，真正花掉時間的都不是難題，
而是「有沒有做某個前置動作」無法一眼確認：

- USB 麥克風的實體靜音鍵重開機後回到靜音，而且**軟體偵測不到也控制不了**——
  唯一驗法是真的錄一段來看訊號。
- 觸發鍵是 KEY_POWER(116)，但前提是 systemd-logind 設了 HandlePowerKey=ignore；
  沒設的話按下玩偶是**關機**。
- ALSA 播放/錄音裝置若沒明示，會走 /etc/asound.conf 的 default，症狀是
  「回答了但聽不到」，而 aplay 還回 0、不報錯。
- 板上「自訂鍵」KEY_HOME(102) 註冊了卻不送任何事件（實測 0 bytes），
  誤按它會以為按鍵壞了。

所以這支不做任何修復，只回答一個問題：**現在能不能直接開始 demo？**
不能的話，逐項給出該執行的指令。

用法（裝置上）：

    cd /root/talkybuddy
    ./.venv/bin/python -m edge.runtime.preflight          # 不含收音測試（快）
    ./.venv/bin/python -m edge.runtime.preflight --mic    # 含 3 秒收音測試

`--mic` 會實際錄 3 秒並判讀訊號，是唯一能抓出「靜音鍵沒按」的檢查，
**演練/demo 前務必跑一次**。判讀門檻沿用 NETCUT_REHEARSAL_CHECKLIST 步驟 0：
peak > 0.05 且人聲頻段（0.5–3kHz）占比 > 25%——只看 peak 會誤判，曾有
peak=0.109 但 98% 能量在 500Hz 以下，其實是噪音不是人聲。

判讀邏輯抽成純函式（`evaluate_*`）以便單元測試：真機狀態沒辦法在 CI 造出來，
但判錯門檻一樣會讓演練前的檢查失去意義。
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import wave

# 檢查結果的三態。WARN = 能 demo 但不理想；FAIL = 現在不能 demo。
OK, WARN, FAIL = "OK", "WARN", "FAIL"

# 收音判讀門檻（見模組 docstring）
MIC_PEAK_MIN = 0.05
MIC_VOICE_BAND_MIN = 0.25

# 板上唯一可用的實體觸發鍵（2026-07-30 實測；KEY_HOME/102 不送事件）
EXPECTED_KEY_CODE = 116


# ---------------------------------------------------------------------------
# 純函式：判讀邏輯（可單元測試）
# ---------------------------------------------------------------------------

def evaluate_power_key_guard(handle_power_key: str, key_code: int) -> tuple[str, str]:
    """觸發鍵設定安全嗎。

    只有在觸發鍵真的是 KEY_POWER 時，logind 的設定才有意義。
    """
    if key_code != EXPECTED_KEY_CODE:
        return (
            WARN,
            f"觸發鍵是 {key_code}，不是實測可用的 {EXPECTED_KEY_CODE}"
            f"（KEY_HOME/102 在本板不送任何事件）",
        )
    if handle_power_key != "ignore":
        return (
            FAIL,
            f"logind HandlePowerKey={handle_power_key!r}——現在按下玩偶會**關機**。"
            f"修法：./edge/deploy/install_services.sh（或見 edge/runtime/README.md）",
        )
    return OK, "KEY_POWER(116) 短按，logind 已放手"


def evaluate_alsa_devices(service_env: dict) -> tuple[str, str]:
    """跑 local_client 的 service 有沒有明示 ALSA 錄音/播放裝置。

    必須看 **service unit 的設定**，不是本行程的環境變數——實際跑對話迴圈的是
    systemd service，preflight 只是旁觀者。看錯對象會產生假警告，而假警告會
    讓人開始忽略真警告。

    兩個裝置都不能靠 ALSA 的 `default`：它由 /etc/asound.conf 決定，不保證是
    已驗證可用的那兩顆（錄音＝USB 麥克風 card 1、播放＝3.5mm Lineout card 0）。
    播放走錯的症狀是「回答了但聽不到」，而且 aplay 還回 0、不報錯。
    """
    capture = service_env.get("TALKYBUDDY_EDGE_ALSA_DEVICE", "")
    playback = service_env.get("TALKYBUDDY_EDGE_ALSA_PLAYBACK", "")
    missing = []
    if not capture:
        missing.append("錄音（TALKYBUDDY_EDGE_ALSA_DEVICE，應為 plughw:1,0）")
    if not playback:
        missing.append("播放（TALKYBUDDY_EDGE_ALSA_PLAYBACK，應為 plughw:0,0）")
    if missing:
        return (
            FAIL,
            "service 未明示 " + "、".join(missing)
            + "；重裝 unit：./edge/deploy/install_services.sh",
        )
    return OK, f"錄音 {capture}／播放 {playback}"


def evaluate_mic(peak: float, voice_band_ratio: float) -> tuple[str, str]:
    """收音訊號夠不夠。

    peak 過低幾乎都是 USB 麥克風的實體靜音鍵沒按（軟體偵測不到）。
    人聲頻段占比是為了擋掉「音量夠但全是低頻噪音」的假通過。
    """
    if peak < MIC_PEAK_MIN:
        return (
            FAIL,
            f"peak={peak:.3f} < {MIC_PEAK_MIN}——幾乎可以確定是"
            f"**USB 麥克風的實體靜音鍵沒按**（重開機後會回到靜音，軟體控制不了）",
        )
    if voice_band_ratio < MIC_VOICE_BAND_MIN:
        return (
            WARN,
            f"peak={peak:.3f} 夠，但人聲頻段只占 {voice_band_ratio:.0%}"
            f"（需 >{MIC_VOICE_BAND_MIN:.0%}）——錄到的可能是噪音而非人聲",
        )
    return OK, f"peak={peak:.3f}，人聲頻段 {voice_band_ratio:.0%}"


def evaluate_status(status: dict) -> tuple[str, str]:
    """/api/status 的必要能力是否就緒。"""
    missing = [k for k in ("asr", "llm", "tts") if not status.get(k)]
    if missing:
        return FAIL, f"這些能力不可用：{', '.join(missing)}"
    notes = []
    if not status.get("cloud_tts"):
        notes.append("cloud_tts=false（缺 ELEVENLABS_API_KEY；純離線 demo 不需要）")
    if status.get("network_mode") != "cloud":
        notes.append(f"network_mode={status.get('network_mode')!r}（斷網演練起點需切 cloud）")
    if notes:
        return WARN, "asr/llm/tts 就緒；" + "；".join(notes)
    return OK, "asr/llm/tts 就緒"


def evaluate_memory(available_mb: int) -> tuple[str, str]:
    """記憶體還夠不夠跑一輪（llama-server + ASR + TTS）。"""
    if available_mb < 400:
        return FAIL, f"可用僅 {available_mb}MB，隨時可能 OOM"
    if available_mb < 800:
        return WARN, f"可用 {available_mb}MB，偏緊"
    return OK, f"可用 {available_mb}MB"


def format_report(rows: list[tuple[str, str, str]]) -> str:
    """把 (項目, 狀態, 說明) 排成對齊的表格。純函式、無 I/O。"""
    if not rows:
        return "（無檢查項目）"
    mark = {OK: "✅", WARN: "⚠️ ", FAIL: "❌"}
    width = max(len(name) for name, _, _ in rows)
    lines = []
    for name, state, detail in rows:
        lines.append(f"{mark.get(state, '? ')} {name.ljust(width)}  {detail}")
    return "\n".join(lines)


def overall_verdict(rows: list[tuple[str, str, str]]) -> tuple[str, str]:
    """整體結論。有任何 FAIL 就是不能 demo。"""
    fails = [n for n, s, _ in rows if s == FAIL]
    warns = [n for n, s, _ in rows if s == WARN]
    if fails:
        return FAIL, f"現在不能 demo，先處理：{', '.join(fails)}"
    if warns:
        return WARN, f"可以 demo，但注意：{', '.join(warns)}"
    return OK, "全部就緒，可以直接 demo"


# ---------------------------------------------------------------------------
# 以下觸及真機狀態（子行程 / 檔案 / 網路），不進單元測試
# ---------------------------------------------------------------------------

def _run(argv: list[str], timeout: float = 10.0) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, timeout=timeout, text=True)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as exc:
        return 1, f"{type(exc).__name__}: {exc}"


def _check_services() -> list[tuple[str, str, str]]:
    rows = []
    for unit in ("talkybuddy-server", "talkybuddy-local-client"):
        rc, out = _run(["systemctl", "is-active", unit])
        active = out.strip() == "active"
        rc2, out2 = _run(["systemctl", "is-enabled", unit])
        enabled = out2.strip() == "enabled"
        if active and enabled:
            rows.append((unit, OK, "active + 開機自啟"))
        elif active:
            rows.append((unit, WARN, "active 但**未** enable，重開機不會自動起來"))
        else:
            rows.append((unit, FAIL, f"不是 active（{out.strip()}）："
                                     f"systemctl start {unit}"))
    return rows


def _service_environment(unit: str) -> dict:
    """讀 service unit 實際生效的 Environment=（不是本行程的環境變數）。"""
    rc, out = _run(["systemctl", "show", unit, "-p", "Environment", "--value"])
    env: dict = {}
    if rc != 0:
        return env
    for token in out.split():
        if "=" in token:
            key, _, value = token.partition("=")
            env[key.strip()] = value.strip()
    return env


def _effective_handle_power_key() -> str:
    rc, out = _run(["busctl", "get-property", "org.freedesktop.login1",
                    "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
                    "HandlePowerKey"])
    if rc == 0 and '"' in out:
        return out.split('"')[1]
    from edge.runtime import audio_io
    return audio_io._effective_handle_power_key()


def _check_status() -> tuple[str, str, dict]:
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:8787/api/status", timeout=8) as r:
            status = json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        return FAIL, f"取不到 /api/status（{type(exc).__name__}）：systemctl status talkybuddy-server", {}
    state, detail = evaluate_status(status)
    return state, detail, status


def _check_memory() -> tuple[str, str]:
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            info = {}
            for line in f:
                k, _, v = line.partition(":")
                info[k.strip()] = v.strip()
        available_mb = int(info["MemAvailable"].split()[0]) // 1024
    except Exception as exc:
        return WARN, f"讀不到 /proc/meminfo（{type(exc).__name__}）"
    return evaluate_memory(available_mb)


def _voice_band_ratio(samples: list[int], rate: int) -> tuple[float, float]:
    """回 (peak, 人聲頻段能量占比)。用 numpy FFT；不可用時占比回 -1。"""
    if not samples:
        return 0.0, -1.0
    peak = max(abs(s) for s in samples) / 32768.0
    try:
        import numpy as np
    except Exception:
        return peak, -1.0
    arr = np.asarray(samples, dtype=np.float64)
    spec = np.abs(np.fft.rfft(arr)) ** 2
    freqs = np.fft.rfftfreq(len(arr), d=1.0 / rate)
    total = float(spec.sum())
    if total <= 0:
        return peak, 0.0
    band = float(spec[(freqs >= 500) & (freqs <= 3000)].sum())
    return peak, band / total


def _check_mic() -> tuple[str, str]:
    from edge.runtime import audio_io
    device = audio_io._ARECORD_DEVICE
    print(f"    錄音 3 秒（裝置 {device}）——請現在說話...", flush=True)
    try:
        wav = audio_io.capture_16k_mono_wav(3.0)
    except Exception as exc:
        return FAIL, f"錄音失敗（{type(exc).__name__}: {exc}）"

    import io
    try:
        with wave.open(io.BytesIO(wav), "rb") as w:
            rate = w.getframerate()
            frames = w.readframes(w.getnframes())
        samples = list(struct.unpack(f"<{len(frames) // 2}h", frames[: len(frames) // 2 * 2]))
    except Exception as exc:
        return FAIL, f"WAV 解析失敗（{type(exc).__name__}）"

    peak, ratio = _voice_band_ratio(samples, rate)
    if ratio < 0:
        return (OK if peak >= MIC_PEAK_MIN else FAIL,
                f"peak={peak:.3f}（無 numpy，未判頻段）")
    return evaluate_mic(peak, ratio)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    do_mic = "--mic" in args

    from edge.runtime import audio_io

    print("=== 說說學伴 · 邊緣端開機自檢 ===\n")
    rows: list[tuple[str, str, str]] = []
    rows += _check_services()

    state, detail, _status = _check_status()
    rows.append(("/api/status", state, detail))

    rows.append(("觸發鍵",) + evaluate_power_key_guard(
        _effective_handle_power_key(), audio_io._KEY_CODE))

    key_dev = audio_io._resolve_key_device()
    rows.append((
        "按鍵節點",
        OK if audio_io._key_device_usable() else FAIL,
        f"{key_dev}" if audio_io._key_device_usable()
        else f"{key_dev} 讀不到——會退回等 Enter，無螢幕裝置上等於沒有觸發方式",
    ))

    rows.append(("ALSA 裝置",) + evaluate_alsa_devices(
        _service_environment("talkybuddy-local-client")))

    rows.append(("記憶體",) + _check_memory())

    if do_mic:
        rows.append(("麥克風收音",) + _check_mic())
    else:
        rows.append((
            "麥克風收音", WARN,
            "未測（加 --mic）。**靜音鍵沒按的話只有這項抓得到**，demo 前務必跑",
        ))

    print(format_report(rows))
    state, summary = overall_verdict(rows)
    print("\n" + {OK: "✅", WARN: "⚠️ ", FAIL: "❌"}[state] + " " + summary)
    return 0 if state != FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
