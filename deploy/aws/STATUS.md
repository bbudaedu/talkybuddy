# AWS 雲端主線 — 現況與待辦（更新 2026-07-26）

決賽 ≈2026-07-30。本檔記錄雲端主線（Claude Code 這條）的狀態；
edge 線（Genio 520 / Phase 10 NPU）由 GSD pi 另一條線負責，**不要動**。

> ⚠️ 兩條線共用同一個 worktree 與分支 `gsd/2-genio-520-edge-mvp`。
> commit 時務必逐檔明確 stage，**絕不可 `git add -A`**（會掃到對方未完成的
> `.planning/` 產出），也不可切換分支（會讓對方的 commit 落到錯的分支）。

---

## 已完成（程式碼層面 100%）

| 項目 | 狀態 |
|---|---|
| `server/bedrock_converse.py` — 原生 boto3 Converse provider | ✅ + 測試 |
| `diagnose.py` 走 Bedrock（教師診斷） | ✅ + 測試 |
| `cloud_llm.py` 走 Bedrock（對話大腦） | ✅ + 測試 |
| `/api/status` 新增 `cloud_provider` 欄位 | ✅ + 容器內實測 |
| 降級鏈 Bedrock → relay → 規則式 | ✅ **實戰驗證過**（Bedrock throttle 時正確降級） |
| Docker image（cloud-only，1.46GB） | ✅ build + run + health 全通過 |
| EC2 部署腳本 / IAM policy / runbook | ✅ 寫好，**未在真 EC2 跑過** |
| model 分流（對話 Haiku / 診斷 Sonnet） | ✅ + 測試 |
| 測試 | ✅ 429 passed |

程式碼已推送至 `origin/gsd/2-genio-520-edge-mvp`（先前整個 Milestone 2 零遠端備份，已解決）。

---

## 🔴 唯一阻塞點：AWS 帳號 Bedrock 配額被歸零

> ⚠️ **2026-07-26 更正**：先前寫的「根因是 Free plan」**已被實測推翻**。
> 帳號早已升級且驗證為 `accountPlanType: PAID / ACTIVE`（credits $51.92），
> IAM 也已掛 `AdministratorAccess`，Bedrock 仍然全模型 `ThrottlingException`。

**實際根因（2026-07-26 以 Service Quotas API 佐證）**：帳號的 Bedrock
on-demand token 配額被**明確套用為 0**，而非 AWS 預設值。

| 配額 | AWS 預設 | 本帳號套用 |
|---|---|---|
| Cross-region TPM — Claude Sonnet 5（`L-D4FBCF4E`） | 6,000,000 | **0.0** |
| Cross-region TPM — Claude Haiku 4.5（`L-58BE175A`） | 5,000,000 | **0.0** |
| Model invocation max tokens per day（各模型） | — | **0.0**（`Adjustable: false`） |

驗證指令：

```bash
aws service-quotas get-aws-default-service-quota --service-code bedrock --quota-code L-D4FBCF4E   # 6000000.0
aws service-quotas get-service-quota             --service-code bedrock --quota-code L-D4FBCF4E   # 0.0
```

**自助管道無效**：`request-service-quota-increase` 回
`You must provide a quota value greater than the default quota value of 6000000.0`
—— 它只受理「高於預設」的申請，對「被歸零」的情況幫不上忙。
`aws support` API 亦不可用（`SubscriptionRequiredException`，Basic plan 無 API 權限）。

**唯一剩下的路**：AWS Console 開 Support case（Account and billing，Basic plan 免費），
說明 applied quota = 0 而 default = 6,000,000，要求恢復預設 on-demand 配額。

**排除法已完整**（全部實測，非推論）：

