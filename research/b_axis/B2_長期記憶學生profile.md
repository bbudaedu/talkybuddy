# 說說學伴 TalkyBuddy — B 軸 B2：長期記憶庫 / 學生 profile 設計

> 日期：2026-07-07
> 母脈絡：`research/11_B軸智慧教學層_設計交接.md` §1 對照表指出「記憶庫（長期 profile）」現況＝
> `store` interactions 表有原始資料，但**未抽成學生模型（興趣／已掌握字彙／歷史錯點）**。本檔補這一塊。
> 本檔基於實際 Read 過的 code（2026-07-07）：store.py / diagnose.py / llm.py / pipeline.py / scaffold.py / app.py / config.py。

---

## 0. 一句話結論

- 學生 profile 用 **SQLite 新表 `student_profile`（單列 / 學生，payload 存 JSON）**，沿用 store.py 既有的
  「JSON payload TEXT + threading.Lock」模式，零 migration 風險。
- 抽取邏輯 **規則式起步、重用現有 code**：興趣 = 重用 `scaffold.VOCAB` 的 `cat` 分類計數；
  歷史錯點 = 把 `diagnose.py` 已有的逐批偵測（冠詞 / 中英夾雜 / 句短）**升級成跨時間累積 + 趨勢**；
  已掌握字彙 = student_text 詞頻 × asr_confidence 門檻 × AI 回應無糾錯 三重交叉。
- **不需要向量資料庫**（規模、檢索型態、邊緣限制三點都不成立；門檻見 §4）。
- 接線只有三個乾淨接點：`store.py` 加 3 個函式、新增純標準庫 `server/profile.py`、`app.py` cloud sync 區塊
  （與診斷同一異步時機）呼叫一次。即時路徑（pipeline / llm）**只讀不算**，用 session 級快取。

---

## 1. 現況盤點（誠實區分「已有」vs「需新增」）

### 已有（可直接重用，不要重造）

| 能力 | 現有 code | file:line |
|---|---|---|
| interactions 原始資料（含 student_id / student_text / asr_confidence / ai_response_text / scores） | `store.add_interaction` 寫入、`list_interactions` 讀出 | store.py:116 / 150 |
| 每筆互動都帶 `student_id`（profile 的關聯鍵） | `add_interaction` 內 `body.setdefault("student_id", ...)` | store.py:127 |
| 中文比例偵測 | `diagnose._chinese_ratio` | diagnose.py:76 |
| 英文句長（詞數）偵測 | `diagnose._english_word_count` | diagnose.py:85 |
| 冠詞 a/an 糾錯偵測 | `diagnose._has_article_correction` | diagnose.py:90 |
| 逐批樣式旗標（article / high_chinese / short_sentences + hits 計數） | `diagnose._detect_patterns` | diagnose.py:144 |
| 中文詞庫命中掃描（含長詞優先卡位） | `scaffold._find_zh_vocab` | scaffold.py:259 |
| 50 詞詞庫 + 六大主題分類（food/school/animal/family/action/color） | `scaffold.VOCAB` 每筆的 `cat` 欄 | scaffold.py:49 |
| 規則式 + 可選 Anthropic API 雙軌（fallback pattern） | `diagnose.generate_diagnosis` / `_call_anthropic_api` | diagnose.py:427 / 355 |
| JSON payload 單列存取範式（可照抄成 profile 表） | `store.add_diagnosis`（INSERT OR REPLACE） | store.py:187 |

### 需新增

- SQLite 第三張表 `student_profile` + 3 個 store 函式（`get_profile` / `save_profile` + init_db 加建表）。
- 新檔 `server/profile.py`：`build_profile()` 規則式抽取器（純標準庫，仿 diagnose.py 風格）。
- `app.py` cloud sync 區塊 3 行接線（build → save）。
- （B1/B3 用）diagnose / llm 讀 profile 的注入點（本檔 §5 給接點，實作留給 B1/B3）。

---

## 2. Profile Schema（欄位、型別）

### 2.1 存哪裡：SQLite 新表（非 JSON 欄硬塞進 interactions）

理由：
- interactions 是 **append-only 事件流**（每輪一列），profile 是**該學生的聚合快照**（單列），語意不同，硬塞會破壞既有契約。
- 沿用 store.py 既有「payload TEXT/JSON + PRIMARY KEY」範式，欄位可自由演進不需改 schema（規則式 → LLM 精煉會加欄）。

```sql
CREATE TABLE IF NOT EXISTS student_profile (
    student_id  TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,      -- 下方 JSON
    updated_at  TEXT NOT NULL       -- ISO8601 台北時區
);
```

