# 交接：pipecat edge 完成、雲端接線完成，卡在憑證

**日期**：2026-07-31（雲端章節同日稍晚更新）
**分支**：`feat/pipecat-edge`（worktree `/home/budaedu/talkybuddy-pipecat`）
**狀態**：edge 全鏈路真人驗證通過；**雲端接線完成並以真網路驗證過，但 Bedrock 本身接不上**
**使用者裁示**：「edge 這樣就可以了，重點是雲端，把雲端接通」→
「AWS 帳號沒救 只能決賽用官方資源」

**下一個 session 最短路徑**：直接讀第三節末的〈決賽現場接通程序〉。
程式碼那邊沒有待辦了，缺的是可用的雲端憑證。

**2026-07-31 深夜追加**：對話品質（生動有趣 + 記得孩子）已完成，見第三之二節。
開發期間的雲端替身是 **Gemini 直連**（`GEMINI_API_KEY`），板子上
`/root/pipecat-lab/.env` 已放好。決賽改 Bedrock 只需換環境變數。

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

## 三之二、對話品質（2026-07-31 深夜追加）

雲端接通之後真人實測，玩偶**四輪回覆幾乎一模一樣**，孩子問「可以跟我練習說
英文嗎？」它還是回「跟我說一遍：I want an apple.」。追下去發現三個原因疊在
一起，而**三個的解法專案裡全都已經有了**，只是 pipecat 一個都沒接。

### 換掉的東西

| 層 | 從 | 到 |
|---|---|---|
| system prompt | `EdgeLLM._SYSTEM_PROMPT`（60 字硬帶讀） | `scaffold.build_live_system_prompt`（教練企鵝） |
| 對話歷史 | 只送一則訊息 | 送完整歷史（`CloudLLM.generate_chat`） |
| 帶讀強制 | 每輪事後硬補 | 雲端不套（edge 保留） |
| 教材 | 寫死一句 | `lesson.build_lesson` + `topic_sentences` |
| 換句子 | 交給模型判斷（實測第 7 輪才換） | `LessonProgress` 狀態機（唸對 1 次就換） |
| 記憶 | 無 | `child_brief` 開場注入 + `TurnRecorder` 收場落地 |

**回合式契約完全沒動**——edge 降級那顆仍吃舊的，斷網時風格明顯不同是刻意的。

### 量得出來的結果（`probe_simulated_conversation.py`，板子實跑）

| 指標 | 之前 | 之後 |
|---|---|---|
| 開場白變化 | 4 輪幾乎一樣 | 8/8 都不同 |
| 孩子提問被回應 | 0（直接無視） | 5/5 |
| 回覆長度 | 76 字 ≈ 17 秒靜音 | 38 字 ≈ 8 秒 |
| 一場練過幾句 | 1 | 4 |

`probe_simulated_conversation.py` 不用麥克風（`TranscriptionFrame` 直接餵進
pipeline，走與真人完全相同的那條路），另一顆 LLM 扮小孩。**小孩在旁邊吵、
沒有安靜環境時就用它。**

### 記憶迴圈

```
對話 → TurnRecorder 落地 interactions
     → profile.build_profile 算出興趣/正在學的字/情緒
     → child_brief 濃縮成一段話
     → 下一場開場注入 system prompt（一次，不佔每輪 1.5s 預算）
```

板子上已經有真實資料（8 次互動）。開場記憶長這樣：

> 【你對這個孩子的記憶】你以前跟這個孩子聊過 8 次了…他平常喜歡聊動物、動作…
> 他最近在學的字有 dog、rabbit。他已經很熟的字有 cat，可以拿來稱讚他。

### ⚠️ 一個沒修的既有 bug

`server/app.py::_store_live_turn` 寫的欄位名**三個全錯**：

| 它寫的 | 讀取端要的 | 誰在讀 |
|---|---|---|
| `asr_text` | `student_text` | `profile.build_profile:112` |
| `reply_text` | `ai_response_text` | 同上 `:113` |
| `asr_conf` | `asr_confidence` | 同上 `:114`、`srs`、`diagnose` |