| 可能原因 | 結果 |
|---|---|
| 模型未開通 | ❌ 排除 — `authorizationStatus: AUTHORIZED` |
| use-case 表單未提交 | ❌ 排除 — 已在檔（Education / children's English tutor） |
| 授權協議未簽 | ❌ 排除 — `agreementAvailability: AVAILABLE` |
| region 不支援 | ❌ 排除 — `regionAvailability: AVAILABLE` |
| **Free plan** | ❌ **排除（2026-07-26 新增）** — 已升級 `PAID / ACTIVE`，仍 throttled |
| **IAM 權限不足** | ❌ **排除（2026-07-26 新增）** — 已掛 `AdministratorAccess`，仍 throttled |
| 配額被歸零 | ✅ **就是這個** — applied 0.0 vs default 6,000,000 |

實測涵蓋 3 個 region × 8+ 模型組合（含東京 `jp.` 前綴、Opus 4.5、Sonnet 4.6），
**全部一致 throttled** → 帳號層級限制，非模型或 region 問題。

### 待辦（使用者操作）

1. **開 Support case** ← 現在唯一的路
   Console → Support → Create case → **Account and billing**（Basic plan 免費）
   內容要點：applied Bedrock on-demand quota = 0，AWS default = 6,000,000，
   要求恢復預設配額。附上 `L-D4FBCF4E` / `L-58BE175A` 兩個 quota code。
2. **刪除 root access key**（root 金鑰無法被 IAM 限權，且本 repo 公開）。
3. 決賽後 **detach `AdministratorAccess`** 並刪掉 `talkybuddy-admin` 的 access key。

### 已建立的 AWS 資源（2026-07-26 更新）

- IAM user `talkybuddy-admin`：`AmazonBedrockFullAccess` + **`AdministratorAccess`**
  （決賽用的臨時全權，賽後務必 detach）
- AWS Budgets `talkybuddy-monthly-5usd`：月度 $5 上限，50/80/100% 實際 +
  100% 預測共 4 個 email 通知 → `coolexam@ntnueng.tw`
- Claude Sonnet 5 foundation model agreement 已建立（`agreementAvailability: AVAILABLE`），
  但因配額為 0 仍無法呼叫
- 本機預設 region `us-west-2`

---

## 配額恢復後的第一件事

```bash
cd ~/talkybuddy
TALKYBUDDY_CLOUD_PROVIDER=bedrock .venv/bin/python scripts/aws_preflight.py
```

第 ③ 步會列出實際可用 model ID；④ 真打 Converse 通過即代表合規成立。

---

## ~~已知待做~~ model 分流 — 已完成（2026-07-26）

對話與診斷路徑的延遲需求差 8 倍，已拆成兩顆 model：

| 路徑 | 逾時上界 | 預設 model | 專屬環境變數 |
|---|---|---|---|
| `cloud_llm`（對話回覆） | **1.5s**（斷網橋段 D-03 的驗收上界） | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | `BEDROCK_MODEL_ID_CHAT` |
| `diagnose`（教師診斷） | 12s（非同步） | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | `BEDROCK_MODEL_ID_DIAG` |

- 優先序：role 專屬變數 → 全域 `BEDROCK_MODEL_ID` → role 預設。
  **向後相容**：既有只設 `BEDROCK_MODEL_ID` 的部署兩條路徑仍沿用那一顆。
- 兩顆預設 ID 已對本帳號 `us-west-2` 實測存在於 `list_models()` 清單（非僅文件推斷）。
- `aws_preflight.py` 同步強化：①印出兩顆 model、③兩顆都比對開通清單、
  ④改用**對話**那顆真打並實測秒數對照 1.5s 預算（超過即列為 failure）。
- 若兩條路徑仍共用同一顆，preflight ① 會出警告——因為它的失敗症狀是
  「安靜降級回 edge」，現場最難察覺。

---

## 環境備註

- MCP：`aws-docs`（官方 awslabs 文件伺服器）與 `context7` 已裝入 user config，
  需重啟 session 才掛載。
- `scripts/verify_bedrock_live.py` 是死程式碼（一跑就 AttributeError），
  已在 `scripts/README.md` 標示，用 `scripts/aws_preflight.py` 取代。
