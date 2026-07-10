# 說說學伴 TalkyBuddy — A 軸 A2（互動/傳輸層）全雙工 barge-in 設計交接

> 日期：2026-07-07
> 用途：給接手 A 軸 A2（互動/傳輸層）的 session。定案：**線上＝雲端全雙工真 barge-in；離線＝降級 push-to-talk 半雙工**。本檔把 6 條研究 lane 的 findings ＋對抗式 verdict 收斂成可執行的設計交接。
> 母脈絡：先讀 `research/10_雙網混成語音方案總分析.md`（選型已定案，勿重推）。本檔只談 A2「怎麼聽、怎麼說」的傳輸/中斷層。
> 邊界：A1 喚醒層、B 軸（智慧/教學層、雙 Agent、記憶、CEFR，見 `research/11`）在別的 session，勿碰。

> **⚠️ 研究完整度（誠實標註，2026-07-07 二次更新）**：本檔 6 條研究 lane 中，**streaming-STT**、**streaming-TTS＋傳輸** 2 條產出完整且經對抗式驗證（信心高，**維持不動**）。**framework 框架選型**與 **AEC** 兩條原本掛空的硬題，已於本輪以**前景 general-purpose Agent 產出具名 prose 報告補齊**（繞開先前反覆失敗的 StructuredOutput schema）：framework 見 `research/13_A2_framework_bakeoff.md`（首選由推論的 LiveKit **翻案為 Pipecat**，BSD-2、原生內建 `FunASRSTTService`＝`SenseVoiceSmall`，信心升「中高」）；AEC 見 `research/14_A2_AEC.md`（PC 原型＝瀏覽器 AEC3、上板＝外掛 XMOS XVF3800，信心升「中高」，並更正「Genio 520 無板載 HiFi DSP」）。§2 第 1、2 列、§1 表、§4、§5、§6 #5、§7 #2/#3、§8 已同步更新。兩者最終定案仍以 **A2-1 spike 上機**為準。**offline 離線降級**、**VAD/turn** 2 條仍為綜合補寫（誠實保留註記）。

---

## 0. 為什麼 A2 可以獨立設計（交界只有一個點）

專案切成乾淨可獨立演進的軸，A2 與其他軸只有**單一交界契約**：

- **交界＝`pipeline._process_text()` 的「一輪 text-in → reply-out」語意。** B 軸依賴它，A2 **不得改寫這條線**。
- 全雙工不是去改 `_process_text`，而是在它**外面新增一個「串流輪管理器（StreamingTurnManager）」**：由管理器決定「何時算一輪講完、何時打斷、何時把整段文字丟進 `_process_text`」。`_process_text` 的簽章與內部語意保持不變。
- 離線降級路徑**直接重用**現有 `run_turn_audio` 批次管線（webm→ffmpeg→SenseVoice→scaffold→LLM→TTS），一行不動。

所以 A2 可以獨立 spec → plan → 實作，最後只在「呼叫 `_process_text` 的時機」這一點對接，不會與 B 軸衝突。

---

## 1. 全雙工願景 vs 現有 code（精準對照，已驗證 file:line）

現況 code 基準（2026-07-07）：

- 前端 `web/index.html`：`getUserMedia({ audio: true })`（`:626`，**未顯式指定 echoCancellation**，吃瀏覽器預設）→ `MediaRecorder`（`:653`，`audio/webm;codecs=opus`）→ `onstop` 時**整段** `ws.send(buf)` + `{"type":"audio_end"}`（`:674-675`）。**軟體半雙工**：`if (recording || busy || playingTTS) return`（`:646`）——播 TTS 時根本不能錄音。
- 後端 `server/app.py` `ws_talk`（`:187`）：binary frame 累積 → `audio_end` 觸發 `process_audio_buffer` → `pipeline.run_turn_audio`（整包）。
- `server/pipeline.py`：`_webm_to_wav`（ffmpeg 16kHz mono）→ SenseVoice 批次 ASR → scaffold → EdgeLLM（逾時降級）→ sherpa-onnx 整段 TTS。**半雙工＝`asyncio.Lock`（`:119`）**，重入 `emit {"type":"busy"}` 回 None（`:131`）。