不會報錯、測試綠，只是 `/ws/live`（Nova Sonic）那條路徑產生的互動**完全不進
畫像**。pipecat 這條已修並用測試釘住（`test_pipecat_turn_recorder.py`），
app.py 那邊決賽前不動，但**不要讓它繼續躺著**。

### 待決定的一行

`child_brief` 目前叫玩偶「不要一口氣講出來，只在自然的時候提一兩件」，所以它
**不一定**會在鏡頭裡說出「上次我們練過…」。要讓那句話必定出現，把指示改成
「第一句就自然提到一件你記得的事」即可——一行的事，但玩偶會顯得比較刻意。
**這是取捨，不是 bug。**

---

## 三之三、下一個 session 從這裡開工（2026-08-01 決賽日）

**使用者裁定**：「決賽兩條線都測、選優的上」，並且想把 pipecat 固化成主線、
local-client 退成備援。

### 已經可以切換了

```bash
# 板子上（unit 檔已推到 /root/pipecat-lab/，但**尚未安裝**）
cp /root/pipecat-lab/talkybuddy-pipecat.service /etc/systemd/system/
systemctl daemon-reload
/root/pipecat-lab/switch_doll.sh pipecat    # 切過去
/root/pipecat-lab/switch_doll.sh local      # 切回來
/root/pipecat-lab/switch_doll.sh status     # 誰在跑
```

`switch_doll.sh` 保證切完一定有一個在跑。**不要直接用 `systemctl stop`**：
`Conflicts=` 只停不啟，兩個都會 inactive、玩偶直接變啞，症狀跟按鍵故障
一模一樣（記憶 `project-edge-deploy`）。

### 🔴 最優先：按鍵觸發（pipecat 唯一還缺的關鍵功能）

**為什麼是最優先**：local-client 是 press-to-talk（power 鍵），對環境噪音
**天生免疫**；pipecat 是 VAD 連續聽。決賽會場很吵，交接文件第二節記著噪音
誤觸真的發生過（「寶貝多米」被聽成「コび」）。

**近場門檻那條備案走不通**，不要浪費時間試：記憶 `project-edge-s2s-tuning`
記著這塊板子上 `TALKYBUDDY_EDGE_NEAR_FIELD_PEAK` **必須是 0，否則玩偶完全
不回話**。預設 0.06 會讓它全聾。

**現成的零件**：`edge/runtime/audio_io.py::wait_for_trigger()`（第 453 行）
——阻塞直到按鍵，已在 local-client 實戰驗證，會自己處理 `KEY_POWER` 的
logind 搶佔問題。

**建議做法**（照 `PlaybackGateFilter` 的形狀，那個已經驗證過）：

- 新增 `pipecat_adapters/press_to_talk.py`，一個 `PressToTalkFilter`
- 擺在 `transport.input()` 之後、`PlaybackGateFilter` 之前
- 未 armed 時把 `InputAudioRawFrame` 的內容換成靜音（不要丟棄 frame，
  VAD 需要連續的時間軸）
- 背景 task 用 `asyncio.to_thread(audio_io.wait_for_trigger)` 等按鍵 → armed
- 收到 `UserStoppedSpeakingFrame` 或逾時（例如 15s）→ disarm，重新等按鍵
- **`wait_for_trigger` 是阻塞的，一定要 to_thread**（`sensevoice_stt` 的
  docstring 解釋過為什麼）
- 用環境變數開關（例如 `TALKYBUDDY_PIPECAT_PTT=1`），預設關閉＝現行 VAD 行為

### 🟡 未解：真人測試時「換句子之後卡住」

2026-08-01 真人測試，第 2 輪玩偶換句子成功之後，**再講話就沒有反應**。

已查證並**排除**的：
- SQLite 寫入阻塞 event loop（實測 3-5ms，不是原因）
- keepalive 餵到播放閘門（keepalive 直接寫 aplay stdin，不經過 pipeline）
- 取樣率算錯（22050 正確，實測 36 字 = 254354 bytes = 5.77s）

