# B3　CEFR 難易度階梯 + 主題迭代（設計）

> 日期：2026-07-07
> 子題：B3（智慧/教學層）。把 `diagnose.py` 的四維 0–100 分映射到「可計算的 Level 微階梯」，並定升/降級與換主題規則。
> 前置：先讀 `research/11_B軸智慧教學層_設計交接.md`（B 軸拆解）與現有 code（`diagnose.py` / `scaffold.py` / `llm.py` / `pipeline.py` / `store.py`）。
> 邊界：B3 只決定「難度等級與主題」，不碰音訊傳輸（A 軸）；不引入 LangGraph/AutoGen（延用 diagnose 純 urllib 雙軌）。

---

## 0. 一句話總結

B3 = 一個**新的純規則模組 `server/curriculum.py`**（stdlib-only，比照 scaffold/diagnose 可獨立測試），輸入 diagnose 已算好的 `scores` 四維 + `flags` + 近窗互動，輸出一個 `level_state`（band-step + CEFR 標籤 + 主題 + move 決策 + 給 B1 的 directive 提示）。它**不重寫 scaffold 規則**，主要透過 B1 的 `companion_directive` 影響難度；等級與主題**持久化在 diagnoses payload**（沿用 `store.add_diagnosis`，向後相容）。

---

## 1. 分級參考：CEFR × 劍橋 YLE × 台灣國小（落地成可計算 Level）

### 1.1 權威對應（已用 web 確認）

| 劍橋 YLE | CEFR | 對象年齡 | 能力錨點 |
|---|---|---|---|
| Pre A1 Starters | **pre-A1** | 6–12 | 認單字、聽指令、指圖、單詞/固定短語回答 |
| A1 Movers | **A1** | 6–12 | 完整簡單句、日常主題、簡單問答 |
| A2 Flyers | **A2** | 6–12 | 擴充句（形容詞/介系詞/連接詞）、簡單對話延展 |

- 來源：Cambridge English 官方——Starters 對準 pre-A1、Movers 對準 A1、Flyers 對準 A2，皆為 6–12 歲設計。
- 台灣 108 課綱：英語自國小三年級起始，**國小畢業目標約落在 pre-A1 ~ A1**（少數學生達 A2），國中才推進 A2。故本專案（國小中英雙語帶讀）的**主戰場是 pre-A1 → A1**，A2 為天花板挑戰區，不預設多數學童會長駐 Band 5。

### 1.2 可計算 Level：5 大帶 × 5 微步（Level 1-1 ～ 5-5，共 25 微階）

用「大帶 band（1–5）對 CEFR 能力階段」＋「微步 step（1–5）在帶內細分」，讓玩偶能小步慢升、避免一次跳太多打擊信心。

| Band | CEFR / YLE 對應 | 台灣國小定位 | 目標語言形式（target form） | 對應 scaffold 產出 |
|---|---|---|---|---|
| **1** | pre-A1 低（前 Starters） | 三年級起步 / 零起點 | **單字 / 名詞片語**：`apple` → `an apple` | `VOCAB[k]["np"]` |
| **2** | pre-A1 高（Starters） | 三～四年級 | **固定框架短句**（3–4 詞）：`I see a dog.` | `VOCAB[k]["sent"]` |
| **3** | A1（Movers） | 四～五年級 | **完整句 + 冠詞/複數正確 + 形容詞**：`I see a big dog.` | `sent` + 顏色/形容詞擴充 |
| **4** | A1 高 / A2 低（Flyers 入門） | 五～六年級 | **擴充句：介系詞/連接詞/疑問句**：`I see a dog in the park.` / `What color is it?` | `sent` + `_EXTENSION_QUESTIONS` |
| **5** | A2（Flyers） | 六年級 / 資優 | **複合句 + 原因/時態 + 對話延展**：`I like dogs because they are cute.` | 多句串接（B1 directive 引導） |

- **編碼**：`level = "{band}-{step}"`；數值索引 `level_index = (band-1)*5 + step`，範圍 1–25，方便比較、加減與畫成長曲線。
- **microsteps 的意義**：同一 band 內 step 1→5 代表「同能力階段內的熟練度累積」——step 主要驅動**主題輪換與提示強度**（step 低給多提示、step 高減提示），**跨 band 才真正換語言形式**。這樣升級體感細緻、降級也不會一次掉一大格。

