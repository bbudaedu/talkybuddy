# 交接：下一個 session 從這裡開始

**更新**：2026-07-27　**決賽**：約 2026-07-30（剩約 3 天）

---

## 0. 三十秒看懂現況

| 項目 | 狀態 |
|---|---|
| 測試基準 | **552 passed** |
| 離線路徑（斷網橋段） | ✅ 端到端跑通，`source` 全為 `rule`、零出境 |
| 三個 agent（派作業／週報／決策判斷） | ✅ 完成並接進 `pipeline._refresh_directive` |
| AgentCore 資源（新加坡） | ✅ Memory ACTIVE、3 個 Harness READY、IAM 已過官方安全稽核 |
| AgentCore 程式碼 | ✅ 已接好，**flag 關閉**（`TALKYBUDDY_AGENT_BACKEND` 未設即走原路徑） |
| **雲端實際產出** | ❌ **零實證** — AWS 帳號被鎖，模型呼叫從未成功 |

**決賽策略已定調（使用者 2026-07-26 拍板）**：以雲端為主，決賽當天用**主辦方提供的 AWS 資源**展示。
所以自己帳號被鎖不是決賽風險，但**代表雲端路徑至今未經任何實跑驗證**——這是最大的未知數。

---

## 1. 待辦，依建議順序

### ① 修 B4：自由文字未去識別化就上雲（隱私，優先做）

**問題**：`report.py:599` 與 `orchestrator.py:396` 把**整個 diagnosis 物件**
`json.dumps` 後送上雲，但 `_deidentify_diagnosis` 只遮罩三個白名單欄位
（`emotional_status` / `strengths` / `weaknesses`）。

漏掉的是 `companion_directive` 與 `instructions`（見 `diagnose.py:518-527`）——
這兩個欄位是 **LLM 依孩子講的話生成的自由文字**。孩子說「我是王小明，今天跟哥哥去…」，
名字就會進到 `companion_directive`，再被週報 agent 原文轉送並由 AgentCore Memory 長期保存。

**修法**：改成**白名單挑欄位**而非黑名單遮欄位——只組 `date` / `scores` /
已遮罩的三欄進 prompt，其餘一律不送。`diagnose._build_diagnosis_prompt` 就是這個做法，直接照抄。

### ② 抓真實課綱（3–6 年級，含文法與句型）

**為什麼要做**：現在只有 `scaffold.VOCAB` 44 個詞、每詞一句 `sent`。
那是示範資料，不是課綱。決賽被問「你的教材依據是什麼」時，44 個詞撐不住。

**來源**（務必實際抓取，不可憑印象寫）：
- 教育部十二年國教**英語文領綱**的參考字彙表（官方、可公開引用）
- 審定版教科書單元主題（康軒／翰林／南一）小三到小六

**目標結構**：

```
grade (3-6) × semester × theme × {
    vocab:          [{zh, en, pos, cat}],
    patterns:       ["I like ___.", "Do you have ___?"],   # 句型，目前完全沒有
    grammar_points: ["現在簡單式", "冠詞 a/an", "Wh- 問句"],
}
```

**對接**：`server/curriculum.py`（303 行）已有 CEFR `BAND_CUTS`、`WEIGHTS`、
難度階梯，新資料掛在它下面，**不要重寫既有邏輯**。

**注意**：`scaffold.VOCAB` 的 schema 是 `{zh_key: {en, cat, np, sent}}`，
`homework.py` 的規則式出題直接依賴它。擴充時要嘛保持相容，要嘛同步改
`_DIM_TO_CATS`（`homework.py:57-62`）與 `_PROMPT_TEMPLATES_BY_CAT`（`:82-115`）。

### ③ 間隔重複（取代「推薦演算法」的構想）

使用者原本想用推薦演算法分析聊天紀錄推薦課程。**協同過濾在此場景會失效**：
冷啟動（只有一個孩子、沒有用戶基數）、資料稀疏、而且問錯問題——
要答的不是「相似的人喜歡什麼」，是「**這個孩子哪個字還沒學會**」。

**該用間隔重複 + 知識追蹤**（FSRS 或 SM-2），語言學習領域數十年實證做法。
原料全都有了：`interactions`（每回合分數）、`diagnoses`（四維）、
`curriculum.py`（難度階梯）、`scaffold.VOCAB`（題庫）。

**缺的只有一張表**：

```sql
CREATE TABLE word_reviews (
  student_id TEXT, word TEXT, last_seen TEXT,
  ease REAL, interval_days INTEGER, due_at TEXT,
  PRIMARY KEY (student_id, word)
)
```

派作業 agent 出題時優先挑 `due_at` 到期且過去答錯的詞。
**演算法是純函式、幾十行、可離線跑**——斷網橋段完全不受影響，這點比推薦系統好太多。

### ④ AgentCore 加值（需 AWS 放行）

