# 離線背單字訓練（spell_along）— 設計

> 2026-07-31 ／ 分支 `gsd/3-offline-spelling-drill`
>
> 一句話：在 `server/games.py` 加第四個遊戲，把「感知 → 決策 → 行動」做成一個
> **純規則、全離線**的背單字迴圈，學習狀況寫進既有的 `word_reviews`。
> 零新模型、零新依賴、零新協定。

## 1. 問題

要在斷網的 Genio 520 上做背單字訓練：玩偶念單字 `apple`、逐字母拼 `A-P-P-L-E`、
念例句，孩子跟著唸，裝置要能**確認他唸得對不對**並把學習狀況記下來。

這需要三段閉環都在本地跑得動：感知（聽得到孩子唸什麼）、決策（挑該練的詞、
決定前進或重來）、行動（把教學內容說出口）。

## 2. 實測證據（2026-07-31，開發機）

設計前先驗證了最大的技術風險——**本地 TTS 到底能不能念字母**。
方法：合成候選寫法 → 用本地 SenseVoice ASR 讀回，看念的是字母還是整個單字。

| TTS 輸入 | ASR 讀回 | 判讀 |
|---|---|---|
| `A, P, P, L, E,` | `A, P, P, L, E.` | ✅ **完美來回** |
| `A. P. P. L. E.` | `A T, T, L, E.` | ✗ 句點被當縮寫 |
| `A P P L E` | `Ppili.` | ✗ 被黏成一個字 |
| `A-P-P-L-E` | `AP.` | ✗ 連字號吞掉後半 |
| `apple`（整字） | `Bbble.` | ✗ 整字反而聽錯 |

三個結論，每一個都直接決定了設計：

1. **逗號分隔的大寫字母是唯一可靠寫法。** 其他四種都壞。這個字串格式要寫死在
   程式裡並用測試釘住，不能讓後人「順手改成看起來比較漂亮的 `A-P-P-L-E`」。
2. **ASR 對字母序列比對整個單字更準。** 反直覺但站得住腳：字母是離散、有停頓、
   音節短的音，正是 ASR 擅長的；`apple` 被聽成 `Bbble`，`A, P, P, L, E` 卻一字不差。
   → **判定主力放在拼音那一步**，不是整字跟讀那一步。
3. **`asr_confidence` 在 SenseVoice 一律回 1.0**（四個測試詞皆是），
   所以 `ASR_CONF_THRESHOLD` 那道閘門在這裡幫不上忙，判定必須自己做模糊比對。

補充驗證：`scaffold.split_tts_segments("我們來拼：A, P, P, L, E,")` 正確切成
`[('zh','我們來拼：'), ('en','A, P, P, L, E,')]`——字母序列會整段進英文 voice，
既有的中英混句切段機制不用動。

前置靜音實驗（0ms vs 300ms padding）顯示開頭遺漏**不是**截斷造成的，
是特定單字合成品質問題（`dog`/`banana`/`book` 都正確，只有 `apple` 壞）。
padding 不修這件事，所以不做。

## 3. 為什麼斷網做得到

| 段 | 做什麼 | 靠既有的什麼 | 需要網路？ |
|---|---|---|---|
| **感知** | ASR 文字 → 正規化 → 字母／單字模糊比對 → 命中率 0–1 | `server/asr.py`（SenseVoice，本地）＋ 新 `server/spelling.py` | 否 |
| **決策** | 挑今天該練哪個詞（SRS 到期優先）＋依命中率決定前進／重來／放行 | `server/srs.py`（純函式）＋ `store.word_reviews`（SQLite） | 否 |
| **行動** | 三步腳本合成語音：單字 → 拼音 → 例句 | `server/tts.py`（sherpa-onnx VITS，本地）＋ `scaffold.split_tts_segments` | 否 |

