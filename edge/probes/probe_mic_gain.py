# -*- coding: utf-8 -*-
"""USB 麥克風擷取增益該設多少？掃一輪實測給答案。

## 為什麼需要這支

裝置的 USB 麥克風 capture gain 開在滿檔（147/147），錄出來 `peak=1.000`——
訊號削波、波形頂端被切平，直接拉低 ASR 準確度。2026-07-30 S2S 實測出現的
誤判（旁邊電視講的話被判成孩子插話、噪音被判成韓文字符）很可能與此有關。

與 3.5mm **輸出**不同（那邊 ALSA mixer 對音量完全無效，實測 -18dB 毫無變化），
USB 麥克風的 capture gain 是真的可調的：

    amixer -c 1 get Mic
      Limits: Capture 0 - 147

但「該調到多少」沒辦法用算的——取決於麥克風本身、玩偶外殼、講話距離。
只能實測。

## 為什麼要打散順序、量很多次（第一版的教訓）

第一版是「由高到低掃一遍，每個增益錄 3 秒」，2026-07-30 實測結果：

    增益 147 → peak 0.131      增益 80 → peak 0.838

**增益降低反而 peak 變高 6 倍，物理上不可能。** 原因是那一版把兩個會隨時間
變動的東西綁在一起了：增益隨掃描進度變化，而人聲音量也隨時間變化——受測者
前幾格還沒開口、第 4 格講得大聲。量到的是說話音量，不是增益的影響。

修法：**每個增益量多次、順序在回合間反轉**，讓人聲的起伏變成隨機雜訊而不是
與增益對齊的系統性偏差，再取中位數。

不改用「播固定音檔當輸入」是因為那測的是喇叭→麥克風的自我迴音路徑，音量
與「孩子在正常距離講話」對不上，會選出偏低的增益。輸入必須是真人聲。

## 用法（在裝置上）

    ./.venv/bin/python -m edge.probes.probe_mic_gain

**全程請以固定音量持續說話**（約 40 秒，念課文或 demo 會用到的句子最貼近
真實使用）。距離與音量跟平常對玩偶講話一樣。

跑完會**還原成原本的增益值**，不擅自改裝置狀態；要不要套用建議值由人決定。

## 判準

- `peak` 落在 0.5–0.9：有足夠動態範圍，又不碰頂
- 削波占比 < 0.1%（`preflight.MIC_CLIP_RATIO_MAX`）
- 人聲頻段占比不因降增益而顯著下滑——若下滑代表降過頭、人聲被雜訊淹沒

**還有一道合理性檢查**：同一個輸入下，增益越高 peak 必須越高。量出來若違反
單調性，代表輸入沒有維持穩定，腳本會直接說「數據不可信」而不是照樣給建議。
給一個從壞數據算出來的建議值，比不給更糟。

量測邏輯直接重用 `preflight._voice_band_ratio()`，不另寫一套 FFT：
兩邊數字必須可比，否則掃出來的建議值拿去跑 `preflight --mic` 會對不上。
"""

from __future__ import annotations

import io
import statistics
import struct
import subprocess
import sys
import wave

from edge.runtime import preflight

CARD = 1
CONTROL = "Mic"
DEVICE = "plughw:1,0"
SECONDS = 2.0
# 147 是滿檔（現況，當對照組）。最低不掃到 0 以下——太低的話錄到的是雜訊，
# 量出來的頻段占比沒有意義。
GAINS = [147, 120, 100, 80, 60]
# 每個增益量幾次。回合間反轉順序（見 _sweep_order），讓「說話音量隨時間變化」
# 這個干擾變成隨機雜訊，而不是與增益對齊的系統性偏差。
ROUNDS = 4