依投資報酬排序：

1. **自訂 skill `taiwan-elementary-english`** — 課綱、CEFR 對應、recast 教學法、
   兒童安全用語寫成 skill 掛上 Harness（`CreateHarness` 有 `skills` 欄位），
   三個 agent 共用、一改全改，比塞 system prompt 乾淨
2. **Gateway 包 `curriculum.py` 成 MCP tool** — agent 能「查」課綱而非把課綱塞進 prompt
3. **Memory 加 `userPreferenceMemoryStrategy`** — 直接支撐 ③ 的間隔重複
4. Evaluations（需先有 OTEL trace）

---

## 2. Code review 剩餘未修項

2026-07-26 外部 review 找到 6 blocker + 9 warning，已修 4 個（commit `6bde284`）。
**剩下的都已逐條驗證為真**，不是誤報：

| ID | 問題 | 位置 |
|---|---|---|
| **B2** | `session_id` 沒有學生維度 → 短期記憶跨童串接。`orch-turn-4` 讓任何學生跑到第 4 回合都共用同一 session；補齊是 sha256 決定性映射，碰撞穩定重現 | `homework.py:442`、`report.py:750`、`orchestrator.py:508` |
| **B4** | 見上方 ① | `report.py:599`、`orchestrator.py:396` |
| W1 | 三個 agent「絕不拋例外」契約不成立：except 區塊呼叫的 `_rule_based_*` 自己可能拋 KeyError；且 `allow_cloud=False` 分支在 `try` 之外 | `homework.py:413` 等 |
| W2 | `_refresh_directive` 的 `except Exception: pass` 無日誌——正是本專案吃過虧的形狀 | `pipeline.py:361` |
| W3 | `student_id` 缺失時節流退化成全域（`store.list_agent_outputs(student_id=None)` 不加 WHERE） | `orchestrator.py:234` |
| W4 | 三個 agent 沒複查 `guardrails.consent_granted()`，而 `diagnose.py:667` 有 | 三個 agent |
| W5 | `/api/agent_outputs` 的 `limit` 未驗證，`?limit=-1` 在 SQLite 等於無上限 | `app.py:325` |
| W6 | `list_agent_outputs` 無條件覆寫 payload 的 `seq/kind/ts` 鍵 | `store.py:315-321` |
| W7 | 規則式作業可能少於契約下限 3 題且無告警 | `homework.py:272` |
| W8 | `report.py:556` `all_items` 是死碼，註解宣稱有跨欄去重但沒實作 | `report.py:556` |
| W9 | 測試驗 mock 不驗行為：節流測試 `assert issued < 6`（派 5 次也會過，正解是 1）；接線測試三個 agent 全被 monkeypatch，所以 B5/B6 在全綠下隱形 | 三個測試檔 |

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
  （曾把 `maxTokens` 靜默重置成 None）

**AWS 帳號阻塞（未解）**
- 所有模型呼叫被擋。CloudWatch 7 天實測 `InputTokenCount=0`、`OutputTokenCount=0`，
  **零消耗卻回報 "Too many tokens per day"** → 證明不是配額問題
- 已排除：Free plan、IAM 權限、模型協議、region、憑證形式（IAM 與 bearer token 都試過）
- AWS Support 說是信用卡授權失敗；卡修好後仍未解，最新回覆改口說是 "service level restriction"
- `aws-verification@amazon.com` **是無人信箱**，會退信並導回 support case

**Kiro CLI 協作**
- 免費 Builder ID 即可跑 headless（官方文件說要 Pro 訂閱，對 2.14.2 不成立）
- 工具旗標是 `--trust-tools=fs_read,fs_write,execute_bash`（非文件寫的 `read,grep,write`）
- 用 `--model claude-sonnet-4.5 --effort high`，不帶 effort 品質差很多
- **免費層有 credits 上限，已用罄**
- 六輪審查下來，**沒有一個缺陷是它的測試抓到的**——它驗結構不驗行為、驗 mock 不驗真實依賴。
  協作模式必須是「它寫、人跑真實情境驗」

---

## 4. 關鍵檔案

| 檔案 | 內容 |
|---|---|
| `deploy/aws/AGENTCORE_RESOURCES.md` | 所有 ARN、環境變數、安全稽核、清理指令 |
| `docs/AGENTCORE_ARCHITECTURE.md` | 架構圖（3 張 mermaid）與六階段遷移路徑 |
| `deploy/aws/STATUS.md` | AWS 阻塞的完整排除法紀錄 |
| `.kiro/steering/*.md` | Kiro 協作的檔案邊界與設計契約 |

**放行後的三步**：撥開關（ARN 在 `AGENTCORE_RESOURCES.md`）→ 端到端實跑確認
`source` 從 `rule` 變 `cloud` → **有實證後才刪規則式實作**。
