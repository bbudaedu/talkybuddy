# 說說學伴 × Amazon Bedrock AgentCore — 架構重新設計

**日期**：2026-07-26　**狀態**：設計，尚未實作　**決賽**：約 2026-07-30

---

## 1. 先講清楚現況

**目前的三個 agent 完全沒有用到 AgentCore。**

| 元件 | 現況 |
|---|---|
| `server/agents/orchestrator.py` | in-process 自建，規則式 + Bedrock Converse |
| `server/agents/homework.py` | in-process 自建 |
| `server/agents/report.py` | in-process 自建 |
| `server/diagnose.py` | in-process 自建 |
| 記憶 | 自建 SQLite（`interactions` / `diagnoses` / `student_profile` / `agent_outputs`）|
| 身分 | 自寫 JWT（`server/auth.py`）|
| 護欄 | 自寫規則（`server/guardrails.py`）|
| 觀測性 | 無 |
| 評估 | 無 |

雲端推理只用到 Bedrock 的**模型層**（`boto3 bedrock-runtime.converse()`），
沒有碰 AgentCore 的**代理基礎設施層**。

**為什麼會這樣**：一是 2026-07-08 拍板的鐵律「不引外部 agent 框架，編排用
in-process 輕量控制器」；二是 AgentCore workshop 從 Prereqs 就卡住——先是 IAM
權限不足，補齊 `AdministratorAccess` 後又卡在 AWS 帳號驗證，9 個 lab 一個都沒跑成。

---

## 2. 這次重新設計要面對的核心張力

> **AgentCore 是純雲端的 serverless 平台。**
> **決賽評分最高的記憶點是「主持人當場斷網，裝置繼續離線對話」。**

把全部搬上 AgentCore 等於親手拆掉最高分的橋段。所以正確的設計不是「全部搬過去」，
而是**明確切一條線**：什麼必須留在裝置上，什麼該交給 AgentCore。

這條線就是既有的 kill-switch（`network_mode`）。

---

## 3. 目標架構

```mermaid
flowchart TB
    subgraph EDGE["🔌 Genio 520 裝置端（離線必須能活）"]
        WAKE["喚醒詞<br/>sherpa-onnx KWS"]
        ASR["ASR<br/>SenseVoice"]
        ELLM["邊緣 LLM<br/>llama.cpp / qwen2.5-1.5B"]
        ETTS["TTS<br/>piper zh/en"]
        SQLITE[("本地 SQLite<br/>互動紀錄 / 產出")]
        SCAF["鷹架題庫<br/>scaffold + curriculum"]
    end

    subgraph BOUNDARY["⚡ 信任與連線邊界"]
        KILL{{"network_mode<br/>kill-switch"}}
        CONSENT{{"家長同意閘門<br/>consent_granted"}}
        DEID["去識別化<br/>deidentify"]
    end

    subgraph CLOUD["☁️ AgentCore（ap-east-2 台北）"]
        subgraph RT["AgentCore Runtime"]
            ORCH["編排 agent<br/>決策判斷"]
            TUTOR["導師 agent<br/>四維診斷"]
            HW["派作業 agent"]
            RPT["家長週報 agent"]
        end
        MEM[("AgentCore Memory<br/>短期 session<br/>長期跨 session")]
        GW["AgentCore Gateway<br/>工具 → MCP"]
        IDP["AgentCore Identity<br/>+ Cognito"]
        POL["AgentCore Policy<br/>Cedar 規則"]
        OBS["AgentCore<br/>Observability"]
        EVAL["AgentCore<br/>Evaluations"]
        FM["Bedrock 模型<br/>Sonnet 5 / Haiku 4.5"]
    end

    subgraph TOOLS["Gateway 後的工具（Lambda / API）"]
        T1["課綱查詢"]
        T2["發音評分"]
        T3["家長通知"]
    end

    WAKE --> ASR --> KILL
    KILL -->|edge| ELLM
    ELLM --> ETTS
    SCAF --> ELLM
    KILL -->|cloud| CONSENT --> DEID --> IDP
    IDP --> ORCH
    ORCH --> TUTOR & HW & RPT
    ORCH <--> MEM
    TUTOR & HW & RPT --> FM
    ORCH --> GW --> POL
    POL --> T1 & T2 & T3
    RT -.OTEL.-> OBS --> EVAL
    RPT -.結果回寫.-> SQLITE
    ETTS --> OUT(["🔊 孩子聽到回覆"])
    RT --> OUT

    classDef edge fill:#1a4d3a,stroke:#2d7a5a,color:#fff
    classDef cloud fill:#1a3a5c,stroke:#2d6a9f,color:#fff
    classDef gate fill:#5c3a1a,stroke:#9f6a2d,color:#fff
    class WAKE,ASR,ELLM,ETTS,SQLITE,SCAF edge
    class ORCH,TUTOR,HW,RPT,MEM,GW,IDP,POL,OBS,EVAL,FM cloud
    class KILL,CONSENT,DEID gate
```

