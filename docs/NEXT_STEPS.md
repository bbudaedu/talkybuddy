# 交接：下一個 session 從這裡開始

**更新**：2026-07-27（凌晨全自動 session）　**決賽**：約 2026-07-30（剩約 3 天）

---

## 0. 三十秒看懂現況

| 項目 | 狀態 |
|---|---|
| 測試基準 | **811 passed**（`pytest -q` 全套：`tests/` 785 + streaming 26） |
| | session 開始時是 552，而且全套根本跑不動 |
| 離線路徑（斷網橋段） | ✅ 端到端跑通，`source` 全為 `rule`、零出境 |
| 三個 agent（派作業／週報／決策判斷） | ✅ 完成並接進 `pipeline._refresh_directive` |
| AgentCore 資源（新加坡） | ✅ Memory ACTIVE、3 個 Harness READY、IAM 已過官方安全稽核 |
| AgentCore 程式碼 | ✅ 已接好，**flag 關閉**（`TALKYBUDDY_AGENT_BACKEND` 未設即走原路徑） |
| Code review 待修項 | ✅ **全部清空**（B2/B4 + W1–W9） |
| 間隔重複（原「推薦演算法」） | ✅ 已實作並接線 |
| 教材依據（課綱） | ✅ 官方資料已抓取入庫，可現場佐證 |
| 題庫 | ✅ 44 → **136 詞**，99.3% 落在教育部基本 1,200 字內 |
| AgentCore 共用 skill | ⚠️ 已產生、**尚未掛上**（等 AWS 放行） |
| **雲端實際產出** | ❌ **零實證** — AWS 帳號被鎖，模型呼叫從未成功 |

**決賽策略（使用者 2026-07-26 拍板）**：以雲端為主，決賽當天用**主辦方提供的 AWS 資源**展示。
所以自己帳號被鎖不是決賽風險，但**代表雲端路徑至今未經任何實跑驗證**——這仍是最大的未知數。

---

## 1. 這個 session 做完的事（commit `7546f68`..`dad8505`）

### ① B4 隱私外洩 — 已修（`7546f68`）

`report.py` 與 `orchestrator.py` 把整個 diagnosis `json.dumps` 後送上雲，遮罩卻只蓋三個白名單欄位。
漏掉的 `companion_directive` 與 `instructions` 是 LLM 依孩子講的話生成的自由文字。
**而且 `guardrails.deidentify` 擋不住中文姓名**——它只遮個資詞、三位以上連續數字、非詞庫的
Title-case 英文專名。所以「有呼叫 deidentify」從來不等於「沒外洩」；profile 的 `name` / `notes`
同樣是原文上雲。

改成白名單投影，抽成 `server/agents/privacy.py` 給三個 agent 共用。測試不 mock `deidentify`，
直接攔截送上雲的 prompt 字串斷言中文姓名不在裡面（修之前三個 agent 全紅）。

### ② 真實課綱 — 已抓取（`49b2c12`、`dad8505`）

`scripts/extract_curriculum.py` 從**國教院官方 ODT**（教育部 107.04.16 發布《十二年國教課程綱要
語文領域－英語文》）抽出四個附錄，連同來源網址與 SHA-256 存進
`data/curriculum/moe_english_2018.json`：

- 附錄五 表一 基本 1,200 字（1211 筆）、表二 其他常用 800 字（794 筆）
- 附錄五 **表三 依主題分類的 2,000 字**（37 個主題，逐字標註是否為基本字）
- 附錄三 主題 40 個 / 體裁 19 種、附錄四 溝通功能 45 條、附錄六 國中文法句構 113 條

查詢層是 `server/curriculum_data.py`，端點是 `GET /api/curriculum`（無需 JWT，全是公開資料）。

**可以直接講的數字**：`scaffold.VOCAB` 的 44 個詞有 43 個落在教育部基本 1,200 字內（97.7%），
唯一例外是 `backpack`——它在其他常用 800 字表裡，同樣是課綱字彙。
測試把這條釘成回歸守門：日後加課綱外的字會當場紅。

**簡報不要講過頭（模組 docstring 有寫）**：
- 領綱字彙表是國中小共用的，官方**沒有**逐年級／逐學期切分，只載明國小畢業口語 ≥300 字、書寫 ≥180 字
- 各版本教科書（康軒／翰林／南一）單元主題是出版社著作，領綱不提供，本專案不猜
- 附錄六是**國中**文法表；領綱明文寫國小「僅止於簡易、常用的句型結構」，只當上限參考
- `band → 字彙難度` 的對應是**本專案的推論**，不是課綱的規定

### ③ 間隔重複 — 已實作（`acfe581`）

`server/srs.py`：SM-2 的二元評分變體。答對 1 → 6 → `interval × ease` 遞增（上限一學期）；
答錯間隔歸零、立刻回到題庫、ease 下調（下限 1.3）。`schedule()` 不讀時鐘，時間由呼叫端傳入。

- 評分判準直接重用 `profile.build_profile` 的掌握度定義，不另立一套
- `word_reviews` 表多一個 `last_seq`：背景刷新每次都讀最近 10 筆，沒有它會重複計分
- 派作業的規則式路徑優先挑到期詞；讀不到排程就退回原本的維度取題

