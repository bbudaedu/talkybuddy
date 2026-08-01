# 交接：pipecat 玩偶深夜這一輪 + 板子狀態 + TTS 探針任務

**日期**：2026-08-01（深夜 16:30–23:00 那段）
**分支**：`feat/pipecat-edge`（worktree `/home/budaedu/talkybuddy-pipecat`，**已推 GitHub**）
**寫這份的原因**：板子改成網路線直連筆電之後，**開發機再也連不到板子**，
我在 pipecat 那條路做的東西全部還在板子上跑著，但我無法遠端驗證或回退。

---

## 〇、最短路徑

0. **最新進度在第八節（AWS 接通）** ——板子已改走 Bedrock，且抓到一個會砸掉
   斷網橋段的問題。**下一個 session 從第八節的「優先序」開工。**
1. 先讀第一節「板子現在到底在跑什麼」——**有東西是我部署的，你可能不知道**
2. 要動板子先看第二節的網路現況（Tailscale）
3. 第三節有**兩個被廣為相信但是錯的前提**，照舊做會白花時間
4. TTS 探針那個交辦任務在第四節，其中一項已有確定答案、不必再試

---

## 一、板子現在在跑什麼（我部署的，非常重要）

板子跑的是 **pipecat 玩偶**（`talkybuddy-pipecat.service`），不是 local-client。
這個 unit 是我今晚裝進 `/etc/systemd/system/` 的，之前不存在。

現行行為：

| 功能 | 狀態 |
|---|---|
| 按鍵觸發（power 鍵） | 開著（`/root/pipecat-lab/.env` 的 `TALKYBUDDY_PIPECAT_PTT=1`） |
| 按下去的提示音（880Hz 嗶聲） | 開著，使用者實測「不刺耳」 |
| **玩偶講完自動開始聽** | 開著——孩子可直接跟讀不必再按鍵，**已真人驗證** |
| aplay 緩衝 | 500000 µs（死區 2.6s → 1.1s） |
| 大腦 | ~~雲端 Gemini~~ → **已改 AWS Bedrock（Haiku 4.5），見第八節**。失敗當輪降級回 llama-server |

**真人驗證過的畫面**（22:51，全程只按一次鍵）：

```
🔘 按鍵觸發                  ← 只有這一次
👂 說說學伴，我們來練習英文吧。
🗣 好久不見…跟我說一遍：I see a dog.
PlaybackGate 開啟上行（關了 6.6s）   ← 自動 arm + 嗶
🎤 偵測到你開始說話…                ← 沒按鍵
👂 聽成：I see a dog.
🗣 你唸得好清楚…跟我說一遍：I see a cat.   ← 教材自動推進
```

四輪對話、三次跟讀、`dog→cat→rabbit→elephant→bird` 全自動。

**開機預設已改成 pipecat**（2026-08-01 06:20，使用者裁示）：
`talkybuddy-pipecat` = enabled、`talkybuddy-local-client` = **disabled**。

> 改的原因：板子當天重開過一次，開機後靜默回到 local-client——PTT、提示音、
> 跟讀自動聽、雲端腦全部消失，而**現場看不出來**（玩偶還是會講話）。
> `available()` 只檢查設定不檢查連線，所以沒網路時 pipecat 照樣起得來、
> 當輪降級回 llama-server，設成開機自動是安全的。
>
> ⚠️ **兩個都 enable 是錯的**：`Conflicts=` 會讓開機時序變成競爭條件。

**要切回備援**：`/root/pipecat-lab/switch_doll.sh local`
**絕對不要用 `systemctl stop`**：`Conflicts=` 只停不啟，兩個都會 inactive、
玩偶直接變啞，症狀跟按鍵故障一模一樣。

`/root/talkybuddy/`（決賽備援路徑）**全程沒碰**。

---

## 二、網路現況（2026-08-01 深夜）

| 節點 | 位址 | 誰連得到 |
|---|---|---|
| 開發機 | `Ubuntu-AI-Server` / `192.168.100.200` | 筆電 ✅ |
| Genio 520 板子 | **`192.168.1.200`**（網路線直連筆電） | **只有筆電** ✅ |
| 板子舊路徑 | `192.168.31.78` | 已失效 ❌ |