---

## 2. 四維分數 → Level 的映射函式（可解釋、可調）

### 2.1 綜合能力分 CAS（Composite Ability Score）

diagnose 已產四維 `pronunciation / fluency / vocabulary / grammar`（0–100，且已做 0.6/0.4 平滑，見 `diagnose.py:111 _compute_scores`）。B3 以**加權平均**收斂成單一 CAS：

```
WEIGHTS = {"fluency": 0.30, "vocabulary": 0.25, "grammar": 0.25, "pronunciation": 0.20}
CAS = sum(scores[d] * WEIGHTS[d] for d in dims)      # 0–100
```

- **權重可解釋**：本產品是「無螢幕、引導開口」的語伴，早期最該獎勵的是**願意開口（fluency）**與**用得出詞（vocabulary）**；grammar/pronunciation 是後期精修，故權重略低。權重集中成一個 `WEIGHTS` 常數，隨 demo 反饋可調。
- 四維鍵順序沿用 `diagnose._DIM_KEYS`（`diagnose.py:41`），避免另立 schema。

### 2.2 CAS → band（帶）＋ 最弱維度 gate（防跛腳升級）

```
BAND_CUTS = [35, 50, 65, 80]      # <35→B1, 35–50→B2, 50–65→B3, 65–80→B4, ≥80→B5
```

只用 CAS 會讓「fluency 85 但 grammar 25」的孩子被平均拉上 Band 4，語言形式卻撐不住。故加**最弱維度 gate**：最弱一維設定 band 天花板，取兩者較小值（可解釋、可調）。

```
def _gate_band(min_dim):                 # 最弱維度限制的最高 band
    if min_dim < 30: return 2
    if min_dim < 45: return 3
    if min_dim < 60: return 4
    return 5

band = min(_band_from_cas(CAS), _gate_band(min(scores.values())))
band = max(1, band)
```

### 2.3 band 內 step（微步）

step 由 CAS 在該 band 區間內的相對位置決定（線性五等分）：

```
low, high = _band_range(band)            # 例：Band3 → (50, 65)；Band1→(0,35)、Band5→(80,100)
pos = (CAS - low) / (high - low)         # 0–1
step = min(5, 1 + int(pos * 5))          # 1–5
level_index = (band - 1) * 5 + step      # 1–25
```

### 2.4 可解釋輸出

映射函式一併回傳「為什麼是這個 level」的欄位，供家長/老師頁與 debug 使用：

```
{
  "level": "3-2", "band": 3, "step": 2, "level_index": 12,
  "cefr": "A1 (Movers)",
  "cas": 58,
  "explain": "fluency 66/vocab 63/grammar 48/pron 61 → CAS 58；grammar 偏低故 gate 上限 Band4，未觸頂。"
}
```

> 誠實區分：`scores` 四維與平滑**是 diagnose 現有**（`_compute_scores`）；CAS、gate、band/step、level_index、cefr 標籤**全為 B3 新增**。

---

## 3. 升降級規則（掛回 diagnose 現有 flags，含遲滯）

### 3.1 現有可用訊號（`diagnose._detect_patterns`，`diagnose.py:144`）

`flags` 已提供三旗標 + 一計數，B3 直接吃：

- `article_correction`（bool）／`article_hits`（int）：冠詞 a/an 被修正。
- `high_chinese_ratio`（bool）：中英夾雜 > 40%。
- `short_sentences`（bool）：平均句長 < 4 英文詞。
- 另可用 `emotional_status` 的趨勢（`diagnose.py:214`，delta ≥+1.5 升／≤−1.5 降）與互動筆數 `len(interactions)`。

### 3.2 升降級決策（move）

Level 用**平滑後 CAS 定「基準 band/step」**，再由 flags/趨勢做**小幅事件微調**，並加**遲滯（hysteresis）**避免每窗跳動。輸出 `move ∈ {promote, hold, lateral, demote}`。

**升級（promote，需連續證據，慢升）**

- 條件（同時成立才 +1 step）：
  - `not short_sentences` 且 `not high_chinese_ratio`（願意整句英文、主動擴句），且
  - `emotional_status` 趨勢非下滑（delta > −1.5），且
  - 本窗 CAS ≥ 上窗 CAS（用 prev level_state 比較）。