def _amixer_get() -> int | None:
    """讀目前的擷取增益（原始值，非百分比）。讀不到回 None。"""
    try:
        out = subprocess.run(
            ["amixer", "-c", str(CARD), "get", CONTROL],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout
    except Exception as exc:
        print(f"  讀取增益失敗（{type(exc).__name__}: {exc}）", file=sys.stderr)
        return None
    # 形如： Mono: Capture 147 [100%] [-0.94dB] [on]
    for line in out.splitlines():
        if "Capture" in line and "[" in line:
            for tok in line.split():
                if tok.isdigit():
                    return int(tok)
    return None


def _amixer_set(value: int) -> bool:
    try:
        subprocess.run(
            ["amixer", "-c", str(CARD), "set", CONTROL, str(value)],
            capture_output=True, text=True, timeout=10, check=True,
        )
        return True
    except Exception as exc:
        print(f"  設定增益 {value} 失敗（{type(exc).__name__}: {exc}）", file=sys.stderr)
        return False


def _record(seconds: float) -> list[int] | None:
    """錄一段回傳 PCM 樣本。stderr 不吞——arecord 起不來時線索只在那裡。"""
    argv = ["arecord", "-D", DEVICE, "-f", "S16_LE", "-r", "16000",
            "-c", "1", "-d", str(int(seconds)), "-t", "wav", "-"]
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=seconds + 15)
    except Exception as exc:
        print(f"  錄音失敗（{type(exc).__name__}: {exc}）", file=sys.stderr)
        return None
    if proc.returncode != 0 or not proc.stdout:
        err = proc.stderr.decode("utf-8", "replace").strip()
        print(f"  錄音失敗（returncode={proc.returncode}）：{err}", file=sys.stderr)
        return None
    try:
        with wave.open(io.BytesIO(proc.stdout), "rb") as w:
            frames = w.readframes(w.getnframes())
        n = len(frames) // 2
        return list(struct.unpack(f"<{n}h", frames[: n * 2]))
    except Exception as exc:
        print(f"  WAV 解析失敗（{type(exc).__name__}）", file=sys.stderr)
        return None


def _verdict(peak: float, clip: float, band: float) -> str:
    if clip > preflight.MIC_CLIP_RATIO_MAX:
        return "削波"
    if peak < 0.2:
        return "太小"
    if peak > 0.9:
        return "太接近頂"
    if band < preflight.MIC_VOICE_BAND_MIN:
        return "人聲頻段低"
    return "good"


def _sweep_order(round_index: int) -> list[int]:
    """回合間反轉掃描順序。

    若每回合都由高到低，「說話音量隨時間遞增」這種漂移仍會與增益對齊。
    反轉之後，同一個增益在不同回合落在錄音時序的不同位置，漂移被打散。
    """
    return GAINS if round_index % 2 == 0 else list(reversed(GAINS))


def _is_monotonic(medians: list[tuple[int, float]]) -> bool:
    """增益由低到高時，peak 中位數應該不遞減。

    這是物理事實而非統計假設：同一個輸入，放大倍率越高輸出越大。量到的
    結果違反它，就代表「輸入相同」這個前提不成立——受測者的音量在變。
    此時任何建議值都是從壞數據算出來的。

    容許 15% 的反向誤差：人聲本來就有起伏，取中位數也消不乾淨，
    要求嚴格單調會把正常的量測誤判成失敗。
    """
    ordered = sorted(medians)  # 依增益由低到高
    for (_g1, p1), (_g2, p2) in zip(ordered, ordered[1:]):
        if p2 < p1 * 0.85:
            return False
    return True