> **⚠️ 上表已過期（2026-08-01 06:15 更新）。** 筆電啟用了 Windows ICS
> （Wi-Fi 分享給乙太網卡）之後，板子同時擁有三個位址：
> `192.168.31.78`（舊路徑**又通了**，開發機從這裡連得回去）、
> `192.168.137.234`（ICS 發的 DHCP）、預設路由 `via 192.168.137.1`。
> **開發機現在連得到板子。** 網路又變動時，先 `ping 192.168.31.78` 再說。
>
> ICS 的注意事項（審查筆電那邊的計畫時整理的）：ICS 會把乙太網卡寫死成
> `192.168.137.1/24`；若要板子重拿 DHCP，背景腳本**一定要加一段「DHCP 失敗
> 就退回同網段靜態位址」**，否則失敗就得接螢幕。另 `New-NetNat` 可以讓板子
> 位址完全不動、不需重開機，風險比 ICS 更低。

**（歷史）開發機到板子沒有路由**——`192.168.1.200` 與 `192.168.31.78` 都不通。

**使用者已指出：板子有網路之後可以用 Tailscale**，那是恢復「開發機直接操作板子」
的正解。在那之前，所有板子操作都得從筆電的 PowerShell 下。

⚠️ 今晚在舊路徑上斷線三次（十幾分鐘到一小時）。判斷方法：斷線後 `uptime`
若是**連續的**就是網路問題、裝置沒重開（記憶 `project-genio520-hardware` 記過）。

---

## 三、兩個錯誤前提，照舊做會白花時間

### 3.1 「service 名稱不叫 talkybuddy*」——**錯的**

有份派工文件寫「`systemctl status talkybuddy*` 完全沒配對到任何 unit，名稱不叫
這個」。我今晚在板子上**實際 start/restart 過**這些 unit：

- `talkybuddy-pipecat.service`（今晚由我安裝）
- `talkybuddy-local-client.service`
- `talkybuddy-server.service`
- `talkybuddy-live-client.service`（出現在 pipecat unit 的 `Conflicts=`）

**原因**：`systemctl status` 的 glob **只比對已載入的 unit**。unit 存在但
inactive 且未載入時，`status talkybuddy*` 會什麼都不回。用
`systemctl list-unit-files | grep -i talky` 才查得到。

### 3.2 效能量測很容易造出一個不存在的情境（我今晚犯了）

我寫了 `edge/probes/probe_playback_buffer.py` 量 aplay 緩衝，得到
「500,000 µs = 0 underrun」就改了設定。真跑起來 underrun 暴增 **54 倍**。

**原因**：那支探針的用法自己就寫著 `systemctl stop talkybuddy-pipecat`——
沒有 VAD／STT／LLM 搶 CPU。

而我第一次回退也是錯的：我只看 underrun **次數**（866）就回退，沒看分布。
分開統計後最大只有 18.7ms、超過 100ms 的 0 筆，總掉音佔 0.019%。
**現在是 500k，玩偶講話期間實測最大 3.3ms。**

**通則：任何效能量測都必須讓真實 pipeline 同時在跑，而且要看分布不是只看次數。**

---

## 四、TTS 探針任務（那份派工的三件事）

### 4.1 ✅ 已有確定答案，不必再試

**第 3 項「端對端量測」量不到，原因確定：架構還沒接線。**

- 全 repo 搜 `polly`（`--include=*.py`）→ **`server/` 底下零命中**，
  只出現在 `edge/probes/probe_tts_latency.py` 三行
- `server/cloud_tts.py` 接的是 **ElevenLabs**（`api.elevenlabs.io`）
- `server/aws_only.py:15` 明列該檔為違規（決賽只准 AWS）

所以不是環境或網路問題，是 Polly 從來沒接進任何 pipeline 介面。

**另外查證**：`server/tts.py` 在 `master` 與 `feat/pipecat-edge` **完全相同**
（diff 無輸出），可放心套用 Polly 結論不必分辨版本。

### 4.2 ⏸ 還沒做：板上部署狀態 + edge RTF

要從**筆電 PowerShell** 跑。探針就是 repo 裡那支，我已改成**可攜**
（原本寫死 `/home/budaedu/talkybuddy`，板子上是 `/root/talkybuddy`，
現在從 `__file__` 推導 repo root）並**加了暖機**：

```
/home/budaedu/talkybuddy/edge/probes/probe_tts_latency.py   ← 尚未 commit
```

**開發機上已實跑通過**（2026-08-01 深夜）：

```
repo root = /home/budaedu/talkybuddy
[edge] 暖機耗時 1.54s
[短句_中文]        RTF=0.06
[短句_英文]        RTF=0.06
[教學回合_中英夾雜] RTF=0.06
[cloud] AWS 憑證無效，略過雲端量測   ← 優雅降級
```

> ⚠️ **暖機這一輪不能拿掉。** 加暖機之前量到 0.63／0.58／0.06，看起來像
> 「短句比較慢、接近會卡」——其實前兩筆各自付了一次 voice 模型載入的錢。
> 沒有暖機就會把「載入一次」誤讀成「每輪都慢」，正好落進本文件 3.2 那個坑。

