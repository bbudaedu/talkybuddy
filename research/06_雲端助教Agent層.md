# 雲端「反思教學 AI 助教」Agent 層技術選型研究

> 研究範圍：說說學伴（無螢幕企鵝語伴玩偶）雲端助教層——每學生一個持久記憶 agent、每日診斷、非同步 best-effort、推論接 AWS Bedrock（Claude）。
> 研究日期：2026-07-03／04。所有數字（star 數、license、最近 commit）皆以 GitHub API / 官方文件即時查證為準，非憑記憶推測。
> 邊緣即時層（ASR/LLM/TTS on-device）不在本篇範圍，本篇只評估「雲端助教層」該用什麼底做。

---

## 0. 核心判斷（先講結論，細節在後面）

決賽 demo 要的是「診斷閉環看起來真實」：學生講完話 → 邊緣層存對話紀錄 → 雲端排程任務讀取歷程 → 呼叫 Bedrock Claude 產出結構化診斷 JSON（弱點分類、下一步鷹架建議）→ 寫回資料庫 → 教師儀表板讀出來顯示。這條鏈路裡，**框架的「持久記憶」機制在 demo 時間尺度（幾天到幾週的對話量）幾乎不產生任何自己去查證不到的價值**——因為：

- 學生一次使用的對話量很小（幾輪到幾十輪對話/天），完全塞得進一次 prompt，不需要 Letta/Hermes 那種「記憶太大要分層摘要、archival 檢索」的機制去解決的問題。
- 「持久」這件事，用一張 `student_id` 為 key 的 SQL 表（歷史逐字稿 + 每日診斷 JSON）就能達成，語意完全等價於框架的「core memory」，但少一層要學的抽象、少一個要跑起來的 server（Postgres+pgvector 或 Docker container）、少一組要 debug 的新失敗模式。
- 5 天內要「真的接通 Bedrock 跑出診斷 JSON」，最快的路是 **boto3 直呼 Bedrock Converse API + tool use 強制結構化輸出**，不是先學一套 agent 框架的資料模型、部署、多租戶概念。

**主線建議：方案 5（無框架基準）＋ Bedrock Converse API tool use，用 EventBridge Scheduler 觸發 Lambda，DynamoDB 存歷程與診斷結果。** 這是 28 天 hackathon 團隊在「不確定會不會有人專職顧這層」的情況下風險最低、故障面最小的選擇。

**唯一值得列為備選、且理由具體的框架是 Letta**：它的「Identities（每個 user 綁一個 agent）＋ Bedrock provider 原生支援＋ REST API」剛好對上「每學生一個持久記憶 agent」的字面需求，如果團隊人力夠、且想要一個「一眼看起來就是持久記憶 agent 系統」的展示效果（例如評審會問「你們的記憶架構是什麼」），Letta 用 Docker 5 天內可以跑起來。但它引入一個要維運的有狀態服務（Postgres），對 28 天 hackathon 是額外風險，只在「demo 敘事非常需要秀出記憶架構」時才值得。

Hermes Agent 這條路徑**驗證結果：專案真實存在、Bedrock 原生整合也真實存在，但架構定位是個人助理／CLI 桌面應用（personal assistant），不是可水平擴充的多租戶後端服務**——不建議採用，理由見下方詳細分析。

---

## 1. Hermes Agent（NousResearch/hermes-agent）

### 存在性與基本資料（GitHub API 直查，2026-07-03/04）
- Repo：https://github.com/NousResearch/hermes-agent
- **確實存在**，並非誤植或幻覺 repo 名稱。
- Star：**208,563**（`stargazers_count`，API 直查）
- License：**MIT**（`license.spdx_id: MIT`）
- 建立時間：2025-07-22；最近 push：**2026-07-03**（活躍）；最近 release：v0.18.0（官方文件頁聲稱 2026-07-01）
- Open issues：**25,339**；forks：37,965
- 語言組成：Python 53.1 MB、TypeScript 9.1 MB、JavaScript 0.9 MB（另有 Rust/Go template/Nix 等），**是一個非常龐大的 monorepo**（CLI + gateway + Electron 桌面 app + TUI + website + 多國語系 docs）。

