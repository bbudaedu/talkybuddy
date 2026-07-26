# 說說學伴 — AWS 部署 Runbook（決賽用）

目標：**決賽當天拿到一個空的 AWS 帳號，30 分鐘內把雲端主線跑起來**，全程不改一行程式碼。

---

## 為什麼可以「無腦搬遷」

程式碼裡**沒有任何** account ID、ARN、region 或憑證是硬編的。所有 AWS 存取都走 boto3 標準憑證鏈：

```
環境變數  →  ~/.aws/credentials  →  EC2 / ECS 的 IAM Role
```

所以同一份 code、同一個 Docker image：

| 跑在哪 | 憑證從哪來 | 要改什麼 |
|---|---|---|
| 你的筆電 | `~/.aws/credentials` | 無 |
| 主辦方給的 AWS 帳號、你自己開的 EC2 | **IAM Instance Profile（金鑰不落地）** | 無 |
| 換 region / 換模型 | — | 兩個環境變數 |

---

## Part A：一次性設定（現在做，約 15 分鐘）

### A-1　開通 Bedrock 模型存取

**每個要用的 region 各做一次**。以 `ap-east-2`（台北）為主 —— 2026-07-26 實測它是本帳號唯一有 Bedrock on-demand 配額的 region，同時也離決賽現場最近（見 `STATUS.md`）。開通不用錢也不用開機器。

```
Bedrock console → 左側 Bedrock configurations → Model access
  → Modify model access → 勾選 Anthropic 全部 Claude
  → Submit use case details（用途填「教育類語音學習應用原型」）→ Submit
```

狀態變 **Access granted** 即可。較新的 Claude 模型多半對所有 Bedrock 客戶開放、免審核。

> 先在 console 的 **Chat playground** 打一句話確認模型真能跑，再回來接程式。

### A-2　建立權限政策

IAM console → Policies → Create policy → JSON 分頁，貼入本目錄的 [`bedrock-policy.json`](bedrock-policy.json)，命名 `TalkyBuddyBedrockInvoke`。

這份政策是最小權限：只有 Converse／Invoke／列模型，沒有任何寫入或管理權。

### A-3　本機憑證（開發用）

IAM → Users → Create user `talkybuddy-dev` → 掛上 `TalkyBuddyBedrockInvoke` → Security credentials → Create access key（選 *Application running outside AWS*）。

```bash
aws configure     # 貼 key / secret，region 填 ap-east-2
```

> ⚠️ 長期 access key 只用於本機開發。**正式部署走 IAM Role，容器內不放金鑰**（Part B）。決賽結束後刪掉這組 key。

### A-4　驗證（關鍵一步）

```bash
cd ~/talkybuddy
.venv/bin/python scripts/aws_preflight.py
```

這支腳本會依序檢查：provider 開關 → 憑證 → 模型開通 → **真打一次 Converse** → 端到端產出診斷，任何一步失敗都會直接告訴你修法。

第 ③ 步會列出**你帳號實際可用的 model ID**。挑一個設進環境變數（程式內建的預設值是推斷的，以這份清單為準）：

```bash
export BEDROCK_MODEL_ID=<清單裡挑的那個>
export TALKYBUDDY_CLOUD_PROVIDER=bedrock
```

對話與診斷兩條路徑的延遲需求差 8 倍（1.5s vs 12s），可分流成兩顆模型；
只設上面那個共用變數也能跑，但對話路徑會冒著逾時降級回 edge 的風險：

```bash
export BEDROCK_MODEL_ID_CHAT=<清單裡的 haiku>    # 對話回覆，要快
export BEDROCK_MODEL_ID_DIAG=<清單裡的 sonnet>   # 教師診斷，要準
```

看到 `✔ 全數通過` 就代表雲端主線合規成立。

---

## Part B：決賽當天部署到 AWS（約 20 分鐘，多半在等 build）

### B-1　建 IAM Role 給 EC2（**這步讓金鑰不落地**）

```
IAM → Roles → Create role
  → Trusted entity: AWS service → EC2
  → 掛上 A-2 建的 TalkyBuddyBedrockInvoke
  → 命名 TalkyBuddyEC2Role
```

### B-2　開 EC2

```
EC2 → Launch instance
  AMI          : Amazon Linux 2023
  Instance type: t3.large（2 vCPU / 8GB；ASR 吃記憶體，t3.micro 會 OOM）
  Key pair     : 建一組，下載 .pem
  Storage      : 30 GB gp3（image 含模型約 2GB，留 build 空間）
  進階詳細資訊 → IAM 執行個體設定檔 → 選 TalkyBuddyEC2Role   ← 別漏
  進階詳細資訊 → 使用者資料 → 貼入 user-data.sh（先改開頭三個變數）
  安全群組     : 見下方
```

安全群組規則：

| 類型 | 連接埠 | 來源 | 用途 |
|---|---|---|---|
| SSH | 22 | **My IP** | 管理 |
| 自訂 TCP | 8000 | My IP | 直連測試 |

> **不要開 0.0.0.0/0**。demo 時瀏覽器怎麼連見 B-4。

### B-3　等開機腳本跑完

