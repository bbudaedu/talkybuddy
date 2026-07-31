# pipecat edge — 夜間進度（2026-07-31 02:20–02:45）

## 先看這段：你的板子沒被動過

決賽今天。我全程只讀不寫地對待決賽路徑：

- `talkybuddy-live-client.service`、`talkybuddy-server.service` **從頭到尾 active running**，沒有 stop/restart/reload
- `/root/talkybuddy/` 底下**零修改**（含 `.venv`、`models`）
- 新東西全部隔離在板子的 `/root/pipecat-lab/`（獨立 venv），刪掉即完全復原
- 主 repo `gsd/2-genio-520-edge-mvp` 沒動、沒 push；本檔在 worktree 分支 `feat/pipecat-edge`
- 板子磁碟：8.5G → 9.4G（pipecat 佔約 600MB），剩 **4.0G**，離 2G 紅線還遠

備份（已實際還原驗證過，不是「應該可以」）：`~/backups/talkybuddy-20260731-022002/`
含 `RESTORE.md`。板子端另有 `/root/backup-20260731/`（venv tar + 三個 unit + `.env`）。

---

## 決賽當天可能用得上的一個發現

`a874141` 查到裝置的 `ANTHROPIC_BASE_URL=http://127.0.0.1:8317` 指向板子自己，
沒人在聽，所以 CloudLLM 每輪 `ConnectionRefused` 降級回邊緣 LLM——**所謂 cloud 模式
跑的一直是 edge LLM**。

而 Ubuntu-AI-Server（192.168.100.200）的 **8317 一直有 `cli-proxy-api` 在跑**
（`LISTEN *:8317 users:(("cli-proxy-api",pid=858))`）。

LLM 佔 round_total 的 78%（3859ms / 5025ms）。改一個環境變數指過去就可能把它換掉。
**但前提是決賽現場連得到那台機器**——實測 edge→server RTT 116ms、頻寬約 550kB/s，
而且你說現場要靠 tunnel。這件事我沒有動，留給你判斷。

---

## 做完的事

### 1. 原本以為要自寫 4 個 adapter，實測後只需要 1 個

第一次判斷是用 `pip download --platform manylinux2014_aarch64` 在開發機模擬的，
結論「onnxruntime 無 aarch64 wheel、`[silero]` 不可行」——**那是假陰性**，
platform tag 訂太舊。真的在板子上 `pip install` 就裝起來了。

**教訓：platform tag 模擬只能證明「可行」，不能證明「不可行」。**

| 元件 | 板子實測 | 結論 |
|---|---|---|
| `pipecat-ai` 1.6.0 核心 | ✅ 裝好，`import pipecat` 0.27s | 直接用 |
| `SileroVADAnalyzer` | ✅ onnxruntime 1.24.4 CPU provider 可用 | 直接用，**不必自寫** |
| `LocalAudioTransport` (pyaudio) | ❌ `Failed to build installable wheels` | **自寫** ← 唯一要做的 |
| 本地 LLM | llama-server 是 OpenAI 相容 | `OpenAILLMService` 指過去即可 |
| 本地 STT / TTS | sense-voice、piper 模型都在板子上 | 待接（下一輪） |

### 2. Silero VAD 在 Genio 520 上的實測數字

- 模型是 pipecat **內建**的，不必另外下載
- 實例化 0.10s、行程 RSS 143MB
- 端到端 `analyze_audio()` **1.90ms/窗**，每窗 512 samples = 32ms 音訊 → **即時率約 6%**，板子綽綽有餘
- 靜音 confidence 0.024、類語音 0.61、`VADState.QUIET` 判斷正確

**兩個會咬人的行為（已寫進 `pipecat_adapters/__init__.py`）**：
1. 建構後 `sample_rate` 是 0、`num_frames_required()` 回 256（8kHz 的窗）；
   要等 pipeline 送 `StartFrame` 觸發 `set_sample_rate(16000)` 才變成正確的 512。
   **任何在 `__init__` 裡就依賴 `self.sample_rate` 的子類別都會拿到 0** ——
   我自寫的 sherpa VAD 版本就是踩到這個才被實測抓出來（該檔已刪，因為官方版可用）。
2. `voice_confidence()` 註記 `-> float` 但實際回 shape `(1,)` 的 ndarray；
   numpy 2.x 對它呼叫 `float()` 會 `TypeError`。

### 3. `alsa_transport.py` — 唯一需要自寫的 adapter

`edge/runtime/pipecat_adapters/alsa_transport.py`，用 arecord/aplay 子行程取代 pyaudio。

- argv 一律 import `live_client.build_arecord_argv` / `build_aplay_argv`，
  **不另寫一份會漂移的 ALSA 參數**（尤其 `--buffer-time 2000000` 那個
  2026-07-30「斷斷續續」的實機教訓）
- 用 `asyncio.create_subprocess_exec` 而非 `Popen`+執行緒，因為 pipecat 是 asyncio 世界
- stderr 一律不吞：arecord 起不來時唯一的線索在那裡
- teardown 會 terminate→逾時 kill，確保**麥克風一定釋放**（不釋放的症狀跟麥克風壞掉
  一模一樣，見 `38aa261`）

**測試：`tests/test_pipecat_alsa_transport.py`，10 passed。** 測試刻意不啟動真的
arecord——板子上 live-client 正持有麥克風，測試若真的開會變成第二個搶麥的行程。

---

---