### B2 + W1–W9 — 全部清空（`703ca33`、`a9085eb`）

| ID | 修法 |
|---|---|
| B2 | `session_id` 在 `agentcore._normalize_session_id` 統一綁上 actor 雜湊前綴。修在封裝層不是三個呼叫點——靠呼叫端自律，下一個呼叫點還是會忘 |
| W1 | 三個 agent 的公開介面各包一層 try，最後保底回最小合法結果（作業有 3 題保底題庫） |
| W2 | `pipeline._refresh_directive` 的 `except: pass` 補上 log |
| W3 | 節流讀取端對齊寫入端的預設學生（`store.default_student_id()`） |
| W4 | 三個 agent 補 `guardrails.consent_granted()`，與 `allow_cloud` 同級閘門 |
| W5 | 兩個列表端點加 `Query(ge=1, le=200)`；`store` 層另加一道 `_safe_limit` |
| W6 | payload 帶保留鍵（seq/kind/student_id/ts）當場拋錯；舊資料衝突時讀取留 warning |
| W7 | 規則式作業少於 3 題時補到下限並告警 |
| W8 | `report` 的跨欄位去重從死碼改成真的實作 |
| W9 | 節流測試 `< 6` 改 `== 1`；新增一條三個 agent 全用真的、不 monkeypatch 的端到端測試 |

---

## 1b. 同一個 session 的第二輪（commit `0852193`..`6b74616`）

### 測試環境修好了（`0852193`）

兩件事讓「跑全套」原本不可能：`pytest -q` 會收集 `third_party/llama.cpp`
並在 import 期失敗；`models/sherpa-.../test_wavs/` 整個資料夾不在，
streaming 的 3 條音訊測試全部 FileNotFoundError。

- `pytest.ini` 限定 `testpaths`，並加 `faulthandler_timeout=60`（真的卡住時印堆疊）
- `setup_env.sh` 單獨補回 178KB 的 `zh.wav`（原本的 guard 只看 `model.int8.onnx`，
  模型在、音檔被清掉的機器會永遠少那三條）

**更正上一版交接文件的誤判**：`test_turn_manager.py` 不是卡住，是慢（38.7 秒）。
先前用 20 秒的 per-file timeout 掃過去，把慢誤判成 hang。

### 題庫 44 → 136 詞（`06dd16c`）

92 個新詞全部落在教育部基本 1,200 字表內，**連例句用字都是**——加詞前用
`curriculum_data` 程式驗過才寫進 `scaffold.py`。覆蓋率 97.7% → 99.3%。

擴充後才浮出來的問題：出題永遠取詞庫前幾個，新詞根本出不來。加了取題輪轉，
種子是 `sha256(學生 + 診斷日期)`：同一天同一個孩子拿到同一份（現場可重現），
換一天換一批，不同學生不同批。

順手修兩個既有缺陷：`_EN_NOUNS` 把 bread/rice/water/milk 也收進複數修正表
（"two rice" → "two rices"，教錯比不教更糟）；兩組重複的目標句會被 homework
的去重靜默丟掉一題。

`tests/test_scaffold_vocab.py` 把加詞規則釘成守門（en 不得重複、sent 必須唯一、
冠詞要對、每個字與例句用字都必須在課綱表內）。

### AgentCore 共用 skill（`6b74616`，**尚未掛上**）

`scripts/generate_agent_skill.py` 從課綱 JSON 與專案常數產生
`deploy/aws/skills/taiwan-elementary-english/SKILL.md`。測試會重跑腳本比對輸出，
不同步就紅——skill 靜默過期等於三個 agent 一起拿舊依據出題。

掛載步驟與那道踩過的坑（`update-harness` 不是 patch 語意）寫在
`AGENTCORE_RESOURCES.md`。

---

## 2. 剩下的待辦

### A. AgentCore 加值（需 AWS 放行）

依投資報酬排序：

1. ~~自訂 skill `taiwan-elementary-english`~~ — **內容已產生**，只差
   `aws s3 sync` + `update-harness`（指令在 `AGENTCORE_RESOURCES.md`）
2. **Gateway 包 `curriculum.py` + `curriculum_data.py` 成 MCP tool** — agent 能「查」課綱
   而非把課綱塞進 prompt。現在課綱查詢層已經是現成的公開函式，包起來很直接
3. **Memory 加 `userPreferenceMemoryStrategy`** — 與 `word_reviews` 互補
4. Evaluations（需先有 OTEL trace）

**放行後的三步**：撥開關（ARN 在 `AGENTCORE_RESOURCES.md`）→ 端到端實跑確認
`source` 從 `rule` 變 `cloud` → **有實證後才刪規則式實作**。

### B. 題庫再擴充（可選，需人審）

136 詞對決賽夠用。要再往 1,200 字推的話，缺的是**中文語意與例句**——官方字彙表
只有英文單字。這一輪的 92 個詞是依既有句型框架擴寫的（機器起草、程式驗過字表），
再往下推建議請老師過目例句的語用自然度。

