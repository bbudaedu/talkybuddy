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

## 🔴 唯一阻塞點：AWS 帳號配額為零

**根因（已用官方文件佐證）**：帳號在 **AWS Free plan**，而
[官方文件](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html)
明載 Free plan「don't include access to AWS services and features that could
possibly deplete your credits」—— Bedrock 正是這類服務，配額趨近於零。

**排除法已完整**（`get-foundation-model-availability` 實測）：

| 可能原因 | 結果 |
|---|---|
| 模型未開通 | ❌ 排除 — `authorizationStatus: AUTHORIZED` |
| use-case 表單未提交 | ❌ 排除 — 已在檔（Education / children's English tutor） |
| 授權協議未簽 | ❌ 排除 — `agreementAvailability: AVAILABLE` |
| region 不支援 | ❌ 排除 — `regionAvailability: AVAILABLE` |
| 配額 | ✅ **就是這個** — `ThrottlingException: Too many tokens per day` |

實測涵蓋 3 個 region × 8 個模型組合（含東京 `jp.` 前綴、剛開通的 Opus 4.5），
**全部一致 throttled** → 帳號層級限制，非模型或 region 問題。

### 待辦（使用者操作）

1. **升級 Paid plan** ← 最關鍵
   `https://console.aws.amazon.com/billing/home?#/freetier/upgrade`
   credit（$52.45）升級後仍可用，不會沒收；Free plan 6 個月後帳號會自動關閉。
2. **開免費 Support case**（Account and billing）要求 provision on-demand quota
   —— 社群回報部分帳號需人工介入，1–24 小時。
3. **刪除 root access key** `AKIAZKQ2XL7Q4R7TPTFJ`（root 金鑰無法被 IAM 限權，
   且本 repo 公開）。已從本機移除，但 AWS 端仍存在。
4. 加 `ServiceQuotasReadOnlyAccess` 給 `talkybuddy-admin`，才能讀到確切配額數字。

### 已建立的 AWS 資源

- IAM user `talkybuddy-admin`（`AmazonBedrockFullAccess`），憑證已寫入 `~/.aws`
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
