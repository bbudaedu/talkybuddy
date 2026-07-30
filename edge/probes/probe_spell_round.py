# 端到端：模擬 pipeline 的遊戲回合，印出裝置實際會講出來的每一句
import sys; sys.path.insert(0,"/home/budaedu/talkybuddy")
from server import games, scaffold

st = games.start_spell_along(target_count=2)
st = games.replace(st, hints=("蘋果","狗"), secret="蘋果", target_count=2)
line = games.spell_along_prompt(st)
print("開場：", " ".join(p for p in (line.zh, line.en) if p))

# 孩子的回答（模擬：第一個詞全對、第二個詞拼錯一次）
answers = ["apple", "A, P, P, L, E.", "I want to eat an apple.",
           "dog", "我不會", "D, O, G.", "I see a dog."]
for ans in answers:
    turn = games.judge_spell_along(st, ans)
    st = turn.state
    # 這行複製自 pipeline._process_text 的遊戲分支
    pieces = [turn.reply_zh]
    if turn.target_en:
        pieces.append(f"跟我說一遍：{turn.target_en}")
    elif turn.reply_en:
        pieces.append(turn.reply_en)
    reply = " ".join(p for p in pieces if p)
    print(f"\n孩子 → {ans!r}")
    print(f"玩偶 → {reply}")
    print(f"       切段 {scaffold.split_tts_segments(reply)}")
    if turn.done:
        print("\n[一局結束]")
        break