| 全雙工願景模組 | 現有對應 | 狀態 / 缺口 |
|---|---|---|
| **免持連續擷取**（邊播邊聽） | `MediaRecorder` 整段錄、`onstop` 才送（`index.html:674`） | **缺**。整段上傳＝錄完才送，物理上無法在 TTS 播放中偵測插話。全雙工必須改連續串流擷取。 |
| **AEC 回音消除**（自體 TTS 不被當插話） | `getUserMedia({audio:true})`（`:626`），未顯式開 echoCancellation | **缺 → 已補研究（見 `research/14`）**。喇叭外放＋麥克風共處一機時回音對近端常 <-10dB，無 AEC 則 TTS 會誤觸發 barge-in。方案分階段定案：PC 原型＝瀏覽器 AEC3（須顯式開 `echoCancellation:true`＋TTS 經瀏覽器播放取得 far-end 參考）、上板＝外掛 XMOS XVF3800（Genio 520 無板載 DSP）。架構信心中高，**兒童繁中外放 double-talk 絕對數值待 A2-3 落地實測。** |
| **串流 VAD / turn 偵測** | 無；靠 `audio_end`/debounce 判斷輪結束 | **缺**。需外掛串流 VAD 偵測「開口→打斷」與「靜音→講完」。 |
| **串流 / 可打斷 STT** | SenseVoice **批次非串流**（已定案） | **不換引擎**。用 VAD 切段餵批次 SenseVoice；雲端串流 STT 僅條件式升級（見 §4）。 |
| **可中途中止的 TTS** | sherpa-onnx 整段合成回傳；client 播放中不可打斷 | **有底層能力、缺接線**。`OfflineTts.generate(callback)->int` 回非 0 即中止（本機驗證）；需句切＋client 靜音＋server flag。 |
| **雙向即時傳輸** | 單向請求/回應式 WS（整段上/整段下） | **缺**。全雙工需連續雙向音訊通道（傳輸框架待選型，見 §2 第 1 列）。 |
| **線上↔離線乾淨切換** | 無網路狀態機（research/10 §三為方向草圖） | **缺**。需狀態機把全雙工元件在 online 掛載、offline 乾淨卸載回 PTT。 |
| **半雙工單輪鎖** | `asyncio.Lock`（`pipeline.py:119`） | **保留給離線**。線上全雙工由串流輪管理器接管中斷語意，離線續用 Lock。 |

**一句話**：現有管線是「錄完→送→算→播」的請求回應式半雙工；全雙工要在它**外層**加一條「連續擷取＋VAD 打斷＋可中止 TTS」的串流迴路，而**內層轉文字與回覆生成的契約不動**。

---

## 2. 決策總表（六 lane，已吃進對抗式 verdict 的修正）

> **framework**：具名選型已於補研究交付（`research/13`），首選由推論候選 LiveKit **翻案為 Pipecat**（BSD-2、純 Python in-process、原生內建 `FunASRSTTService`＝`SenseVoiceSmall`，直接消滅「批次 SenseVoice 包進串流框架」的最大關卡），LiveKit 降為並列備援；信心升「中高」，最終以 A2-1 spike 為準。**AEC** 亦已交付（`research/14`）：PC 原型＝瀏覽器 AEC3、上板＝外掛 XMOS XVF3800（Genio 520 無板載 HiFi DSP），信心升「中高」。**VAD/turn** 一條仍為綜合補寫（信心「中」）。其餘 lane 信心已依 verdict 的牴觸/過時修正。