- **跨 band（step 5 → 下一 band step 1）需連續 2 窗都達升級條件**（遲滯），單窗最多 +1 step。
- grammar-gate 仍卡著時（`_gate_band` < CAS band），優先要求把 `article_correction` 清乾淨才放行跨 band。

**降級（demote，快降護信心，但語氣包裝成「換個玩法」）**

- 觸發任一：
  - `giveup_signal`（新增，見 §3.3）達門檻：近窗多次「不會/I don't know/沉默」。
  - `short_sentences` 且 `high_chinese_ratio` **持續兩窗**（退回中文骨架）。
  - `emotional_status` 趨勢 delta ≤ −1.5（明顯遇挫）。
- 動作：`level_index -= 1`（最多每窗 −1，降到 band 底時 step→上一 band step5）。**對外不說「降級」**，B1 directive 改用「換個更好玩的說法／二選一」。

**橫移（lateral，疲態不降級，只換主題）**

- 觸發：互動筆數較上窗明顯下降（疲倦），或同主題重複率高（B2 提供），但分數沒掉。
- 動作：**level 不動**，只把 `topic` 換成同 band 適配的新主題（§4），並降一級提示強度讓孩子喘口氣。

**維持（hold）**：以上皆不成立。

### 3.3 需新增的訊號：`giveup_signal`（重要接點與風險）

- **現況坑**：pipeline 在 ASR 低信心/空字串時走 `FALLBACK_LINES` 且**不寫 DB**（`pipeline.py:182-190`）。因此「真沉默」根本不會進 interactions，diagnose 看不到 → **「沉默→降級」訊號現在拿不到**。
- **兩個落地選項（擇一，建議 A）**：
  - **A（低侵入）**：在 `diagnose._detect_patterns` 加一條字面偵測——`student_text` 命中「不會 / 不知道 / 我不會 / I don't know / dunno」→ `giveup_signal` 計數。這些是**有被寫進 DB 的放棄語**，零改 pipeline。
  - **B（完整）**：pipeline 在 fallback 分支也記一筆極簡事件（新欄位 `event:"fallback"`），或在 `store` 加一個 `silence_count`。可反映真沉默，但要動 pipeline/store schema，風險較高。
- demote 門檻：近窗 `giveup_signal ≥ 2`（可調常數 `GIVEUP_DEMOTE_TH`）。

### 3.4 遲滯與韌性

- 用 prev diagnosis 的 `level_state`（diagnose 已收 `prev` 參數，`diagnose.py:427`）比較，跨 band 需連兩窗、單窗僅動 1 step。
- level_state 缺失（首次、或舊資料無此欄）→ 直接用 §2 的 CAS 冷啟動定 level，`move="hold"`，不炸。

---

## 4. 主題庫與迭代（動物→食物→太空…，接 B2 興趣）

### 4.1 主題庫從現有 scaffold 分類長出來

scaffold 已有 6 類（`VOCAB[*]["cat"]`，`scaffold.py:49`）：`food / school / animal / family / action / color`，且每類已有 `_EXTENSION_QUESTIONS`（`scaffold.py:152`）。B3 把「主題」= scaffold 的 `cat`，並標註各主題**解鎖 band**與**難度傾向**：

| 主題 topic | 來源 | 建議解鎖 band | 備註 |
|---|---|---|---|
| animal | scaffold `animal` | 1+ | 冠詞 a/an 練習首選（a dog / an elephant） |
| food | scaffold `food` | 1+ | 冠詞 + some（不可數）鷹架 |
| color | scaffold `color` | 2+ | 形容詞，接 Band3 `big/red` 擴句 |
| family | scaffold `family` | 2+ | 所有格 my，he/she |
| school | scaffold `school` | 2+ | this/that、介系詞 in |
| action | scaffold `action` | 3+ | 動詞 + to、三單 -s |
| **space / weather / hobby …** | **新增詞條** | 3+ | 「太空」等主題**現無詞條**，需擴 `VOCAB`（見 §6 風險） |

- **誠實區分**：animal/food/color/family/school/action **是 scaffold 現有**；space、weather、hobby 等**需新增 `VOCAB` 詞條**（B3 只定策略，詞庫擴充是另一小工）。

