# AgentCore 已建立的資源（2026-07-26）

Region **`ap-southeast-1`（新加坡）**。

## 為什麼不是台北

`ap-east-2`（台北）**沒有 AgentCore 服務** —— 實測
`bedrock-agentcore-control.ap-east-2.amazonaws.com` endpoint 不存在，
AWS console 按台北的 AgentCore 會直接跳到雪梨。

同時具備 AgentCore **與**滿額 Bedrock 配額的 region 只有三個：

| Region | AgentCore | Sonnet 5 TPM | 離台灣 |
|---|---|---|---|
| `ap-southeast-1` 新加坡 | ✅ | 6,000,000 | 約 50ms ← **採用** |
| `ap-southeast-2` 雪梨 | ✅ | 6,000,000 | 約 130ms |
| `eu-central-1` 法蘭克福 | ✅ | 6,000,000 | 很遠 |
| `ap-east-2` 台北 | ❌ | 6,000,000 | 最近但沒服務 |
| `ap-northeast-1` 東京 | ✅ | **0** | 近但沒配額 |
| `us-east-1` / `us-west-2` | ✅ | **0** | — |

## 資源清單

| 類型 | 名稱 | ARN / ID | 狀態 |
|---|---|---|---|
| IAM Role | `TalkyBuddyAgentCoreExecution` | `arn:aws:iam::<AWS_ACCOUNT_ID>:role/TalkyBuddyAgentCoreExecution` | ✅ |
| Memory | `TalkyBuddyStudentMemory` | `...:memory/TalkyBuddyStudentMemory-sO0KeDB7kP` | ACTIVE |
| Harness | `TalkyBuddyOrchestrator` | `...:harness/TalkyBuddyOrchestrator-Iq4xJkd3Ln` | READY |
| Harness | `TalkyBuddyHomework` | `...:harness/TalkyBuddyHomework-jTJ4Czs45L` | READY |
| Harness | `TalkyBuddyReport` | `...:harness/TalkyBuddyReport-KjXfIJOS75` | READY |

Memory 策略兩種（比照 workshop Lab 2）：

- `StudentSemantic` — namespace `/student/{actorId}/semantic`，記孩子的興趣、
  已掌握詞彙、重複出現的錯誤
- `SessionSummary` — namespace `/student/{actorId}/session/{sessionId}/summary`，
  每次練習的摘要

> ⚠️ summarization 策略的 namespace **必須**含 `{sessionId}`，否則
> `CreateMemory` 會回 ValidationException。這是 API 實測得知，文件未強調。

## 環境變數

```bash
export TALKYBUDDY_AGENT_BACKEND=agentcore
export AGENTCORE_REGION=ap-southeast-1
export AGENTCORE_MEMORY_ARN=arn:aws:bedrock-agentcore:ap-southeast-1:<AWS_ACCOUNT_ID>:memory/TalkyBuddyStudentMemory-sO0KeDB7kP
export AGENTCORE_HARNESS_ORCHESTRATOR=arn:aws:bedrock-agentcore:ap-southeast-1:<AWS_ACCOUNT_ID>:harness/TalkyBuddyOrchestrator-Iq4xJkd3Ln
export AGENTCORE_HARNESS_HOMEWORK=arn:aws:bedrock-agentcore:ap-southeast-1:<AWS_ACCOUNT_ID>:harness/TalkyBuddyHomework-jTJ4Czs45L
export AGENTCORE_HARNESS_REPORT=arn:aws:bedrock-agentcore:ap-southeast-1:<AWS_ACCOUNT_ID>:harness/TalkyBuddyReport-KjXfIJOS75
```

未設 `TALKYBUDDY_AGENT_BACKEND=agentcore` 時，既有 in-process 路徑行為完全不變。

## 安全稽核（2026-07-26，依 AWS Agent Toolkit 官方 skill 修正）

裝了 `aws agent-toolkit add-skill --skill-name amazon-bedrock --agent claude-code`
之後，拿 `references/agentcore-harness.md` 對照本專案的實作，抓到三個缺陷並已修復：