| # | 決策項 | 決策 | 信心 | 一句理由 |
|---|---|---|---|---|
| 1 | **語音傳輸框架** | 線上傳輸/中斷「水管」**定為 Pipecat（BSD-2）**，取代原推論候選 LiveKit（見 `research/13` 具名 bake-off）。決定性理由：Pipecat 純 Python **in-process** asyncio 管線（無常駐 daemon），且**上游已原生內建 `FunASRSTTService(SegmentedSTTService)`、預設 `iic/SenseVoiceSmall`**——「批次 SenseVoice 包進串流框架」由待驗證變**既成事實**；`LocalAudioTransport` 可免瀏覽器、mount/unmount 最輕。**腦不換**，Anthropic 直呼、sherpa 句級中止走 `InterruptibleTTSService`。**LiveKit（Apache-2.0）降為並列備援**（`StreamAdapter` 能乾淨包批次 STT，但需常駐 Go SFU、對單機殺雞用牛刀）；TEN／Vocode 剔除、自建保底（見剔除註記）。四項既定裁決逐項相容：①Anthropic 直呼、不引 LangGraph/AutoGen；②新增 `StreamingTurnManager`、不改 `_process_text`；③離線重用 `run_turn_audio`；④框架僅當水管、非 agent 編排。 | 中高（具名選型＋逐項來源已補齊；桌面＋原始碼查證，最終以 A2-1 spike 上機為準） | Pipecat 原生內建 SenseVoice 批次 STT＋純 Python in-process 最貼合單機離線玩偶，且完全相容四裁決；剩餘僅 spike 驗 sherpa 句級中止嵌入其 TTS 基類。 |
| 2 | **AEC 回音消除** | **線上全雙工強制項**，分兩條物理不同堆疊（見 `research/14`，不可共用一份實作）。**PC 原型（A2-3）＝瀏覽器 AEC3**：顯式開 `getUserMedia({audio:{echoCancellation:true}})`，**前提 TTS 必須經瀏覽器音訊輸出播放**（否則 AEC 拿不到 far-end 參考、形同未開）；AEC3 ERLE 典型 20–40dB，遠超 <-10dB 門檻。**上板（A2-6）＝外掛 XMOS XVF3800 硬體語音前端**（AEC＋beamforming＋NS，~US$50/顆）首選，次選 SoC CPU 跑 libwebrtc APM。**重要更正：Genio 520 本身無 HiFi audio DSP（同系列 510/700 才有 Cadence HiFi 5），原「板上 DSP 做 AEC」假設不成立。** 瀏覽器 AEC **不移轉**上板，兩階段僅共用 VAD 門檻調校經驗。離線 PTT 停播才收音，**不需 AEC**。 | 中高（三路線具名＋官方文件＋多來源交叉；絕對數值與兒童繁中外放 double-talk 未實測，屬 A2-3 必測） | 架構取捨與移轉缺口有具名產品＋官方文件佐證；double-talk「消回音 vs 不吃插話」的尖銳張力須靠 VAD＋能量差門檻＋真實兒童錄音在 A2-3 落地驗收。 |
| 3 | **VAD / turn 偵測** | 外掛**串流 VAD（Silero VAD 首選 / WebRTC energy VAD）**偵測「開口即打斷」與「靜音逾時＝講完」；語意「講完了」可選 **Smart Turn v2/v3（BSD-2、含中文、~99%）只借模型不引框架**。SenseVoice 維持批次、由 VAD 切段餵入。 | 中 | 原 lane 空殼；方向與 streaming-stt lane 一致故可信，但**無繁中 VAD 延遲/門檻實測**。研究/01「Smart Turn 用不上」條件化於半雙工，全雙工下**已過時**，不得照抄。 |
| 4 | **串流 STT** | 線上**預設不換引擎**：本地 VAD 管打斷＋**SenseVoice 批次短窗切句**；雲端串流 STT 僅**條件式升級**（實測延遲不可接受才做），首選 **Azure Speech zh-TW（獨立官方 locale）**，Google Chirp zh-TW 備援；**不採 AssemblyAI**（中文不在低延遲清單）、**不採 OpenAI Realtime**（整合式代理與 Anthropic 直呼衝突、貴一個量級）。 | 高（已簽核採納） | 核心洞見（VAD 管打斷、STT 維持批次）與裁決一致、反面否決有獨立查證；**使用者已簽核採納此分岔**（見 §7 #1）。 |
| 5 | **串流 TTS ＋播放中止** | **不換引擎**。`sherpa-onnx OfflineTts.generate(text, callback)->int` 回**非 0 即中途停止合成**（本機 docstring 一手驗證）；按標點把回覆切短句、逐句 `generate()` 串流播放，配 **client 端 <10ms 靜音 ＋ server flag 中止下一次 callback**。 | 中高 | 中止機制**一手驗證屬實**；但「端到端 200ms barge-in」為**工程目標、未實測**（非自迴歸模型單句幾乎一次前向合成完，callback 中止的即時效益主要在「句與句之間」），故自 high 下修。 |
| 6 | **離線降級** | **完全維持**現有整段 webm＋WS＋ffmpeg＋SenseVoice 批次＋`asyncio.Lock` 半雙工 PTT，**一行不動**。全雙工元件（傳輸框架/AEC/串流 VAD/可中止 TTS）**只在 online 路徑掛載**，斷網時乾淨卸載回這條既有管線。 | 高 | 重用既有已驗證管線，零新風險；A2 對此路徑只需定義「如何乾淨切回」，不重寫。 |