### 定位
官方描述："The agent that grows with you"——一個**個人**用的自我改進 AI agent：透過 CLI/TUI、Telegram/Discord/Slack/WhatsApp/Signal 等 gateway 與使用者對話，具備技能自我建立/改進、跨會話搜尋、定期自我提醒等「個人助理」特性。專案根目錄可見 `apps/desktop`（Electron 桌面 app）、`gateway`（多平台聊天機器人閘道）、`hermes_cli`、`tui_gateway` 等，並有 `profile-switcher`／`create-profile-dialog` 這類「同一台機器上切換多個人格 profile」的功能。

### 授權
MIT，寬鬆，可二次開發、可商用，無附加限制。

### 離線／ARM(aarch64)
本研究的「雲端助教層」跑在 AWS 端（Lambda/EC2/ECS），離線與 ARM 裝置支援不是這層的評估重點；但附帶一提，Hermes Agent 本身有 Docker/Nix 打包、支援多種 sandbox 終端後端（本地/Docker/SSH/Singularity/Modal/Daytona），理論上可在 arm64 EC2/Graviton 上跑，但沒有為此做過針對性驗證，也非其設計重心。

### 中英（zh-TW/夾雜）支援
文件本身有 `README.zh-CN.md` 等多國語系文檔，代表專案對多語系使用者友善，但這與「AI 是否擅長診斷中英夾雜學生語料」無關——那完全取決於背後接的 LLM（若接 Bedrock 上的 Claude，中英夾雜能力等同於 Claude 本身，與 Hermes 這層無關）。

### 延遲特性
不適用（不是即時互動層，是背景排程/異步任務執行框架）。

### 活躍度
非常活躍：昨日剛推送、7 月初剛發新版、25k+ open issues／38k forks 顯示使用者規模巨大（用「病毒式成長的個人 AI 助理產品」量級來理解比較合理）。但 issue 數量巨大也意味著這是個仍在快速迭代、bug 面很寬的專案，二開時遇到的坑不見得能很快得到回應。

### 二開難易 / 對本專案適配
**已用原始碼直接查證**（非僅讀行銷文案）：
- `cron/scheduler.py`：真實存在的 cron 排程器，每 60 秒 tick 一次、有檔案鎖，可用來跑「每日診斷」任務。
- `agent/memory_manager.py`、`agent/memory_provider.py`、`plugins/memory/{byterover,hindsight,holographic}`：真實的可插拔記憶後端。
- `mcp_serve.py` + `plugins/`：真實的 MCP 支援。
- **AWS Bedrock 原生整合是真的**：`agent/bedrock_adapter.py`、`agent/transports/bedrock.py`、`plugins/model-providers/bedrock/`、官方指南 `website/docs/guides/aws-bedrock.md`——用 Bedrock **Converse API**（非 OpenAI-相容端點），支援 IAM role 免金鑰、Guardrails、cross-region inference profile，`pyproject.toml` 有明確的 `bedrock = ["boto3==1.42.89"]` optional dependency group。這點確認了任務描述裡「能否經 LiteLLM 接 Bedrock」的疑問其實不需要 LiteLLM——**Hermes 自己原生支援 Bedrock**，比 LiteLLM 轉接更直接。

**但架構不對題**：Hermes 是為「一個人在自己的機器/VPS 上，跟一個會自我進化的個人助理長期互動」設計的（Electron 桌面 app＋多聊天平台 gateway＋單機 profile 切換），不是「一個後端服務，同時管理數百個學生各自獨立的診斷 agent，並讓教師儀表板用 API 查詢」的多租戶服務型態。要塞進我們的架構，需要：
1. 為每個學生跑一個獨立 Hermes process/profile（而非原生多租戶 API），維運成本隨學生數線性增加；
2. 這是 53MB Python + Electron 桌面應用的 monorepo，光是搞懂 30 個頂層目錄裡哪些是我們用得到的最小子集（cron + bedrock adapter + memory），就要花掉不成比例的時間；
3. 25k+ open issues 意味著踩坑機率不低，而我們沒有時間深入這個陌生的大型 codebase 排錯。

**結論：淘汰。** 專案真實、成熟、Bedrock 整合紮實，但是「個人助理」定位與我們「多租戶診斷後端」需求的架構錯位，加上龐大的程式碼規模，在 28 天（且僅 5 天要接通 demo）的時間預算下二開成本過高、風險報酬比差。

