# 交接：雲端 demo 線（2026-08-01）

決賽 8/1–8/2。這份記錄**已知但未修的問題**與修法方向，給下一個 session 直接接手。
今天完成的事寫在 auto-memory（`~/.claude/projects/-home-budaedu-talkybuddy/memory/`），
本文件只列**還沒解決的**。

## 現況一句話

雲端全線可用：`https://d1lh9vytcx1utq.cloudfront.net`（CloudFront→ALB→Fargate）。
程式碼已併回 `master`。

**2026-08-02 04:xx 更新**：教師診斷已真正走雲端（P1-1b 已修並部署），
帶讀句對齊單元也已修（P1-1）。剩下的 P1 是第 2、3 項（material agent 的
`cat` 分類、Unit 5 只提煉出 4 個詞），兩者都不影響 demo 畫面。

---

## P1 — 會被評審看到的

### 1. 帶讀的目標句沒有對齊課本單元 — ✅ 已修（commit `06c340f`）

問題本身已解決，以下原始記錄保留作為背景。

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

### 1b. 教師診斷實際上沒有走雲端 — ✅ 已修並已部署（2026-08-02 04:xx）

**現在線上驗過 4/4 `source=cloud`、6.2–7.8s，教師端徽章顯示
「由 AWS Bedrock（Claude）direct converse 產出」**，即本節原訂的判準。
commit `fe14d91` → `5a462a0` → `37ecdb3`，image 已推 ECR 並滾動部署完成。

原本猜的兩個候選**都對，而且不只兩個——是三個 bug 疊在一起**，每修好一個
才會露出下一個。這個形狀值得記住：

1. **model 沒開通**。`bedrock_converse.DEFAULT_MODEL_ID` 是
   `global.anthropic.claude-sonnet-5`，實打回 `AccessDeniedException: not
   available for this account`。對話路徑用的是 haiku-4-5（該帳號可用），
   **這就是為什麼對話正常、只有診斷是假的**。
   退而求其次的 `sonnet-4-6`（AgentCore 用的那顆）也不行：同一份 prompt 要 21.8s。
   → 診斷改用 haiku-4-5。
2. **逾時**。`_API_TIMEOUT_SEC = 12`。我一開始拿兩筆假互動量到 7.1s 就放行，
   **那是假綠燈**——呼叫端真正送的是 `list_interactions(limit=10)`，用線上真實
   資料重量是 11.0s / 15.3s，正好跨在 12s 兩邊，所以間歇失敗。→ 放寬到 25s。
3. **輸出被截斷**。prompt 對輸出長度毫無約束，而第一次成功後產生了 `prev`，
   第二次模型為了呼應前次診斷就越寫越長，撞上 maxTokens →
   `JSONDecodeError` → 靜默降級。**調高 maxTokens 只是延後爆炸**，真正的修法是
   prompt 補明確長度上限 + 新增 `_prev_brief()`（原本把整份 prev 診斷塞回
   prompt，等於每輪都把前一輪全文再餵一次，prompt 隨歷史單調成長）。

**真正讓這個 bug 活了一整天的是靜默降級**：`generate_diagnosis` 的兩個
`except` 是徹底的黑洞，連 log 都沒有，症狀只有徽章上的 `rule`——跟「沒憑證」
長得一模一樣。已補 `_log.warning(exc_info=True)`（降級行為不變），
`converse_chat` 也會在 `stopReason == max_tokens` 時警告。
**補上日誌後，第 3 個 bug 一次就定位了。**

順帶修掉一個被它掩蓋的地雷：`/api/network_mode` 原本在 `async def` 裡**同步**
呼叫 `generate_diagnosis`。先前沒事只是因為 AccessDenied 在 0.3s 內就失敗——
是 bug 掩蓋了 bug。修好後那裡會用整個 event loop 等 7 秒（現場有孩子在講話時
那條 WebSocket 會一起卡住），已包進 `asyncio.to_thread`。

**下次換帳號必做**：實打 `converse` 每一顆 model id。`list-foundation-models`
與 preflight ④ 列的是「存在」不是「已開通」。

**量測方法的教訓**：探針要用**真實筆數**的資料。這條路徑的耗時與輸出長度都由
`list_interactions(limit=10)` 的實際內容決定，拿兩筆假資料量出來的數字對它沒有意義。

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
- ~~**`feat/finals-cloud-demo` 尚未併回 master**~~ — 已併回，master 是最新的。
- **兩個 API 細節**（2026-08-02 各浪費了一次來回）：
  `/api/login` 的欄位是 `email` 不是 `username`；
  切換網路模式的路由是 `/api/network_mode`（**底線**），不是 `network-mode`。
- **重新部署的完整流程**（image 內建 `TALKYBUDDY_CLOUD_PROVIDER=bedrock`，
  改 task definition 環境變數沒用，要 rebuild）：
  ```
  set -a && . ./.env.aws && set +a
  aws ecr get-login-password --region us-west-2 | docker login --username AWS \
    --password-stdin 953089054952.dkr.ecr.us-west-2.amazonaws.com
  docker build -f deploy/aws/Dockerfile -t talkybuddy:x .
  docker run --rm --entrypoint sh talkybuddy:x -c "grep ... /app/server/..."   # 先驗 image 內容
  docker tag talkybuddy:x 953089054952.dkr.ecr.us-west-2.amazonaws.com/talkybuddy:latest
  docker push 953089054952.dkr.ecr.us-west-2.amazonaws.com/talkybuddy:latest
  aws ecs update-service --cluster talkybuddy --service talkybuddy --force-new-deployment
  ```
  **`rolloutState` 顯示 `COMPLETED` 時 `runningCount` 可能還是 0，那個綠燈不可信**；
  要等到 `runningCount=1 / pendingCount=0 / deployments=1`。整輪約 3–5 分鐘。
- **既有失敗測試 2 個**（與本次修正無關，改動前後都紅）：
  `test_e2e.py::test_network_mode_switch_affects_live_ws_session`、
  `test_student_identity.py::test_teacher_html_has_no_hardcoded_student_name`。
  另有 3 個 `test_pipeline_cloud`／`test_pipeline` 測試曾間歇性失敗，但在原始
  commit 上重跑也是綠的——是環境敏感的不穩定測試，不是被誰改壞的。

## 尚未完成（非程式）

- **備援影片沒錄**——唯一沒有 plan B 的環節。TTS 改走 Polly 後多了網路依賴，
  而現場網路是手機熱點。
- **6 分鐘 demo 沒有完整計時過**。腳本在 memory 的
  `project_finals_demo_2026-08-01.md`。
