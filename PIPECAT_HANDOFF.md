# 交接：pipecat edge 完成、雲端接線完成，卡在憑證

**日期**：2026-07-31（雲端章節同日稍晚更新）
**分支**：`feat/pipecat-edge`（worktree `/home/budaedu/talkybuddy-pipecat`）
**狀態**：edge 全鏈路真人驗證通過；**雲端接線完成並以真網路驗證過，但 Bedrock 本身接不上**
**使用者裁示**：「edge 這樣就可以了，重點是雲端，把雲端接通」→
「AWS 帳號沒救 只能決賽用官方資源」

**下一個 session 最短路徑**：直接讀第三節末的〈決賽現場接通程序〉。
程式碼那邊沒有待辦了，缺的是可用的雲端憑證。

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

## 三、雲端：接線已完成，卡在憑證（2026-07-31 更新）

> ⚠️ **本節原本寫的「憑證有效，只差找對 model ID」是錯的**，已重寫。
> model ID 那題 `server/bedrock_converse.py` 與 `deploy/aws/STATUS.md` 早就解過；
> 換上正確的 region + model 之後，真正的阻塞才浮出來。保留這句是因為它正是
> 第四節那個教訓的又一次重演。

### 三條雲端路，全部實測過，卡點都不在程式碼

| 路徑 | 實測結果 | 阻塞點 |
|---|---|---|
| **Bedrock**（決賽主線） | `AccessDeniedException: Your account is currently being verified` | AWS 帳號驗證卡死（自 07-26 起 5 天，遠超「2 小時」）。**使用者裁定：此帳號放棄，決賽只用官方資源** |
| **relay** `192.168.100.200:8317` | 通，但接 **Claude 系列**時 system prompt 被上游 Claude Code 蓋掉 —— 玩偶回「我是 Claude Code，Anthropic 的官方 CLI 工具」並拒絕教英文 | 那個中轉的性質，不是我們的 bug |
| **Anthropic 官方端點** | HTTP 401 `invalid x-api-key` | 板子那把 key 是中轉用的，非官方 key |

補三個查證到的事實：

- `ap-east-2` 的 11 個 anthropic inference profile 都在，`bedrock_converse.py`
  寫死的兩顆預設（`global.anthropic.claude-sonnet-5`、
  `global.anthropic.claude-haiku-4-5-20251001-v1:0`）**確實存在**。region 與
  前綴都是對的，問題純粹在帳號。
- 開發機 `~/.aws` 與板子 `.env` 是**同一組 access key**（`AKIAZKQ2…`），
  所以在板子上裝 boto3 也會撞同一堵牆 —— **不要為此去動決賽 venv**。
- **板子對外網路是通的**（`api.anthropic.com` HTTP 405、0.199s）。
  也就是說憑證一對，板子直連雲端**不需要 tunnel** —— 上一版說「決賽現場要靠
  tunnel」只對 relay 那條路成立。

### 可用的替身：中轉的非 Anthropic 模型

要在沒有 Bedrock 的情況下驗證接線，用中轉的 **Gemini／GPT** 上游，它們不會被
注入 Claude Code 的人格：

| 模型 | 延遲（開發機→中轉） |
|---|---|
| `gemini-3.1-flash-lite` | **2.3–3.0s**（目前最快，建議用這顆驗證） |
| `Gemini 3.5 Flash` | 2.6s ~ 11.8s（同一天內差 4 倍，中轉本身會慢） |
| `gpt-5.4-mini` | 7.1s |

⚠️ **這些數字不能拿來預測 Bedrock**。中轉的吞吐會隨時間大幅變動（同一顆模型、
同一個 prompt，稍早 2.6s、稍後 11.8s），它只是驗證接線的替身。

### pipecat 端已完成（測試綠，真網路驗證過）