關聯：`student_profile.student_id` ↔ 每筆 interaction payload 內的 `student_id`
（config.STUDENT_ID = `STUDENT-AMING-004`，store.py:127 已保證每筆都有）。demo 只有一位學生 → 單列；
PRIMARY KEY 設計已天然支援未來多學生。

### 2.2 payload JSON 結構

```json
{
  "student_id": "STUDENT-AMING-004",
  "updated_at": "2026-07-07T21:30:00+08:00",
  "source_seq": 40,
  "interaction_count": 40,

  "interests": [
    {"topic": "animal", "label": "動物", "hits": 12},
    {"topic": "food",   "label": "食物", "hits": 9}
  ],

  "mastered_vocab": [
    {"en": "apple", "zh": "蘋果", "cat": "food",   "seen": 8, "correct": 7, "last_seq": 38}
  ],
  "learning_vocab": [
    {"en": "orange", "zh": "橘子", "cat": "food",  "seen": 3, "correct": 1, "last_seq": 39}
  ],

  "error_patterns": [
    {"type": "article_ao", "label": "冠詞 a/an 誤用",      "hits": 9, "recent_hits": 1, "trend": "improving"},
    {"type": "mixed_lang", "label": "中英夾雜比例偏高",    "hits": 20, "recent_hits": 2, "trend": "improving"},
    {"type": "short_sent", "label": "句子偏短（<4 英文詞）", "hits": 14, "recent_hits": 0, "trend": "improving"}
  ],

  "difficulty": {
    "level": 2,
    "avg_en_words": 4.3,
    "en_ratio": 0.55,
    "score_avg": 66
  },

  "emotional_recent": "整體分數較前次上升，開口意願與自信明顯提升",

  "generated_by": "rule"
}
```

型別：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `student_id` | str | 關聯鍵 |
| `updated_at` | str (ISO8601) | 台北時區，仿 store._now / diagnose._today |
| `source_seq` | int | 已消化到的最大 interaction `seq`（增量抽取用；起步可全量重算） |
| `interaction_count` | int | 累積互動筆數 |
| `interests` | list[{topic:str, label:str, hits:int}] | 依 VOCAB `cat` 命中次數排序，取 top-N |
| `mastered_vocab` | list[{en, zh, cat:str, seen:int, correct:int, last_seq:int}] | 已穩固字彙 |
| `learning_vocab` | list[同上] | 出現過但未穩固 |
| `error_patterns` | list[{type, label:str, hits:int, recent_hits:int, trend:str}] | trend ∈ improving/regressing/stable |
| `difficulty` | {level:int(1-5), avg_en_words:float, en_ratio:float, score_avg:int} | 難度落點（B3 CEFR 接手的底座） |
| `emotional_recent` | str | 快取最新 diagnosis 的 emotional_status |
| `generated_by` | str | "rule" 或 "llm"（標示是否經導師 LLM 精煉） |

> `mastered_vocab` / `error_patterns` / `difficulty` 直接餵 B1 的 companion_directive 與 B3 的 CEFR 升降級規則，
> 是 B 軸後續子系統的共同資料底座（呼應交接檔 §2「B2 是 B1/B3 的資料底座」）。

---

## 3. 抽取邏輯（規則式起步 → 導師 LLM 精煉）

輸入：`store.list_interactions(limit=大)`（回傳新→舊，抽取時可自行排序）＋ `store.list_diagnoses()`（取最新診斷的
emotional_status / scores）＋ 上一版 profile（增量用）。全部重用既有讀取函式，不碰 A 軸即時路徑。

### 3.1 興趣主題 interests
- 對每筆 `student_text`：
  1. `scaffold._find_zh_vocab(text)`（scaffold.py:259）抓中文詞庫命中；
  2. 掃 `re.findall([A-Za-z]+)` 對照 `scaffold.VOCAB` 各筆 `en`，抓英文命中。
- 每個命中詞取其 `VOCAB[...]["cat"]`，累加到 `Counter[cat]`。
- 次數排序 → `interests`，label 用固定對照（food→食物 / school→學校 / animal→動物 / family→家庭 / action→動作 / color→顏色）。
- **零新詞庫**：完全靠 scaffold 既有 50 詞 × 6 分類。

### 3.2 已掌握字彙 mastered_vocab / learning_vocab
規則式三重交叉（規則起步、之後 LLM 精煉）：
1. 該英文詞在 `student_text` 出現（重用 3.1 的英文命中掃描）；
2. 該輪 `asr_confidence >= 0.8`（發音辨識清楚，config.ASR_CONF_THRESHOLD=0.5 是兜底門檻，掌握門檻另設較高）；
3. 該輪 `ai_response_text` **不含糾錯**（用 `diagnose._has_article_correction` 反向判定，以及是否含「記得/要用/改成」等提示語）→ 視為「正確使用」，`correct += 1`。
- 統計每個英文詞的 `seen` / `correct` / `last_seq`：
  - `correct >= 2` 且 `correct/seen >= 0.6` → `mastered_vocab`；
  - 其餘出現過的 → `learning_vocab`。