**已知事實**：log 裡輪 2 之後**一個 VAD 事件都沒有**，代表麥克風那條路被靜音
了，但沒能證明是哪個閘門（`PlaybackGateFilter` 或 `AlwaysUserMuteStrategy`）。

**最可能但未證實的解釋**：一輪 15 秒、玩偶講完後還有 2.6s 死區，而使用者
看不到閘門何時重開，講太早就被吃掉。**下一步應該先加診斷**（把
`PlaybackGateFilter.muted_frames` 與閘門開關轉換印進 log），再跑一次真人測試。

### 🟡 一輪 15 秒，瓶頸不在雲端

板子實測分解（2026-08-01）：

| 環節 | 時間 |
|---|---|
| 孩子講話 + VAD 收尾 | ~3s |
| ASR | 0.15s |
| **雲端 LLM** | **0.85s（只佔 5%）** |
| TTS 合成 | 3.12s（逐句推之後大部分被播放蓋掉） |
| TTS 播放 | 5.77s（36 字） |
| 閘門死區 | 2.6s |

已做：逐句推（TTS 不必等整段合成完）、回覆上限 40→25 字。

**還躺著沒拿的 2 秒**：aplay 緩衝死區。交接文件說 keepalive 就是為了讓緩衝
可以調小而做的，但「未驗證」。

> **2026-08-01 更正**：上一版寫「`live_client.py:75` 與 `audio_io` 的
> `--buffer-time` **兩個地方要一起改**」，那**只對 local-client 那條路成立**。
> pipecat 這條路**只有一個旋鈕**：`alsa_transport.py:204` 是向
> `live_client.build_aplay_argv` 借 argv 的，而 `PlaybackGate` 的 `buffer_delay`
> 也預設跟著同一個 `_PLAYBACK_BUFFER_US` 走（`live_client.py:172`）。
> 也就是說這 2.6 秒是**純設定實驗、不必改程式**：
>
> ```
> TALKYBUDDY_EDGE_PLAYBACK_BUFFER_US=2000000   # 2.0s，aplay 緩衝＝閘門假設的延遲
> TALKYBUDDY_EDGE_PLAYBACK_TAIL_S=0.6          # 0.6s，吃喇叭殘響
> ```
>
> 兩個都寫進 `/root/pipecat-lab/.env` 就會生效（service 有 `EnvironmentFile`）。
> 而且**現在量得到了**：`PlaybackGateFilter` 每次重開閘門會印
> 「關了 X.Xs，靜音 N 幀」，調完直接看那個數字，不必再靠推理。

調小的失敗樣子是聲音斷斷續續，比慢更糟，所以要在有時間驗證時才動。

**不要換 TTS 模型**：板子上只有一個中文聲音（`zh_CN-huayan-medium`），而且
播放時間是真實時間、換模型砍不掉。雲端 TTS 會讓音訊也變成網路依賴——斷網
橋段就從「變樸素」變成「變啞巴」，直接砸掉最大權重那 25%。

---

## 三之四、2026-08-01 下午：按鍵觸發完成，並挖出兩個「靜默變啞」

### 🔴 按鍵觸發：已完成並真機驗證

`edge/runtime/pipecat_adapters/press_to_talk.py`，環境變數 `TALKYBUDDY_PIPECAT_PTT=1`
opt-in（板子的 `/root/pipecat-lab/.env` 已加）。板子實測完整一輪：

```
按一下按鍵開始錄音...（讀 /dev/input/event1，鍵碼 116）
👂 聽成：可以跟我練習英文嗎？
按一下按鍵開始錄音...          ← disarm 後自動重新等按鍵，迴圈閉合
🗣 玩偶說：太棒了，我們一起練習動物英文。跟我說一遍：I see a dog.
```

**上一版建議的接法行不通，別照抄。** 原文說「擺在 `transport.input()` 之後、
`PlaybackGateFilter` 之前，收到 `UserStoppedSpeakingFrame` 就 disarm」——但
`vad` 是**獨立的 `VADProcessor`**（`probe_live_conversation.py:391-392`），
那個 frame 由它往**下游**推，擺在它前面的 processor 永遠看不到。

