# -*- coding: utf-8 -*-
"""童聲拼字母，SenseVoice 到底認不認得？在真機上量一次給答案。

## 為什麼需要這支

`server/spelling.py` 的 `PASS_THRESHOLD = 0.6` 是量出來的，但**量的是 TTS
合成音**：2026-07-31 開發機上 8 個詞各跑 5 輪共 40 個樣本，平均字母命中率
0.83，門檻 0.6 之下 38/40 過關。

合成音不是童聲。開發機沒有錄音裝置（`edge/probes/probe_mic_gain.py` 那支
也是為此才必須上真機），所以「孩子唸 A-P-P-L-E，裝置聽不聽得懂」這件事
在開發機上**驗不了**——它是整個背單字功能最大的未驗證假設。

這支就是去驗它。跑完會印出一張表，直接告訴你門檻該不該調、調到多少。

## 怎麼跑

裝置上（伺服器不必開，這支只用本地引擎）：

    cd ~/talkybuddy && .venv/bin/python -m edge.probes.probe_spell_child

每一輪：玩偶念字母 → 按一下按鍵 → 孩子跟著拼 → 放開後自動判定。
五個詞 × 三輪 ≈ 5 分鐘。孩子會累，中途想停就 Ctrl-C，已完成的輪次照樣出表。

## 為什麼要多輪、而且每輪換順序

抄 `probe_mic_gain.py` 被咬出來的教訓：**別把兩個會隨時間變動的東西綁在
一起**。那支第一版「由高到低掃增益」，量到的其實是受測者前幾格還沒開口、
後幾格講得大聲——增益的影響被說話音量蓋過去了。

同一個陷阱在這裡是：孩子第一輪還在狀況外、第三輪已經熟了（或已經不耐煩）。
固定順序測，「第幾個唸的」就會混進「這個詞好不好認」裡。所以每一輪把詞序
反轉，讓熟練度的漂移變成隨機雜訊而不是與詞對齊的系統性偏差，最後取中位數。

## 為什麼錄音長度要隨字數變

預設 4 秒是為對話設計的。孩子拼 `banana` 是六個字母、中間還會停頓思考，
4 秒會從中間切斷——而被切斷的錄音判出來的低命中率是**量測工具的問題**，
不是孩子的問題，卻長得一模一樣。所以錄音長度隨字母數給。
"""

from __future__ import annotations

import statistics
import sys

# 受測詞：刻意涵蓋不同長度與形狀。
#   dog/cat  3 個字母，短到 ASR 容易整包吞掉
#   book     有連續重複字母（O,O），最容易被黏成一個 token
#   apple    重複字母在中間
#   banana   6 個字母，最長，最考驗孩子的耐心與 ASR 的斷詞
WORDS = ("dog", "cat", "book", "apple", "banana")

ROUNDS = 3

# 錄音長度：基礎秒數 + 每個字母的秒數。孩子拼字母比唸單字慢得多。
_BASE_SECONDS = 2.5
_SECONDS_PER_LETTER = 0.7
_MAX_SECONDS = 12.0


def _record_seconds(word: str) -> float:
    return min(_MAX_SECONDS, _BASE_SECONDS + _SECONDS_PER_LETTER * len(word))


def _speak(tts, text_zh: str, text_en: str) -> None:
    """把一句中文引導 + 英文內容念出來；沒有 TTS 就退回印在畫面上。

    念不出來不該讓整支探針停擺——現場沒有喇叭時，大人照著螢幕唸也量得到。
    """
    from edge.runtime import audio_io

    print(f"\n  玩偶：{text_zh} {text_en}")
    if tts is None:
        return
    try:
        wav = tts.synth([("zh", text_zh), ("en", text_en)])
        if wav:
            audio_io.play_wav_bytes(wav)
    except Exception:
        print("  （TTS 播放失敗，請照著上面那行唸給孩子聽）")