**剔除/降級註記（verdict 修正已吃進；框架取捨已由 `research/13` 具名選型定案，AEC 由 `research/14` 定案）：**

- ❌ **OpenAI Realtime 當 STT**：架構整合衝突＋成本高一量級，剔除。
- ❌ **AssemblyAI 串流**：繁中不在真正低延遲清單，剔除。
- ⚠️ **Google Chirp zh-TW**：僅單一開發者論壇軼事撐「region/模型不一致」，風險等級被 n=1 放大，列**備援待官方矩陣覆核**。
- ⚠️ **COPPA「2026-04-22 生效」**：verdict 已更正——該日是多數義務的 **compliance deadline，非生效日**（規則 2025-06-23 生效）；實質（voiceprint 首次納生物特徵、AI 訓練需額外家長同意）正確。
- ✅ **框架取捨（已由 `research/13` 具名選型定案）**：**Pipecat（BSD-2）升為首選**（原「本階段不引入」翻案）——原生內建 `FunASRSTTService`＝`SenseVoiceSmall`＋純 Python in-process 是決定性差異。**LiveKit（Apache-2.0）並列備援**。❌ **TEN 剔除**——`LICENSE` clause 1(i) 明文「不得 host 於 End User devices」恐直接禁止上板玩偶（比舊「Docker/Agora/Go 過重」更硬的法務理由），且 Rust+Go+Node+C++ 多 runtime 無 pip 路徑。❌ **Vocode 剔除**——OSS 停滯（last commit 2024-11-15、末版 v0.1.113、README 徵維護者）＋電話/IVR 導向＋不內建 AEC。**自建（aiortc）降為保底逃生路線**（僅 Pipecat/LiveKit 在 A2-1 spike 雙雙撞牆時啟用）。

---

## 3. 目標架構（線上全雙工 ＋ 離線降級 ＋ 網路狀態機）