改成 `PlaybackGateFilter`/`PlaybackGateSink` 那個已驗證過的形狀，兩個節點共享一個
state：`PressToTalkFilter`（VAD 前封嘴）+ `PressToTalkDisarmer`（VAD 後收訊號）。

失效方向刻意選「開」：按鍵讀不到就永久 armed，退回 VAD 連續聽。玩偶變吵救得回來，
玩偶全聾救不回來。

### 🔴🔴 兩個「service active 但玩偶啞了」——比 PTT 本身更該先看

**這是決賽最貴的失敗模式**：監控說一切正常，而玩偶不會講話，症狀跟按鍵故障、
麥克風被佔用一模一樣。

**(1) pipecat 的閒置逾時會砍掉 pipeline。** `PipelineWorker` 預設
`idle_timeout_secs=300` + `cancel_on_idle_timeout=True`，而它判斷「活著」只看
`(BotSpeakingFrame, UserSpeakingFrame)`——**沒人講話就算閒置**。板子實測，
17:18:00 啟動、17:23:00 準時被砍。

→ 修法：`IDLE_TIMEOUT_SECS = None`（`probe_live_conversation.py`）。
真機驗證：17:28:54 啟動、17:34:00 仍活著，`Idle timeout` 警告 0 行。

**這不是 PTT 帶來的。** PTT 只是讓它必然發生。VAD 連續聽的版本在安靜房間裡
一樣會死——**現場架好玩偶等上台的那幾分鐘正好踩中**。

**(2) pipeline 死了行程卻不退出，`Restart=always` 因此救不到。**
原本是 `asyncio.gather(runner.run(worker), stop_after())`，而服務模式的
`stop_after()` 是 `while True: await asyncio.sleep(3600)`——runner 死了，gather
還在等那個睡一小時的協程。

→ 修法：抽出 `serve_pipeline()`，服務模式直接 `await runner.run(worker)`。
它一回來，`main()` 就收尾退出，systemd 約 7 秒重啟一次。**對任何死法都成立**，
不只閒置逾時。

### ⚠️ 地雷：不要把 `run(worker)` 換成官方建議的 `add_workers()`

pipecat 1.6.0 把 `WorkerRunner.run(worker)` 標成 deprecated，建議改用
`add_workers(worker)` + `run()`。**不要改**——1.5.0 與 1.6.0 實測語意相反：

| 寫法 | worker 死掉時 |
|---|---|
| `run(worker)` | runner 跟著結束 → 行程退出 → systemd 重啟。**會自癒** |
| `add_workers()+run()` | runner **繼續跑** → 行程不退出 → 退回上面那個 (2) |

改過去會把自癒能力整個拿掉。只把那一行警告消音即可
（`silence_runner_deprecation()`）。用
`test_add_workers_would_break_self_healing` 釘住；哪天它變紅代表 pipecat
修好了語意差異，那時才可以改。

### 診斷 log 現在真的看得到了（之前加了等於沒加）

`probe_live_conversation.py` 收尾原本是 `logger.add(sys.stderr, level="WARNING")`，
所以任何 `logger.info` 診斷在 journal 裡**一行都不會出現**。那行 WARNING 有正當
理由（pipecat 每個 frame 都有 DEBUG，全開會蓋掉對話），所以改成兩個互斥 filter 的
sink：**我們自己的 `edge.runtime.pipecat_adapters.*` 開到 INFO，其餘維持 WARNING**
（`configure_logging()`）。

現在每輪會看到：

```
🔘 按鍵觸發，開始聽
PlaybackGate 關閉上行（玩偶在講話）
PlaybackGate 開啟上行（關了 2.6s，靜音 130 幀）      ← 死區的實測值
⚠️ PlaybackGate 已關閉上行 12.3s（超過 10s）……      ← 卡住時才出現
```