| # | 缺陷 | 風險 | 修法 |
|---|---|---|---|
| 1 | **Confused deputy** — 執行角色的 trust policy 只寫 `Principal: bedrock-agentcore.amazonaws.com`，沒有任何 Condition | 🔴 **任何 AWS 帳號的 harness 都能假冒服務主體 assume 這個角色**，進而用我們的權限打 Bedrock 與讀 Memory | 加上 `aws:SourceAccount` + `aws:SourceArn` 兩個 confused-deputy 條件 |
| 2 | 模型權限 `Resource: "*"` | 🟠 執行角色可呼叫帳號內任何 Bedrock 模型，超出需要 | 收斂到 `anthropic.claude-*` 的 foundation-model 與 inference-profile ARN |
| 3 | `update_harness` 只傳 `model` 時，**`maxTokens` 被靜默重置為 None** | 🟠 官方明列 maxIterations/maxTokens/timeoutSeconds 須顯式設定為成本與濫用護欄；微 VM 每次呼叫都帶 shell 存取，不設上限等於開放資源耗盡 | 更新時一併重傳三個上限，並已驗證回讀值 |

> 缺陷 3 的教訓：`update_harness` **不是 patch 語意**。只傳部分欄位會讓其他欄位掉回預設。
> 每次更新都要把三個執行上限一起傳。

現行值：`maxIterations=3` / `timeoutSeconds=60` /
`maxTokens` 依角色 512（orchestrator）、1024（homework）、2048（report）。

其他官方要點（目前無風險，記錄備查）：

- **inbound auth 只能二選一**：無 `authorizerConfiguration` 即 SigV4，有則為 OAuth JWT，沒有混合模式。
  本專案由後端伺服器以 IAM 憑證呼叫，SigV4 正確；若日後要讓瀏覽器直連才需改 JWT。
- **`InvokeHarness` 的呼叫端需要兩個權限**：`bedrock-agentcore:InvokeHarness`
  **與** `bedrock-agentcore:InvokeAgentRuntime`。目前 `talkybuddy-admin` 掛
  AdministratorAccess 所以沒問題，賽後收斂權限時要記得同時給。
- **`CreateHarness` 需要 `iam:PassRole`** —— 官方說這是 CreateHarness 出現
  AccessDenied 最常見的原因。
- `messageStop.stopReason` 的 `max_iterations_exceeded` 是官方文件列出的正常值，
  與我們實測到的一致。

## 實測踩到的三個坑（都不在文件裡）

1. **`runtimeSessionId` 最短 33 字元。** 傳「verify-1」會被
   `ParamValidationError` 擋下。`server/agentcore.py::_normalize_session_id`
   以 sha256 做決定性補齊——必須決定性，否則同一個教學循環的多次呼叫會落在
   不同 session，短期記憶就斷了。

2. **`InvokeHarness` 回傳的是 EventStream，不是 dict。** 與
   `bedrock-runtime.converse` 的形狀完全不同，不能照抄。

3. **`AmazonBedrockFullAccess` 不涵蓋 `bedrock-agentcore:*`。**
   Harness 存取 Memory 需要 `ListEvents` / `CreateEvent` /
   `RetrieveMemoryRecords` 等，已用 inline policy
   `TalkyBuddyAgentCoreRuntime` 補上。

## 目前的阻塞

Harness 已能執行代理迴圈（串流回傳 `messageStop`），但底層模型呼叫仍被
**帳號驗證**擋住：

- Haiku 4.5 / Sonnet 4 → `ThrottlingException: Too many tokens per day`
- Sonnet 5 → `AccessDeniedException: not available for this account`
  （跨 region 一致，即使 `agreementAvailability` 已是 AVAILABLE）

三個 Harness 目前設為 `global.anthropic.claude-sonnet-4-5-20250929-v1:0`
（帳號確實有的模型）。驗證放行後改回 Sonnet 5 只需 `update_harness`。

**驗證超過 2 小時請寄 `aws-verification@amazon.com`。**

## 自訂 skill：`taiwan-elementary-english`（待掛載）