```
                        ┌──────────────────────────────────────┐
                        │        網路狀態機 NetworkFSM           │
                        │  健康檢查 + hysteresis 防抖 + 手動 override │
                        └───────────────┬──────────────────────┘
             online (雲端可達)          │           offline (斷網/雲端逾時)
        ┌───────────────────────────────┘───────────────────────────────┐
        v                                                                v
╔══════════════════════════════════╗                    ╔══════════════════════════════╗
║   線上全雙工路徑（新增，A2 主戰場）  ║                    ║  離線降級路徑（重用既有，不動）  ║
╠══════════════════════════════════╣                    ╠══════════════════════════════╣
║ 前端: WebRTC 連續擷取             ║                    ║ 前端: MediaRecorder 整段錄      ║
║   getUserMedia{echoCancellation} ║                    ║   webm/opus → onstop 整段送     ║
║        │ (Opus, 瀏覽器 AEC/NS)    ║                    ║        │                        ║
║        v                         ║                    ║        v (WS binary + audio_end)║
║ ┌────────────────────────────┐   ║                    ║ pipeline.run_turn_audio        ║
║ │ 串流輪管理器 StreamingTurnMgr│  ║                    ║   webm→ffmpeg→SenseVoice(批次) ║
║ │ ┌────────┐  ┌─────────────┐│   ║                    ║   →scaffold→LLM(逾時降級)→TTS  ║
║ │ │串流 VAD │→│barge-in: 取消 ││  ║                    ║   asyncio.Lock 半雙工單輪      ║
║ │ │(Silero)│  │TTS+LLM task ││   ║                    ╚══════════════════════════════╝
║ │ └───┬────┘  └─────────────┘│   ║
║ │     │ 靜音逾時/turn 判定「講完」│  ║       ┌──────────── 乾淨切回規則 ────────────┐
║ │     v                      │   ║       │ online→offline:                     │
║ │  切段音訊 → SenseVoice 批次  │   ║       │  1. 中止進行中串流輪、關 VAD/AEC 掛載 │
║ │           (短窗解碼)         │   ║       │  2. 前端 WebRTC 降回 MediaRecorder   │
║ │     │ 整段 text              │   ║       │  3. 後端切回 run_turn_audio + Lock   │
║ │     v                      │   ║       │ offline→online: 反向，且需重建 AEC   │
║ │  ★ pipeline._process_text() │◄──╫───────┤    共用同一顆 pipeline._process_text │
║ │    (契約不變，僅呼叫時機改)   │   ║       └──────────────────────────────────┘
║ │     │ reply text            │   ║
║ │     v                      │   ║   雲端串流 STT（Azure zh-TW）＝條件式旁路：
║ │  句切 → sherpa-onnx 逐句合成  │   ║   僅當「等講完再批次解碼」總延遲實測不可接受、
║ │    generate(callback)->int  │   ║   且確認需要 partial 提早餵 LLM 時才接入，
║ │    ↑ barge-in 時回非0 中止   │   ║   取代上方「切段→SenseVoice 批次」一格。
║ │     │ 串流播放              │   ║
║ │     v client<10ms 靜音打斷   │   ║
║ └────────────────────────────┘   ║
║  傳輸層＝Pipecat（BSD-2、in-proc）  ║
║  原生 FunASRSTTService＝SenseVoice ║
║  LLM/TTS 包 plugin；LiveKit 備援    ║
╚══════════════════════════════════╝
```

**兩個關鍵不變量**：
1. `_process_text()` 是**兩條路徑共用的同一顆**「text-in → reply-out」，兩邊只是「餵它的方式」不同。
2. 全雙工的一切新增都在 `_process_text` **之外**（串流輪管理器、VAD、AEC、句切、可中止 TTS）。

---

## 4. 與現有 pipeline 的整合點（明確：新增而非改寫）

| 動作 | 具體做法 | 對既有契約的影響 |
|---|---|---|
| **不改** `_process_text` | 保留「一段 text → 一段 reply」簽章與語意 | 零影響，B 軸繼續依賴 |
| **新增** `StreamingTurnManager` | 擁有 VAD、barge-in 取消、輪邊界判定；拿到整輪最終文字後**一次**呼叫 `_process_text(text)` | 只是**改變呼叫時機**，非改內部 |
| **新增** 可中止串流 TTS 包裝 | 在 sherpa-onnx `generate()` 傳 `callback`，讀 server 端 `interrupt_flag`，回非 0 中止；LLM token 串流時做**句界偵測**（標點/子句）逐句餵 TTS | `_synth_tts`（`pipeline.py:243`）可抽出串流版本；不動批次版 |
| **新增** Pipecat STT/TTS plugin 殼 | Pipecat 原生 `FunASRSTTService`＝`SenseVoiceSmall` 直接吃批次 SenseVoice；句級 sherpa 包成 `InterruptibleTTSService`（框架已定 Pipecat，見 `research/13`） | **待 A2-1 spike 上機驗**（句級中止嵌入 TTS 基類）；不改 ASR/TTS 本體 |
| **重用** 離線批次管線 | offline 直接走 `run_turn_audio`＋`asyncio.Lock` | 完全不動 |
| **新增** `NetworkFSM` + 路由 | online 掛載串流輪管理器；offline 卸載回 `run_turn_audio`；含 hysteresis 與手動 override（demo 用） | 與 research/10 §三、A1 對齊 |