**那個「關了 X.Xs」就是調 aplay 緩衝時要盯的數字**，不必再靠推理。
而「換句子之後卡住」若再發生，最後那行警告會直接指認是不是這個閘門。

### ⚠️ 未解：SSH ad-hoc 模式下按 power 鍵曾造成重開機

一次觀察，機制不明，**但操作上要避開**：

- **服務模式下按 power 鍵完全正常**（local-client 與 pipecat 都實測過多次）
- 但用 `ssh root@… python -c "…"` 這種 ad-hoc 方式跑等待按鍵的程式時，按下去
  板子直接重開（16:52 那次）
- `systemd-logind` 執行中的設定確認是 `HandlePowerKey=ignore`（`busctl` 查的
  執行中屬性，不是設定檔），所以**不是 logind 幹的**
- `key_probe.py` 的 docstring 早就寫著「⚠️ 不要按 KEY_POWER，可能觸發關機」，
  與記憶裡「power 鍵短按已驗證可用」是兩筆互相矛盾的紀錄——現在知道**兩者都對**，
  差別在跑法

**操作規則：驗證按鍵一律用服務模式（`switch_doll.sh`），不要用 SSH ad-hoc。**

### ⚠️ 板子的 journal 是 volatile，跨開機查不到東西

`/etc/systemd/journald.conf.d/10-journald-default-volatile.conf` 設了
`Storage=volatile`——journal 只在 RAM。上面那次重開機**查不到原因**就是因為它。
決賽前若還有時間，改成 `Storage=persistent` 會讓任何現場事故都可事後追查。

### 🔴 按下去沒有提示音 → 人以為玩偶壞了（已修）

真人測試回報「我按了 沒反應」。**PTT 完全正常**，log 裡使用者自己講出了原因：

```
🔘 按鍵觸發，開始聽
👂 聽成：要按按鍵才開始說，我都不知道。
```

按下去玩偶沒有任何回應，人會以為它壞了——而「按了沒反應」的樣子跟玩偶真的
壞掉分不出來。決賽現場小孩一定會犯一模一樣的錯。

**用嗶聲不用「我有在聽」**：玩偶只要講話，`PlaybackGate` 就得關上行（否則它把
自己的話收回去），成本是每輪多 3.4 秒（0.8s 語音 + 2.0s 緩衝 + 0.6s tail），
而一輪才 15 秒。純音不會被 SenseVoice 收成字、也不易觸發 Silero VAD（它是
**語音**偵測器），所以不必關閘門。這是使用者裁定的取捨。

880Hz / 150ms / 淡入淡出（方波邊緣是寬頻「喀」聲，反而可能觸發 VAD）。直接寫進
`transport.output().write_audio_frame()`，不往 pipeline 推 `TTSAudioRawFrame`
——後者會讓 `PlaybackGateSink` 關閘 2.6 秒，正好把要聽的時間吃掉。

板子實測（100 秒 3 次嗶聲）：VAD 觸發 0、ASR 收字 0、閘門關閉 0、underrun 0，
且 aplay 讀入的位元組與嗶聲精準對應（每次正好 6614 bytes = 22050×0.15×2）。

### 🔴 keepalive 從來沒有被部署過（已修）

板子上的 `edge/runtime/pipecat_adapters/alsa_transport.py` 比 HEAD **少 42 行**
——整個 keepalive 功能不在上面。`AlsaTransportParams()` 連 `keepalive_enabled`
這個屬性都沒有，而 `start()` 裡有 `if self._params.keepalive_enabled:`。

證據：aplay 的 `rchar` 在沒有播放時是 **0 bytes/s**（keepalive 若運作應約
44100）。部署 HEAD 之後變成穩定 43218 bytes/s。

這是記憶 `project-edge-deploy` 的「推檔≠生效」又一次。**下次改 pipecat adapter
之後，先比對板子與 HEAD 再下結論**——15 個 adapter 裡只有這一個是舊的，很難
用眼睛看出來。

### 🟡 aplay 緩衝：2.0s → 0.5s（已量、已套用）

