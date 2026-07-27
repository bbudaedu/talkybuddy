# 雲端路徑實跑驗證（走 Anthropic 相容中轉，不碰 AWS）

**目的**：把「雲端路徑至今零實證」這個最大未知數砍掉。
AWS 帳號被鎖住的是 **Bedrock 那一條**，`server/anthropic_relay.py` 這條從來沒被擋，
只是沒有憑證所以一直沒走過。

**金鑰只讀環境變數，絕不寫進 repo、絕不 commit。**

---

## 這條路驗得到什麼、驗不到什麼

專案有兩條語音路徑，中轉只覆蓋其中一條：

| | `/ws/talk`（主線） | `/ws/live`（S2S） |
|---|---|---|
| 語音辨識 | 邊緣 SenseVoice | Nova Sonic 直接吃音訊 |
| 大腦 | `cloud_llm` → Bedrock **或中轉** | Nova Sonic |
| 合成 | 邊緣 sherpa / 雲端 TTS | Nova Sonic 直接吐音訊 |
| 中轉救得到嗎 | ✅ | ❌ |

`/ws/live` 走 `nova_sonic.py` 的 `InvokeModelWithBidirectionalStream`，那是
**Bedrock 專屬協定**，Anthropic Messages API 沒有對應端點，中轉轉不出來。
`app.py` 的 gate 是 `LIVE_S2S_ENABLED AND nova_sonic.available()`，
沒有 AWS 憑證會直接回 `live_error: unavailable`。

| ✅ 驗得到 | ❌ 驗不到 |
|---|---|
| 對話大腦的雲端路徑端到端通 | Nova Sonic S2S |
| 教師診斷的雲端路徑端到端通 | AgentCore Harness / Memory |
| 護欄與去識別化在**真回應**上有效 | Bedrock 專屬的 model 分流與配額 |
| 降級鏈在真實失敗下會動 | |
| 斷網時零出境是真的 | |

---

## 環境變數

```bash
export ANTHROPIC_BASE_URL=https://<你的中轉>      # root / /v1 / 完整端點都吃
export ANTHROPIC_AUTH_TOKEN=<你的 token>          # → Authorization: Bearer
export ANTHROPIC_DEFAULT_OPUS_MODEL=claude-sonnet-5
```

三點說明：

- `messages_url()` 會自動正規化：給 root 補 `/v1/messages`，給 `/v1` 補 `/messages`，
  已經是完整端點就原樣用
- `anthropic_relay.DEFAULT_MODEL` **本來就是 `claude-sonnet-5`**，中轉若吃這個名稱，
  第三個變數可以不設
- 用官方金鑰的話改設 `ANTHROPIC_API_KEY`（→ `x-api-key`）。
  兩個都設時 `ANTHROPIC_AUTH_TOKEN` 優先，與官方 SDK 一致

中轉必須滿足：

1. **Anthropic Messages API 格式** — 送出 `{model, max_tokens, system, messages}`，
   回應解析 `content[].type == "text"`
2. 接受 `anthropic-version: 2023-06-01` 標頭
3. 延遲（見下面第 2 步）

---

## 第 1 步：確認中轉連得通（放寬逾時）

⚠️ `verify_cloud_llm_live.py` 走的是 `CloudLLM.generate()`，
**同樣受 `CLOUD_LLM_TIMEOUT_S`（預設 1.5 秒）限制**。
第一次跑先放寬，把「連不通」和「太慢」分開，不然兩種病同一個症狀。

```bash
cd ~/talkybuddy
CLOUD_LLM_TIMEOUT_S=15 .venv/bin/python scripts/verify_cloud_llm_live.py
```

預期輸出（憑證會自動遮蔽成 `abcd…wxyz（共 N 碼）`）：

```
=== CloudLLM 實機驗證 ===

解析設定（anthropic_relay.resolve_config）：
  端點 url  : https://<你的中轉>/v1/messages
  model     : claude-sonnet-5
  認證方式  : Bearer (AUTH_TOKEN)
  憑證      : sk-a…wxyz（共 108 碼）
  版本標頭  : 2023-06-01

available(): True

發真請求 → 學生說：「我喜歡蘋果」／目標句：I like apples.

✓ 連通成功（耗時 1234ms）
────────────────────────────────────────────────
你很棒，說得很清楚！跟我說一遍：I like apples.
────────────────────────────────────────────────

✓ 回覆含目標英文句「I like apples.」
```

離開碼 0 = 連得通 + 回應解析正確 + **通過兒童安全護欄**。

### 失敗判讀

| 症狀 | 意義 |
|---|---|
| `✗ 無憑證` | 環境變數沒吃到（注意 `export`，以及不要在別的 shell 跑） |
| `available(): False` | 同上 |
| `✗ generate() 回 None` | 連線失敗／逾時／回應格式不符／輸出被護欄擋掉 |
| 連通但 `⚠ 回覆未含目標句` | 中轉的 model 沒照 system prompt 走，品質問題不是接線問題 |

`generate()` 吞例外只記 log，所以 `None` 分不出是哪一種。要細分就直接打中轉：

