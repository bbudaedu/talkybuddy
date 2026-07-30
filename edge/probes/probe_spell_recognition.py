"""量測真迴路的變異：同一批詞跑 N 輪，看命中率分布。"""
import sys, os, statistics; sys.path.insert(0,"/home/budaedu/talkybuddy")
from server import scaffold, spelling
from server.tts import TTSEngine
from server.asr import ASREngine
OUT="/tmp/claude-1000/-home-budaedu-talkybuddy/969b8140-f101-4764-b32a-60afa9a3533e/scratchpad/var"
os.makedirs(OUT, exist_ok=True)
tts, asr = TTSEngine(), ASREngine()
WORDS=["apple","dog","book","banana","cat","pencil","water","mom"]
N=5
rows={}
for w in WORDS:
    rates=[]
    for i in range(N):
        wav=tts.synth([("en", spelling.letters_for_tts(w))])
        p=f"{OUT}/{w}_{i}.wav"; open(p,"wb").write(wav)
        heard,_=asr.transcribe(p)
        rates.append(spelling.spell_hit_rate(w, heard))
    rows[w]=rates
print(f"{'詞':<9}{'每輪命中率':<32}{'平均':>6}{'過關率':>8}")
print("-"*58)
allr=[]
for w,rates in rows.items():
    allr+=rates
    passed=sum(1 for r in rates if r>=spelling.PASS_THRESHOLD)
    print(f"{w:<9}{str([f'{r:.2f}' for r in rates]):<32}{statistics.mean(rates):>6.2f}{passed}/{N:>7}")
print("-"*58)
print(f"整體平均命中率 {statistics.mean(allr):.2f}，過關率 {sum(1 for r in allr if r>=spelling.PASS_THRESHOLD)}/{len(allr)}")
for th in (0.5,0.6,0.7):
    print(f"  門檻 {th}: 過關率 {sum(1 for r in allr if r>=th)}/{len(allr)}")