**barge-in 取消語意（server 端）**：VAD 偵測到使用者開口 → 設 `interrupt_flag` →（a）client 立即靜音（<10ms）；（b）下一次 TTS callback 回非 0 中止合成；（c）`asyncio.Task.cancel()` 取消進行中的 LLM 生成。這一整套是**串流輪管理器**的職責，`_process_text` 不感知。

---

## 5. A2 子專案拆解與建議實作排序（依風險／離 demo 距離）

> 原則同 research/11：一次只完整設計一個；把**最高未驗證風險先 spike**，再做離 demo 最近的體驗。

- **A2-0　網路狀態機骨架 + 乾淨切回**（低風險、地基）
  `NetworkFSM`（健康檢查＋hysteresis＋手動 override）＋ online/offline 路由開關；offline 直接復用現有 `run_turn_audio`。先讓「線上/離線切換」在現有半雙工上跑通，作為之後掛載全雙工的載體。與 A1 對齊「AEC/VAD 開關如何乾淨切回」。

- **A2-1　傳輸框架選型 ×（批次 SenseVoice ＋ 句級 sherpa）整合 spike**（**最高風險，必先驗**）
  **框架已定案 Pipecat**（`research/13`，LiveKit 為備援）。本 spike 從「選型」收斂為「上機驗證」：①確認 Pipecat 原生 `FunASRSTTService`（預設 `SenseVoiceSmall`）能否直接接本專案批次 SenseVoice；②**句級中止** sherpa 能否乾淨嵌入 `InterruptibleTTSService` 基類（`start_segment/end_segment/flush`＋`StartInterruptionFrame`）；③`LocalAudioTransport` 免瀏覽器裸麥路徑的 AEC 接線；④Genio 520 footprint——全程**不改** `_process_text`。若 Pipecat 在句級中止或 `LocalAudioTransport` 撞牆則切 LiveKit `StreamAdapter` 備援。**過關才展開後續。**

- **A2-2　串流輪管理器 + 可中止 TTS**（barge-in 核心體驗，離 demo 最近）
  Silero VAD 偵測開口即打斷 → 取消 TTS/LLM task；靜音逾時判輪結束 → 整段文字進 `_process_text`；LLM 串流句切 → sherpa 逐句合成 → client 收 barge-in 立即靜音。**這是「能打斷的玩偶」最有感的展示。**

- **A2-3　AEC 落地（瀏覽器 AEC3）+ 自體回授誤觸發實測**
  前端顯式開 `echoCancellation:true`**＋TTS 必經瀏覽器音訊輸出播放**（否則 AEC 拿不到 far-end 參考、形同未開），喇叭外放情境用**真實兒童錄音**實測「TTS 是否被麥克風收回誤觸發 barge-in」「童聲插話是否被 AEC 削掉（double-talk）」，調 VAD＋能量差門檻。AEC 架構已定案（`research/14`），此子項為**絕對數值落地實測**；上板硬體 AEC（XMOS XVF3800）另案（見 §6、A2-6）。

- **A2-4　線上全雙工 ↔ 離線 PTT 乾淨切換整合**
  把 A2-2 掛上 A2-0 狀態機，驗證斷網瞬間中止串流輪、降回 MediaRecorder＋`run_turn_audio`＋Lock，反向亦然。與 A1 喚醒層交界收尾。

- **A2-5　（條件式）雲端 Azure zh-TW 串流 STT 升級**
  **僅當** A2-2 端到端延遲實測不可接受、且確認需要 partial 提早餵 LLM 時才做；先談妥「不訓練/可刪除/限存取」條款。預設**不做**。

- **A2-6　（另案／上板）Genio 520 AEC + NPU 延遲實測**
  **注意：Genio 520 本身無板載 HiFi DSP**（`research/14`），上板 AEC 須**外掛 XMOS XVF3800 硬體前端**（首選）或 SoC CPU 跑 libwebrtc APM；三件套 NPU 端到端延遲。屬硬體階段，非 PC 原型當務之急，正式規格以 datasheet 覆核。

---

## 6. 誠實對照：通用/直覺建議 vs 本專案已驗證裁決（別照抄）