來源：
- [NousResearch/hermes-agent](https://github.com/nousresearch/hermes-agent)
- [Hermes Agent 官方文件](https://hermes-agent.nousresearch.com/docs/)
- [AWS Bedrock 整合指南](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/guides/aws-bedrock.md)
- [Integrations 總覽](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/integrations/index.md)

---

## 2. Letta（原 MemGPT）

### 基本資料
- Repo：https://github.com/letta-ai/letta
- Star：**23,636**；License：**Apache-2.0**（GitHub API 直查）
- 建立時間：2023-10-11（比 Hermes 成熟兩年多）；最近 push：2026-06-26（一週內，活躍但節奏比 Hermes 慢一點）
- Open issues：52／forks：2,503（issue:fork 比例健康，維護狀態良好）
- 語言：Python 10.3 MB 為主（核心比 Hermes 小非常多），另有少量 Go/C++/Java/TS（多語言 SDK client）

### 定位
UC Berkeley Sky Computing Lab（Spark/Ray 團隊）孵化，OS 啟發式的三層記憶模型：**core memory**（永遠在 context 裡，像 RAM）、**archival memory**（外部向量庫，主動查詢）、**recall memory**（對話歷史，可搜尋）。是一個**stateful agent 平台**——agent 狀態存在 Postgres/SQLite，透過 REST API（`POST /agents/{id}/messages`）操作，並提供 ADE（Agent Development Environment）網頁 UI 除錯。

### 授權
Apache-2.0，寬鬆、可商用、無 copyleft 疑慮。

### 離線／ARM(aarch64)
雲端層，同上不是評估重點；Docker 自架有官方教學（`docs.letta.com/guides/docker`），跑在 arm64 的 AWS Graviton 上理論可行（Python + Postgres 生態普遍支援 arm64），但需自行驗證。

### 中英支援
與 Hermes 相同，這一層與底層 LLM（Bedrock 上的 Claude）能力掛鉤，Letta 本身不影響中英夾雜理解品質。

### 延遲特性
不適用（背景診斷任務，非即時對話層）。

### 活躍度
健康：兩年半的專案生命週期、23k+ star、100+ 貢獻者、issue 數量可控（52 open vs. 2500+ forks），比 Hermes 更像是「工程成熟、可信賴依賴」而非「爆紅但混亂」的專案。

### 二開難易 / 對本專案適配
這是五個候選裡**架構上最貼合題目字面需求**的：
- **原生 Bedrock 支援**：`provider_type: "bedrock"`，`BedrockModelSettings` 物件（`max_output_tokens`、`parallel_tool_calls` 等），列在官方 model provider 清單裡，與 OpenAI/Anthropic/Azure 並列一級支援。
- **原生多使用者設計**：Letta 的 "Identities" API 就是為了「幫應用程式裡的每個使用者關聯一個獨立 agent」而設計——用 tag 把 agent 和使用者 ID 綁在一起，這正是「每學生一個持久記憶 agent」的字面實作。
- **REST API 優先**，容易跟現有 FastAPI 服務／教師儀表板串接，不需要理解一個聊天機器人 gateway 架構。
- 程式碼規模（10MB Python 核心）比 Hermes 小非常多，理解成本可控。

代價：引入一個要自架的**有狀態服務**（Postgres + Letta server，Docker compose 可一鍵起），對 28 天 hackathon 團隊而言多了一個要顧的 moving part（資料庫遷移、server 健康檢查、與現有 SQLite 原型的資料模型要不要合併）。這在「5 天內要看到 demo」的壓力下是額外風險，但風險是可控、有明確 Docker 教學可循的那種。

**結論：備選（Tier 2）。** 如果評審會特別問「你們雲端助教怎麼做記憶」，或團隊有餘裕（例如某成員專職負責這條線且過去用過 Letta），Letta 是比自己手刻更快長出「看起來像正經記憶架構」demo 效果的路。但不是 5 天內最低風險的路——那條路是方案 5。

來源：
- [letta-ai/letta](https://github.com/letta-ai/letta)
- [Letta Model providers 文件](https://docs.letta.com/guides/docker/providers)
- [Letta User identities（多使用者）文件](https://docs.letta.com/guides/agents/multi-user)
- [Letta Multi-agent 文件](https://docs.letta.com/guides/agents/multi-agent/)
- [Letta Docker 自架教學](https://docs.letta.com/guides/docker)

---

## 3. Claude Agent SDK（Anthropic 官方）＋ Bedrock

### 基本資料
- 官方文件：https://code.claude.com/docs/en/agent-sdk/overview
- 授權／條款：使用受 **Anthropic Commercial Terms of Service** 規範（非開源 license，是服務條款），需注意品牌使用限制（不可稱作 "Claude Code"，需維持自己的產品品牌）。
- Python/TypeScript 兩種 SDK，皆由 Anthropic 官方維護，changelog 持續更新。

### Bedrock 設定方式（官方文件直查，已核實確切環境變數）
```bash
export CLAUDE_CODE_USE_BEDROCK=1
# 並依 boto3 credential chain 設定 AWS 憑證（IAM role / env vars / profile 皆可）
```
與另一個常被搞混的變數 `CLAUDE_CODE_USE_ANTHROPIC_AWS`（那個是 "Claude Platform on AWS"，不同產品）要分清楚——我們要的是前者。

### 定位
**Agent SDK 本質是「把 Claude Code 的 agent loop 包成一個函式庫」**：內建 Read/Write/Edit/Bash/Glob/Grep/WebSearch/WebFetch 等**檔案系統與命令列導向**的工具、hooks、subagents、MCP、以及以本機 JSONL 檔案儲存的 session（`~/.claude/projects/<cwd>/*.jsonl`）。官方文件自己給的比較表講得很清楚：

| | Agent SDK | Managed Agents（另一個 Anthropic 產品） |
|---|---|---|
| 執行環境 | 你自己的 process/infra | Anthropic 代管 sandbox |
| Session 狀態 | 本機檔案系統 JSONL | Anthropic 代管 event log |
| 最適合 | 本機原型開發、直接操作檔案系統的 agent | **正式環境、無 sandbox/session 維運需求、長時間非同步 session** |

也就是說，**官方自己的定位建議就指向：我們這種「無伺服器、非同步排程」的正式環境用例，不是 Agent SDK 的最佳落點**。Session resume 機制設計給「同一台機器、同一個工作目錄」使用，要拿到 serverless/跨機器場景（我們的 Lambda 排程正是這種），得自己寫 `SessionStore adapter` 把 transcript 鏡射到共用儲存——這其實比直接用 Bedrock Converse API 存自己的 DB 還多繞一層。

### 二開難易 / 對本專案適配
Agent SDK 對「讓 Claude 自主操作檔案、跑指令、多輪工具鏈」這種任務是神器（我們如果要做的是"Claude 自己去讀 log 檔案、跑分析腳本"那種複雜多步驟診斷，它會很好用）。但我們的需求其實很單純：**給定一批逐字稿 → 一次 Bedrock 呼叫（或最多幾次 tool use round-trip）→ 吐出結構化診斷 JSON**。這不需要 Bash/Edit/Write 這些檔案操作工具、不需要 permission 系統、不需要 hooks/subagents/skills。用 Agent SDK 等於扛著一整套「編碼 agent 基礎設施」的重量去做一個「函式呼叫＋結構化輸出」的任務。

**結論：淘汰（非最佳落點，官方文件自證）。** 但保留一個變體：如果之後雲端助教層要演化成「真的要讓 agent 自主查資料庫、跑多步驟分析、決定要不要主動介入」的複雜任務，Agent SDK（或者其代管版 Managed Agents）會是屆時該重新評估的對象——但那已超出 28 天 hackathon 範圍。

來源：
- [Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview)（含 Bedrock 環境變數與 Agent SDK vs Managed Agents 比較表）
- [Work with sessions](https://code.claude.com/docs/en/agent-sdk/sessions)
- [Claude in Amazon Bedrock](https://platform.claude.com/docs/en/build-with-claude/claude-in-amazon-bedrock)

---

## 4. mem0 / LangGraph ＋ EventBridge

### mem0
- Repo：https://github.com/mem0ai/mem0，Star：**60,029**，License：**Apache-2.0**，最近 push：**2026-07-03**（非常活躍）。
- 定位：「Universal memory layer for AI Agents」——一個記憶抽取/儲存/檢索的library，不是完整 agent runtime，通常搭配某個 agent 框架（LangGraph、Strands 等）使用。
- Bedrock 整合真實存在：`mem0/llms/aws_bedrock.py`，官方文件 [AWS Bedrock 整合頁](https://docs.mem0.ai/integrations/aws-bedrock)；AWS 官方部落格也發過用 mem0 + Bedrock + OpenSearch/Neptune/ElastiCache 做記憶層的案例，AWS 甚至和 mem0 官方宣布合作把 mem0 整進 **Strands Agents SDK**。
- 對本專案：mem0 解決的是「LLM 對話量太大要做語意記憶抽取與檢索」的問題——這正是我們用不到的規模（見第 0 節判斷）。硬套會多一層向量資料庫（OpenSearch/pgvector）依賴，純屬過度工程。

### LangGraph
- Repo：https://github.com/langchain-ai/langgraph，Star：**36,409**，License：**MIT**，最近 push：**2026-07-01**（活躍）。
- AWS 官方有現成範例 repo：[aws-samples/langgraph-agents-with-amazon-bedrock](https://github.com/aws-samples/langgraph-agents-with-amazon-bedrock)，以及更新到 2026 年 5 月、支援 Claude Haiku 4.5 / Sonnet 4.6 的 workshop 教材，加上更進階的 [Bedrock AgentCore + LangGraph 無伺服器多 agent 範例](https://aws.amazon.com/blogs/machine-learning/build-highly-scalable-serverless-langgraph-multi-agent-systems-in-aws-with-amazon-bedrock-agentcore/)。
- 「雲端層用 LangGraph 反而合理」——這個直覺**部分成立**：如果診斷邏輯本身需要「多步驟、有條件分支的工作流」（例如：先分類錯誤類型 → 依類型走不同分析路徑 → 彙整），LangGraph 的 graph/state machine 模型确实比手寫 if/else 更好維護、更好加 checkpoint（`langgraph-checkpoint` 系列套件可接 Postgres/DynamoDB 做狀態持久化）。EventBridge 排程觸發 Lambda 跑 LangGraph 這條路線在 AWS 官方範例裡是被驗證過的組合。
- 但代價：多一層框架抽象（node/edge/state schema），對「診斷邏輯目前還很簡單（可能就是一次 tool use 呼叫）」的現況是提前優化。

**結論：兩者皆為 Tier 3 備選，非首選。** mem0 目前用不上（記憶規模不到需要語意檢索的門檻）；LangGraph 若診斷邏輯後期真的長出多步驟分支，值得升級進來（且 checkpoint 機制剛好能做「持久記憶」），但 28 天內先別為了「看起來更像框架」而引入。

來源：
- [mem0ai/mem0](https://github.com/mem0ai/mem0)、[mem0 AWS Bedrock 文件](https://docs.mem0.ai/integrations/aws-bedrock)、[AWS×mem0 合作公告](https://mem0.ai/blog/aws-and-mem0-partner-to-bring-persistent-memory-to-next-gen-ai-agents-with-strands)
- [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph)、[AWS LangGraph+Bedrock 範例](https://aws.amazon.com/blogs/machine-learning/build-multi-agent-systems-with-langgraph-and-amazon-bedrock/)、[LangGraph+AgentCore 無伺服器範例](https://aws.amazon.com/blogs/machine-learning/build-highly-scalable-serverless-langgraph-multi-agent-systems-in-aws-with-amazon-bedrock-agentcore/)

---

## 5. 最簡路線基準：無框架（Lambda cron + Bedrock 結構化輸出 + DynamoDB）

### 做法
- **排程**：EventBridge Scheduler（cron 語法同其他 AWS 服務），例如每天固定時間對每位學生觸發一次 Lambda（或用單一 Lambda 迴圈跑一批學生）。
- **推論**：直接用 `boto3.client("bedrock-runtime").converse(...)`，在 `toolConfig` 裡定義診斷 JSON schema 當作一個 tool，並用 `toolChoice: {"tool": {"name": "diagnose"}}` **強制**模型一定要呼叫這個 tool——這是 Bedrock 官方文件明確支援、記錄完整的模式，回傳的 `toolUse.input` 就是結構化 JSON，不需要自己做脆弱的「叫模型輸出 JSON 然後 regex 解析」。
- **儲存**：DynamoDB（`student_id` 為 partition key，`timestamp` 或 `session_id` 為 sort key）存逐字稿與診斷結果；若團隊更熟悉關聯式查詢（教師儀表板要做較複雜的統計/排序），RDS/Aurora Serverless 也可以，看團隊既有技能選。
- **記憶＝資料表**：所謂「持久記憶」對這個任務範疇而言，就是「上次診斷結果 + 最近 N 次逐字稿摘要」被塞進下一次呼叫的 prompt 裡而已，不需要框架幫你做這件事。

### 授權／離線／ARM
全部是 AWS 官方 SDK（boto3，Apache-2.0）與 AWS 服務，無額外第三方授權疑慮；離線與 ARM 議題不適用（雲端服務）。

### 中英支援
完全取決於 Bedrock 上選的 Claude 模型本身的中英夾雜能力，與這條路線本身無關（這條路線對中英夾雜是中性的，不增不減能力）。

### 延遲特性
不適用（背景 best-effort 任務，沒有即時性要求，正好吻合「非同步 best-effort」定位）。

### 活躍度／二開難易
**這條路線的「活躍度」就是 AWS Bedrock Converse API 本身的成熟度**——官方文件完整、`toolChoice` 強制工具呼叫、JSON schema 驗證都是文件化功能，非 hack。程式碼量最小（一個 Lambda handler、一個 boto3 呼叫、一個 DynamoDB put_item），二開難度最低、故障面最小、最容易在 5 天內讓一個對 agent 框架不熟的人接手完成。

**結論：採用（Tier 1，主線）。** 這是「28 天內可靠交付的底線」，也應該是 demo 的主線。

來源：
- [Bedrock Converse API 呼叫工具](https://docs.aws.amazon.com/bedrock/latest/userguide/tool-use-inference-call.html)
- [Converse API tool use 範例（含強制 toolChoice）](https://docs.aws.amazon.com/bedrock/latest/userguide/tool-use-examples.html)
- [用 Converse API 產生結構化 JSON](https://builder.aws.com/content/2hWA16FSt2bIzKs0Z1fgJBwu589/generating-json-with-the-amazon-bedrock-converse-api)
- [EventBridge Scheduler cron/one-time events](https://oneuptime.com/blog/post/2026-02-12-eventbridge-scheduler-cron-one-time-events/view)

---

## 排序推薦表

| 排名 | 候選 | 授權 | 活躍度 | 二開難度 | 適配結論 | 一句話理由 |
|---|---|---|---|---|---|---|
| 1（主線） | 無框架：Lambda + Bedrock Converse tool use + DynamoDB | AWS SDK / Apache-2.0 | Bedrock API 本身持續在更新 | 最低（單一 Lambda handler） | **採用** | 5 天內最快接通、故障面最小、demo 風險最低 |
| 2（條件備選） | Letta | Apache-2.0，23.6k★，2 年半成熟 | 健康（一週內有 push，issue 可控） | 中（要自架 Postgres+Docker） | **備選** | 唯一原生「per-user 持久 agent + Bedrock」都對題的框架，敘事效果好，但多一個要顧的有狀態服務 |
| 3 | LangGraph（+ EventBridge） | MIT，36.4k★ | 高（近日有 push） | 中（graph/state 抽象要學） | 備選 | 診斷邏輯若後期變多步驟分支，checkpoint 機制可當持久記憶用；現階段是提前優化 |
| 4 | mem0 | Apache-2.0，60k★ | 極高（當日 push） | 低-中（多一個向量庫依賴） | 備選/暫緩 | 解決的是語意記憶檢索問題，我們的記憶規模用不到 |
| 5 | Claude Agent SDK + Bedrock | Anthropic 商用條款 | 官方持續維護 | 中-高（要拆掉不需要的檔案系統/工具/session 機制） | **淘汰（非最佳落點，官方文件自證）** | 為編碼 agent 設計，我們的任務是「一次結構化呼叫」，用不到其重量級能力 |
| 6 | Hermes Agent | MIT，208k★，25k+ open issues | 極高（每日 push） | 高（53MB monorepo，個人助理架構） | **淘汰** | 真實存在且 Bedrock 原生支援紮實，但是個人助理/桌面應用定位，與多租戶後端需求架構錯位 |

---

## 28 天內的整合路徑

### Day 1-3：主線骨架（無框架路線）
- 設計 DynamoDB schema：`Transcripts`（student_id, session_id, timestamp, text, lang_mix_tag）、`Diagnostics`（student_id, date, json_result）。
- 寫一個本機腳本，用 boto3 `converse()` + 固定 `toolConfig`（診斷 JSON schema：弱點分類、發音/語法/詞彙錯誤標記、下一步鷹架建議）手動跑一筆假資料，驗證 Bedrock 回傳格式符合預期。**這一步就是「5 天內接通 Bedrock 跑出診斷 JSON」的達成點，理論上 1-2 天可完成。**
- 用邊緣層原型已有的 SQLite 逐字稿資料，寫一支同步腳本把資料灌進 DynamoDB（或直接評估：如果決賽只需要單機 demo，可以先跳過雲端同步，直接讓雲端層讀本機 SQLite 匯出的 JSON 檔）。

### Day 4-7：排程化 + 教師儀表板串接
- 包成 Lambda handler，設定 EventBridge Scheduler（cron 或 rate expression）每日觸發。
- 教師儀表板（FastAPI 或既有前端）加一支 API 讀 `Diagnostics` 表，畫出「本週弱點趨勢」等基本圖表。
- **降級（mock）方案準備**：寫一份人工預先跑好、存成靜態 JSON 的「範例診斷結果」，在 Bedrock 額度用盡、網路不穩、或決賽現場斷網時，儀表板可切換讀取這份 mock 資料，不影響 demo 敘事完整性。這是決賽現場斷網情境下的保命方案，務必在 Day 5-7 內就準備好，不要拖到最後一週才做。

### Day 8-14：打磨診斷品質 + 觀察是否需要升級
- 調整 tool schema、prompt，讓診斷 JSON 更貼近「中英夾雜」語料的實際錯誤模式（例如標出是英文詞彙缺口還是中文語法干擾）。
- 這時候評估：如果診斷邏輯開始需要「先分類再依類型分支處理」的多步驟工作流，**才**考慮引入 LangGraph（用其 checkpoint 機制順便取得「持久記憶」，一魚兩吃）。如果邏輯還是「一次呼叫搞定」，維持現狀，不要為了框架而框架。

### Day 15-21：（可選）Letta 展示分支
- 若團隊決定要在 demo 敘事裡強調「持久記憶 agent 架構」（例如評審提問导向），且有餘裕人力，另開一條分支：Docker 自架 Letta，用其 Identities API 把每個學生綁一個 agent，接 Bedrock provider，驗證跟主線 DynamoDB 資料能不能共存或互相同步。**這條分支不應阻塞主線**——主線永遠是 fallback。

### Day 22-28：整合測試、斷網演練、Buffer
- 完整跑一次「邊緣層離線 demo + 雲端助教層斷網 mock 切換」的彩排，確認教師儀表板在雲端不可達時能無縫顯示 mock 診斷資料。
- 保留至少 3-5 天 buffer 處理不可預期問題（Bedrock 額度/區域可用性、IAM 權限踩坑等）。

---

## 核心判斷題回答（總結）

**哪條路 5 天內能真的接通 Bedrock 跑出診斷 JSON？**
方案 5（無框架，boto3 直呼 Bedrock Converse API + 強制 `toolChoice` 產生結構化 JSON）。這是文件最完整、依賴最少、程式碼量最小的路徑，1-2 天可以跑出第一筆診斷 JSON，5 天內完成排程化與儲存絕對足夠寬裕。

**框架的持久記憶在 demo 時間尺度有多少實際價值？**
幾乎沒有。Letta 的三層記憶（core/archival/recall）、Hermes 的多重記憶後端插件、mem0 的語意記憶抽取，全部是為了解決「對話量大到塞不進單次 context」的問題——而我們的學生一天的對話量遠遠塞得進一次 prompt。這些框架的核心賣點在我們的規模下轉不成 demo 上看得到的差異，卻要多付出真實的整合與維運成本。「持久」這個詞，在我們的規模下，等於「有一張資料庫表格」，不等於「需要一套記憶管理框架」。

**主線 ＋ 降級（mock）方案：**
- 主線：EventBridge Scheduler → Lambda → boto3 `converse()` 強制 tool use → DynamoDB（逐字稿 + 診斷 JSON）→ 教師儀表板讀取。
- 降級：預先準備好的靜態 mock 診斷 JSON，儀表板具備「雲端不可達時自動切換 mock 來源」的開關，決賽現場斷網不影響雲端助教層敘事的展示完整性（邊緣即時層本來就設計為離線可用，雲端層斷線不影響核心 demo，只影響「診斷報表」這個加分項，mock 保底即可）。