題庫也不用新增：`scaffold.VOCAB` 的每個詞已經同時帶 `en`／`np`／`sent`，
例如 `"蘋果": {"en":"apple", "cat":"food", "np":"an apple", "sent":"I want to eat an apple."}`。
拼音由 `en` 現算，例句直接用 `sent`。**零新詞庫**，與三個既有遊戲同一個來源。

## 4. 一局長什麼樣

一局 3 個詞，每個詞走三步。約 3 分鐘，對齊國小專注力上限（既有遊戲是 5 題／2–3 分鐘）。

```
開場：「我們來背單字！我念一次，你跟著念。」

每個詞：
  ① say_word   「apple，蘋果。跟我念 apple」        → 聽 → 單字模糊命中
  ② spell      「我們來拼：A, P, P, L, E」          → 聽 → 字母命中率  ← 判定主力
  ③ sentence   「I want to eat an apple.」          → 聽 → 例句命中
  → 記錄 word_reviews → 下一個詞

收尾：「今天練了 3 個詞，apple 你拼得最好！」
```

**寬鬆鼓勵制**（已與使用者確認）：任一步命中率 ≥ 0.6 就過。沒過就放慢再來一次，
**同一步最多重試 2 次，第 3 次一律往下走**。

理由：實測已經證明 ASR 會把 `apple` 聽成 `Bbble`。嚴格比對等於讓孩子替 ASR 的
問題受罰，而卡在同一個詞出不去是現場最糟的失敗模式——比答錯還糟。

## 5. 判定演算法 — `server/spelling.py`（新，純函式，零依賴）

```python
def letters_for_tts(word: str) -> str
    # "apple" → "A, P, P, L, E,"
    # 這個格式是實測選出來的唯一可靠寫法，見 §2。改它之前先重跑 spike。

def heard_letters(asr_text: str) -> list[str]
    # ASR 文字 → 字母序列。容忍標點、大小寫、夾雜中文、多餘空白。
    # "A, P, P, L, E." → ["A","P","P","L","E"]

def letter_hit_rate(ref: list[str], heard: list[str]) -> float
    # edit-distance 對齊後的命中率 0–1。沿用 pronunciation._align_score 的作法
    # （同一種 DP，但比的是字母不是音素），刻意不另立一套對齊邏輯。

def word_hit_rate(ref_word: str, asr_text: str) -> float
    # 整字／例句用。以正規化 token 做 edit-distance 比率，取最佳命中的 token。

PASS_THRESHOLD = 0.6
MAX_RETRIES = 2

def record_word_result(student_id, word_zh, correct) -> None
    # 包 srs.schedule + store.upsert_word_review。任何失敗只記 log 不外拋——
    # 記錄是加值，不得拖垮教學迴圈（與 games._due_first 同一個原則）。
```

`spelling.py` 除了 `record_word_result` 之外全是純函式，可以完全離線單元測試、
不需要模型也不需要 DB。

## 6. 接進 games.py 的方式

走**既有的遊戲外掛契約**，不動 pipeline、不動 app.py、不動 WS 協定：

```python
start_spell_along(...)  -> GameState     # 註冊進 _STARTERS
spell_along_prompt(...) -> Line          # 註冊進 _PROMPTS
judge_spell_along(...)  -> GameTurn      # 註冊進 _JUDGES
GAMES += ({"kind":"spell_along", "zh":"背單字", "en":"Spell Along", ...},)
```

`GameState` 欄位重用（frozen dataclass，每回合產生新的）：

| 欄位 | 這個遊戲拿來裝什麼 |
|---|---|
| `hints` | 這一局要練的詞（中文鍵 tuple），由 `_due_first` 挑——**SRS 到期詞排前面** |
| `secret` | 目前正在練哪個詞 |
| `step` | `say_word` \| `spell` \| `sentence` |
| `found` | 已經練完的詞 |
| `target_count` | 3 |

**新增一個欄位** `retries: int = 0`（同一步已重試幾次）。有預設值，
其他三個遊戲完全不受影響。

