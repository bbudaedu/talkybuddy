# -*- coding: utf-8 -*-
"""近場門檻該設多少？量一段真實說話的逐塊音量分布來決定。

## 為什麼需要這支

`live_client.py` 的 `TALKYBUDDY_EDGE_NEAR_FIELD_PEAK`（預設 0.06）會把每個
100ms 音訊塊的 peak 低於門檻的整塊丟掉，用來擋環境噪音（2026-07-30 實測，
旁邊電視講的「我明白了」曾被判成使用者插話）。

但 2026-07-30 的 S2S 實測顯示這道防線調過頭了：

    上行統計：已送 182400 bytes（~5.7s）、低於近場門檻丟棄 31.0s
    下行統計：音訊 0 bytes、JSON 事件 3 則

**講了約 37 秒，31 秒被丟掉，Nova Sonic 從頭到尾沒回過一句話。**
被剁掉 84% 之後它的 server VAD 判不出完整的 turn。

成因與同日的麥克風量測吻合：這支麥克風訊號本來就偏弱，滿檔增益下 3 秒窗的
peak 中位數只有 0.284——而那是**視窗內的最大值**。拆成 100ms 小塊之後，
音節之間、氣音、句尾的塊大多低於 0.06。

門檻不能用猜的：訂太高會吃掉孩子的話（現況），訂太低則電視聲會穿透。
要看的是**真實說話的逐塊 peak 分布**。

## 用聲音提示，不是用畫面

這支要分兩段錄（先靜默、再說話），受測者必須知道**何時該講、何時該閉嘴**。
2026-07-30 第一版靠 `print()` 提示，結果經 SSH 執行時輸出被緩衝，受測者
在 12 秒內完全看不到任何字，量到的「說話」與「靜默」分布一模一樣——
中位數 0.0061 vs 0.0062，而「靜默」的最大值還比「說話」高。整組數據作廢。

所以改用**嗶聲**：裝置有喇叭，讓它自己講。受測者不需要盯螢幕，也不受
SSH 緩衝影響。

    嗶 ×1  → 開始錄靜默（請閉嘴）
    嗶 ×2  → 開始錄說話（請講話）
    嗶 ×3  → 結束

## 用法（在裝置上，需先停掉佔用麥克風的 client）

    systemctl stop talkybuddy-live-client talkybuddy-local-client
    ./.venv/bin/python -m edge.probes.probe_near_field_threshold

預設會錄靜默＋說話兩段。只想量說話（不管噪音）可加 `--no-silence`。

理想的門檻要落在「人聲塊的大多數之下」與「靜默塊的最大值之上」的空隙裡。
若兩者重疊，代表這個環境下靠音量分不開人聲與噪音，得換別的方法。
"""

from __future__ import annotations

import io
import math
import statistics
import struct
import subprocess
import sys
import time
import wave

from edge.runtime.live_client import UPLINK_CHUNK_BYTES, chunk_peak

DEVICE = "plughw:1,0"
PLAYBACK = "plughw:0,0"
SECONDS = 6
RATE = 16000
# 要評估的候選門檻。0.06 是目前的預設值（實測過高）。
CANDIDATES = [0.0, 0.01, 0.02, 0.03, 0.04, 0.06, 0.08]

# 嗶聲：880Hz 短音。刻意用高頻——它落在人聲頻段之外，就算殘響被錄進去
# 也不會被誤認為語音而汙染量測。
_BEEP_HZ = 880
_BEEP_S = 0.18
_BEEP_GAP_S = 0.12


def _beep_wav(count: int) -> bytes:
    """產生 count 聲短嗶的 WAV bytes。"""
    rate = 22050
    frames = bytearray()
    for i in range(count):
        n = int(rate * _BEEP_S)
        for k in range(n):
            # 首尾各做一小段淡入淡出，避免爆音（方波邊緣）掩蓋掉嗶聲本身
            env = min(1.0, k / (rate * 0.01), (n - k) / (rate * 0.01))
            v = int(12000 * env * math.sin(2 * math.pi * _BEEP_HZ * k / rate))
            frames += struct.pack("<h", v)
        if i < count - 1:
            frames += b"\x00\x00" * int(rate * _BEEP_GAP_S)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def _beep(count: int) -> None:
    """播 count 聲嗶。

    **為什麼不用 print 提示**：經 SSH 執行時 stdout 會被緩衝，受測者在整段
    量測期間看不到任何字。2026-07-30 第一版就因此量到兩段一模一樣的靜默
    （受測者根本不知道何時該講話），整組數據作廢。
    嗶聲不受緩衝影響，而且不必盯著螢幕。
    """
    try:
        subprocess.run(["aplay", "-D", PLAYBACK, "-q", "-"],
                       input=_beep_wav(count), timeout=15)
    except Exception as exc:
        print(f"（嗶聲播放失敗：{type(exc).__name__}，請改看畫面提示）",
              file=sys.stderr)
    # 讓嗶聲的殘響與喇叭餘韻散掉再開始錄，否則會被算進第一個塊
    time.sleep(0.4)