PowerShell：

```powershell
scp budaedu@192.168.100.200:/home/budaedu/talkybuddy/edge/probes/probe_tts_latency.py $env:TEMP\probe_tts_latency.py
scp $env:TEMP\probe_tts_latency.py root@192.168.1.200:/root/talkybuddy/edge/probes/probe_tts_latency.py

ssh root@192.168.1.200 @'
systemctl list-unit-files | grep -i talky
ls /etc/systemd/system/ | grep -i talky
systemctl is-active talkybuddy-server talkybuddy-local-client talkybuddy-live-client talkybuddy-pipecat 2>&1
cd /root/talkybuddy && git branch --show-current && git log --oneline -3 && git status --short
cd /root/talkybuddy && ./.venv/bin/python edge/probes/probe_tts_latency.py
'@
```

**三個會讓結果失真的地方：**

1. **必須用 `./.venv/bin/python`，不能用 `python3`**——sherpa-onnx 只在 venv 裡，
   系統 python 會 `ModuleNotFoundError`，那不是「引擎不可用」
2. **Polly 那半會自己跳過**（板子沒有 boto3），印 `[cloud] 沒有 boto3，略過`
   是**正常的**，要的 RTF 在 edge 那半
3. 探針會先印 `repo root = /root/talkybuddy`，**印出來不是這個就是放錯目錄**

**RTF 判讀**：`合成秒數 / 音訊秒數`，與網路無關，純 CPU 負擔。
`< 1` 比即時快、`≥ 1` 比即時慢（會卡）。

兩個參考點：

| 來源 | RTF |
|---|---|
| 開發機（x86，本次實測穩態） | **0.06** |
| Genio 520（`PIPECAT_HANDOFF.md` 記載的 edge TTS 即時率） | **0.25** |

板子比開發機慢是預期內的。**要判斷「EDGE CPU 跑卡」，看的是穩態那三行**——
若板子量到仍在 0.25 附近，就代表 TTS 合成不是卡點（合成比播放快 4 倍，而且
pipecat 那條路已做逐句推，合成大多被播放蓋掉）；若量到接近或超過 1，才真的
是 CPU 跑不動，那時再談 NPU／雲端 TTS 才有意義。

⚠️ **暖機那 1.54s（板子上會更久）是每次行程啟動付一次**，不是每輪。
玩偶是常駐服務，所以使用者感受不到——**不要把它算進「一輪多久」**。

---

## 五、pipecat 那條路今晚的完整成果（16 commit，已推 GitHub）

除了第一節那些功能，還挖出並修掉四個沒人知道的問題：

| 問題 | 為什麼要緊 |
|---|---|
| **待機 5 分鐘玩偶靜默變啞** | `PipelineWorker` 預設 `idle_timeout_secs=300`，沒人講話就算閒置。**與 PTT 無關**，VAD 版在安靜房間一樣會死——現場架好等上台正好踩中。而且 systemd 顯示 active、`Restart=always` 救不到 |
| **pipeline 死了行程不退出** | `gather(runner.run(), stop_after())` 裡 `stop_after` 睡一小時。修好後實測 12 秒自癒 |
| **按鍵讀不到每秒開 50 條執行緒** | `_ensure_waiter` 每 frame 呼叫，等待迴圈放棄後就死，下一 frame 又開。板子 journal 在 RAM，會被洗爆 |
| **提示音卡住會讓玩偶變聾** | 它原本在**上行路徑上** `await`，`drain()` 一等就擋住 VAD。症狀是「按了、有嗶聲、講話沒反應」，跟麥克風壞掉分不出來 |

還擋掉一個地雷：pipecat 1.6 建議的 `add_workers()+run()` 寫法會把自癒能力拿掉
（實測語意相反），已用 `test_add_workers_would_break_self_healing` 釘住。

**1530 測試綠**，`edge/probes/probe_playback_buffer.py`（緩衝量測）已進版控。
細節全在 `talkybuddy-pipecat` worktree 的 `PIPECAT_HANDOFF.md` 三之四節。

---

## 六、未解 / 需要裁示

1. **孩子在玩偶還在講話時按鍵**：會嗶一聲但閘門還關著，嗶聲給了錯的保證。
   跟讀自動化之後急迫性降低，但小孩不耐煩時仍會遇到。兩個修法（延後 vs 忽略）
   取捨不同，寫在 `PIPECAT_HANDOFF.md`。
