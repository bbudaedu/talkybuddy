"""同一 process 內跑三輪，分離冷載入與穩態成本。"""
import subprocess, sys, time, wave
sys.path.insert(0, "/root/talkybuddy")
from server.tts import TTSEngine
from server.asr_sensevoice import SenseVoiceASREngine

TEXT = "小朋友你好，今天我們一起學英文"
MIC, SPK = "plughw:1,0", "plughw:0,0"
tts, asr = TTSEngine(), SenseVoiceASREngine()

for r in range(1, 4):
    t0 = time.time(); wav = tts.synth([("zh", TEXT)]); t_tts = time.time() - t0
    open("/tmp/lb.wav", "wb").write(wav)
    with wave.open("/tmp/lb.wav") as w:
        dur = w.getnframes() / w.getframerate()
    rec = subprocess.Popen(["arecord", "-D", MIC, "-f", "S16_LE", "-r", "16000",
                            "-c", "1", "-d", str(int(dur) + 2), "/tmp/lb_r.wav"],
                           stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    subprocess.run(["aplay", "-D", SPK, "/tmp/lb.wav"], stderr=subprocess.DEVNULL)
    rec.wait()
    t0 = time.time(); text, conf = asr.transcribe("/tmp/lb_r.wav"); t_asr = time.time() - t0
    ok = "✓" if text.rstrip("。") == TEXT else "✗"
    print(f"第 {r} 輪  TTS={t_tts:6.2f}s ({dur:.2f}s 音訊)  ASR={t_asr:6.2f}s  {ok} 「{text}」", flush=True)