# 第二輪（02:50–03:10）

## 查證了一條專案既有決策，一半推翻一半成立

記憶／`docs/DEPLOY_EDGE.md:136` 記載「Pipecat 串流管線：刻意不裝」，理由之一是
**「pipecat 會升級 numpy，而 sherpa-onnx ASR/TTS 依賴現版」**。這條直接決定我的
STT adapter 能不能成立（它要讓 pipecat 與 sherpa 跑在同一個 process），所以先驗證：

| venv | numpy | sherpa-onnx |
|---|---|---|
| `/root/talkybuddy/.venv`（決賽路徑，只讀） | 2.5.1 | ✅ |
| `/root/pipecat-lab/.venv` | 2.4.6 | ✅ 事後裝入無衝突無降級 |

**numpy 衝突不成立**（方向甚至相反，決賽 venv 的 numpy 還比較新）。同 process
共存實測：`import pipecat`+`import sherpa_onnx` 0.16s、VAD 仍可用、
SenseVoice 載入 1.93s、**2 秒音訊辨識 147ms**。

**但那條記錄的其他理由仍然成立，而且多了一個新數字**：

- 行程 **RSS 664MB**（板子可用僅 1.7G，llama-server 還要吃）← 這是新的風險
- `pyaudio` 確實要編譯（已於第一輪確認）
- **最關鍵的沒變**：接通後 round_total 仍由 LLM 的 3.9s 主宰

順帶一提，餵雜訊給 SenseVoice 得到的辨識結果是 `'그.'`——**正好複現了
`live_client` docstring 記的「把噪音判成韓文字符」**。決賽會場很吵，
「空結果即雜音」那條兜底不能拿掉。

## 兩個 adapter 完成，27 測試全綠

- `sensevoice_stt.py`：重用 `SenseVoiceASREngine._ensure_model()`（`CONTRACTS.md`
  明列的公開契約）共用模型單例，不重複載入吃掉板子 RAM。
  **sherpa 的 decode 是阻塞 native 呼叫，一律 `asyncio.to_thread`**——
  在 pipecat 的單一 event loop 上阻塞會同時凍住 VAD、麥克風讀取與播放。
- `edge_tts.py`：`synth()` 回完整 WAV，這裡用 `wave` 模組解析剝掉 header
  （硬切 44 bytes 不安全，WAV 允許 `data` 前插別的 chunk）。
  **誠實記錄：這條路徑不是串流的**，first-audio 延遲 = 整句合成時間，切 chunk
  不會讓第一個 byte 早一點出來。

## 又踩到同一個坑，這次釘住了

第一輪在 VAD 上踩過的「`sample_rate` 建構後是 0」，**STT 與 TTS 完全一樣**：

| 元件 | 何時才生效 |
|---|---|
| `VADAnalyzer` | `set_sample_rate()` |
| `STTService` | `StartFrame` → `stt_service.py:315` |
| `TTSService` | `StartFrame` → `tts_service.py:549` |

生產路徑是對的（pipeline 啟動時會設），是單元測試繞過 pipeline 才拿到 0。
已加 `_with_rate()` 補這一步，並用
`test_sample_rate_is_zero_until_pipeline_starts` 釘住行為——pipecat 哪天改掉會紅。

---

---

# 第三輪（03:16–03:35）— 板子中途斷線

## 板子在本輪開始時就不可達

第一件事（鐵律檢查）就 SSH 逾時。診斷：

```
ping 192.168.31.78 → 3 packets transmitted, 0 received, 100% packet loss
TCP 22             → 不可連
```

本輪結束前重試兩次，仍然不可達。與記憶 `project-genio520-hardware` 記的
「連線中斷是上游手機網路非裝置」一致；這條鏈路本來就只有 116ms RTT / ~550kB/s。

**所以 RSS 量測與端到端跑分本輪做不成**，改做不需要板子的兩項。

**這件事本身是設計資料**：edge↔server 鏈路會整條斷，而且是在無人值守時斷的。
這正是降級狀態機存在的理由，時機巧得像是安排好的。

## 降級狀態機完成（14 測試）

`edge/runtime/pipecat_adapters/failover.py` — 純狀態機，不做 I/O、時鐘可注入，
所以測試不必真的拔網路或等待。

| 參數 | 預設 | 為什麼 |
|---|---|---|
| `failure_threshold` | 2 | 單次逾時不算數，鏈路本來就會抖 |
| `recovery_threshold` | 3 | **刻意比降級高**——降級安全（本地一定在），升級有風險 |
| `cooldown_s` | 30 | 防 flapping；每次切換對孩子是一次語音風格突變 |

降級**不看冷卻**（雲端已壞，等冷卻只是讓每輪白等一次逾時）；升回**要同時**滿足
連續成功次數與冷卻。有一個測試專門驗證成敗交錯 20 次不觸發任何切換。

**核心設計約束：這個狀態機只換 service，永遠不碰 transport。** 麥克風由單一
transport 從頭持有到尾。這直接解掉記憶裡的兩個事故——`Conflicts=` 只停不啟
導致玩偶變啞、以及兩個 client 搶麥克風。最壞結果從「玩偶不會講話」降級成
「這一輪回答比較笨」。

## spec 完成

`docs/PIPECAT_EDGE_DESIGN.md`。含架構、元件盤點、降級設計、實測數字表、
未驗證風險（按嚴重度排序），以及一節**「什麼情況應該放棄這個方案」**——
誠實的設計要寫退出條件。

