# 交接：pipecat edge 已完成，下一步接雲端

**日期**：2026-07-31　**分支**：`feat/pipecat-edge`（worktree `/home/budaedu/talkybuddy-pipecat`）
**狀態**：edge 全鏈路真人驗證通過；雲端未接
**使用者裁示**：「edge 這樣就可以了，重點是雲端，把雲端接通」

---

## 一、決賽路徑從頭到尾沒被動過

板子 `root@192.168.31.78`：

- `/root/talkybuddy/` **零修改**（含 `.venv`、`models`、`.env`）
- 所有新東西在 `/root/pipecat-lab/`（獨立 venv + `models` symlink 唯讀指過去），**刪掉即完全復原**
- 三個 service 狀態未被 stop/start/restart

備份：`~/backups/talkybuddy-20260731-022002/`（含 `RESTORE.md`，已實際還原驗證）

⚠️ **安全**：對話過程中 `/root/talkybuddy/.env` 的 `ANTHROPIC_API_KEY`、
`AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY` 曾明文外洩到 session 記錄。
使用者表示決賽會用官方資源、不用這組，但**開發用的仍建議輪替**。

---

## 二、edge 端已完成（真人驗證通過）

最後一次真人對話 5 輪全部辨識正確，中英文都對：

```
👂 聽成：你好，有聽到我說話嗎？
🗣 玩偶說：很好！你說得真清楚！跟我說一遍：I want an apple.
👂 聽成：I want an apple.          ← 英文也對
🗣 玩偶說：很好！你說得真準！跟我說一遍：I want an apple。
完成輪數 5 │ 自我打斷 0
```

使用者確認**英文有念出來**。

### 元件清單（全部真機驗證過）

| 元件 | 檔案 | 備註 |
|---|---|---|
| ALSA transport | `pipecat_adapters/alsa_transport.py` | 唯一必須自寫（pyaudio 裝不了）；含 keepalive |
| VAD | 官方 `SileroVADAnalyzer` | 1.90ms/窗，不必自寫 |
| STT | `pipecat_adapters/sensevoice_stt.py` | 繼承 `SegmentedSTTService`；147ms |
| TTS | `pipecat_adapters/edge_tts.py` | 即時率 0.25x；分段用 `scaffold.split_tts_segments` |
| 教材注入 | `pipecat_adapters/lesson_prompt.py` | 共用 `server.llm.build_user_prompt` |
| 安全閘門 | `pipecat_adapters/safety_gate.py` | 句子級，攔在 TTS 前 |
| 帶讀護欄 | `pipecat_adapters/readalong_guard.py` | 串流下只能補不能改 |
| 簡轉繁 | `pipecat_adapters/opencc_processor.py` | 委派 `guardrails.to_traditional` |
| 無狀態 context | `pipecat_adapters/stateless_context.py` | ctx-size 512 塞不下歷史 |
| 播放閘門 | `pipecat_adapters/playback_gate.py` | 重用 `live_client.PlaybackGate` |
| 降級策略 | `pipecat_adapters/failover.py` | 遲滯／冷卻，待接官方 `ServiceSwitcher` |

**測試 100+ 全綠。** probe 在 `edge/probes/probe_*.py`，
真人對話用 `edge/probes/run_live_conversation.sh`（抗斷線包裝，保證還回麥克風）。

### edge 已知未解（使用者裁定可接受）

1. **環境噪音誤觸** — 寶貝多米曾被辨識成「コび」等。`live_client` 有兩個現成解：
   `is_near_field()` 近場門檻、按鍵觸發。**pipecat 版兩個都沒接。**
2. **閘門死區 2.6s** — 玩偶講完後上行仍聾（2.0 緩衝 + 0.6 tail），
   話音剛落就講會被吃掉開頭。keepalive 已加但**未驗證降緩衝後的效果**。
3. `fallback_text` 未接 `scaffold.reply_text`；VAD params 未調。

---

## 三、雲端：下一個 session 的主線

### 已查證的現況

