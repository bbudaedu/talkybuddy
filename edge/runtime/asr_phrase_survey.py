"""ASR 逐字稿普查：連續錄幾句話，看 SenseVoice 實際聽成什麼。

**為什麼要普查而不是修一次**：2026-07-29 實測「我要玩火眼金睛」被聽成
「我要玩佛火眼鏡」——意圖詞「我要玩」完全正確，壞的是四字成語遊戲名。
但**一個樣本設計不出別名**：ASR 的錯法每次可能不同，照單次結果硬加別名
等於過擬合到一次錄音（`NATIVE_KWS_PLAN.md` 的教訓就是單次/合成音會給假信心）。

這支把要測的句子一次跑完，印出對照表，讓別名依**實際分布**設計。

用法（裝置上，需要終端機才看得到倒數）：

    ssh -t root@192.168.31.78 'cd /root/talkybuddy && \
      ./.venv/bin/python edge/runtime/asr_phrase_survey.py'

每句會倒數 2 秒再錄 5 秒。全部跑完約 1 分鐘。
"""
import subprocess
import sys
import time
import wave

sys.path.insert(0, "/root/talkybuddy")

MIC = "plughw:1,0"
SEC = 5
WAV = "/tmp/asr_survey.wav"

# 要測的句子：三個遊戲名各兩種講法（全名 vs 簡稱），加一句對照組。
# 簡稱是為了驗證「孩子講短的會不會比較準」——若是，遊戲名本身該改。
PHRASES = [
    "我要玩火眼金睛",
    "我要玩火眼金睛",      # 同一句測兩次，看錯法穩不穩定
    "我要玩找東西",
    "我要玩猜猜我是誰",
    "我要玩點餐時間",
    "我要玩點餐",
]


def record_once(np, asr, text: str, idx: int, total: int) -> tuple[str, float, int]:
    print(f"\n[{idx}/{total}] 請講：「{text}」", flush=True)
    for i in (2, 1):
        print(f"    {i}…", flush=True)
        time.sleep(1)
    print("    【錄音中】", flush=True)
    subprocess.run(
        ["arecord", "-D", MIC, "-f", "S16_LE", "-r", "16000", "-c", "1",
         "-d", str(SEC), WAV],
        stderr=subprocess.DEVNULL,
    )
    with wave.open(WAV) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(float)
    spec = np.abs(np.fft.rfft(x))
    freq = np.fft.rfftfreq(len(x), 1 / 16000)
    ratio = 100 * spec[(freq >= 500) & (freq <= 3000)].sum() / max(spec.sum(), 1e-9)
    heard, _ = asr.transcribe(WAV)
    rms = int((x ** 2).mean() ** 0.5)
    print(f"    → 聽成：「{heard}」  (人聲{ratio:.0f}% rms={rms})", flush=True)
    return heard, ratio, rms


def main() -> int:
    import numpy as np
    from server.asr_sensevoice import SenseVoiceASREngine

    print("載入 ASR 模型…", flush=True)
    asr = SenseVoiceASREngine()
    asr.transcribe(WAV) if False else None  # 不預先跑，避免檔案不存在

    rows = []
    for i, text in enumerate(PHRASES, 1):
        heard, ratio, rms = record_once(np, asr, text, i, len(PHRASES))
        rows.append((text, heard, ratio, rms))

    print("\n\n=== 逐字稿對照表 ===")
    print(f"{'說的':<16} {'聽成':<24} {'人聲%':>6} {'命中':>5}")
    print("-" * 58)
    for said, heard, ratio, _ in rows:
        hit = "✓" if said in heard else "✗"
        print(f"{said:<16} {heard:<24} {ratio:>5.0f}% {hit:>5}")

    print("\n把這張表貼回對話，我依實際分布設計別名（不憑想像加）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