### 4.2 主題迭代規則（三種驅動）

1. **掌握驅動（mastery → 前進）**：某主題 vocab 命中且正確率高（用 B2 的 `mastered_vocab` 覆蓋率，或 diagnose 窗內該類詞高分）→ 標記主題「已熟」，輪到**下一個未熟且已解鎖**主題。順序預設：`animal → food → color → family → school → action →（新主題）`，可被興趣重排。
2. **疲態驅動（fatigue → 橫移換主題）**：§3.2 lateral 觸發時，**同 band 內**換一個孩子沒玩膩的主題，重新點燃興趣，level 不動。
3. **興趣驅動（B2 → 重排隊列）**：B2 profile 提供興趣排序，B3 用它對「已解鎖且未熟」主題**加權挑選**，讓孩子先玩喜歡的。

### 4.3 與 B2 的介面（B3 消費 B2 profile）

B3 期望 B2 提供（若 B2 尚未就緒，全部給安全預設，不炸）：

```
profile = {
  "interests": ["animal", "food", ...],      # 興趣排序 → 主題隊列加權
  "mastered_vocab": {"apple", "dog", ...},   # 已掌握字彙 → 算主題熟練度、避免重練
  "error_history": {"article": 5, "plural": 2}  # 歷史錯點 → 偏置 focus 與 sub-step 提示
}
```

- B3 對 B2 的**唯一硬需求**只有 `interests`（排主題）；其餘缺省可退化為「用 diagnose 窗內統計近似」。介面用 dict + `.get(默認)`，B2/B3 可各自演進。

---

## 5. Level 如何餵給 B1 的 directive（B3 ↔ B1 介面）

### 5.1 B3 產出 `level_state`（結構化，供 B1 組 directive）

B3 的最終輸出是一個 dict，**掛進 diagnosis**（新欄位，向後相容）：

```
level_state = {
  "level": "3-2", "band": 3, "step": 2, "level_index": 12,
  "cefr": "A1 (Movers)", "cas": 58,
  "move": "promote",                 # promote|hold|lateral|demote
  "topic": "animal", "next_topic": "food",
  "target_form": "完整句：I see a + 形容詞 + 名詞.",
  "focus": "冠詞 a/an",              # 由最弱維度 / flags 決定
  "prompt_strength": "reduce",       # step 高→減提示；demote/step 低→加提示
  "directive_hint": "孩子能用完整句描述動物，下一步引導加顏色形容詞，多問 What color is the dog？；若困惑退回單字二選一（a dog / a cat）。"
}
```

### 5.2 兩種接法（依 B1 進度擇一，皆低侵入）

**接法 A（推薦，與 B1 §3 一致）——經 diagnose 回寫 `companion_directive`**

- B1 規劃在 `diagnose.generate_diagnosis`（`diagnose.py:427`）新增 `companion_directive` 欄位。B3 的 `curriculum.compute_level_state()` 就在 diagnose 算完 `scores`+`flags` 後被呼叫，把 `level_state` 併入診斷，並把 `directive_hint`／`target_form`／`focus` 拼成 B1 的 `companion_directive` 字串。
- 具體接點（新增，不改既有回傳既有欄位）：在 `diagnose._rule_based_diagnosis`（`diagnose.py:296`）與 `_validate_diagnosis`（`diagnose.py:315`）各補 `level_state` / `companion_directive` 的組裝與驗證。

**接法 B（直達陪聊）——`EdgeLLM.generate` 收 directive**

- B1 已規劃讓 `EdgeLLM.generate` 增收 `directive: str | None`，拼進 `user_prompt`（`llm.py:114`）。B3 的 `directive_hint` 就是 directive 內容之一。
- pipeline 接線點：`pipeline._process_text`（`pipeline.py:179`）在呼叫 `self.llm.generate(...)`（**現為 `pipeline.py:206`**）前，讀「最新 level_state.directive_hint」傳入。directive 來源：最新 diagnosis payload（`store.list_diagnoses()` 取最後一筆）。

> 職責切線：**B3 只產結構化 `level_state` 與一段 `directive_hint`**；把它變成自然語言 directive 塞進 prompt、以及 prompt 拼裝，是 **B1** 的事。B3 不碰 `llm.py` 的 prompt 字串本體，只提供資料。

