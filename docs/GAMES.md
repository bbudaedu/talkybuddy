# 四個互動小遊戲 — 現場操作

實作：`server/games.py`（規則式核心）＋ `pipeline.start_game/play_turn`＋
`/api/games`、`/api/game`。測試 89 條（`tests/test_games_*.py`）。

## 三個遊戲與課綱依據

| 遊戲 | 課綱附錄四溝通功能 | 句型 | 題庫來源 |
|---|---|---|---|
| 火眼金睛 I Spy | `Naming common toys and household objects` | `I see a ___.` | animal 29／food 33／school 26 詞 |
| 猜猜我是誰 20 Questions | `Asking about abilities` | `Is it ___?` | 同上 |
| 點餐時間 Restaurant | `Ordering food & drinks` | `I want a ___.` | food 33 詞 |
| 背單字 Spell Along | `Spelling and reading aloud familiar words` | `A, P, P, L, E,` | 全詞庫 136 詞，到期詞優先 |

**零新詞庫**——全部取自 `scaffold.VOCAB`（136 詞，99.3% 落在教育部基本 1,200 字內）。

## 現場怎麼開

```bash
T=$(curl -s -X POST localhost:8787/api/login -H 'content-type: application/json' \
      -d '{"email":"tutor@demo","password":"demo1234"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")

curl -s localhost:8787/api/games -H "Authorization: Bearer $T"          # 可玩清單
curl -s -X POST localhost:8787/api/game -H "Authorization: Bearer $T" \
     -H 'content-type: application/json' -d '{"game":"i_spy","topic":"animal"}'
curl -s -X POST localhost:8787/api/game -H "Authorization: Bearer $T" \
     -H 'content-type: application/json' -d '{"game":"none"}'           # 結束
```

開局後直接對著裝置講話即可，`/ws/talk` 的回合會自動走遊戲判定。

## 斷網橋段可以怎麼演

**遊戲進行中一次都不碰雲端**，所以拔網路後：

- 判定、回覆、計分**完全一樣**（有測試守著：`test_game_judgement_is_identical_online_and_offline`）
- 回合延遲反而更穩（不吃 1.5 秒的雲端預算）

真正的落差在**遊戲 B**：離線版只能回答類別與開頭字母，其他問題會誠實說
「這個問題我還答不出來」；接上雲端後同樣的問題答得出來。

> 這是刻意的設計。瞎猜 Yes/No 會讓孩子學到錯的東西，比承認做不到更糟。
> 而那個落差正是斷網橋段要讓評審看見的東西——**不是「斷網就掛」，
> 是「斷網仍可用，接上雲端更聰明」**。

## 間隔重複的可見出口

三個遊戲的提示詞（`hints`）都優先給**這孩子答錯過又到期的詞**
（`server/srs.py` 的 `word_reviews`）。遊戲 B 更直接：謎底就從到期詞挑。

現場可以講：「這個孩子上禮拜 banana 沒說對，今天的菜單第一個就是 banana。」

## 已知邊界（不要在台上講過頭）

- 遊戲 B 的離線問句理解是**小而準的關鍵詞比對**，不是語意理解。
  能答類別（animal／food／school…）與開頭字母，其餘誠實回 unknown
- 三個遊戲的雲端加值（追問、場景敘述）**尚未接**。目前雲端與離線的
  差異只在遊戲 B 的答題能力
- 前端還沒有遊戲 UI，目前靠 API 開局 + 語音互動

### 背單字（遊戲 D）專屬邊界

- **字母念法是實測選出來的。** 只有 `"A, P, P, L, E,"` 這個格式能被本地 TTS
  正確念成字母並被 SenseVoice 完整讀回；句點（`A. P. P.` → `A T, T, L, E.`）、
  空白（→ `Ppili.`）、連字號（→ `AP.`）三種寫法實測全壞。
  改格式前先跑 `edge/probes/probe_spell_tts.py`
- **ASR 把字母黏成一個字時，分不出孩子是在拼還是在唸整個單字**
  （兩者的 ASR 文字都是 `Apple.`）。分不出來就不假裝分得出來，一律當作拼對
- **判定靠 ASR 文字，不是真發音評分。** 腔調很怪但字母對，仍然會過。
  真音素評分（`server/pronunciation.py`）需要 torch，Genio 520 上能不能跑未驗證，
  且它的定位是背景診斷層、不進即時路徑
- **孩子真聲的字母辨識率尚未驗證。** 實測用的是 TTS 合成音當代理，不是童聲。
  開發機沒有錄音裝置，這件事只能上真機驗收——這是本功能最大的未驗證假設