def main() -> int:
    original = _amixer_get()
    if original is None:
        print("讀不到目前的增益值——確認 card 1 是 USB 麥克風（amixer -c 1 get Mic）")
        return 1
    total_s = len(GAINS) * ROUNDS * SECONDS
    print(f"目前增益：{original}（滿檔 147）")
    print()
    print("=" * 68)
    print(f"  請從現在開始以**固定音量**持續說話，約 {int(total_s)} 秒不要停。")
    print("  距離與音量跟平常對玩偶講話一樣。念課文或 demo 的句子都行。")
    print("  音量忽大忽小會讓數據不可信——腳本會抓出來並拒絕給建議值。")
    print("=" * 68)
    print()

    # gain -> [(peak, band, clip), ...]
    samples_by_gain: dict[int, list[tuple[float, float, float]]] = {g: [] for g in GAINS}
    try:
        for rnd in range(ROUNDS):
            order = _sweep_order(rnd)
            print(f"  第 {rnd + 1}/{ROUNDS} 輪（{' → '.join(map(str, order))}）", flush=True)
            for gain in order:
                if not _amixer_set(gain):
                    continue
                samples = _record(SECONDS)
                if samples is None:
                    continue
                samples_by_gain[gain].append(
                    preflight._voice_band_ratio(samples, 16000))
    finally:
        # 不擅自改變裝置狀態：掃描是量測，套用是人的決定
        _amixer_set(original)
        print()
        print(f"已還原增益為 {original}")

    rows = []
    for gain in GAINS:
        obs = samples_by_gain[gain]
        if not obs:
            continue
        peaks = [o[0] for o in obs]
        bands = [o[1] for o in obs]
        clips = [o[2] for o in obs]
        rows.append({
            "gain": gain,
            "peak": statistics.median(peaks),
            "peak_lo": min(peaks), "peak_hi": max(peaks),
            "band": statistics.median(bands),
            # 削波取**最大值**而非中位數：偶爾爆一次也是問題，
            # 中位數會把它平均掉
            "clip": max(clips),
            "n": len(obs),
        })
    for r in rows:
        r["verdict"] = _verdict(r["peak"], r["clip"], r["band"])

    print()
    print(f"{'增益':>6} {'peak中位':>9} {'peak範圍':>16} {'削波(最大)':>11} "
          f"{'人聲頻段':>9}  判定")
    for r in rows:
        band_txt = "n/a" if r["band"] < 0 else f"{r['band']:.0%}"
        rng = f"{r['peak_lo']:.3f}–{r['peak_hi']:.3f}"
        print(f"{r['gain']:>6} {r['peak']:>9.3f} {rng:>16} {r['clip']:>10.2%} "
              f"{band_txt:>9}  {r['verdict']}")

    print()
    if len(rows) < 2:
        print("量到的資料太少，無法判斷。")
        return 1

    # 物理合理性檢查優先於任何建議：壞數據算出來的建議值比不給建議更糟
    if not _is_monotonic([(r["gain"], r["peak"]) for r in rows]):
        print("⚠️ 數據不可信，不給建議值。")
        print()
        print("  增益越高 peak 應該越高（同一個輸入，放大倍率越大輸出越大），")
        print("  但量到的結果違反這條——代表「輸入維持穩定」這個前提不成立，")
        print("  也就是說話音量在掃描過程中變了。量到的是你的音量，不是增益。")
        print()
        print("  請維持固定音量、不要停頓，再跑一次。")
        return 1

    good = [r for r in rows if r["verdict"] == "good"]
    if not good:
        print("沒有一格落在理想區間。可能原因：說話音量太小、距離太遠，")
        print("或掃描範圍不對（改 GAINS 再掃一次）。")
        return 1

    # 同樣 good 的話取 peak 最接近 0.7 的：留足夠餘裕給突然提高的音量，
    # 又不會小到被環境噪音淹沒
    best = min(good, key=lambda r: abs(r["peak"] - 0.7))
    print(f"建議增益：{best['gain']}"
          f"（peak 中位數 {best['peak']:.3f}、削波 {best['clip']:.2%}、"
          f"人聲頻段 {best['band']:.0%}、n={best['n']}）")
    print()
    print("套用並持久化（不下 alsactl store 的話重開機會打回原形）：")
    print(f"    amixer -c {CARD} set {CONTROL} {best['gain']} && alsactl store")
    print()
    print("套用後重跑自檢確認：")
    print("    ./.venv/bin/python -m edge.runtime.preflight --mic")
    return 0


if __name__ == "__main__":
    sys.exit(main())
