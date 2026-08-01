# 交接：雲端 demo 線（2026-08-01）

決賽 8/1–8/2。這份記錄**已知但未修的問題**與修法方向，給下一個 session 直接接手。
今天完成的事寫在 auto-memory（`~/.claude/projects/-home-budaedu-talkybuddy/memory/`），
本文件只列**還沒解決的**。

## 現況一句話

雲端全線可用：`https://d1lh9vytcx1utq.cloudfront.net`（CloudFront→ALB→Fargate）。
程式碼在 `feat/finals-cloud-demo`（已推 GitHub，**尚未併回 master**）。

---

## P1 — 會被評審看到的

### 1. 帶讀的目標句沒有對齊課本單元 ⚠️ 最該修

**現象**（線上實測）：

```
孩子: 今天天氣 sunny
玩偶: 哇，今天天氣真的很好呢！…一起來練習：I see a dog.
```

系統認得 `sunny`（教材有進 VOCAB），但帶讀句是舊的預設目標句，
跟 Unit 3 的天氣句型（`How's the weather today?` / `It's sunny.`）無關。

**根因**：目標句由 `server/lesson.py` / `scaffold` 決定，與 `demo_class.UNITS`
（Unit 3~6）是兩套獨立資料，從未對齊。

**修法方向**：讓 lesson 依「本週單元」選目標句。單元定義在 `server/demo_class.py`
的 `UNITS`（含每單元 `pattern`），教材詞條在 `server/seed_units.py`。
注意 `demo_class` 是展示資料層，若要讓 lesson 依賴它，得先想清楚正式版的歸屬。

**繞過方式**（若不修）：demo 講「孩子開口 → 玩偶陪他說對」這個行為本身，
不要強調帶讀句來自本週單元。

### 1b. 教師診斷實際上沒有走雲端 ⚠️ 2026-08-02 新增

`/api/diagnoses` 最新幾筆的 `source` 都是 `rule`。2026-08-02 線上實測：跑滿 6 輪
對話觸發背景診斷（`DIRECTIVE_REFRESH_EVERY=5`），新產生的那筆仍是 `rule`。

**不是憑證問題**——Fargate 用自己的 IAM task role，與本機 `.env.aws` 無關。
（診斷時若在本機用過期憑證測容器，會得到同樣的 `rule`，那是假象，別被誤導。）

**已做的只是改標籤**：教師端徽章文案從「離線規則式產出（未走雲端）」改為
「邊緣端即時產出」（commit f9cba39）。**問題本身沒修。**

**根因待查**：`server/diagnose.py` 的 `_call_anthropic_api()` 為何失敗。兩個候選：
1. 診斷 model `global.anthropic.claude-sonnet-5` 沒開通——`sonnet-4-5` 昨天就是這樣，
   而 `list-foundation-models`／preflight ④ 列的是「存在」不是「已開通」。
   驗法：拿有效憑證真的 `converse` 一次那個 model id。
2. 逾時（診斷走大模型，上界 12s）。

要查 CloudWatch log 需要有效憑證。修好的判準：教師端徽章自己變成
「由 AWS Bedrock（Claude）direct converse 產出」。

**demo 繞過方式**：真正證明 AI 在跑的是「Agent 產出」卡片（派作業／週報，走 AgentCore）
與學生端對話本身；診斷卡片可以講成「斷網時教師端照樣有診斷」的離線賣點。

### 2. material agent 的 `cat` 分類明顯錯誤

Unit 3 的天氣詞（sunny/rainy/cloudy…）全被標成 `cat="color"`。
`_MATERIAL_CATS` 是 `{food, school, animal, family, action, color}`，天氣沒有對應分類。

**影響**：`profile.build_profile()` 的興趣統計依 `cat` 聚合，會把天氣算成顏色偏好。
demo 看不出來，但教師端的「興趣主題」可能失準。