- 起步的偽陽性風險（asr_confidence ≠ 語言掌握）見 §6，交由導師 LLM 精煉收斂。

### 3.3 歷史錯點 error_patterns（把 diagnose 的逐批旗標升級成跨時間累積）
- 逐筆重跑 diagnose 既有偵測：
  - `article_ao`：`diagnose._has_article_correction(ai_response_text)`（diagnose.py:90）命中 → hits+1；
  - `mixed_lang`：`diagnose._chinese_ratio(student_text) > 0.40`（diagnose.py:76）；
  - `short_sent`：`diagnose._english_word_count(student_text) < 4`（diagnose.py:85）。
- **趨勢 trend**：把互動依 seq 排序，比較「最近 5 筆的命中率」vs「全體命中率」：
  - 最近明顯較低 → `improving`；較高 → `regressing`；相近 → `stable`。
  - `recent_hits` = 最近 5 筆的命中次數。
- 這正是交接檔 §1 說的「diagnose 只有逐批 flags，缺跨時間累積」的補位。

### 3.4 難度落點 difficulty
- `avg_en_words` = 全體 `_english_word_count` 平均；`en_ratio` = 英文詞 /（英文詞+中文字）平均（重用 compute_scores 內同款算法）。
- `score_avg` = 最新 diagnosis 四維平均（`store.list_diagnoses()` 末筆）。
- `level`（粗 1-5）＝ 由 `score_avg` 分段 + `avg_en_words` 微調（例：score<50→1、50-60→2、60-70→3…）。
  這只是**種子**，正式 CEFR 微階梯（1-1～5-5）與升降級規則屬 B3，本檔只提供欄位與初值。

### 3.5 增量 vs 全量
- profile 存 `source_seq`；interactions 是 append-only、seq 遞增（store.py:116 自增主鍵）→ 天然支援「只吃 seq > source_seq 的新資料累加」。
- **起步建議全量重算**：本專案規模（單機、數十～數百筆、純 Python 統計）成本毫秒級，全量最簡單且無狀態漂移。
  `source_seq` 欄先寫上、留給資料量成長後再切增量。

### 3.6 導師 LLM 精煉（可選第二段，沿用現有雙軌）
- `diagnose._call_anthropic_api`（diagnose.py:355）已把近 10 筆組成 `brief` 餵 Sonnet。可比照新增
  `profile.refine_with_llm(rule_profile, interactions)`：把規則式 profile 當草稿，請 Sonnet 精煉
  `interests` 的自然語言描述、複核 `mastered_vocab` 是否真掌握、把 `error_patterns` 寫成可教學的描述。
- **失敗一律 fallback 回規則式 profile**（`generated_by` 標 "rule"），與 diagnose 現有韌性一致。純標準庫、import 期不連外。

---

## 4. 是否需要向量資料庫？——不需要（附跨越門檻）

誠實評估，三點皆不成立：

1. **資料規模小**：單學生數十～數百筆互動，詞庫固定 50 詞。結構化欄位（cat 計數、詞頻表）即可表達
   「興趣 / 已掌握字彙」，O(n) 全掃毫秒級。
2. **檢索型態是「聚合統計」不是「語意相似」**：目前需求是 count / avg / trend，不是「找語意相近的歷史片段」。
   向量庫解決的是大規模非結構化語意搜尋，本專案沒有這需求。
3. **邊緣限制**：目標硬體 MediaTek Genio 520，CPU/RAM 吃緊。額外跑 embedding 模型 + 向量索引
   （faiss/chroma）是純負擔，與「本地離線、降級韌性」的專案裁決相悖。

**未來跨越以下任一門檻才重新評估**：
- (a) 對話脫離固定詞庫、累積數千筆以上開放式語料，且要對當前話題做語意 RAG 檢索歷史片段；
- (b) 詞庫開放化、要對任意學生自由發言做語意分群 / 主題發現；
- (c) 多學生規模化、要「找相似學習者」推薦教學策略。
- 即使到那時，也可先用 **SQLite FTS5 全文檢索**頂著，仍不必立刻上專用向量庫；SQLite + 結構化 profile 是本階段正解。

---

## 5. 接回 code 的接點（file:line / 函式 / 參數）

### 5.1 store.py（新增，仿既有範式）
- **init_db()（store.py:98）**：在既有兩條 `CREATE TABLE` 後加第三條建 `student_profile`（見 §2.1 SQL）。
- **新增 `get_profile(student_id: str | None = None) -> dict | None`**：
  `SELECT payload FROM student_profile WHERE student_id = ?`，無列回 None；student_id 預設用 `_student_id()`（store.py:64）。