累計 **41 測試全綠**（transport 10 + STT/TTS 17 + failover 14）。

---

---

# 第四輪（03:50–04:05）— 用官方 harness 抓到一個結構性錯誤

板子第四輪仍不可達。原本要寫 pipeline 組裝，但先找到了更好的東西：
**pipecat 內建 `pipecat.tests.utils.run_test`**，可以在**真實 pipeline 驅動下**
測 processor，不需要板子。用它立刻抓到一個單元測試永遠抓不到的錯誤。

## 🔴 STT 繼承錯基底類別（已修）

`SenseVoiceSTTService` 原本繼承 `STTService`。**那是給串流式 ASR 用的**——
它對收到的**每一個** `AudioRawFrame`（約 20ms）都呼叫一次 `run_stt`。

SenseVoice 是離線非自回歸模型，單次推論約 147ms、需要完整語句才有意義。兩者湊在一起：

- 每 20ms 觸發一次 147ms 推論 → 佇列直接爆掉
- 每次只拿到 20ms 音訊 → 辨識結果無意義

**而 17 個單元測試全部通過**，因為它們直接呼叫 `run_stt()`，繞過了 pipeline 的分派。
這種結構性錯誤只有真實驅動才看得見。

改繼承 `SegmentedSTTService`（官方 Whisper 也是它），並覆寫
`wants_wav_segments → False`（基底給本地模型的正式契約，預設會包成 WAV 給雲端 API）。
它還附帶一個好處：**維護前置緩衝補償 VAD 偵測延遲**，正好對應
`project-edge-s2s-tuning` 記的「孩子話音剛落就跟讀，開頭會被吃掉」。

真實驅動下的驗證結果：

```
辨識次數: 1              ← 一句話只辨識一次（改之前會是 3）
sample_rate: 16000        ← StartFrame 補上了
收到 640 samples 純 PCM   ← 含 WAV header 會是 662
DOWN: [STTMetadataFrame, VAD…, InputAudio×2, VAD…, TranscriptionFrame]
```

## 🟡 另外兩個 pipecat 抱怨（已修）

真實 pipeline 啟動時 pipecat 會檢查 service 設定，log 直接指出：

1. `STTSettings / TTSSettings: the following fields are NOT_GIVEN` —
   settings 必須在 `__init__` 初始化。已加 `Settings` 類別屬性與初始值
   （`language=None` 是「服務自己偵測語言」的正式表達，SenseVoice 正是多語言自動偵測）
2. `ttfs_p99_latency not set, using default 1.0s` —
   預設值比實測慢了將近一個數量級，會讓 pipeline 多等。已用實測值設為 0.4s
   （依據：SenseVoice 辨識 2 秒音訊 147ms，留餘裕）

## 🔴 未解：TTS 在 run_test 下輸出 0 個 frame

同一個 harness 下，`EdgeVitsTTSService` 的行為是：

```
synth 呼叫: [[('zh', '你好')]]   ← 合成確實被觸發
DOWN: []                          ← 但下游一個 frame 都沒收到
audio frames: 0
```

已排除的可能：settings 未初始化（已修）、`push_start_frame`／`push_stop_frames`
未開（已依官方 Piper 的做法設為 True）、非同步未完成（SleepFrame 等到 4 秒仍為 0）。

pipecat 的 TTS 音訊走 audio context + 背景 `_audio_context_task`，比 STT 複雜得多。
**傾向判斷是 `run_test` 對 `TTSService` 的支援限制而非 adapter 缺陷**——依據是
同一 harness 下 STT 完全正常、`run_tts` 確實被正確驅動、且遍尋 pipecat 原始碼
找不到任何官方用 `run_test` 測 TTSService 的範例。**但這只是傾向，沒有證實。**

⚠️ **所以 TTS adapter 目前的狀態是「單元測試通過、真實 pipeline 未驗證」**，
必須在板子上實跑才能確認。這是接線的必經之路，不能跳過。

累計 **44 測試全綠**。

---

## 停在這裡的理由

剩下的驗證**全部需要板子**，而板子已連續四輪不可達：

1. 完整 pipeline RSS（最大未知數，超過約 1.2G 就該停下重新評估）
2. 端到端 round_total
3. CPU 爭用
4. **TTS 在真實 pipeline 中到底有沒有出聲**（本輪新增的未解項）

不需要板子的工作已經做完了：四個元件 + 44 測試 + spec。

**沒有繼續空轉的價值**——再往下就是在沒有驗證回饋的情況下累積程式碼，
而這四輪的教訓完全一致：**憑推測會錯**。onnxruntime 假陰性、numpy 顧慮不成立、
sample_rate 陷阱、STT 繼承錯基底——每一個都是實測才發現的，沒有一個是想出來的。

---

# 第五輪（04:20）— 板子回來，最大的未知數解除

半夜斷網（使用者確認），板子約 04:20 恢復，RTT 87ms。

## ✅ 完整 pipeline RSS：747MB，通過

```
[0] baseline         8 MB
[1] +pipecat        34 MB   (+26)
[2] +VAD           136 MB   (+102)
[3] +SenseVoice    664 MB   (+528)  ← 最大宗
[4] +TTS           744 MB   (+80)
[5] 跑過一輪       747 MB
```

可用 1759MB，spec 設的紅線是 1.2G——**通過，還剩約 1G 餘裕**。
llama-server 另吃 1413MB，兩者相加約 2.16G / 總共 3.7G：塞得下，
但沒有第二個大模型的空間了。