**唯讀一句話**：kill-switch 左邊全部在裝置上、斷網照跑；右邊全部是 AgentCore，
斷網時整塊消失但不影響孩子繼續對話。

---

## 4. 自建元件 → AgentCore 對應

| 現有自建 | AgentCore 服務 | 換掉的理由 | 風險 |
|---|---|---|---|
| `orchestrator.py` 決策判斷 | **Runtime**（或 **Harness**）上的主 agent | Harness 一次 API 呼叫就有 agent loop + 工具執行 + 記憶，不必自己寫編排 | 決策延遲從 in-process 變成一次網路往返 |
| `homework.py` / `report.py` | **Runtime** 非同步 agent | Runtime 明確支援長時間非同步代理，正好對上 12s 預算的路徑 | 冷啟動 |
| `diagnose.py` 四維診斷 | **Runtime** agent + **Evaluations** | Evaluations 能對 session/trace 做自動化品質評估，取代目前「無評估」的狀態 | 需 OTEL 埋點 |
| SQLite `interactions` / `diagnoses` | **Memory**（短期 = 多輪對話，長期 = 跨 session） | 官方支援跨 agent 共享記憶庫，目前三個 agent 各自讀 store 是重複邏輯 | **斷網時完全不可用**，本地必須保留 |
| `auth.py` 自寫 JWT | **Identity** + Cognito | 不必自己維護簽發/驗證，且與 Gateway 的工具授權打通 | 現有 `_resolve_student` 邏輯要重寫 |
| `guardrails.py` 護欄 | **Policy**（Cedar）+ Bedrock Guardrails | Policy 在 Gateway 攔截**每一次工具呼叫**，比事後過濾強；兒童安全是硬限制 | Cedar 要學 |
| `scaffold.py` / `curriculum.py` 題庫 | **Gateway** 包成 MCP tool | 讓任何 MCP 相容的 agent 都能取用課綱，不必複製邏輯 | 題庫查詢變成網路呼叫 |
| 無 | **Observability**（OTEL → CloudWatch） | 目前完全看不到 agent 在做什麼，現場出事無法查 | — |
| 無 | **Optimization** | 用 trace 自動產生 prompt 改善建議 + Gateway 分流做 A/B | 需先有 Evaluations |

---

## 5. 兩條時序：連線 vs 斷網

### 5.1 連線時（AgentCore 全開）

```mermaid
sequenceDiagram
    participant K as 孩子
    participant D as Genio 520
    participant ID as AgentCore Identity
    participant O as 編排 agent<br/>(Runtime)
    participant M as AgentCore Memory
    participant G as Gateway + Policy
    participant B as Bedrock FM

    K->>D: 「說說學伴，I like apple」
    D->>D: 喚醒 + ASR（本地）
    D->>D: 去識別化 + consent 檢查
    D->>ID: 帶 token 請求
    ID->>O: 驗證通過，建立 session
    O->>M: 取回長期記憶（程度、興趣、弱項）
    O->>B: 組 prompt 生成回覆
    B-->>O: 回覆文字
    O->>M: 寫入本回合短期記憶
    O-->>D: 回覆
    D->>K: 🔊 TTS 播放

    Note over O,G: 回合後（非同步，不佔即時預算）
    O->>O: 決策：要不要派作業／發週報
    O->>G: 呼叫課綱工具
    G->>G: Cedar 政策檢查
    G-->>O: 題目
    O-->>D: 產出回寫本地
```