def _trial(tts, asr, word: str) -> tuple[float, str]:
    """念一次字母、錄一次孩子的跟讀，回 (命中率, ASR 聽到什麼)。"""
    import tempfile
    import os

    from edge.runtime import audio_io
    from server import spelling

    letters = spelling.letters_for_tts(word)
    _speak(tts, f"跟我拼「{word}」：", letters)

    audio_io.wait_for_trigger()
    secs = _record_seconds(word)
    print(f"  錄音中 {secs:.1f} 秒——請孩子拼 {letters}")
    wav = audio_io.capture_16k_mono_wav(seconds=secs)

    fd, path = tempfile.mkstemp(suffix=".wav")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(wav)
        heard, _ = asr.transcribe(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    rate = spelling.spell_hit_rate(word, heard)
    print(f"  聽到 {heard!r} → 命中率 {rate:.2f}")
    return rate, heard


def _report(results: dict[str, list[float]]) -> None:
    from server import spelling

    flat = [r for rates in results.values() for r in rates]
    if not flat:
        print("\n一筆資料都沒有，沒得報告。")
        return

    print("\n" + "=" * 62)
    print(f"{'詞':<9}{'每輪命中率':<26}{'中位數':>8}{'過關':>8}")
    print("-" * 62)
    for word, rates in results.items():
        if not rates:
            continue
        passed = sum(1 for r in rates if r >= spelling.PASS_THRESHOLD)
        shown = " ".join(f"{r:.2f}" for r in rates)
        print(f"{word:<9}{shown:<26}{statistics.median(rates):>8.2f}"
              f"{passed:>5}/{len(rates)}")
    print("-" * 62)
    print(f"樣本數 {len(flat)}，整體中位數 {statistics.median(flat):.2f}、"
          f"平均 {statistics.mean(flat):.2f}")
    print("\n各門檻下的過關率（拿去跟合成音基準線比）：")
    print(f"  {'門檻':<8}{'童聲（這次）':<16}{'合成音（開發機基準）'}")
    baseline = {0.5: "40/40", 0.6: "38/40", 0.7: "27/40"}
    for th in (0.5, 0.6, 0.7):
        hit = sum(1 for r in flat if r >= th)
        mark = "  ← 現行" if abs(th - spelling.PASS_THRESHOLD) < 1e-9 else ""
        print(f"  {th:<8}{f'{hit}/{len(flat)}':<16}{baseline[th]}{mark}")

    print("\n怎麼讀這張表：")
    print("  現行門檻的過關率 ≥ 8 成 → 不用動，直接上")
    print("  落在 5–8 成         → 把 PASS_THRESHOLD 降到 0.5 再跑一次")
    print("  低於 5 成           → 不是門檻的問題，先查麥克風收音")
    print("                        （edge/probes/probe_mic_gain.py）")


def main() -> int:
    from server.asr import ASREngine
    from server.tts import TTSEngine

    asr = ASREngine()
    if not asr.available():
        print("ASR 引擎不可用——這支要在裝置上跑，而且模型要在位。", file=sys.stderr)
        return 1
    tts = TTSEngine()
    if not tts.available():
        print("TTS 不可用，改成把字母印在畫面上，請大人唸給孩子聽。")
        tts = None

    print(f"童聲拼字母量測：{len(WORDS)} 個詞 × {ROUNDS} 輪。")
    print("每一輪按一下按鍵開始錄音。中途想停按 Ctrl-C，已完成的輪次照樣出表。")

    results: dict[str, list[float]] = {w: [] for w in WORDS}
    try:
        for rnd in range(ROUNDS):
            # 每輪反轉詞序：孩子的熟練度會隨時間漂移，固定順序會讓
            # 「第幾個唸的」混進「這個詞好不好認」裡（見模組 docstring）。
            order = WORDS if rnd % 2 == 0 else tuple(reversed(WORDS))
            print(f"\n{'=' * 62}\n第 {rnd + 1} 輪 / 共 {ROUNDS} 輪")
            for word in order:
                try:
                    rate, _ = _trial(tts, asr, word)
                    results[word].append(rate)
                except Exception as exc:
                    # 單次錄音失敗不該讓整場重來——孩子已經在旁邊等了。
                    print(f"  這次量測失敗（{type(exc).__name__}: {exc}），跳過")
    except KeyboardInterrupt:
        print("\n\n中止，用已完成的資料出表。")

    _report(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