| 元件 | 檔案 |
|---|---|
| 雲端 LLM service | `pipecat_adapters/cloud_llm_service.py` |
| 真網路驗證探針 | `edge/probes/probe_cloud_llm_service.py` |
| 真人對話接上雲端 | `probe_live_conversation.py`（`TALKYBUDDY_PIPECAT_CLOUD=1` opt-in） |
| 已組好的 prompt 進入點 | `CloudLLM.generate_from_prompt` / `EdgeLLM.generate_from_prompt` |
| 上雲前去識別化 | `LessonPromptInjector(deidentify=True)` |
| 降級後升得回來 | `FailoverPolicy.should_try_primary()` |

`tests/` 1404 全綠（新增 22 條）。
`server/streaming/tests/` 的 11 紅 + 3 error 是**開發機缺 sherpa 模型檔**，
在乾淨的樹上一模一樣，與這些改動無關。

**兩個偏離既有文件的設計決定**（理由都寫在原始碼與 `docs/PIPECAT_EDGE_DESIGN.md`）：

1. **降級是「當輪」不是「下一輪」**。原文件說用官方 `ServiceSwitcher`，但它是
   收到 `ErrorFrame` 後換掉 service，**觸發切換的那一輪沒有回覆** —— 孩子聽到
   沉默，而沉默的症狀跟玩偶壞掉一模一樣。改成兩層：`CloudLLMService` 當輪降級
   （不掉輪），`FailoverPolicy` 決定接下來幾輪還要不要試雲端。
2. **包既有 `CloudLLM` 而不是用 pipecat 原生 `AWSBedrockLLMService`**。前者帶著
   去識別化、`verified()` 證據追蹤、Bedrock→relay 兩層降級，且與 edge 路徑逐字
   共用同一份護欄 helper。代價是非串流（完整回覆一次推下去）。

### 決賽現場接通程序（因為 Bedrock 事前驗不了）

拿到官方 AWS 憑證之後，照這個順序做，每一步都會產生可以拿出來的證據：

```bash
# 1. 列出「這個帳號」實際可用的 model ID —— 各帳號不同，寫死的字串很容易過期
PYTHONPATH=. python -m server.bedrock_converse

# 2. 不佔麥克風、失敗成本最低的一跑
PYTHONPATH=. TALKYBUDDY_CLOUD_PROVIDER=bedrock BEDROCK_REGION=<region> \
  python edge/probes/probe_cloud_llm_service.py

# 3. 上一步綠了再跑真人對話（會佔麥克風）
TALKYBUDDY_PIPECAT_CLOUD=1 TALKYBUDDY_CLOUD_PROVIDER=bedrock BEDROCK_REGION=<region> \
  PYTHONPATH=/root/pipecat-lab ./.venv/bin/python probe_live_conversation.py
```

**看 `雲端實際走的` 那一行**：它是 `verified_backend()`，只有真的成功過才不是
`none`。設定讀數會騙人，這個不會。

板子上還要做的兩件事（**動決賽 venv 需要使用者授權**）：
`pip install boto3`、設 `BEDROCK_REGION`。

### 接雲端之後別忘了

- **`CLOUD_LLM_TIMEOUT_S` 預設 1.5s**。每輪延遲超過它就會**每輪都降級回 edge**，
  雲端等於白接。探針會在超過時直接印警告。
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
| 自己試 `ap-southeast-1` + `apac.` 前綴 | `bedrock_converse.py:28-46` 與 `deploy/aws/STATUS.md` 早就查證過：`ap-east-2` 只提供 `global.` 前綴 | 拿到誤導性的 `ValidationException`，於是**把「帳號被鎖」誤判成「model ID 沒找對」**，還寫進交接文件，害下一個 session 從錯的前提出發 |

**下一個 session 接雲端前，先讀 `server/cloud_llm.py`、`server/bedrock_converse.py`、
`server/pipeline.py` 既有的雲端路徑怎麼寫的**，不要重新發明。

另一條通則：**單元測試綠不代表對**。STT 繼承錯基底、TTS 不出聲、aplay 取樣率
錯、英文沒念出來 —— 全都是測試全綠但真機／真人一跑就現形。