2. **音訊 starvation 的殘留風險**：較大的（曾量到 1.65s）都落在 STT 推論那幾秒，
   目前**剛好**都在靜音期所以聽不到，那是運氣不是設計保證。根治要讓 keepalive
   有餘裕（寫得比實時稍快，靠緩衝滿了阻塞調節）——**未實作未驗證**。
   使用者今日裁定不動。
3. **合併到 master**：已驗證可乾淨合併、檔案零重疊、合併後 **1734 測試綠**，
   但決賽兩條路都不從 master 跑，所以當天合併沒有 demo 效益。使用者未裁示。
4. **NPU 分擔 CPU**：已評估，**不值得投入**——板子 8 核、負載 1.06、pipecat 只吃
   0.96 核，**七顆核心閒著**，CPU 壓力根本不存在。而且 ASR 上 NPU 已兩次否證
   （140 個 `BATCH_MATMUL` 被拒 + session 崩潰），LLM 結構性不可行。唯一有實證
   的是 TTS vocoder 子圖（8×），但 pipecat venv 的 ORT 1.24.4 **沒有 NeuronEP**，
   且逐句推已把大部分合成時間藏起來。
5. **發音評測接進 pipecat**：已評估，決賽前不可行。板子可用記憶體只剩 **657MB**，
   而 `wav2vec2-xls-r-300m` fp32 要 ~1260MB；兩個 venv 都沒 torch/transformers。
   **且該功能在裝置上從來沒跑過**——實測 `pronunciation.available() = False`，
   `diagnose.py:150` 一直靜默用 `asr_confidence × 100` 的假分數。
   提示詞另存（見該次對話）。

---

## 七、這個專案反覆咬人的兩個教訓

1. **既有程式碼看起來簡陋，通常是踩過坑之後的正確解。** 動手前先讀
   `PIPECAT_HANDOFF.md` 第四節那張表。今晚我又踩了一次：交接文件建議的 PTT
   接法（收 `UserStoppedSpeakingFrame` 就 disarm）行不通，因為 `vad` 是獨立
   processor、那個 frame 往下游推，擺在它前面永遠收不到。
2. **測試綠不代表對**，而且**很容易造出一個不存在的情境去測**（見 3.2）。

---

## 八、AWS 接通（2026-08-01 下午～傍晚，最新進度）

### 8.1 已完成並實測

板子的 pipecat 玩偶**已改走 AWS Bedrock**，不再是開發期的 Gemini。

```
大腦　　　　：雲端 bedrock（失敗當輪降級回 llama-server）
雲端暖機　　：1817ms（成功）
雲端後端證據：verified=True backend=bedrock     ← 真的成功過，非設定讀數
```

**做法**：只改 `/root/pipecat-lab/.env`（憑證**沒有**進 repo，備份 `.env.bak-preaws`）：

```
AWS_DEFAULT_REGION / AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN
TALKYBUDDY_CLOUD_PROVIDER=bedrock
BEDROCK_REGION=us-west-2          # bedrock_converse.py:28 預設是 ap-east-2，這個帳號用不到
TALKYBUDDY_CONSENT_GRANTED=true
CLOUD_LLM_TIMEOUT_S=4             # ← 見 8.2，少了它等於沒接
```

boto3 1.43.60 板子上本來就有，不必安裝。

⚠️ **憑證是 session token（`AWS_SESSION_TOKEN`），會過期。** 過期後玩偶會每輪
降級回 llama-server 而**不會報錯**，只有 `verified_backend()` 看得出來。

### 8.2 ⚠️ 不設 `CLOUD_LLM_TIMEOUT_S=4` 等於沒接

`server/cloud_llm.py:32` 預設 **1.5s**。板子到 us-west-2 的實測：

| | 每輪延遲 |
|---|---|
| 預設 1.5s 上界 | `[1762, 6817, 1939] ms` ← **三輪全超過**，每輪降級 |
| 放寬到 4s 後 | `[1816, 1771, 1527] ms` ← 全部通過 |

驗法是跑 `/root/pipecat-lab/probe_cloud_llm_service.py`（不佔麥克風），
它會自己印出「最慢一輪超過逾時上界」的警告。

### 8.3 真人對話分項延遲（三輪，Bedrock，零降級）

| 環節 | 量法 | 實測 |
|---|---|---|
| 孩子開口 → 逐字稿 | `🎤 偵測到` → `👂 聽成` | 0.82 / 0.90 s |
| **Bedrock + TTS 首段** | `👂` → `🗣` | 2.31 / 2.15 / 2.19 s |
| 玩偶講話 + 死區 | 閘門關 → 開 | 6.5 / 6.9 / 5.8 s |
| **round_total** | 前後兩個 `👂` | **12.1 / 11.0 s** |