`game_intent.detect_start` 從 `games.GAMES` 自動讀名稱，不必改——
「我要玩背單字」＝意圖詞「我要」＋名字「背單字」，開局即可用。
選「背單字」而不是四字成語當名字是刻意的：`PROMPT_ORDERING_FINDING` 之外，
2026-07-29 真機實測「火眼金睛」被聽成「佛火眼鏡」，冷僻用字對 ASR 和對孩子都難。

## 7. 學習狀況怎麼記

每個詞走完三步時寫一次 `word_reviews`：

- `correct` = **拼音那一步第一次嘗試就 ≥ 0.6**（判定主力，見 §2 結論 2）
- 經 `srs.schedule()` 算出 `ease`／`interval_days`／`due_at` — 既有 SM-2 二元變體，不改
- 答錯 → `interval_days = 0` → **下一局第一個就會挑到它**（`_due_first` 已經是這個行為）

三步的命中率一併進 `interactions` payload 的既有 `scores` 欄位，教師端看得到。
不新增 DB 表、不新增 API。

現場可以講的一句話：「這孩子上禮拜 `banana` 拼錯，今天第一個練的就是 `banana`。」

## 8. 檔案落點

| 檔案 | 動作 | 規模 |
|---|---|---|
| `server/spelling.py` | 新增 | ~130 行，純函式 |
| `server/games.py` | 三個函式 + 註冊 + `retries` 欄位 | ~+150 行 |
| `tests/test_spelling.py` | 新增 | 純單元，免模型免 DB |
| `tests/test_games_spell.py` | 新增 | 含斷網一致性測試 |
| `docs/GAMES.md` | 補第四個遊戲 | 文件 |

**不動**：`pipeline.py`、`app.py`、`game_intent.py`、WS 協定、DB schema、`edge/`。

## 9. 測試

- `spelling.py` 純函式全覆蓋：字母格式、模糊比對邊界（0.6 上下）、空／亂碼輸入不拋
- **格式回歸測試**：`letters_for_tts("apple") == "A, P, P, L, E,"` — 釘住 §2 的實測結論
- **切段回歸測試**：`split_tts_segments` 對字母序列切出單一 en 段
- **斷網一致性測試**：仿 `test_game_judgement_is_identical_online_and_offline`，
  同一組輸入在 `network_mode` edge／cloud 下判定結果逐字相同
- 重試上限測試：連續 3 次沒過一定往下走，不會卡在同一個詞
- SRS 寫入測試：答錯的詞 `interval_days == 0`，下一局排第一

## 10. 已知邊界（不要在台上講過頭）

- **判定靠 ASR 文字，不是真發音評分。** 孩子唸得腔調很怪但字母對，仍然會過。
  真音素評分（`server/pronunciation.py`）需要 torch + transformers，
  在 Genio 520 上跑不跑得動未驗證，且它目前的定位就是背景診斷層、不進即時路徑。
  → 這次刻意不接（已與使用者確認）。
- **孩子真聲的字母辨識率未知。** §2 的實測是 TTS 合成音當代理，不是童聲。
  開發機沒有錄音裝置，這件事只能上真機驗收。這是本設計最大的未驗證假設。
- 例句那一步的判定較弱（整句 ASR 更容易糊），所以它**不參與** `word_reviews` 的
  對錯判定，只記命中率給教師看。
- 沒有前端 UI，跟三個既有遊戲一樣靠語音開局。

## 11. 風險與對策

| 風險 | 對策 |
|---|---|
| 童聲字母辨識率遠低於 TTS 代理 | 閾值 `PASS_THRESHOLD` 提成可調常數；真機驗收後調整，不必改邏輯 |
| 後人「順手」改掉字母字串格式 | §9 的格式回歸測試直接釘住 |
| 一局太長孩子失去耐心 | `target_count = 3`（既有遊戲是 5），且重試有上限 |
| `record_word_result` 寫 DB 失敗拖垮回合 | 全程 try/except 只記 log，與 `_due_first` 同原則 |
