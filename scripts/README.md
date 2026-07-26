# scripts/ — 雲端大腦實機驗證腳本

本目錄的 `verify_*.py` 是**實機 smoke test**：真打雲端 API，確認 `feat/cloud-llm-bedrock`
的接線在真環境可用（非只有單元測試的 mock）。**不進 CI、demo 前手動重跑即可。**

> ⚠️ **憑證**：所有腳本一律由呼叫端 env 提供金鑰，**腳本本身不含任何明文憑證**。
> 請用當下自行生成的短期金鑰，**跑完立即撤銷**（見文末安全須知）。

---

## 一、三支腳本速覽

| 腳本 | 驗什麼 | 後端 | 認證 |
|------|--------|------|------|
| `aws_preflight.py` | **決賽現場用這支**：provider→憑證→模型開通→真打 Converse→端到端診斷 | Bedrock Converse | boto3 credential chain（含 EC2 IAM Role） |
| ~~`verify_bedrock_live.py`~~ | ⛔ **已失效，勿用** | — | — |
| `verify_nova_sonic_live.py` | Nova (2) Sonic 端到端雙向串流 S2S（ASR＋文字回覆＋語音輸出） | Bedrock bidi stream | **僅 SigV4**（不吃 bearer token） |
| `verify_via_proxy.py` | 同一套 prompt／前後處理邏輯在真 LLM 上的**語義正確性** | Anthropic 相容中轉 | 中轉 token |

三者互補：Bedrock 配額用盡時，用 proxy 驗語義；Nova Sonic 驗全雙工 S2S 亮點路徑。

---

## 二、跑法

先進 `talkybuddy/`，用共用 `.venv`。憑證用 `set -a; source <env 檔>; set +a` 匯入（env 檔請設 `600` 權限、勿進版控）。

### 1. `aws_preflight.py` — Bedrock 端到端就緒檢查（決賽現場用這支）
```bash
cd talkybuddy
.venv/bin/python scripts/aws_preflight.py

# 指定 region（未設則吃 BEDROCK_REGION → AWS_REGION → 內建預設）
BEDROCK_REGION=ap-northeast-1 .venv/bin/python scripts/aws_preflight.py
```

依序檢查 provider 開關 → 憑證（並指出是走 IAM Role 還是長期金鑰）→ 模型開通
狀態（**列出你帳號實際可用的 model ID**）→ 真打一次 Converse → 端到端產出診斷。
每一步失敗都會附上修法。憑證走 boto3 標準鏈，在 EC2 上自動吃 IAM Instance Profile。

> ⛔ **`verify_bedrock_live.py` 已失效，請勿使用。** 它是 2026-07-08 廢棄計畫的
> 殘留，引用了從未實作的 `config.LLM_CLOUD_PROVIDER` / `COMPANION_MODEL_ID` /
> `TUTOR_MODEL_ID`，執行即 `AttributeError`（2026-07-26 實測）。功能已由
> `aws_preflight.py` 取代，該檔待刪。

### 2. `verify_nova_sonic_live.py` — Nova Sonic S2S
```bash
cd talkybuddy
AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... BEDROCK_REGION=us-east-1 \
  .venv/bin/python scripts/verify_nova_sonic_live.py [音檔.wav]
```
可調 env：`NOVA_MODEL_ID`（預設 `amazon.nova-2-sonic-v1:0`）、`NOVA_VOICE`（`tiffany`）、
`NOVA_SYSTEM`（預設要求繁中回覆）、`NOVA_WAV`（餵中文語音檔即可驗中文 ASR）。

### 3. `verify_via_proxy.py` — 中轉驗語義
```bash
cd talkybuddy
ANTHROPIC_BASE_URL=http://<host>:<port> ANTHROPIC_AUTH_TOKEN=... \
  .venv/bin/python scripts/verify_via_proxy.py
```

---

## 三、關鍵已知事項（踩雷紀錄）

- **依賴**：Nova Sonic 走 `InvokeModelWithBidirectionalStream`，標準 boto3 **沒有**此 API →
  需 `aws-sdk-bedrock-runtime`（含 awscrt/smithy 系，已裝進共用 `.venv`）。
- **SigV4 identity resolver 必顯式設定**：這版 SDK 只塞 access_key/secret 到 `Config` 會
  `SmithyIdentityError`（假性 hang）→ 必須 `aws_credentials_identity_resolver=EnvironmentCredentialsResolver()`。
- **Nova Sonic 協定順序**：音訊 `contentEnd` 後**別立刻送 `promptEnd`**——太早送會抑制助理 turn（回覆全空）。
  正解＝送完 audio `contentEnd` 就等模型自動回覆，收到 `ASSISTANT` + `audioOutput` 後才 `promptEnd`+`sessionEnd`；
  並補 ~0.8s 尾端靜音幫 VAD 判 end-of-speech。Nova Sonic 分多個 completion（先 user-ASR、後 assistant），別在第一個 `completionEnd` 就收。
- **中轉不適合驗陪聊**：Claude Code router 會注入身分蓋過 system prompt，模型拒演角色扮演 →
  proxy 只適合驗導師（分析類），陪聊語義請走 Bedrock 或 Nova Sonic。
- **Bedrock 配額**：試用帳號有**帳號級跨模型 daily token 配額**，耗盡時各模型皆 `ThrottlingException`，
  換模型／換 key（同帳號）無效，需等 reset 或提額。

---

## 四、安全須知

- 一律使用**當下新生成的短期金鑰**，跑完立即撤銷：
  - Bedrock bearer token：帳號可隨時重生。
  - Nova Sonic 的 SigV4 用 IAM user access key → 測完到 **IAM → Deactivate/Delete**。
- 憑證只放 `600` 權限的 env 檔（如 `/tmp/tb_*_env`），**勿寫進腳本、勿 commit**。
- `.gitignore` 已排除 `.env*` / `*.key` / `*.pem`。