```bash
curl -sS -X POST "$ANTHROPIC_BASE_URL/v1/messages" \
  -H "Authorization: Bearer $ANTHROPIC_AUTH_TOKEN" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"claude-sonnet-5","max_tokens":64,
       "messages":[{"role":"user","content":"說一句話"}]}' | head -40
```

---

## 第 2 步：量延遲，決定要不要調上界

連得通之後，用**預設的 1.5 秒**再跑一次：

```bash
.venv/bin/python scripts/verify_cloud_llm_live.py
```

`_TIMEOUT_S` 預設 1.5 秒，是為了滿足斷網橋段「恢復 <1–2 秒」的驗收門檻。
中轉多一跳，很可能來不及。

**這件事的失敗症狀最危險**：不會報錯，會**安靜降級回 edge**——畫面照跑、
有回覆、但那是邊緣 Qwen 生的，不是 Sonnet 5。你會以為雲端在跑，其實沒有。

| 第 1 步耗時 | 判讀 | 動作 |
|---|---|---|
| < 1200ms | 有餘裕 | 用預設值 |
| 1200–1500ms | 邊緣，現場網路一抖就降級 | 考慮 `CLOUD_LLM_TIMEOUT_S=2.5` |
| > 1500ms | 預設值下**必定**降級 | `export CLOUD_LLM_TIMEOUT_S=4`，並接受恢復時間變長 |

程式碼註解已經留好這個出口（`server/cloud_llm.py`），改的是環境變數不是原始碼。

診斷路徑（`diagnose.py`）是非同步的，逾時 `_API_TIMEOUT_SEC = 12` 秒，
中轉延遲對它基本無影響。

---

## 第 3 步：起 server 走完整回合

```bash
ANTHROPIC_BASE_URL=... ANTHROPIC_AUTH_TOKEN=... \
CLOUD_LLM_TIMEOUT_S=<第 2 步決定的值> \
  .venv/bin/python -m uvicorn server.app:app --host 0.0.0.0 --port 8000
```

### 3-1 確認後端身分

```bash
curl -s localhost:8000/api/status | python3 -m json.tool
```

`cloud_provider` 應為 **`"relay"`**（`_cloud_provider_name()` 的優先序是
Bedrock → relay → none；沒設 Bedrock 就會落在 relay）。

若是 `"none"` → server 沒吃到環境變數，不要再往下測。

### 3-2 實際講一句話

用瀏覽器開 `http://<host>:8000/`，講一句中文（例如「我喜歡蘋果」）。

要看的三件事：

1. 回覆內容是不是「一句中文稱讚 + 跟我說一遍：<英文句>」的格式
2. server log 有沒有降級的痕跡（`CloudLLM` 逾時會記 log）
3. 回合延遲

### 3-3 確認診斷也走雲端

切到 cloud 模式後會產生新診斷：

```bash
curl -s "localhost:8000/api/diagnoses" | python3 -m json.tool | tail -40
```

診斷若走雲端會有 `companion_directive` 等 LLM 生成欄位；
規則式產出的內容明顯是模板。

### 3-4 教師儀表板

`http://<host>:8000/teacher`（demo 帳號 `tutor@demo` / `demo1234`）——
雷達圖、14 天趨勢、最新診斷、**Agent 產出**、互動紀錄。

「Agent 產出」那一區是現場的證據區：派作業與週報各有一個 **`source` badge**，
`cloud`（藍）代表雲端 agent 產出、`rule`（黃）代表離線規則式。
**那是唯一能當場證明雲端真的在跑的欄位**，斷網時會當著評審的面從藍變黃。

要看原始資料的話：

```bash
curl -s "localhost:8000/api/agent_outputs" \
  -H "Authorization: Bearer <token>" | python3 -m json.tool
```

---

## 第 4 步：斷網降級演練（決賽橋段）

這是決賽當天真的會做的動作，先在桌機演一遍。

```bash
# 切到 edge 模式（不必真的拔網路）
curl -s -X POST localhost:8000/api/network_mode \
  -H 'content-type: application/json' -d '{"mode":"edge"}'
```

預期：

- 對話照常，但回覆改由邊緣 Qwen 生成
- 新診斷的 `source` 是 `rule`
- agent 產出的 `source` 全部是 `rule`
- **零出境**：`allow_cloud=False` 一路傳到三個 agent（有測試守著）

再切回 `cloud` 確認會回復。真機彩排時改成**真的拔乙太網路線**，
因為那才會驗到 DNS/連線逾時這條路徑。

---

## 隱私上要能講清楚的一點

自架中轉是資料路徑上的第三方——孩子講的話（已經過 `guardrails.deidentify`
去識別化）會經過它。B4 的家長同意閘門與去識別化都還在，
但**中轉營運方看得到內容**。

決賽若被問隱私架構，這一層要主動講，不要被問出來。
對照組是斷網橋段：那時候連文字都不出境。

---

## 跑完之後

把結果記進 `docs/NEXT_STEPS.md` 的現況表：
「雲端實際產出」那一列從 ❌ 改成「✅ relay 路徑已實證（AgentCore 仍未驗）」，
並補上量到的延遲數字。那是決賽現場唯一能拿出來的實證。