## ✅ TTS 即時率 0.25x（一度誤判成 1.0x）

第一次量到「合成 2.3s / 音訊 2.23s」，差點下結論說 TTS 即時率只有 1.0x。
**那是冷啟動**——含 voice 模型首次載入 2068ms。暖機後：

| 文字 | 合成 | 音訊 | 即時率 |
|---|---|---|---|
| 你好 | 153ms | 0.63s | 0.24x |
| 你好，我們來練習說蘋果 | 533ms | 2.11s | 0.25x |
| 蘋果是 apple，你跟我念一次好不好 | 746ms | 2.87s | 0.26x |

**合成比播放快 4 倍**，first-audio 150–750ms。TTS 不是瓶頸。
（冷啟動那 2s 只發生一次，開機預熱一句就消化掉了。）

## 完整成本圖像

```
VAD    1.90ms/窗（即時率 6%）
STT   147ms（2 秒音訊）
TTS   150-750ms（即時率 0.25x）
LLM  3859ms  ←──── 瓶頸，pipecat 動不到它
```

**全部量完之後這個結論只有更強**：除 LLM 外所有元件加起來不到 1 秒，
LLM 一個人 3.9 秒。

## ⚠️ 順帶發現：live-client 變成 inactive

板子回來後 `talkybuddy-live-client` 是 **inactive**（之前 active）。
不是我停的（鐵律禁止碰 service，斷網期間也連不上）。記憶記過它
**刻意沒有 `[Install]` 段、開機不會自動起來**，所以板子很可能重開過。
要 demo S2S 需 `switch_mode.sh live`（別用 `systemctl stop`，那會兩個 client 都死）。

---

# 第六輪 — TTS 真實 pipeline 驗證通過，最高風險項解除

## ✅ 根因不是 adapter，是我的驅動方式漏了 turn 邊界

先在主機用**真實 `PipelineWorker`**（非 `run_test` 簡化 harness）重現：仍是 0 個
音訊 frame。**這推翻了前一輪「傾向是 run_test 限制」的判斷**——真實 pipeline
也一樣，所以問題不在 harness。

繼續追進 pipecat 原始碼才找到真正的機制：

```
run_tts yield 的 frame → audio context（不是直接 push 下游）
                       → 背景 _audio_context_task 在 context 關閉後才 drain
                       → 關閉 context 的是 on_turn_context_completed()
                       → 它要等 turn 邊界：LLMFullResponseEndFrame
```

只送 `TextFrame` 而不送 turn 邊界 → `synth()` 確實被呼叫，但音訊永遠卡在
context queue 裡，下游連 TextFrame 都收不到。**症狀跟「TTS 壞掉」一模一樣。**

補上 `LLMFullResponseStartFrame` / `LLMFullResponseEndFrame` 後，主機立刻正常：

```
StartFrame → LLMFullResponseStartFrame → AggregatedTextFrame → TTSStartedFrame
→ TTSAudioRawFrame ×5 → TTSTextFrame → TTSStoppedFrame → LLMFullResponseEndFrame
```

## ✅ 板子上真實 sherpa VITS 引擎跑通

```
TTSAudioRawFrame 數量: 23
音訊總 bytes: 99328  (= 2.25s @22050Hz)
```

與單獨量測 `synth()`（「你好，我們來練習說蘋果」→ 2.11–2.37s）完全吻合。

已補 `tests/test_pipecat_tts_in_pipeline.py`，**兩個方向都釘住**：有 turn 邊界
要出聲、沒有 turn 邊界要卡住（後者是為了讓下一個人三秒認出這個症狀，
而不是再查一次）。

## 📌 順帶發現：pipecat 內建 ServiceSwitcher，與自寫 failover 互補

`pipeline/service_switcher.py` 有 `ServiceSwitcher`（`ParallelPipeline` + filters
做 frame 路由）與 `ServiceSwitcherStrategyFailover`。查證後**不重複**：

- 官方 strategy 是「收到 `ErrorFrame` **就立刻切**」，無遲滯
- 官方文件自己寫「Recovery and fallback policies are **left to application code**」
- 自寫 `FailoverPolicy` 提供的正是那塊：連續失敗計數、遲滯、冷卻、防抖動

**接線方式已寫進 spec**：用官方 `ServiceSwitcher` 做路由，`FailoverPolicy` 掛在
`on_service_switched` 決定何時切／何時切回。不要自己另做 frame 路由。

累計 **46 測試全綠**。

---

# 第七輪 — 端到端跑通，而且**推翻了我自己講了六輪的結論**

接上 llama-server（`127.0.0.1:8080`，qwen2.5-1.5b，ctx 512）跑完整一輪。
刻意不接麥克風（live-client 可能持有），從 `TranscriptionFrame` 起頭。

## ⚠️ 我一直說「pipecat 不會讓對話變流暢」——那只對了一半

第一次用簡短 system prompt 量到 round_total 1948ms，我沒有拿它當結論，
因為現行的 4685ms 用的是 288 字的真實教材 prompt。換上 `EdgeLLM._SYSTEM_PROMPT`
重量四次：

| | llm_first | llm_done | tts_first_audio | round_total |
|---|---|---|---|---|
| 1 | 2903 | 4881 | 3336 | 5405 |
| 2 | 1054 | 3261 | 3381 | 3959 |
| 3 | 1073 | 2706 | **1462** | 3129 |
| 4 | 1171 | 2709 | **2328** | 3159 |