三個 agent 共用的教學依據（課綱字彙分級、主題與溝通功能、國小語言形式
上限、兒童安全用語）不塞進三份 system prompt，而是做成一份 Harness skill。
改一次三個 agent 一起生效。

檔案由腳本產生，**不要手改**：

```bash
python3 scripts/generate_agent_skill.py
# → deploy/aws/skills/taiwan-elementary-english/SKILL.md
```

內容全部取自 `data/curriculum/moe_english_2018.json`（教育部領綱官方抽取）
與專案既有常數（`guardrails.CHILD_SAFETY_CLAUSE`、`curriculum._TARGET_FORM`）。
課綱資料更新或安全條款改字時重跑一次即可，不會靜默過期。

### 掛上去（AWS 放行後才做，目前**未執行過**）

`skills` 的四種來源是 `path` / `s3` / `git` / `awsSkills`（union，擇一）。
本專案用 S3：

```bash
R=ap-southeast-1
BUCKET=<你的 bucket>
aws s3 sync deploy/aws/skills/ s3://$BUCKET/skills/ --region $R

# ⚠️ update-harness 不是 patch 語意——只傳 skills 會讓 model / maxTokens /
#    maxIterations / timeoutSeconds 掉回預設（本專案被它咬過一次）。
#    三個 harness 各跑一次，每次都要把上限一起重傳。
aws bedrock-agentcore-control update-harness --region $R \
  --harness-id TalkyBuddyHomework-jTJ4Czs45L \
  --skills "[{\"s3\":{\"uri\":\"s3://$BUCKET/skills/taiwan-elementary-english/\"}}]" \
  --model '{"bedrockModelConfig":{"modelId":"global.anthropic.claude-sonnet-4-5-20250929-v1:0","maxTokens":1024,"apiFormat":"converse_stream"}}' \
  --max-iterations 8 --max-tokens 1024 --timeout-seconds 60 \
  --memory '{"agentCoreMemoryConfiguration":{"arn":"'"$AGENTCORE_MEMORY_ARN"'"}}'
```

掛完後回讀確認欄位沒被重置：

```bash
aws bedrock-agentcore-control get-harness --region $R --harness-id <id> \
  --query '{skills:skills,maxTokens:maxTokens,maxIterations:maxIterations,timeoutSeconds:timeoutSeconds}'
```

### 安全注意（官方文件的 harness skills 段）

skill 的內容——**包含它帶的任何腳本**——會被當成**可信輸入**注入 agent
context，而且**沒有 IAM condition key 能限制 per-invocation 的 `skills`
欄位**：invoke 時傳同名 skill 會覆蓋 harness 上掛好的那份。

所以 S3 bucket 要當成程式碼來管（限制寫入權限、開版本控制），而且
應用層絕不可以把外部輸入透傳到 `InvokeHarness` 的 `skills`。
本專案的 `agentcore.invoke()` 參數是固定組出來的、沒有 kwargs 透傳，
`tests/test_agentcore_client.py::test_invoke_never_forwards_a_skills_override`
把送出的參數鍵集合釘死，避免日後有人加一個 `**extra` 就把洞開了。

## 清理指令（決賽後）

```bash
R=ap-southeast-1
aws bedrock-agentcore-control delete-harness --region $R --harness-id TalkyBuddyOrchestrator-Iq4xJkd3Ln
aws bedrock-agentcore-control delete-harness --region $R --harness-id TalkyBuddyHomework-jTJ4Czs45L
aws bedrock-agentcore-control delete-harness --region $R --harness-id TalkyBuddyReport-KjXfIJOS75
aws bedrock-agentcore-control delete-memory  --region $R --memory-id TalkyBuddyStudentMemory-sO0KeDB7kP
aws iam delete-role-policy --role-name TalkyBuddyAgentCoreExecution --policy-name TalkyBuddyAgentCoreRuntime
aws iam detach-role-policy --role-name TalkyBuddyAgentCoreExecution --policy-arn arn:aws:iam::aws:policy/AmazonBedrockFullAccess
aws iam delete-role --role-name TalkyBuddyAgentCoreExecution
```