1. **「線上就得換成雲端串流 STT」** → **半對，且已定案**。無螢幕玩偶不需 partial 逐字稿顯示，barge-in 打斷由 **VAD** 提供、與 STT 是否串流**無關**。維持 SenseVoice 批次即可，雲端串流僅條件式升級。**使用者已簽核採納此分岔**（見 §7 #1），無須再攤開徵詢。
2. **「SenseVoice 非串流＝不能 barge-in」** → **錯**。外掛 VAD 切段即可打斷，不需換 ASR。SenseVoice「批次非串流」裁決成立，但別誤讀為「做不出 barge-in」。
3. **「全雙工要引 Pipecat/LiveKit 當編排框架」** → **只用其傳輸/中斷層**，agent 仍自呼 Anthropic，**不違反《不引 agent 編排框架》裁決**。腦不換、只換水管。（惟具名框架選型仍待有效研究重跑，見 §2 第 1 列。）
4. **research/01 舊裁決「Smart Turn 用不上、淘汰 Pipecat/LiveKit」** → **已過時**。該否決白紙黑字條件化於 push-to-talk 半雙工；全雙工前提翻轉後不成立。Smart Turn 已到 v2/v3（BSD-2、含中文），線上可「只借模型不引框架」。
5. **「瀏覽器原生 AEC 解決回授」** → **只對 PC 原型成立，且有前提**。瀏覽器 AEC3 須「顯式開 `echoCancellation:true`＋TTS 必經瀏覽器音訊輸出播放」才拿得到 far-end 參考。Genio 520 上板**不會自動移轉**（不跑 Chrome），且**新事實：Genio 520 本身無板載 HiFi audio DSP（510/700 才有 Cadence HiFi 5）**，故原「板上 DSP 做 AEC」假設不成立——上板須外掛 XMOS XVF3800 硬體前端或吃 CPU 跑 libwebrtc APM（見 `research/14`，建議以正式 datasheet 最終覆核）。
6. **「OpenAI Realtime 一站式最省事」** → **不採**。與已定案 Anthropic 直呼＋sherpa TTS 架構衝突，成本貴一個量級。
7. **「線上線下共用同一套打斷邏輯」** → **有張力**。若本地 VAD barge-in 離線也成立，會模糊「離線=PTT 半雙工」的乾淨切分；A2 須與 A1 界定：離線因 on-device 全鏈路資源限制**仍降級 PTT**，AEC/VAD 開關如何乾淨切回。

---

## 7. 開放問題與待驗證