**關鍵不是那些數字，是 `tts_first_audio` 早於 `llm_done`**（四次中兩次明顯如此）。
那代表 pipecat 在 LLM 還在生成時，就把已完成的第一句送去合成播放了。

現行架構做不到：它是「LLM 全部跑完（3859ms）→ 才開始 TTS（+1163ms）」
≈ **5022ms 才出聲**。pipecat 的 first-audio 是 **1462–3381ms**。

| 指標 | 現行 | pipecat |
|---|---|---|
| **first-audio**（孩子聽到第一個字） | 約 5022ms | **1462–3381ms** |
| round_total（整段講完） | 4685–5025ms | 3129–5405ms（同量級） |

**所以要拆成兩個指標講**：pipecat 不會讓 LLM 變快（仍是 2.7–3.3s），
但它讓孩子**不必等 LLM 講完才聽到聲音**。對感知上的流暢度，first-audio 才是關鍵。

⚠️ 樣本僅四次、變異大（round_total 3129–5405ms），量測方式也與現行的
`probe_latency` 多輪中位數不完全可比。**時間數字當量級參考，不是定論**；
但 `tts_first_audio < llm_done` 這個現象本身不受樣本數影響。

## 編排開銷幾乎為零

第一次短 prompt 那輪：llm_done 1489ms + TTS 458ms = 1947ms，
而實測 round_total 1948ms。**pipecat 沒有引入可測量的額外開銷。**

## 順帶記兩件事

- llama-server 的 **ctx-size 只有 512**，而 `build_live_system_prompt`（S2S 用）
  是 1170 字——那條 prompt 是給雲端 Nova Sonic 的，不是給 edge LLM 的。
  edge LLM 用的是 `server/llm.py:51` 的 288 字版本。
- llama-server 回的是**簡體**（「苹果」）。edge TTS 那條路徑沒有 OpenCC，
  ASR 那條有。接線時要補。

---

# 第八輪 — 補 OpenCC，但先驗證了「該不該轉」

使用者要求補簡轉繁。動手前先確認一件事：**TTS 模型是 `zh_CN-huayan-medium`
（簡體中文模型）**，餵繁體字給它會不會反而念不出來？

## 閉環驗證：用 ASR 當耳朵

先量音長，繁體版一致地短 0.10–0.13s（約 10%），像是有字被吃掉。我聽不到，
所以讓 **SenseVoice 回頭辨識 TTS 的輸出**：

| 輸入 | 聽成 | | 輸入 | 聽成 |
|---|---|---|---|---|
| 蘋果 | 苹果。 | | 苹果 | 苹果。 |
| 跟我說一遍 | 跟我说一遍。 | | 跟我说一遍 | 跟我说一遍。 |
| 你說得很棒 | 你说的很棒。 | | 你说得很棒 | 你说的很棒。 |

**繁簡念出來完全一致、沒有漏字**（音長差是韻律）。
→ **發音層面不需要簡轉繁**，需要繁體的是**給人看的文字**。

## 所以 OpenCC 放在 TTS 之後

`opencc_processor.py`，只轉 `TTSTextFrame`。兩個理由都有實測支撐：

1. 發音不需要轉（上面的閉環驗證）
2. 放 TTS 之前會壞事——那裡的 `LLMTextFrame` 是串流 **token 片段**，
   而 `s2twp` 含詞彙轉換（「軟件」→「軟體」），**逐 token 轉會破壞詞彙邊界**

官方的 `text_transforms` 鉤子也改不到逐字稿：`TTSTextFrame` 帶的是**原始未轉換
文字**（`tts_service.py:1182` 刻意如此）。

真實 `s2twp` 行為已在板子驗證：「苹果的英文是 apple」→「蘋果的英文是 apple」
（英文不受影響）、「软件」→「軟體」（台灣用詞）。

## 誠實結果：接上真實 prompt 後，OpenCC 一次都沒觸發

連續四次端到端，LLM **全部輸出繁體**——真實教材 prompt 裡寫明「只用繁體中文和
英文回覆」，qwen 遵守了。（先前那個「苹果的英文是 apple」是我直接 curl、
沒帶 system prompt 的結果。）

**所以它是低頻保險，不是熱路徑**。仍然留著：system prompt 是請求不是保證。

## 順帶：四次穩定樣本，串流 4/4 生效

| | llm_done | tts_first_audio | round_total |
|---|---|---|---|
| 1 | 2996 | **1881** | 3423 |
| 2 | 2656 | **2017** | 3257 |
| 3 | 2728 | **2700** | 3161 |
| 4 | 2792 | **1733** | 3245 |

`tts_first_audio` **四次全部早於 `llm_done`**。round_total 穩定在 3161–3423ms
（上一輪那個 5405ms 是冷啟動）。

⚠️ **但 LLM 那段的差異不要歸給 pipecat**：本量測 `llm_done` 2656–2996ms，
`a874141` 是 3859ms，差距可能來自 prompt 長度或量測方式，**pipecat 動不到推論速度**。
可明確歸功的只有 `tts_first_audio < llm_done` 這個重疊。

累計 **53 測試全綠**（1 skipped：主機未裝 opencc 的真實轉換測試）。

---

# 第九輪 — 全鏈路閉環，兩個未驗證項都過，但發現一個產品缺口

