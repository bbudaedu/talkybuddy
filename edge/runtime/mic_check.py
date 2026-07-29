"""麥克風 + ASR 真人驗證：錄 N 秒 → SenseVoice 逐字稿 + 訊號判讀。

**為什麼要有這支**：`loopback_audio_check.py` 走 TTS→喇叭→空氣→麥克風→ASR，
喇叭沒接時整條驗不了；而且用合成音驗辨識會給出假的信心（`NATIVE_KWS_PLAN.md`
的教訓：合成音自檢通過、真人卻失敗）。這支只驗真人聲音進 ASR 這一段。

裝置上執行（要有終端機，你得聽得到倒數才知道何時開口）：

    ssh -t root@192.168.31.78 'cd /root/talkybuddy && \
      ./.venv/bin/python edge/runtime/mic_check.py 6 "我要玩火眼金睛"'

判讀：
- `人聲頻段 > 25%` 才代表真的錄到人聲。**只看 peak 看不出來**——USB 麥克風有
  實體靜音鍵，按下去時軟體偵測不到也控制不了（見交接文件現場風險清單第 1 項）。
- 逐字稿與期望不符時，**照實際聽到的字**去 `server/game_intent.py` 加別名，
  不要憑想像加。
"""
import subprocess
import sys
import time
import wave

sys.path.insert(0, "/root/talkybuddy")

SEC = int(sys.argv[1]) if len(sys.argv) > 1 else 6
EXPECT = sys.argv[2] if len(sys.argv) > 2 else ""
MIC = "plughw:1,0"  # USB 麥克風；`default` 會錄到板載音效卡（/etc/asound.conf）
WAV = "/tmp/mic_check.wav"


def main() -> int:
    import numpy as np
    from server.asr_sensevoice import SenseVoiceASREngine

    print("載入 ASR 模型…", flush=True)
    asr = SenseVoiceASREngine()

    print(f"\n>>> 倒數後開始錄音 {SEC} 秒" + (f"，請講：「{EXPECT}」" if EXPECT else ""), flush=True)
    for i in (3, 2, 1):
        print(f"    {i}…", flush=True)
        time.sleep(1)
    print(">>> 【錄音中，請講話】", flush=True)

    subprocess.run(
        ["arecord", "-D", MIC, "-f", "S16_LE", "-r", "16000", "-c", "1",
         "-d", str(SEC), WAV],
        stderr=subprocess.DEVNULL,
    )
    print(">>> 錄音結束，辨識中…\n", flush=True)

    with wave.open(WAV) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(float)
    spec = np.abs(np.fft.rfft(x))
    freq = np.fft.rfftfreq(len(x), 1 / 16000)
    ratio = 100 * spec[(freq >= 500) & (freq <= 3000)].sum() / max(spec.sum(), 1e-9)
    rms = int((x ** 2).mean() ** 0.5)
    peak = int(abs(x).max())

    verdict = "✓ 有錄到人聲" if ratio > 25 and rms > 300 else "✗ 疑似空錄音（檢查麥克風實體靜音鍵）"
    print(f"訊號：rms={rms}  peak={peak}  人聲頻段={ratio:.1f}%  → {verdict}")

    text, conf = asr.transcribe(WAV)
    print(f"逐字稿：「{text}」  conf={conf}")

    if EXPECT:
        hit = EXPECT in text
        print(f"期望：「{EXPECT}」 → {'✓ 聽對了' if hit else '✗ 沒聽對'}")
        if not hit:
            print("\n若逐字稿是同音字，就照上面實際聽到的字去 game_intent.py 加別名。")
        return 0 if hit else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