1. **✅ 定案分岔（已簽核採納）**：使用者**已簽核採納**「線上維持 **SenseVoice 批次＋本地 VAD 管 barge-in**、雲端串流 STT 僅**條件式升級**」；原列為 settled fork 的「線上=走雲端串流做完整 barge-in」正式由此取代。論證（無螢幕不需 partial、barge-in 由 VAD 提供、與 STT 是否串流無關）成立。下游傳輸層以此為前提推進，**此項不再是待決問題**。
2. **✅ framework 具名選型已解**（見 `research/13`）：具名候選 × 9 維度＋逐項來源＋量化比較已完成，首選**翻案為 Pipecat（BSD-2）**、LiveKit 並列備援、TEN/Vocode 剔除、自建保底，四項既定裁決逐項相容。剩餘僅 **A2-1 spike 上機驗證**：sherpa 句級中止能否乾淨嵌入 Pipecat `InterruptibleTTSService` 基類、Genio 520 footprint、`LocalAudioTransport` 裸麥 AEC 接線。
3. **✅ AEC 專屬研究已交付**（見 `research/14`）：三路線具名比較、來源、數據、double-talk 專節、移轉缺口分階段建議全數補齊，PC 原型＝瀏覽器 AEC3／上板＝XMOS XVF3800 定案。剩餘僅 **A2-3 實機尾巴**：兒童繁中外放的 ERLE/VAD 門檻絕對數值須落地實測。**另更正一項既有事實：Genio 520 無板載 HiFi DSP**，上板 AEC 須外掛硬體或吃 CPU（原 §1／§6 #5「板上 XMOS 型 DSP」假設已改）。
4. **兒童語音上雲合規**：COPPA 2026（voiceprint 納生物特徵、AI 訓練需額外可驗證家長同意；注意 2026-04-22 是 compliance deadline 非生效日）僅美國方向性類比；**須查證台灣個資法、教育部校園 AI 工具規範、家長同意書格式**。雲端 STT 導入前須談妥「不用於訓練、可刪除、限縮存取」；自助方案（非企業合約）能否達成待確認。
5. **Genio 520 上板 AEC 與延遲**：硬體 XMOS 型 DSP vs 軟體 libwebrtc APM/speexdsp 的選擇；端到端 barge-in 延遲（VAD＋批次解碼＋LLM＋TTS 首音）在 NPU 上是否 <200ms / 可接受，須上板實測（現有延遲皆 PC x86）。
6. **批次 SenseVoice 包進串流傳輸框架 plugin 可行性**：多數串流框架 plugin 偏串流導向，包裹批次 ASR 未驗證——**A2-1 spike（含框架選型）為進場前置條件**。
7. **雲端串流 STT 繁中品質與計費**：Azure zh-TW streaming 每分鐘價格未核；Google Chirp zh-TW region/模型風險僅單一論壇軼事，須以官方支援矩陣覆核。
8. **VAD 靜音逾時門檻調校**：使用者長句夾長停頓（非真講完）時，如何不誤判「講完」而過早送不完整語句給 LLM；繁中適配待實測。
9. **聲學物理**：企鵝玩偶喇叭外放 vs 耳機、麥克風/喇叭實體距離與遮蔽（機殼設計）會決定 WebRTC AEC 之外是否還需實體聲學隔離。
10. **✅ 佈署拓撲已定案（使用者簽核 2026-07-08）**：線上全雙工**編排跑在裝置端**（Pipecat in-process，拓撲一），**非**雲端編排（拓撲二）。決定樞紐＝**「離線必須能降級對話」**——既然離線要對話就必須在裝置上保留 SenseVoice＋scaffold＋LLM＋sherpa 模型，雲端薄客戶端省不掉這些模型，故編排留裝置端、雲端只當**條件式旁路服務**（雲端 neural TTS／Azure zh-TW STT 被本機 Pipecat 呼叫，斷網乾淨卸載回裝置端降級對話）。TEN 雖在雲端佈署下授權**解禁**（`LICENSE` clause 1(i) 只禁 host 於 End User devices、雲端伺服器不受限），但雲端編排本身破壞雙網混成、對本專案淨損，故不採；**框架維持 Pipecat**。詳見 `research/15_A2_TEN_vs_Pipecat_雲端佈署.md`。自然情緒台灣聲需求由雲端 TTS 條件式旁路滿足（TTS 聲音選型另案）。殘留提醒：Genio 520 離線跑真 LLM 對話品質天花板有限（edge LLM 選型題，非 A2）＋Python 編排在板上的記憶體/CPU 開銷待 A2-6 上板實測。

---

## 8. 一行 kickoff（貼進未來接手 A2 的 session）

> 接手 TalkyBuddy 的 A 軸 A2（互動/傳輸層）。先讀 `research/12_A2_全雙工barge-in_設計交接.md`、`research/13`（framework bake-off）、`research/14`（AEC）與 `research/10`，然後用 brainstorming/office-hours 針對 **A2-1（Pipecat × 批次 SenseVoice ＋ 句級中止 sherpa 整合 spike，且不改 `pipeline._process_text` 契約）** 做完整設計：spec → plan。**兩條硬題已補齊定案**：framework＝**Pipecat（BSD-2、原生內建 `FunASRSTTService`＝`SenseVoiceSmall`）**、LiveKit 備援；AEC＝PC 原型瀏覽器 AEC3／上板 XMOS XVF3800（Genio 520 無板載 DSP）。**第一件事**＝完成 A2-1 spike（驗 sherpa 句級中止嵌入 Pipecat `InterruptibleTTSService`）。「線上維持 SenseVoice 批次、雲端串流 STT 僅條件式升級」**使用者已簽核採納，無須再徵詢**。全雙工一律用「新增串流輪管理器」而非改寫 `_process_text`；離線路徑重用既有 `run_turn_audio` 半雙工 PTT，不動。B 軸與 A1 喚醒層在別的 session，勿碰。
