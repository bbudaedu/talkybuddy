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
| IAM Role | `TalkyBuddyAgentCoreExecution` | `arn:aws:iam::641079926753:role/TalkyBuddyAgentCoreExecution` | ✅ |
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
export AGENTCORE_MEMORY_ARN=arn:aws:bedrock-agentcore:ap-southeast-1:641079926753:memory/TalkyBuddyStudentMemory-sO0KeDB7kP
export AGENTCORE_HARNESS_ORCHESTRATOR=arn:aws:bedrock-agentcore:ap-southeast-1:641079926753:harness/TalkyBuddyOrchestrator-Iq4xJkd3Ln
export AGENTCORE_HARNESS_HOMEWORK=arn:aws:bedrock-agentcore:ap-southeast-1:641079926753:harness/TalkyBuddyHomework-jTJ4Czs45L
export AGENTCORE_HARNESS_REPORT=arn:aws:bedrock-agentcore:ap-southeast-1:641079926753:harness/TalkyBuddyReport-KjXfIJOS75
```

未設 `TALKYBUDDY_AGENT_BACKEND=agentcore` 時，既有 in-process 路徑行為完全不變。

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