| 項目 | 狀態 |
|---|---|
| 板子 `.env` 的 `ANTHROPIC_BASE_URL` | `http://127.0.0.1:8317` ← **指向板子自己，沒人在聽** |
| Ubuntu-AI-Server `192.168.100.200:8317` | `cli-proxy-api` 活著（relay 可用） |
| 板子上的 `boto3` | ❌ **沒有安裝** ← AWS 路徑從未成功的底層原因 |
| `AWS_REGION` | ❌ 未設 |
| AWS 憑證有效性 | ✅ **有效**（見下） |

### 關鍵發現：Bedrock 憑證是通的

在 `/root/pipecat-lab/.venv`（已裝 boto3）用 `ap-southeast-1` 呼叫 Converse：

```
❌ ValidationException: The provided model identifier is invalid.
```

**這不是認證失敗，是 model ID 不對** —— 表示憑證有效、region 可連、網路通。
下一步只要找對 model ID（試 inference profile ARN 或 `bedrock list-foundation-models`）。

我試的是 `apac.anthropic.claude-sonnet-4-5-20250929-v1:0`。

### 建議的接法（兩條路，擇一或都做）

**A. relay（快，但依賴這台機器）**
`ANTHROPIC_BASE_URL` → `http://192.168.100.200:8317`。
pipecat 端直接用 `OpenAILLMService(base_url=...)` 換掉 llama-server 那顆。
⚠️ 實測 edge→server RTT 116ms、**鏈路 2026-07-31 斷過兩次**（半夜一次、白天一次）。
決賽現場要靠 tunnel。

**B. Bedrock（決賽方向）**
決賽用官方 AWS 資源，所以這條才是主線。要做的：
1. 找對 model ID（憑證已證實可用）
2. 板子上裝 `boto3`（決賽 venv 或 lab venv —— **改決賽 venv 需要使用者授權**）
3. 設 `AWS_REGION`
4. pipecat 端接：pipecat 有 `services/aws/`，或用 `server/bedrock_converse.py` 包成 `LLMService`

### 接雲端之後別忘了

- **`failover.py` 就是為這一刻寫的** —— 雲端不可達時切回本機 llama-server，
  麥克風所有權不轉移。接線方式寫在 `docs/PIPECAT_EDGE_DESIGN.md`
  （用官方 `ServiceSwitcher` 做路由，`FailoverPolicy` 決定何時切）
- **ctx 限制會消失** —— 雲端模型 context 遠大於 512，
  `StatelessContextProcessor` 可以拿掉，玩偶就能記得上一輪
- **round_total 的瓶頸也會變** —— 目前 LLM 佔 78%（3.9s），換雲端後
  瓶頸可能變成網路 RTT，`docs/PIPECAT_EDGE_DESIGN.md` 的數字表要重量

---

## 四、這個 session 反覆出現的一個教訓

**既有程式碼看起來簡陋，其實是踩過坑之後的正確解，我卻自己發明了更差的。**
至少發生五次：

| 我自己寫的 | 既有的正確解 | 後果 |
|---|---|---|
| CJK 啟發式判斷語言 | `scaffold.split_tts_segments` | **英文完全沒念出來** |
| 自寫 OpenCC processor | `guardrails.to_traditional` | 重複實作 |
| 自寫 sherpa VAD | 官方 `SileroVADAnalyzer` | 白寫，還踩到 sample_rate 陷阱 |
| 累積對話歷史（pipecat 預設） | `EdgeLLM.generate` 的無狀態設計 | **ctx 爆掉** |
| `AlwaysUserMuteStrategy` | `live_client.PlaybackGate` | 擋錯層，玩偶還是聽到自己 |

**下一個 session 接雲端前，先讀 `server/cloud_llm.py`、`server/bedrock_converse.py`、
`server/pipeline.py` 既有的雲端路徑怎麼寫的**，不要重新發明。

另一條通則：**單元測試綠不代表對**。STT 繼承錯基底、TTS 不出聲、aplay 取樣率
錯、英文沒念出來 —— 全都是測試全綠但真機／真人一跑就現形。