`edge/probes/probe_pipecat_full_chain.py`：**從真實音訊起頭**走完
STT → LLM → OpenCC → TTS。輸入音訊用 TTS 自己合成再降採樣到 16kHz，
所以**不需要碰麥克風**。

## ✅ 閉環成功

```
輸入音訊：我想要蘋果（TTS 合成降採樣 16k，1.34s）
STT 聽成：我想要蘋果。          ← 完全正確
LLM 原始輸出：跟我說一遍：我想要蘋果。
逐字稿(繁)：跟我說一遍：我想要蘋果。
```

## ✅ 兩個未驗證項都通過

| 項目 | 結果 | 餘裕 |
|---|---|---|
| **CPU 爭用** | **16% 單核當量**（4.92s CPU / 31.5s wall，板子 8 核） | 極大 |
| **峰值 RSS** | **944MB** | 可用 1745MB |

CPU 完全不是問題——與 llama-server 搶 8 核的疑慮可以劃掉。
RSS 比純 pipeline 的 747MB 多約 200MB（STT+TTS 模型同時常駐），仍在安全範圍。

## 🔴 產品缺口：教材 prompt 沒有被注入

LLM 回應是「跟我說一遍：**我想要蘋果**。」——**目標句變成中文了**，
而且沒有先稱讚。對照 `server/llm.py:130`，真實的 user prompt 是：

```
學生剛剛說：「{student_text}」
目標英文句：{target}
{directive}
請照規則回覆：先一句繁體中文稱讚鼓勵，再用「跟我說一遍：<英文句>」帶讀目標英文句。
```

pipecat 的 context aggregator 直接把 `TranscriptionFrame` 當成 user message，
**沒有套用那個模板**，所以 LLM 拿不到目標英文句與教材 directive。

**這正是「可控」那一半價值要動手的地方**：接線時必須有一個 processor
（或自訂 aggregator）把 lesson／SRS／directive 注入 user message。
不做的話，pipecat 版的教學品質會比現行差——**這是目前最重要的接線待辦**。

## 📌 串流重疊取決於回應有幾句

這次 `tts_first_audio 5151ms ≈ llm_done 5152ms`，**沒有重疊**。
因為回應「跟我說一遍：我想要蘋果。」只有一個句子邊界，TTS 只能等整句完成。

先前四次有重疊的案例，回應都是**兩句**（「太棒了！…跟我說一遍：…」）。

**所以 first-audio 的改善不是無條件的**：回應越長、句子越多，重疊效益越大；
單句回應則退化成與現行架構相同。而教材 prompt 要求的正是「先稱讚再帶讀」＝兩句，
所以真實情境下應該會有重疊——但這要等 prompt 注入補上後才能確認。

## ⚠️ 這次 round_total 5152ms 不可直接與現行比

`stt_done` 是 2458ms，遠高於單獨量測的 147ms——因為本 probe 一次
`queue_frames` 送入全部音訊 frame，那 1.34s 的音訊要先流過 pipeline。
真實情境下音訊是即時到達的，孩子說完時 STT 只剩最後一段要算。

**這個數字是「鏈路能跑通」的證據，不是延遲基準。**延遲基準看第八輪的
`probe_pipecat_e2e`（round_total 3161–3423ms）。

---

# 第十輪 — 補上教材注入，教學品質恢復

## ✅ 缺口補上，而且串流重疊也跟著回來

`LessonPromptInjector` 攔 `TranscriptionFrame`，用
`server.llm.build_user_prompt`（**抽成共用函式，兩條路徑同一份模板**）
把逐字稿包成含目標句與教學策略的完整 prompt。

```
修復前：跟我說一遍：我想要蘋果。            ← 目標句掉成中文、沒稱讚
修復後：你真棒！跟我說一遍：I want an apple.  ← 先稱讚 + 正確英文目標句
```

而且 `tts_first_audio 5880ms < llm_done 6932ms`——**串流重疊回來了**，
正好印證上一輪的推論：教材要求「先稱讚再帶讀」＝兩句，真實情境會有重疊。
（代價是 prompt 變長，`llm_done` 從 5152ms → 6932ms。）

**原始逐字稿不會被蓋掉**：覆寫 `frame.text` 前先把原文放進 `frame.result`。
擺放順序有要求——要在「記錄逐字稿的處理器」之後、`agg.user()` 之前。

## ✅ 消掉一個自己造成的重複實作

發現 `server/guardrails.py:113` **早就有 `to_traditional`**（s2twp，docstring
還記著 2026-07-29「看到一只兔子」的實測背景）。我的 `OpenCCProcessor` 是
第二份實作。已改成委派給它——本模組只決定「在 pipeline 哪個位置、對哪種 frame
轉換」，轉換規則用既有那一份。

板子上跑真實 opencc 測試 **8 passed**（主機沒裝 opencc 會 skip 那一項，
這也是為什麼要在板子上再跑一次）。

## 🔴 還缺兩道既有系統有、pipecat 版沒有的護欄

`EdgeLLM.generate` 在輸出後做三件事，pipecat 版目前只補了第二件：

| 護欄 | pipecat 版 | 說明 |
|---|---|---|
| `passes_guardrail(text)` | ❌ **缺** | 安全過濾；不通過要降級回 scaffold |
| `to_traditional(text)` | ✅ 已補 | `OpenCCProcessor` |
| `ensure_readalong(text, target)` | ❌ **缺** | 確保回覆恰含一句合規帶讀（漏句要補、格式跑掉要修、不得重複） |