def _record(seconds: int) -> bytes | None:
    """錄一段 raw PCM16。stderr 不吞——arecord 起不來時線索只在那裡。"""
    argv = ["arecord", "-D", DEVICE, "-f", "S16_LE", "-r", str(RATE),
            "-c", "1", "-d", str(seconds), "-t", "raw", "-"]
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=seconds + 15)
    except Exception as exc:
        print(f"錄音失敗（{type(exc).__name__}: {exc}）", file=sys.stderr)
        return None
    if proc.returncode != 0 or not proc.stdout:
        err = proc.stderr.decode("utf-8", "replace").strip()
        print(f"錄音失敗（returncode={proc.returncode}）：{err}", file=sys.stderr)
        return None
    return proc.stdout


def chunk_peaks(raw: bytes) -> list[float]:
    """切成與 live_client 上行相同大小的塊，逐塊算 peak。

    塊大小必須與 `UPLINK_CHUNK_BYTES` 一致，否則量出來的分布與實際被丟棄的
    單位對不上——這正是要回答的問題。
    """
    return [
        chunk_peak(raw[i:i + UPLINK_CHUNK_BYTES])
        for i in range(0, len(raw) - UPLINK_CHUNK_BYTES + 1, UPLINK_CHUNK_BYTES)
    ]


def describe(label: str, peaks: list[float]) -> None:
    if not peaks:
        print(f"{label}：沒有資料")
        return
    ordered = sorted(peaks)
    def pct(p):
        return ordered[min(len(ordered) - 1, int(len(ordered) * p))]
    print(f"{label}（{len(peaks)} 塊 × 100ms）")
    print(f"  最小 {ordered[0]:.4f} / p25 {pct(0.25):.4f} / 中位 "
          f"{statistics.median(ordered):.4f} / p75 {pct(0.75):.4f} / "
          f"最大 {ordered[-1]:.4f}")


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    do_silence = "--no-silence" not in args

    silence_peaks: list[float] = []
    if do_silence:
        print(f"=== 嗶×1 後錄 {SECONDS} 秒靜默 ===")
        _beep(1)
        raw = _record(SECONDS)
        if raw is None:
            return 1
        silence_peaks = chunk_peaks(raw)
        describe("靜默", silence_peaks)
        print()

    print(f"=== 嗶×2 後錄 {SECONDS} 秒說話 ===")
    _beep(2)
    raw = _record(SECONDS)
    if raw is None:
        return 1
    speech_peaks = chunk_peaks(raw)
    _beep(3)
    describe("說話", speech_peaks)

    # 這一段量到的若與靜默無異，代表受測者沒講話或麥克風沒收到——
    # 繼續往下算門檻只會得到看似合理實則無意義的建議值
    if silence_peaks:
        s_med = statistics.median(speech_peaks)
        n_med = statistics.median(silence_peaks)
        if s_med < n_med * 1.5 and max(speech_peaks) < max(silence_peaks):
            print()
            print("⚠️ 「說話」與「靜默」的分布幾乎一樣，這段沒有錄到人聲。")
            print("   常見原因：沒聽到嗶聲、講話時機沒對上、或麥克風被別的")
            print("   行程佔著。確認兩個 client 都停掉後再跑一次。")
            return 1

    print()
    print(f"{'門檻':>8} {'說話通過率':>12} {'靜默誤放率':>12}")
    for th in CANDIDATES:
        passed = sum(1 for p in speech_peaks if p >= th) / len(speech_peaks)
        if silence_peaks:
            leak = sum(1 for p in silence_peaks if p >= th) / len(silence_peaks)
            leak_txt = f"{leak:>11.0%}"
        else:
            leak_txt = f"{'未測':>12}"
        mark = "  ← 目前預設" if abs(th - 0.06) < 1e-9 else ""
        print(f"{th:>8.2f} {passed:>11.0%} {leak_txt}{mark}")

    print()
    # 目標：讓說話的塊絕大多數通過。低於 90% 就代表在吃掉孩子的話——
    # Nova Sonic 需要連續的音訊流，斷斷續續的輸入它判不出 turn 邊界。
    ok = [th for th in CANDIDATES
          if sum(1 for p in speech_peaks if p >= th) / len(speech_peaks) >= 0.90]
    if silence_peaks:
        # 同時要求靜默幾乎全被擋掉，否則等於沒有防線
        ok = [th for th in ok
              if sum(1 for p in silence_peaks if p >= th) / len(silence_peaks) <= 0.05]
    if not ok:
        print("⚠️ 沒有門檻能同時「放行人聲」與「擋住噪音」——這個環境下靠音量")
        print("   分不開兩者。可行的方向是把門檻設 0（完全信任 Nova Sonic 的 VAD），")
        print("   代價是環境噪音會穿透。")
        return 1
    best = max(ok)  # 滿足條件的前提下取最高，保留最多抗噪能力
    print(f"建議門檻：{best:.2f}")
    print()
    print("套用（寫進 unit 檔才會持久，見 edge/deploy/talkybuddy-live-client.service）：")
    print(f"    Environment=TALKYBUDDY_EDGE_NEAR_FIELD_PEAK={best}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