[`user-data.sh`](user-data.sh) 會自動：裝 Docker → clone repo → build image（含下載模型，約 5–10 分鐘）→ 起容器。

```bash
ssh -i your-key.pem ec2-user@<公有IP>
sudo tail -f /var/log/talkybuddy-bootstrap.log
```

看到 `/api/status` 回應就成功了。在機器上再跑一次 preflight 確認 IAM Role 生效：

```bash
docker exec talkybuddy python scripts/aws_preflight.py
```

第 ② 步應該顯示 `✔ 走 IAM Role（金鑰不落地）`。

### B-4　瀏覽器連上去 —— 麥克風的坑

**瀏覽器只在 HTTPS 或 localhost 下允許用麥克風。** 直接開 `http://<公有IP>:8000` 會發現錄不到音。

兩個解法：

**① SSH 埠轉發（推薦，決賽現場最穩，零額外設定）**

```bash
ssh -i your-key.pem -L 8000:localhost:8000 ec2-user@<公有IP>
```

然後在筆電開 **`http://localhost:8000`** —— 瀏覽器把 localhost 當安全來源，麥克風正常可用。安全群組也不必對外開 8000。

**② 網域 + Caddy 自動 TLS**（若你有網域）

```bash
sudo dnf install -y caddy
echo 'your.domain { reverse_proxy 127.0.0.1:8000 }' | sudo tee /etc/caddy/Caddyfile
sudo systemctl enable --now caddy
```

安全群組需開 80/443。Caddy 會自動處理 Let's Encrypt 憑證與 WebSocket 升級。

---

## 環境變數一覽

| 變數 | 必要 | 說明 |
|---|---|---|
| `TALKYBUDDY_CLOUD_PROVIDER` | ✅ | 設 `bedrock` 才啟用原生 Converse；未設則走既有 Anthropic relay |
| `BEDROCK_REGION` | ✅ | 已開通模型的 region。**與 Nova Sonic 共用同一變數** |
| `BEDROCK_MODEL_ID` | 建議 | 兩條路徑的共用預設；以 preflight 第③步查到的值為準 |
| `BEDROCK_MODEL_ID_CHAT` | 選用 | **對話回覆**專用（逾時上界 1.5s，該用 Haiku 這類快模型）。未設時預設 `global.anthropic.claude-haiku-4-5-20251001-v1:0`；若有設 `BEDROCK_MODEL_ID` 則沿用它 |
| `BEDROCK_MODEL_ID_DIAG` | 選用 | **教師診斷**專用（非同步，上界 12s，可用 Sonnet/Opus）。未設時預設 `global.anthropic.claude-sonnet-5` |
| `TALKYBUDDY_PIPELINE_PROFILE` | ✅ | 雲端設 `cloud` |
| `TALKYBUDDY_JWT_SECRET` | ✅ | 登入 JWT 密鑰，user-data 會自動產生 |
| `TALKYBUDDY_CONSENT_GRANTED` | ✅ | 家長同意閘門；`false` 會**強制切斷所有雲端呼叫** |
| `ELEVENLABS_API_KEY` | 選用 | 情感 TTS；未設會降級到容器內的 piper 語音 |
| `AWS_*` 金鑰 | ❌ | **在 EC2 上不要設**。設了會蓋掉 IAM Role，失去自動輪換 |

---

## 降級鏈（現場保命線）

```
教師診斷：Bedrock Converse ──失敗──▶ Anthropic relay ──失敗──▶ 規則式 mock
語音合成：ElevenLabs ──失敗──▶ 容器內 piper（zh/en）
```

任何一層掛掉 demo 都還跑得動，只是品質降級。preflight 的第⑤步會明確告訴你當下走的是哪條路。

---

## 容器裡有什麼、沒有什麼

| 項目 | 在容器內 | 理由 |
|---|---|---|
| SenseVoice ASR（457MB） | ✅ | 雲端主線的輸入路徑仍在 server 端 |
| piper zh/en 語音（122MB） | ✅ | ElevenLabs 掛掉時的保命線 |
| **qwen2.5-1.5B GGUF（1.1GB）** | ❌ | 雲端腦走 Bedrock，edge LLM 用不到 |
| **pipecat + torch（~800MB）** | ❌ | 只有 A2 全雙工 real-wiring 用；`/ws/talk` 主線實測不 import |
| AWS 金鑰 | ❌ | 走 IAM Role |

image 約 2GB。要跑 edge 離線路徑（Genio 520 斷網橋段）請用 `docs/DEPLOY_EDGE.md`，那條路徑不共用這個容器。

---

## 已知待處理

- `scripts/verify_bedrock_live.py` 是 **2026-07-08 廢棄計畫的殘留死程式碼**，引用了從未實作的 `config.LLM_CLOUD_PROVIDER` / `COMPANION_MODEL_ID` / `TUTOR_MODEL_ID`，一跑就 `AttributeError`。請用 `scripts/aws_preflight.py` 取代，該檔待刪。
- `scripts/setup_env.sh` 開頭硬編了 `/home/budaedu/hackathon/talkybuddy` 絕對路徑，換機器會失效（容器路徑不受影響）。