兩者都比 OpenCC 難接：

- **安全過濾必須在 TTS 之前**（不安全的內容不該先被念出來），但 LLM 輸出是
  串流 token，要在句子層級攔截；而且「不通過」時需要一個降級來源（scaffold 輸出）
- **`ensure_readalong` 針對整則回覆**，不是單句，所以要累積到
  `LLMFullResponseEndFrame` 才能判斷

累計 **61 測試全綠**（1 skipped：主機未裝 opencc；該項在板子上通過）。

---

# 第十一輪 — 補上兩道護欄，功能面補齊

## ✅ `SafetyGateProcessor`：句子級安全過濾，攔在 TTS 之前

**必須在 TTS 之前**——不安全的句子一旦合成、進了 aplay 緩衝，再攔沒有意義。

LLM 輸出是串流 token，逐 token 比對禁詞會兩頭落空（詞被切開比不中、單字誤判），
所以用 pipecat 內建的 `SimpleTextAggregator` 累積到句子邊界才檢查。副作用是下游
拿到整句而非 token——但 TTS 本來就要聚合成句子，**first-audio 不受影響**。

不通過時丟棄該回合**後續所有** LLM 文字（同一則出現一句不安全，後面的也不該信任），
改送 `fallback_text`（呼叫端應帶入 scaffold 的確定性回覆，行為才跟現行一致）。

沿用既有的保守策略：`passes_guardrail` 在安全模組不可用時回 False。代價是安全模組
壞掉時每輪都變罐頭回覆——**刻意的**，但會 log warning，因為「玩偶只會講同一句話」
這個症狀不看 log 猜不到原因。

## ✅ `ReadalongGuardProcessor`：確保帶讀了正確的目標句

累積整則回覆，在 `LLMFullResponseEndFrame` 用 `guardrails.ensure_readalong` 檢查。

**串流下只能補、不能改**（誠實限制）：

| 情況 | 能做什麼 |
|---|---|
| 已合規 | 什麼都不做（多數情況） |
| 漏了帶讀句 | ✅ 結尾補一句 |
| 帶讀了錯的句子／格式跑掉 | ⚠️ 只能**再補一句正確的**，錯的那句已經唸出去了 |

第三種情況孩子會聽到兩句帶讀（一錯一對）。**這是換取 first-audio 提前的代價。**
若哪天判定「寧可慢也不能唸錯」，做法是緩衝整則回覆再送 TTS——那會失去串流重疊，
是明確取捨，不是可以兩全的事。

## 🐛 修掉一個自己寫的 bug

`SimpleTextAggregator` **沒有 `reset()`**（只有 `handle_interruption`）。
我誤呼叫 `reset()`，它既不生效也不報錯——結果上一則被擋下的殘句留在緩衝裡，
把**下一則**回覆也一起汙染成不安全。單元測試抓到了（連續兩則都變 fallback）。
改成建新實例，不依賴內部 API。

## 板子端到端驗證：護欄沒有誤傷正常對話

```
LLM 原始輸出：你說的很好！跟我說一遍：I want an apple.
逐字稿(繁)　：你說的很好！跟我說一遍：I want an apple.
tts_first_audio 4870ms < llm_done 5303ms   ← 串流重疊仍在
CPU 16% │ RSS 925MB
```

沒有誤擋、沒有多補句子（回應本來就合規）、串流重疊沒有被護欄破壞。

累計 **73 測試全綠**（1 skipped：主機未裝 opencc，該項在板子通過）。

---

## 功能面盤點：與現行架構的差距已補齊

| `EdgeLLM.generate` 的護欄 | pipecat 版 |
|---|---|
| 教材 prompt（目標句／directive） | ✅ `LessonPromptInjector` |
| `passes_guardrail` | ✅ `SafetyGateProcessor` |
| `to_traditional` | ✅ `OpenCCProcessor`（委派 guardrails） |
| `ensure_readalong` | ✅ `ReadalongGuardProcessor`（串流限制已記錄） |

---

# 第十二輪 — 真機碰麥克風，抓到一個只有真機才會現形的 bug

使用者授權後，測了最後一個沒在真機跑過的元件 `alsa_transport`。

## ✅ 麥克風：讀得到、也還得回去

```
✅ 前置檢查：沒有 arecord 在跑
Recording raw data '-' : Signed 16 bit Little Endian, Rate 16000 Hz, Mono
收到 frame：143 │ 音訊 91520 bytes（預期 96000，95%）│ 取樣率 {16000}
首個 frame：開始後 146 ms
✅ 音訊持續流入
✅ 後置檢查：arecord 已終止，麥克風已釋放
```

`Aborted by signal Terminated` 正是 teardown 送 SIGTERM 的證據。
probe 自己做前後檢查（執行前有 arecord 就中止不介入、執行後殘留就強制收掉），
因為**沒還回麥克風的症狀跟按鍵故障、麥克風壞掉一模一樣**。

順帶更正一個先前的疏漏：`talkybuddy-local-client` 其實是 **active** 的
（前幾輪只檢查了 live-client 與 server），它按鍵時會用同一支麥克風。

## 🔴 aplay 取樣率寫死 — 只有真機會現形

輸出測試第一次跑就抓到：

```
Playing raw data 'stdin' : Rate 24000 Hz   ← aplay 用 24000
音訊長度：0.95s（41984 bytes @22050Hz）      ← 但音訊是 22050
```