`curriculum_data.vocab_for_topic()` 可直接列出某主題還缺哪些詞。
加詞規則見 `server/scaffold.py` 的 VOCAB 區塊註解，違反的話
`tests/test_scaffold_vocab.py` 會擋下來。

---

## 3. 血淚換來的事實（別重踩）

**AWS region**
- `ap-east-2`（台北）**沒有 AgentCore**，endpoint 不存在，console 按台北會跳雪梨
- 東京有 AgentCore 但 Bedrock 配額 0；台北有配額但沒 AgentCore
- **同時具備兩者的只有新加坡、雪梨、法蘭克福**，新加坡離台灣最近（約 50ms）
- 台北只吃 `global.` / `apac.` 前綴，`us.` 會回 `model identifier is invalid`

**AgentCore API（文件沒寫）**
- `runtimeSessionId` **最短 33 字元**
- `InvokeHarness` 回傳 **EventStream**，不是 dict
- `AmazonBedrockFullAccess` **不涵蓋** `bedrock-agentcore:*`
- summarization 策略的 namespace **必須**含 `{sessionId}`
- **`update_harness` 不是 patch 語意** — 只傳部分欄位會讓其他欄位掉回預設

**AWS 帳號阻塞（未解）**
- 所有模型呼叫被擋。CloudWatch 7 天實測 `InputTokenCount=0`、`OutputTokenCount=0`，
  **零消耗卻回報 "Too many tokens per day"** → 證明不是配額問題
- 已排除：Free plan、IAM 權限、模型協議、region、憑證形式（IAM 與 bearer token 都試過）
- AWS Support 說是信用卡授權失敗；卡修好後仍未解，最新回覆改口說是 "service level restriction"
- `aws-verification@amazon.com` **是無人信箱**，會退信並導回 support case

**抓官方課綱時踩到的**（寫在 `scripts/extract_curriculum.py` 註解裡）
- Google 與 DuckDuckGo 都擋 headless；直接進 `naer.edu.tw` 的 `PageSyllabus?fid=52`，
  領域課綱在 JS tab 後面（`tagChange(177)`）
- ODT 為了記錄格式會把一個詞拆進兩個 `<text:span>`，**只在段落／儲存格邊界斷行**
  才不會把 `campus` 切成 `ca` + `mpus`
- `airplane (plane)` 這種條目逗號一切就散了，要看括號配對接回去

**AgentCore Harness skills（官方文件的安全注意事項）**
- `skills` 是 union，四種來源：`path` / `s3` / `git` / `awsSkills`，擇一
- skill 內容（**含它帶的腳本**）會被當成**可信輸入**注入 agent context
- **沒有 IAM condition key 能限制 per-invocation 的 `skills` 欄位**——invoke 時
  傳同名 skill 會覆蓋 harness 上掛好的那份。應用層絕不可把外部輸入透傳到
  `InvokeHarness` 的 `skills`
- `systemPrompt` 是 content block 的 list，不是字串；`model` 的 variant key 是
  `bedrockModelConfig`（不是裸的 `bedrock`），調參是平的、沒有 `inferenceConfig` 包層

**跑測試**
- `pytest -q` 現在直接跑得動（`pytest.ini` 限定 `testpaths`）
- streaming 那套第一次跑會經 modelscope 下載 SenseVoice（~900MB），
  之後有快取就快很多（首次約 6 分鐘，之後約 2.5 分鐘）

**Kiro CLI 協作**
- 免費 Builder ID 即可跑 headless（官方文件說要 Pro 訂閱，對 2.14.2 不成立）
- 工具旗標是 `--trust-tools=fs_read,fs_write,execute_bash`
- 用 `--model claude-sonnet-4.5 --effort high`，不帶 effort 品質差很多
- **免費層有 credits 上限，已用罄**
- 六輪審查下來，**沒有一個缺陷是它的測試抓到的**——它驗結構不驗行為、驗 mock 不驗真實依賴

---

## 4. 關鍵檔案

| 檔案 | 內容 |
|---|---|
| `deploy/aws/AGENTCORE_RESOURCES.md` | 所有 ARN、環境變數、安全稽核、清理指令 |
| `docs/AGENTCORE_ARCHITECTURE.md` | 架構圖（3 張 mermaid）與六階段遷移路徑 |
| `deploy/aws/STATUS.md` | AWS 阻塞的完整排除法紀錄 |
| `server/agents/privacy.py` | 上雲前的白名單投影（B4 的修法） |
| `server/srs.py` | 間隔重複排程（純函式、可離線） |
| `server/curriculum_data.py` | 課綱官方資料查詢層（含誠實邊界說明） |
| `scripts/extract_curriculum.py` | 課綱抽取腳本（可重跑、帶 SHA-256） |
| `scripts/generate_agent_skill.py` | 產生 Harness 共用 skill（改內容要改來源不是改 md） |
| `deploy/aws/skills/taiwan-elementary-english/` | 產生出來的 skill（尚未掛上 Harness） |
| `tests/test_scaffold_vocab.py` | 加詞守門（重複、冠詞、課綱依據） |
| `.kiro/steering/*.md` | Kiro 協作的檔案邊界與設計契約 |