**修法**：加 `weather`／`place`／`time` 分類，或在 `_SYSTEM_PROMPT` 說明沒有合適分類時
該怎麼選。改 `_MATERIAL_CATS` 要同步看 `scaffold`／`games`／`homework` 有無硬編分類。

### 3. Unit 5 只提煉出 4 個詞

`accepted=4 rejected=4`。其中 breakfast/lunch/dinner 是**正確**被拒（課綱 VOCAB 已有，
教材只能新增不能覆蓋）；但 `thirty`／`late` 也沒進去，原因未查。

**修法**：用 `register_material_vocab` 的逐條驗證（見今天診斷 Unit 6 的做法：
monkeypatch 攔截 entries 再逐條跑 `_is_valid_material_entry`）找出被哪一關擋掉。

---

## P2 — 架構債，決賽後再處理

### 4. 核心 schema 是單學生設計

| 表 | 多學生 |
|---|---|
| `student_profile` / `word_reviews` / `agent_outputs` | ✅ 有 student_id |
| `interactions` | ❌ 無 student_id |
| `diagnoses` | ❌ `date` 是 PRIMARY KEY，同一天多學生互相覆蓋 |

所以教師端的全班資料是 `server/demo_class.py` 這個**獨立展示層**（回傳固定帶
`source: "demo"`），不是真的來自 DB。使用者 2026-08-01 明確決定**決賽前不動 schema**，
診斷與互動只保留阿明一人。

**修法順序**（動的話照這個來）：先 `interactions` 加 `student_id`
（`ALTER TABLE ADD COLUMN`，低風險）→ 再重建 `diagnoses` 為複合主鍵
（SQLite 不支援改主鍵，要建新表搬資料）→ 改 `add_diagnosis`／`list_diagnoses`／
`build_profile` →改教師端前端。**不要把 `demo_class.py` 當成這條路的起點**，它是展示層。

### 5. JWT secret 明文寫在 task definition 環境變數

值在 ECS task definition 裡（明文）。正式做法是 AWS Secrets Manager +
`secrets` 欄位。demo 可接受，但不要把 task definition JSON 貼到公開場合。

### 6. `scripts/aws_preflight.py` 的降級鏈顯示不可信

第 90 行 `os.environ.setdefault("TALKYBUDDY_CLOUD_PROVIDER","bedrock")` 之後才檢查，
等於檢查自己剛塞的值，永遠顯示 `agentcore → bedrock → rule`。
真實環境沒設該變數時，`bedrock_converse.resolve_config()` 一律回 `None`，
鏈其實是 `agentcore → rule`。

**修法**：移除 setdefault，改成「沒設就明確報錯並給修正指令」。
在那之前，驗降級鏈一律看 `agent_backends.chain(role)` 的實際輸出。

---

## P3 — 維運注意

- **CloudFront 會快取**：改完前端一定要
  `aws cloudfront create-invalidation --distribution-id E3HAP54WHGBZQN --paths '/*'`，
  否則現場看到舊畫面（今天被咬過一次）。診斷順序：先直連 ALB 比對，
  能區分「CloudFront 快取」與「image 沒更新」。
- **憑證會過期**：`/home/budaedu/talkybuddy/.env.aws` 是 workshop 臨時憑證。
  過期只影響操作 AWS，不影響已跑起來的 Fargate（它用自己的 task role）。
- **Fargate 持續計費**：決賽結束記得 `aws ecs update-service --desired-count 0`。
- **`feat/finals-cloud-demo` 尚未併回 master**：
  `git checkout master && git merge feat/finals-cloud-demo && git push`

## 尚未完成（非程式）

- **備援影片沒錄**——唯一沒有 plan B 的環節。TTS 改走 Polly 後多了網路依賴，
  而現場網路是手機熱點。
- **6 分鐘 demo 沒有完整計時過**。腳本在 memory 的
  `project_finals_demo_2026-08-01.md`。