- **新增 `save_profile(d: dict) -> None`**：仿 `add_diagnosis`（store.py:187）的
  `INSERT OR REPLACE INTO student_profile (student_id, payload, updated_at) VALUES (?, ?, ?)`，
  用 `_lock` + `_get_conn()` 包住。
- **seed_demo()（store.py:209）末端（可選）**：灌完 20 筆 demo 互動後呼叫一次 build+save，
  讓 teacher 端 demo 一開就有 profile 可展示。

### 5.2 新檔 server/profile.py（純標準庫，仿 diagnose.py）
- `build_profile(interactions: list[dict], diagnoses: list[dict], prev: dict | None = None) -> dict`：
  規則式抽取（§3.1–3.4），重用 `scaffold.VOCAB` / `scaffold._find_zh_vocab` /
  `diagnose._chinese_ratio` / `diagnose._english_word_count` / `diagnose._has_article_correction`。
- （可選）`refine_with_llm(...)`：§3.6，有 `ANTHROPIC_API_KEY` 才走，失敗 fallback。
- 放獨立檔而非塞進 diagnose.py，保持介面乾淨、可獨立測試（與 scaffold/diagnose 一致的零依賴原則）。

### 5.3 app.py cloud sync 接線（app.py:139–147）
在 `generate_diagnosis` → `add_diagnosis` 之後、同一 try 區塊內加：
```python
from server import profile
all_inter = store.list_interactions(limit=500)
prof = profile.build_profile(all_inter, store.list_diagnoses(), store.get_profile())
store.save_profile(prof)
```
- 與診斷**同一異步時機**（切 cloud 時），不進即時對話路徑 → 符合交接檔 §3「導師異步、絕不擋即時」。
- 失敗不影響同步結果（比照 app.py:148 既有 except 韌性）。

### 5.4 diagnose 讀 profile（B1/B3 用，本檔給接點）
- `generate_diagnosis`（diagnose.py:427）可多收 `profile: dict | None`，把 profile 摘要
  （已掌握詞、興趣、錯點 trend）併入 `_call_anthropic_api` 的 `brief`（diagnose.py:362），
  讓 Sonnet 產 B1 的 `companion_directive` 時知道「已會 apple/dog、興趣動物、冠詞在改善」。

### 5.5 llm.py / pipeline.py 讀 profile（陪聊即時路徑，B1 用）
- **pipeline.py**：在 `VoicePipeline.__init__`（pipeline.py:111）或 session 首輪，
  `self._profile = store.get_profile()` **快取一次**；`_process_text`（pipeline.py:179）呼叫
  `self.llm.generate(...)`（pipeline.py:204–208）時把 profile 濃縮字串一併傳入。
- **llm.py**：`EdgeLLM.generate`（llm.py:102）沿 B1 規劃新增 `directive` / profile 摘要參數，
  拼進 `user_prompt`（llm.py:114），例如「這位學生已掌握：apple, dog；興趣：動物；常見錯點：冠詞 a/an（改善中）」。
  `_SYSTEM_PROMPT`（llm.py:44）保留骨架，可變狀態放 user turn。
- **鐵律**：profile 查詢**不可**進 `generate` 內（延遲敏感）；只在 session 級快取、由 pipeline 準備好再傳入。

---

## 6. 未解風險 / 待人決策

1. **更新時機耦合 cloud sync**：profile 目前跟 diagnosis 綁在「切 cloud」時更新（app.py:139）。若 demo 全程 edge，
   profile 不會刷新。決策：是否在 seed 時預建 + 每 N 輪或 session 結束觸發一次（仍須異步、不進即時路徑）。
2. **「已掌握」判定偏寬**：asr_confidence 高 ≠ 語言真掌握，規則式會有偽陽性；需靠「AI 回應無糾錯」交叉驗證 +
   導師 LLM 精煉收斂。門檻（correct≥2、比率≥0.6、conf≥0.8）為初值，待實測校準。
3. **詞庫外盲區**：mastered/interests 只能涵蓋 scaffold VOCAB 50 詞內；學生說的詞庫外英文詞無法分類 cat，
   興趣抽取有盲點。開放化需 §4(b) 門檻（屆時才考慮語意化）。
4. **單學生假設**：schema 已支援多學生（PRIMARY KEY），但 demo 固定 STUDENT-AMING-004；多學生的 UI/切換未設計。
5. **增量未實作**：起步全量重算，資料量大才需切 `source_seq` 增量（本專案規模無虞，但需標記為技術債）。
6. **隱私 / 資料留存**：profile 存學生學習軌跡，COPPA、留存政策、家長同意屬 B4 橫切，本檔未涵蓋，須併 B4 一起收口。