跟讀三次全對、教材自動推進 dog→cat→rabbit，第三輪還聽懂「再跟我說一遍」並
正確處理（沒硬推下一句）。

### 8.4 🔴 拔網測試抓到的問題：斷網第一輪沉默 10.5 秒

實體拔線後的 log：

```
07:20:41  👂 I see. A bird.
07:20:44  CloudLLM 失敗，降級回 edge          （2.9s）
07:20:51  EdgeLLM 失敗，降級回 scaffold 回覆   （再 7.6s，且**它自己也失敗**）
          ← 這一輪沒有任何 🗣，玩偶沒出聲
07:20:58  🔘 使用者忍不住按了 power
07:21:05  🗣 看得到一隻鳥。跟我說一遍：I see a bird.   ← 第二次才出聲
```

**三層降級鏈本身是有效的**（第二輪 scaffold 兜住了），問題是**前兩層加起來
10.5 秒**，而且中間那層在降級路徑上失敗。

**現場看到的不會是「玩偶變樸素」，而是「玩偶沉默十秒」**——那正好砸掉斷網橋段。

**已查證**：llama-server 本身**是健康的**（直接 curl 回 HTTP 200、1.05s）。
**未查證的推測**：`EdgeLLM` 自己的逾時比板子真實生成時間短（交接文件記載
edge LLM 一輪約 3.9s，加 prompt 處理很容易超過）。**下一個 session 請先查證
這一條，不要當成已知結論。**

### 8.5 AgentCore 不適用於 edge（不是漏接）

`server/cloud_llm.py` 與 `edge/runtime/pipecat_adapters/cloud_llm_service.py`
**各 0 處**引用 AgentCore。原因寫在 `server/agent_backends.py:28`：

> 三個 agent 的雲端診斷全部用 diag 這顆模型（**12s 非同步預算，非對話路徑**）。

對話預算 4s、孩子在等；AgentCore 是 microVM 裡的 managed agent loop，光啟動就
吃掉整個預算。**「板子連雲端強腦」已經達成，執行者是 Bedrock 的 Haiku 4.5。**

### 8.6 要更正 AWS 派工的一項

它寫「診斷：`global.anthropic.claude-sonnet-5`」——**實測 AccessDenied**。
對話用的 `global.anthropic.claude-haiku-4-5-20251001-v1:0` 正常（開發機 1.11s）。
edge 只用對話模型，不受影響，但雲端那條路要知道。

### 8.7 Polly TTS：**還沒做**，而且不是設定是整合

使用者要求「有網路一樣走 Polly」。現況：

- `server/polly_tts.py` **只存在 master**，`feat/pipecat-edge` 沒有
- pipecat 的 `edge_tts.py`（`EdgeVitsTTSService`）**完全沒有接點**
- master 上由 `server/app.py` 與 `server/pipeline.py` 使用

⚠️ **一個容易寫錯的設計差異**：

| | 中文段由誰唸 | 斷網時 |
|---|---|---|
| 實驗版（`demo_mix.mp3`） | Polly `Zhiyu` | 中英**都啞** |
| **`polly_tts.py` 實作版** | **本地 piper** | 只有英文退回本地音色，**中文不受影響** |

`polly_tts.py` 標題行寫「**只接英文段**」——刻意沒把中文交給 Zhiyu。
**這個選擇對玩偶是對的**：Zhiyu 沒童聲也沒台灣腔，送上雲只增加風險。
接的時候**務必保留這個形狀**，否則斷網橋段從「變樸素」變成「變啞巴」。

技術細節：Polly `pcm` 只支援 8000/16000，22050 只有 mp3/ogg 有；
`polly_tts.py` 取 pcm 16000 再線性重取樣到 22050，避免多一個 mp3 解碼相依。
全鏈 22050Hz，**取樣率對不上 aplay 不會報錯、只會變調**（本專案踩過）。

### 8.8 下一個 session 的優先序（我的建議）

1. **先修 8.4 那 10.5 秒沉默** —— 它直接砸掉一個 demo 橋段，而且可能只是一個
   逾時值。先查證 `EdgeLLM` 的逾時設定，再決定是調它還是縮短降級鏈。
2. **Polly TTS（8.7）** —— 加分項（英文童聲），不是止血。務必保留「中文留本地」。
3. 憑證過期時的行為 —— 目前會靜默降級，考慮讓它明確報出來。

**驗收一律看 `verified_backend()` 或 log 的實際 `🗣`，不要看設定讀數**——
這個專案被「以為在跑雲端、其實沒有」咬過三次。