### 5.3 難度如何真的「變難/變簡單」

- **主路徑（近 demo）**：透過 B1 directive 引導 LLM 生成不同複雜度的帶讀句（Band 越高，directive 要求越長、越多形容詞/連接詞/追問）。scaffold 仍是降級主幹，directive=None 時＝現況，韌性不變。
- **可選強化（未來）**：讓 `scaffold.respond` / 目標句產生器**收 `band` 參數**，依 §1.2 的 target form 選字數與句型（Band1 給 `np`、Band2 給 `sent`、Band3 疊形容詞、Band4 疊 `_EXTENSION_QUESTIONS`/介系詞、Band5 串複合句）。這是較大改動，列為 optional，不進第一版。

---

## 6. 落地清單、接點與風險

### 6.1 新增（B3 本體）

- **新檔 `server/curriculum.py`**（stdlib-only，比照 scaffold/diagnose 可 `python3 server/curriculum.py` 自我驗證）：
  - `compute_cas(scores) -> int`
  - `scores_to_level(scores) -> dict`（band/step/level_index/cefr/cas/explain）
  - `decide_move(level, prev_level_state, flags, emotional_delta, giveup, fatigue) -> str`
  - `pick_topic(level, profile, prev_topic, mastery) -> (topic, next_topic)`
  - `compute_level_state(diagnosis, profile, prev_level_state) -> level_state`
  - 常數：`WEIGHTS / BAND_CUTS / _gate_band 門檻 / GIVEUP_DEMOTE_TH`（集中可調）。

### 6.2 需接回現有 code 的具體接點（file:line、函式、參數）

- `diagnose.py:427 generate_diagnosis(interactions, prev)`：算完後呼叫 `curriculum.compute_level_state(diag, profile, prev.get("level_state"))`，把 `level_state`（＋B1 的 `companion_directive`）併入回傳 dict。
- `diagnose.py:144 _detect_patterns`：新增 `giveup_signal` 計數（偵測 student_text 的「不會 / I don't know / 不知道」），供 §3.3 demote。
- `diagnose.py:315 _validate_diagnosis`：API 路徑回傳也要能容納/正規化 `level_state`（缺就用規則式補算，不因雲端漏欄而炸）。
- `store.add_diagnosis` / `list_diagnoses`（`store.py:187 / 199`）：payload 為 JSON TEXT，**新增 `level_state` 欄位天然相容**，無需改 schema；讀最新 level 用 `list_diagnoses()[-1]`。
- `scaffold.py:49 VOCAB` 的 `cat` 與 `scaffold.py:152 _EXTENSION_QUESTIONS`：作為主題庫來源與 Band4 追問素材（B3 讀取，不改寫）。
- （接法 B 時）`pipeline.py:206`（`self.llm.generate` 呼叫處）：傳入最新 `directive_hint`；`llm.py:114 user_prompt`：由 B1 拼入。

### 6.3 未解風險 / 待人決策

- **沉默訊號拿不到**：demote 的「沉默」訊號因 pipeline fallback 不寫 DB 而缺（`pipeline.py:182`）；第一版採 §3.3-A 只偵測「說得出口的放棄語」，真沉默要等選項 B 動 pipeline/store。需拍板走 A 或 B。
- **「太空」等新主題無詞條**：`VOCAB` 目前僅 6 類、無 space/weather/hobby；B3 定了迭代策略，但**詞庫擴充需另一小工**，否則高 band 主題輪換會落空。
- **CAS 權重與 band 門檻是拍腦袋起點**：`WEIGHTS / BAND_CUTS / _gate_band` 需用 seed_demo（阿明 14 天曲線，`store.py:238`）跑一遍校準——阿明分數 pron 48→68、gra 42→65，預期兩週應從 Band1-x 升到約 Band3，可當回歸基準。
- **pronunciation 來源限制**：per-interaction 無 pronunciation，只有 `asr_confidence` 映射（`diagnose.py:124`），CAS 的 pron 維度信號較弱；權重 0.2 已保守，但需知其侷限。
- **B2 未就緒**：主題興趣重排依賴 B2 `interests`；B2 未完成前，`pick_topic` 需能只靠 diagnose 窗內統計運作（已設計預設退化）。