`build_aplay_argv` **寫死 `DOWNLINK_RATE = 24000`**（Nova Sonic 的下行取樣率），
而邊緣 TTS 輸出 22050Hz。播放會**快 8.8%、音調偏高**——正是 `live_client`
docstring 開頭警告的「兩個取樣率混用會變怪腔怪調」，只是這次踩到的是我們自己。

**`aplay` 不會因為取樣率對不上而報錯**，argv 也「組得成功」，所以單元測試
完全看不出來。這是真機測試的價值最具體的一次體現。

修法：`build_arecord_argv` / `build_aplay_argv` 加上可選 `rate` 參數
（**預設仍是 16k/24k，既有呼叫端行為不變**），transport 一律傳入
`StartFrame` 協商出來的 `self._sample_rate`。修後重測：`Rate 22050 Hz` ✅

## ✅ 輸出：玩偶真的出聲

```
播放裝置：plughw:0,0（3.5mm Lineout）
音訊長度：0.95s │ 寫入成功／失敗：10 / 0
✅ 音訊全部寫入 aplay，且行程已正常結束
```

累計 **76 測試全綠**（1 skipped：主機未裝 opencc，該項在板子通過）。

---

## 全部元件都在真機驗證過了

| 元件 | 真機狀態 |
|---|---|
| `alsa_transport`（in／out） | ✅ 本輪 |
| VAD（官方 Silero） | ✅ 1.90ms/窗 |
| `sensevoice_stt` | ✅ 147ms，分段正確 |
| `edge_tts` | ✅ 即時率 0.25x |
| LLM（官方 OpenAI service） | ✅ 端到端 |
| 四道教學／安全護欄 | ✅ 端到端未誤傷 |

---

# 第十三輪 — 真人一輪：pipeline 就緒，但被斷線打斷

## ✅ 完整互動 pipeline 組好並能啟動

`edge/probes/probe_live_conversation.py`：

```
麥克風(arecord) → VADProcessor(Silero) → SenseVoice STT → 教材注入
   → llama-server → 安全閘門 → 帶讀護欄 → 邊緣 TTS → 簡轉繁 → 喇叭(aplay)
```

8 秒空跑驗證（無人說話）：

```
Recording raw data '-' : Rate 16000 Hz, Mono     ← 麥克風開了
Playing raw data 'stdin' : Rate 22050 Hz, Mono   ← 喇叭開了，取樣率正確
完成輪數 0（無人說話，正常）│ ✅ 麥克風與喇叭都已釋放
```

1.6.0 的 VAD 不掛在 `TransportParams` 上（那裡根本沒有 `vad_analyzer` 欄位），
而是獨立的 `pipecat.processors.audio.vad_processor.VADProcessor`。

## 🔴 真人一輪沒跑成：板子中途斷網

90 秒窗口啟動後板子斷線（與凌晨那次同一個症狀），ssh 被切，輸出檔全空。

**而且暴露了一個我自己的錯誤做法**：直接用前景 ssh 跑會碰硬體的 probe。
連線一斷，probe 的 `finally` 清理有沒有跑到無從得知——**殘留的 arecord 會佔住
麥克風**，讓 local-client 開不了錄音，症狀跟麥克風壞掉一模一樣。決賽當天出這種
事極難查。

已補 `edge/probes/run_live_conversation.sh`，三層保險：

1. `setsid` + `nohup`：脫離 ssh session，連線斷了不會被 SIGHUP 帶走
2. `timeout`：不管 probe 卡在哪，硬性上限一到就收掉
3. 收尾一律 `pkill arecord/aplay`：**不依賴 Python 的 finally 有沒有跑到**

用法：

```bash
ssh root@<板子> 'bash -s' < edge/probes/run_live_conversation.sh 90
# 之後
ssh root@<板子> 'cat /tmp/live_conversation.log'
```

## 仍未驗證

1. **真人對著麥克風講話的完整一輪** ← 就差這個
   - STT 對真人語音／環境噪音的表現（先前輸入全是 TTS 合成的乾淨音訊）
   - **會不會自我打斷**（喇叭與麥克風同在玩偶內，板子裝不了 AEC）。
     刻意先不加 mute 策略，想看真實行為；pipecat 有 `AlwaysUserMuteStrategy`
     可以當 half-duplex 閘門，確認會自我打斷再補
   - VAD 斷句的實際手感
2. **`fallback_text` 該接 scaffold** — 目前是預設字串，接線時要帶入
   `scaffold.reply_text` 才跟現行降級行為一致
3. **VAD params 未設** — 警告 `stop_secs (0.0s) differs from recommended 0.2s`

## 還沒有答案的問題

- **決賽現場 tunnel 的穩定性**：RTT 116ms 是可接受的，但 tunnel 斷掉的頻率沒有資料
- **pipecat 疊在 llama-server 之上的 CPU 爭用**：VAD 只吃 6% 即時率，但完整 pipeline
  （VAD+STT+TTS 同時跑）還沒量過，板子只有 1.7G 可用 RAM
- **最關鍵的那個沒變**：round_total 由 LLM 的 3.86s 主宰。pipecat 換的是編排，
  **不會讓那 3.86s 變快**。真正的槓桿仍然是把 LLM 換掉（雲端或 GPU），
  這一點從 `LATENCY_2.96S_EXPLAINED.md` 到現在都沒有改變。