### 5.2 斷網時（主持人切 kill-switch）

```mermaid
sequenceDiagram
    participant H as 主持人
    participant D as Genio 520
    participant C as AgentCore

    H->>D: 切斷雲端連線
    D->>D: network_mode = edge
    Note over D,C: 以下全程零出境

    participant K as 孩子
    K->>D: 「I like apple」
    D->>D: 喚醒 + ASR（本地）
    D->>D: 邊緣 LLM 生成回覆
    D->>D: 本地 TTS
    D->>K: 🔊 照常回覆
    D->>D: 規則式診斷 + 規則式派作業
    D->>D: 寫入本地 SQLite

    Note over D: 恢復連線後才把derived 文字補傳
```

**這張圖是決賽的核心賣點**：右邊那條線整個消失，孩子端**完全無感**。

---

## 6. 遷移路徑（分階段，可隨時停在任一階段）

```mermaid
flowchart LR
    P0["現況<br/>全部自建<br/>528 tests ✅"] --> P1
    P1["階段 1<br/>Observability<br/>OTEL 埋點"] --> P2
    P2["階段 2<br/>Gateway<br/>題庫 → MCP tool"] --> P3
    P3["階段 3<br/>Runtime<br/>三個 agent 上雲"] --> P4
    P4["階段 4<br/>Memory<br/>取代雲端側 store"] --> P5
    P5["階段 5<br/>Identity + Policy<br/>Cognito + Cedar"] --> P6
    P6["階段 6<br/>Evaluations<br/>+ Optimization"]

    style P0 fill:#1a4d3a,stroke:#2d7a5a,color:#fff
    style P1 fill:#5c3a1a,stroke:#9f6a2d,color:#fff
    style P2 fill:#5c3a1a,stroke:#9f6a2d,color:#fff
```

**階段排序的理由**：

1. **Observability 先做** — 零風險、不改行為，但立刻讓現場出事查得到。而且
   Evaluations 與 Optimization 都建立在 OTEL trace 上，先埋才有後面。
2. **Gateway 次之** — 把題庫包成 MCP tool，既有程式碼不動，只是多一條取用路徑。
3. **Runtime 第三** — 這一步才真的改變執行位置，也是第一個會影響延遲的步驟。
   對話路徑（1.5s 預算）**不建議**搬，只搬非同步的診斷/作業/週報。
4. Memory / Identity / Policy / Evaluations 屬於加值，時間不夠可全部不做。

**現實提醒**：階段 1 之後的每一步都需要 AWS 帳號驗證通過。
截至 2026-07-26 仍是 `Your account is currently being verified`，因此
**目前一階段都動不了**。

---

## 7. 我對「全面改用 AgentCore」的保留意見

必須誠實講三件事：

1. **對話路徑不該上 AgentCore。** 1.5 秒是斷網橋段 D-03 的驗收上界，
   目前 `cloud_llm` 直呼 Converse 已經很緊。多一層 Runtime 就是多一次網路往返，
   風險大於收益。**建議只搬非同步路徑。**

2. **離線能力無法被 AgentCore 取代，只能並存。** Memory 再好，斷網時就是零。
   本地 SQLite 與規則式 fallback 必須保留，這代表「兩套邏輯」的維護成本
   不會因為上了 AgentCore 而消失，反而增加。

3. **4 天內做不完，也不該做完。** 528 個測試守著的自建版本現在是可用的、
   離線端到端跑通的。全面重寫會把已驗證的東西換成未驗證的。
   **建議只做階段 1–2 當作加分敘事，主線維持現狀。**

如果決賽評分明確要求「使用 AgentCore」而非「使用 Bedrock」，那就以階段 3
只搬週報 agent 為最小可展示切片——它是非同步、延遲無所謂、且在教師儀表板上
看得見，風險最低而展示效果最完整。