交接文件說「keepalive 就是為了讓緩衝可以調小而做的，但未驗證」。現在量出來了
（`buffer_probe.py`，模擬逐句推 TTS：4 句 × 1.5s，中間 1.2s 合成空檔）：

| 緩衝 | 無 keepalive | 有 keepalive |
|---|---|---|
| 2,000,000 µs（原設定） | 0 underrun | 0 underrun |
| 1,000,000 µs | 0 underrun | 0 underrun |
| **500,000 µs** | ❌ underrun | **0 underrun** |
| 300,000 µs | ❌ underrun | 0 underrun |

### ❌ 但那張表是假的，500k 實跑爆掉了——已回退（同日稍晚）

依上表把緩衝設成 500,000 µs 之後，**真實運行的數字完全相反**：

| | underrun |
|---|---|
| 緩衝 2,000,000（原設定） | 16 次／小時 |
| 緩衝 500,000 | **866 次（約 290 次／小時）**，最大一次 **9.1 秒** |
| 回退回 2,000,000 | 5 分鐘 2 次（回到基線） |

**方法論錯在哪**：`probe_playback_buffer.py` 是在**服務停止**的情況下跑的
（它的用法那行自己就寫著 `systemctl stop`），所以沒有 VAD／STT／LLM 搶 CPU
——而 pipeline 光是閒置就吃 **1.3 核**。keepalive 是 asyncio task，被延遲就
餵不上；它每次寫入的量恰好是 **1.0 倍實時速率、零餘裕**，只要有排程抖動，
小緩衝立刻餓死。

**已回退**，`/root/pipecat-lab/.env` 不再有 `TALKYBUDDY_EDGE_PLAYBACK_BUFFER_US`
（備份 `.env.bak-buffer`）。死區維持 2.6 秒。

**要再挑戰這 2 秒的話，先修 keepalive 而不是先調緩衝**：讓它寫得比實時稍快
（例如每 0.1 秒寫 0.12 秒份），靠 aplay 緩衝滿了自然阻塞來調節，緩衝就會一直
是滿的、抖動也吃得下。**這是推論，沒有實作也沒有驗證。** 而且驗證時**服務
必須是跑著的**，否則又會拿到假綠燈。

⚠️ 另外，即使將來調得動，這個值也**依賴 keepalive**（上表顯示沒有它時 500k
必 underrun），所以 `alsa_transport.py` 若又退回舊版，聲音會變斷斷續續。

### 🟡 未解（需要你裁示）：孩子在玩偶還在講話時按鍵

自我審查發現的**設計缺口**，不是 bug，因為兩種行為都說得通：

1. 孩子按鍵 → `PressToTalkGate` armed 並**嗶一聲**
2. 但 `PlaybackGateFilter` 在下游，玩偶還在講話時仍把上行靜音
3. 孩子聽到嗶聲以為可以講了 → 講了沒人聽到 → 15 秒後自己 disarm

**嗶聲反而給了錯的保證**，而不耐煩的小孩一定會在玩偶講完前按。

死區降到 1.1 秒之後這個窗口小了很多，但玩偶回覆本身的播放時間（25 字約 4 秒）
仍在窗口內。

兩個方向，取捨不同：

| 做法 | 好處 | 代價 |
|---|---|---|
| **延後**：閘門開了才嗶、才起算 15 秒 | 嗶聲永遠是誠實的 | 孩子按了要等一下才聽到回應，可能又按一次 |
| **忽略**：玩偶講話時不理會按鍵 | 簡單、行為明確 | 孩子按了完全沒反應，正是這次踩過的坑 |

**我沒有動它**——這是行為改變，而且當下連不上板子驗不了。

### 還沒做的

- `server/app.py::_store_live_turn` 的三個欄位名仍是錯的（三之二末尾）。
- **嗶聲沒有人耳驗證過**（我測得到位元組與 underrun，聽不到音量與音色）。
  第一件事就是按一下確認聽得到、而且不刺耳。
- ~~降到 0.5s 緩衝~~ **已回退**（實跑 underrun 暴增 54 倍，見上）。死區維持 2.6s。

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
