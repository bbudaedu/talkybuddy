# 說說學伴 TalkyBuddy — A2 全雙工傳輸/中斷「水管」框架 bake-off（具名選型）

> 日期：2026-07-07
> 用途：補跑 `research/12` §2 第 1 列、§7 #2 標記為 **REJECT（佔位無效）** 的 framework 具名選型。前兩輪自動化流程交付純佔位（`"test"`），本檔以**具名候選＋逐項來源＋量化比較**重跑，取代之。
> 母脈絡：先讀 `research/10`（選型母脈絡）、`research/12`（A2 設計交接、四項既定裁決、A2-1 spike）。本檔只回答「線上全雙工的傳輸/中斷水管要用哪個具名框架」，不碰 B 軸、A1 喚醒層。
> 現況 code 基準（已驗 file:line）：`pipeline.py` `VoicePipeline`（`_process_text:179`、`run_turn_audio:125`、`_synth_tts:243`、`asyncio.Lock:119`）、`app.py` `ws_talk:187`、前端 `web/index.html` `getUserMedia:627`／`MediaRecorder:653`／整段送 `audio_end:675`。
> **信心標註誠實原則**：所有主張標來源；未實測者標「未驗證」。本檔為**桌面研究＋官方 repo/docs／原始碼查證**，最終定案仍須通過 A2-1 spike（見 §5）。

---

## 0. 一句話結論（具名首選 + 為何）

**首選＝Pipecat（BSD-2-Clause）。它從 `research/12` 推論候選 LiveKit 手中接掌首選。**

理由一句話：本專案是**單機、可離線、mount/unmount、批次 SenseVoice、句級中止 sherpa、腦自呼 Anthropic** 的 on-device 玩偶；Pipecat 是**純 Python in-process asyncio 管線（無常駐 daemon）**，且**上游已原生內建 `FunASRSTTService(SegmentedSTTService)`、預設 `model="iic/SenseVoiceSmall"`**——`research/12` §5「最大關卡：批次 STT 包進串流框架」在 Pipecat 是**既成事實、非待驗證**。LiveKit 一樣優秀且 Apache-2.0，但它的 WebRTC **SFU 是為多方網路串流而生**（`research/10` 已判「單機殺雞用牛刀」），需**常駐 Go server daemon**（mount/unmount 較重、雙程序），對單機玩偶是多付了一個不需要的傳輸層。**兩者皆完全相容四項既定裁決（僅當水管、腦仍自呼 Anthropic）**；差別在「對本專案形狀的貼合度」，Pipecat 勝。

> ⚠️ **LiveKit 是否仍是首選？→ 不是。** 換成 **Pipecat**。原因：①Pipecat 原生支援 SenseVoice 批次 STT（LiveKit 需用 `StreamAdapter` 自寫包裝，雖乾淨但要自己做）；②Pipecat in-process 純 Python，離線 mount/unmount 輕，LiveKit 需常駐 Go SFU；③本專案不需要 SFU 的多方串流能力。**LiveKit 降為強力次選／備援**（若日後要多裝置或瀏覽器多方接入再升級）。

---

## 1. 候選 × 9 維度 比較大表（2026-07 現況，半量化）

評分：✅ 強／佳、🟡＝可但有摩擦、⚠️＝明顯風險、❌＝不可行或出局級缺陷。括號為關鍵事實。

| # 維度 | **Pipecat** ⭐首選 | **LiveKit Agents** 次選 | TEN Framework | Vocode | 自建（aiortc/raw WebRTC） |
|---|---|---|---|---|---|
| **1 維護活躍度（2026-07）** | ✅ v1.5.0（2026-07-04），今日仍有 commit，近7天98 commits | ✅ `livekit-agents` 1.6.4（2026-06-24），362 releases，server v1.13.x | ✅ 0.11.67（2026-06-27），~10 rel/4月 | ❌ **停滯**：last commit 2024-11-15，末版 v0.1.113（2024-06），README 徵求維護者 | ✅ aiortc commit 到 2026-03、支援 Py3.14 |
| **2 授權（自架商用/上板）** | ✅ **BSD-2-Clause** | ✅ **Apache-2.0**（framework＋server）；⚠️ turn-detector 權重另屬「LiveKit Model License」 | ❌ **Apache-2.0＋Agora 附加條款**，clause 1(i)「不得 host 於 End User devices」——上板玩偶恐直接違約 | ✅ MIT | ✅ aiortc BSD/自寫碼自有 |
| **3 Python 阻抗／部署footprint** | ✅ **純 Python，in-process**，pip extras；無 Go/Docker/Agora 強制 | 🟡 SDK 純 Python，但**傳輸需常駐 Go SFU（獨立程序）**；單機 `--dev` 免 Redis | ❌ **多語言重棧**：Rust `tman`＋Go runtime＋Node18＋Py3.10（唯一）＋C++；無 pip 路徑 | 🟡 純 Python 但**電話/IVR 導向**、依賴肥（FastAPI＋Redis＋Twilio） | ⚠️ 純 Python 但**一切自寫**（狀態機/中斷/VAD 接線全自幹） |
| **4 barge-in／中斷狀態機成熟度** | ✅ 內建 VAD 驅動中斷；`InterruptibleTTSService`；⚠️部分 transport 邊角仍在修（#2460/#3985） | ✅ **一流**：`AgentSession` 內建中斷＋auto-VAD（Silero）＋Turn Detector v1（`v1-mini` 本地 ONNX） | ✅ 成熟但**要自己在 graph 接線**（TEN VAD＋TEN Turn Detection 節點） | 🟡 有 `InterruptibleWorker`/`interrupt_sensitivity`，但**半雙工、靠 ASR endpointing**、無內建 VAD | ❌ 無，全自寫 |
| **5 AEC／NS** | 🟡 **不內建**；瀏覽器 WebRTC 路徑吃 `getUserMedia` AEC；裸麥 SBC 須 BYO | 🟡 **假設 client 端 WebRTC AEC**；另有 `livekit-plugins-noise-cancellation`（Krisp）；裸麥 SBC 須 BYO | 🟡 **委派 Agora RTC／瀏覽器**；裸麥無 AEC | ❌ **無**，docs 明言用耳機或 Krisp | ❌ 無，全自寫（libwebrtc APM/speexdsp） |
| **6 串流傳輸（WebRTC/WS/Opus）** | ✅ **可換傳輸**：`SmallWebRTCTransport`(P2P)／`FastAPIWebSocketTransport`／`LocalAudioTransport`／`DailyTransport`／`LiveKitTransport`；Opus | ✅ **WebRTC SFU（Pion, Go）**、Opus、production 級 | 🟡 Agora RTC 或 WebSocket | 🟡 **電話（Twilio/Vonage）為主**＋WS；WebRTC 靠 LiveKit 外掛 | 🟡 raw WebRTC（Opus）自組，品質自負 |
| **7 批次 SenseVoice ＋ 句級 sherpa 包 plugin（最大關卡）** | ✅✅ **已原生內建** `FunASRSTTService(SegmentedSTTService)` 預設 `iic/SenseVoiceSmall`；TTS 走 `run_stt`/`InterruptibleTTSService` 句級 | ✅ **`stt.StreamAdapter(stt,vad)`** 包非串流 `recognize()`（`STTCapabilities(streaming=False)`）；TTS 亦有 `StreamAdapter`＋句 tokenizer | 🟡 可但**逆流**：streaming graph 內自建 buffer/VAD 分段再批次推論 | 🟡 可但逆流：`BaseThreadAsyncTranscriber._run_loop` 內自 buffer＋自寫 endpointing | ⚠️ 全自寫（此關卡＝自建的全部工作量） |
| **8 ARM/Genio 520 上板** | 🟡 純 Python 好搬；重點在模型 footprint（未對 Genio 實測） | 🟡 Go server 多架 Docker（arm64）＋Py worker；模型 footprint 為主（arm64 binary 未證） | ⚠️ 多 runtime 可 build arm64 但肥；**且 clause 1(i) 恐禁上板** | 🟡 純 Python 可搬但無官方 ARM 佐證 | 🟡 aiortc 可 ARM；但全鏈自寫工作量大 |
| **9 離線乾淨 mount/unmount** | ✅ **最輕**：in-process `PipelineRunner`/`PipelineTask`，`cancel()` 拆管；模型 warm 著只切 transport | 🟡 需**常駐 Go daemon**；agent 進出 room；非 in-process 物件，較重 | ⚠️ 無 offline/local 模式文件；要維兩張 graph 切換 | 🟡 `start()/terminate()` 單 session 乾淨；無 hot 切換 | ✅ 自寫＝完全自控（但要自己寫對） |

**來源（維度表）**：Pipecat 官方 repo `github.com/pipecat-ai/pipecat`（`src/pipecat/services/funasr/stt.py`、`stt_service.py`、GitHub releases/commits API）、`docs.pipecat.ai`；LiveKit `github.com/livekit/agents`（`stt/stt.py`、`stt/stream_adapter.py`、`tts/tts.py`）、`github.com/livekit/livekit`、`docs.livekit.io`、PyPI `livekit-agents`；TEN `github.com/TEN-framework/ten-framework`（releases、`LICENSE` raw）、`theten.ai/docs`、DeepWiki；Vocode `github.com/vocodedev/vocode-core`（commits/releases API、`pyproject.toml`、`streaming_conversation.py`）、`docs.vocode.dev`；aiortc `github.com/aiortc/aiortc`、`aiortc.readthedocs.io`、PyPI。

---

## 2. 逐候選具名評述（含來源）

### 2.1 ⭐ Pipecat — 首選（BSD-2-Clause）

Pipecat 由 Daily.co 開源，2026-07 極度活躍：最新 **v1.5.0（2026-07-04）**，1.0.0 於 2026-04-14，近乎雙週一版，last commit 為今日（2026-07-07），近 7 天 98 commits、13k+ stars、未 archived（來源：GitHub releases/commits API）。授權 **BSD-2-Clause**，自架商用與上板零阻礙（GitHub license API）。它是**純 Python（98%+）in-process asyncio 管線**，透過 pip extras 模組化安裝（`pipecat-ai[funasr]`、`[webrtc]`、`[websocket]`），**無 Go/Docker/Agora 任何強制依賴**；傳輸可換：`SmallWebRTCTransport`（serverless P2P WebRTC）、`FastAPIWebSocketTransport`、`LocalAudioTransport`（本機麥克風/喇叭、免網路、免瀏覽器），皆走 Opus（來源：`docs.pipecat.ai` supported-services、reference-server transports）。

**決定性事實（直接消滅 `research/12` 的最大關卡）**：Pipecat 上游**已內建** `src/pipecat/services/funasr/stt.py` 的 `class FunASRSTTService(SegmentedSTTService)`，**預設 `model="iic/SenseVoiceSmall"`**，本地載入 FunASR，`run_stt(audio: bytes)` 對 VAD 分段後的 buffer 呼叫 `self._model.generate(...)` 並 yield 一段整段轉錄；另附 `tests/test_funasr_stt.py`。批次 ASR 是 `SegmentedSTTService` 的**一等公民模式非 hack**：VAD 判使用者停話→整段交 `run_stt`→`push_frame` 自動 finalize。sherpa 句級中止 TTS 走 `STTService`/`InterruptibleTTSService` 基類、以 `TTSSpeakFrame`＋`StartInterruptionFrame` 中止（來源：官方 repo 原始碼、GitHub code API）。barge-in 為 v1.x 內建、VAD 驅動、自動，惟部分 transport 邊角仍在修（open issues #2460 FastAPIWebSocket 中斷、#3985 TTS graceful release）。Smart Turn v2/v3 由 pipecat-ai 擁有（`LocalSmartTurnAnalyzerV3`、含中文 zh、可本地推論，v0.0.77+；來源：`docs.pipecat.ai/.../smart-turn`、Daily blog），正對應 `research/12` §2 第 3 列「只借模型」策略。**AEC 不內建**（WebRTC 路徑吃瀏覽器；裸麥 SBC 須 BYO——與所有候選同）。mount/unmount 最輕：in-process `PipelineTask`/`PipelineRunner`，`StartFrame`/`EndFrame`/`cancel()`，模型 warm 著只切 transport（來源：docs pipeline guide、`funasr/stt.py` 於 `__init__` 載模型）。

### 2.2 LiveKit Agents — 強力次選／備援（Apache-2.0）

`research/12` 的推論首選，實測仍是**極強、極活躍**的候選，但被 Pipecat 在「本專案形狀」上超車。Agents 1.0 GA（2025-04），最新 `livekit-agents` **1.6.4（2026-06-24）**、362 releases、Python >=3.10,<3.15；server `github.com/livekit/livekit` **Apache-2.0、Go/Pion、可單機自架、單節點免 Redis**（來源：PyPI、官方 repo、docs self-hosting）。barge-in/turn detection 是其強項：`AgentSession` 內建中斷、auto-provision Silero VAD、**Turn Detector v1** 的 `v1-mini` 為**本地 CPU ONNX**（bundled 於 1.6.1，<500MB RAM、~50–160ms；來源：docs turn-detector、LiveKit blog）。

**批次 SenseVoice 包裝：乾淨、有解、但要自己做。** LiveKit `stt.STT` 基類同時有非串流 `recognize()`（實作 `_recognize_impl()`、宣告 `STTCapabilities(streaming=False)`）與串流 `stream()`；**`stt.StreamAdapter(stt, vad)`** 正是把非串流 STT＋VAD 包成串流介面的官方機制：VAD `END_OF_SPEECH`→`utils.merge_frames`→呼叫 wrapped STT 的 `recognize()`→emit `FINAL_TRANSCRIPT`。官方 `livekit-plugins-mistralai` 即以 `streaming=False` 做批次示範。TTS 端亦有 `StreamAdapter`＋句 tokenizer 提供句級分段（來源：官方原始碼 `stt/stream_adapter.py`、`tts/tts.py`、docs models/stt、issue #2930）。

**為何降為次選（三個對本專案的摩擦）**：①**傳輸永遠要一個常駐 Go SFU 程序**——不是 in-process 函式呼叫，離線 mount/unmount 是「管理長生命 daemon＋worker 進出 room」，比 Pipecat 的 in-process 物件重；②**turn-detector 權重屬「LiveKit Model License」非 Apache**（程式碼 plugin 仍 Apache，但上板前要讀模型授權）；③**AEC 假設在 client 端 WebRTC**——裸麥 SBC 無瀏覽器時要 BYO（與 Pipecat 同缺，但 Pipecat 有 `LocalAudioTransport` 免瀏覽器路徑，接自寫 AEC 較順）。SFU 的多方網路串流強項**正是本單機玩偶不需要的**（`research/10` §二早判「單機殺雞用牛刀」）。**未驗證**：Genio 520 的 arm64 release binary 實體、單 session 的 Go server RAM/CPU 數字、TTS 中斷內部（由方法名 `start_segment/end_segment/flush` 推得）。

### 2.3 TEN Framework — 剔除（授權＋過重）

2026 仍非常活躍（**0.11.67, 2026-06-27**，~10 rel/4月）、全雙工/turn-detection/VAD 是其旗艦強項、arm64 官方列「Fully supported」（來源：releases、DeepWiki、README）。**但對 on-device 玩偶是錯的形狀，且恐授權禁止**：授權為 **Apache-2.0＋Agora 附加條款**，`LICENSE`（raw，© 2025 Agora）clause 1(i) 明文「**You may not host the TEN Framework … on any End User devices, including … mobile terminal devices**」——上板到使用者手中的 Genio 520 玩偶**恰好可能就是被禁的 End User device**，這是**法務級剔除理由**（比 `research/12` 舊的「Docker/Agora/Go 過度工程」更硬）。技術上亦重：核心非 Python，需 Rust `tman`＋Go runtime＋Node18＋**Python 3.10 為唯一支援版**＋C++，**無 pip 安裝路徑**；批次 SenseVoice 需在 streaming graph 內自建 buffer/VAD 逆流包裝；無 offline/local 模式文件（來源：DeepWiki getting-started、extension dev doc、Plivo 比較）。**剔除。**

### 2.4 Vocode — 剔除（停滯＋錯形狀）

**維護是決定性剔除點**：`main` last commit **2024-11-15**、末穩定版 **v0.1.113（2024-06-18）**、2025/2026 零 commit（~20 個月靜默）、README 明文「actively looking for community maintainers」（來源：GitHub API `repos/vocodedev/vocode-core`、releases、PyPI）——與「公司轉託管、OSS 停擺」的訊號一致。授權 MIT、純 Python，但**電話/IVR 導向**（FastAPI＋Redis＋Twilio/Vonage 為核心，WebRTC 只是 LiveKit 外掛）；barge-in 相對成熟（`InterruptibleWorker`、`interrupt_sensitivity`、backchannel 偵測）但**半雙工、靠 ASR endpointing、無內建 VAD**；**完全不內建 AEC/NS**（docs 明言用耳機或 Krisp）；批次 SenseVoice 要在 `BaseThreadAsyncTranscriber._run_loop` 內自 buffer＋自寫 endpointing（逆流）（來源：官方 repo 原始碼、`docs.vocode.dev`）。技術即使可用，**不維護＝上板風險不可接受。剔除。**

### 2.5 自建（aiortc / raw WebRTC + 自寫串流輪管理器）— 降級為「LiveKit/Pipecat 皆不可行時的保底」

aiortc 本身健康（commit 到 2026-03、支援 Python 3.14、asyncio 原生；來源：GitHub、readthedocs changelog、PyPI），純 Python、授權乾淨、**完全自控**（離線 mount/unmount 100% 自己說了算）。但「自建」的代價**正是被剔除框架幫你做掉的一切**：barge-in 中斷狀態機、VAD 接線、句界偵測、AEC 整合、傳輸協商、可中止 TTS callback 串接——**全部自寫**。這與 `research/12` §5 A2-1「先 spike 最高風險」相悖：自建把最高風險攤成一整條自研工程。**結論**：既然 Pipecat 已把「批次 SenseVoice＋句級 TTS＋可換傳輸＋內建中斷」現成給齊，自建**沒有理由當首選**；僅保留為「Pipecat/LiveKit 在 A2-1 spike 雙雙撞牆（例如 sherpa 句級中止無法乾淨嵌入其 TTS 基類）」時的**保底逃生路線**。

---

## 3. 明確「剔除／降級」理由（逐項）

| 候選 | 判定 | 一句理由（帶最硬證據） |
|---|---|---|
| **Pipecat** | ✅ **首選** | 原生內建 `FunASRSTTService`（預設 SenseVoiceSmall）＋純 Python in-process＋BSD-2＋輕 mount/unmount，最貼合單機離線玩偶且相容四裁決。 |
| **LiveKit Agents** | 🟢 **次選／備援** | 一樣 Apache-2.0 且 `StreamAdapter` 能乾淨包批次 STT，但需常駐 Go SFU（單機殺雞用牛刀）、turn-detector 權重非 Apache；多方串流強項本專案用不到。 |
| **TEN Framework** | ❌ **剔除** | `LICENSE` clause 1(i) 恐**禁止 host 於 End User devices**（上板玩偶）＋Rust/Go/Node/C++ 多 runtime 無 pip 路徑，過重且法務有雷。 |
| **Vocode** | ❌ **剔除** | OSS **停滯**（last commit 2024-11-15、徵求維護者）＋電話導向＋無 AEC，上板不可接受。 |
| **自建 aiortc** | ⬇️ **降級為保底** | 技術可行且全自控，但把最高風險攤成整條自研工程；有 Pipecat 現成即無理由當首選，僅留作 spike 撞牆逃生。 |

---

## 4. 與四項既定裁決的相容性檢核（首選 Pipecat）

| 既定裁決（`research/12`） | Pipecat 是否相容 | 依據 |
|---|---|---|
| ①雲端直呼 Anthropic Messages API、**不引** LangGraph/AutoGen | ✅ | Pipecat 只當傳輸/中斷水管；LLM 節點可為自寫，腦仍自呼 Anthropic（`_process_text` 內既有 urllib 直呼不動）。Pipecat 非 agent 編排框架。 |
| ②全雙工用**新增** `StreamingTurnManager`、**不改** `_process_text()` 契約 | ✅ | Pipecat 的 `FrameProcessor`/`PipelineTask` 在 `_process_text` **之外**組管線；輪邊界判定完成後**一次**呼叫既有 `_process_text(text)`，簽章語意不動。 |
| ③離線降級**完全重用** `run_turn_audio` 半雙工、一行不動 | ✅ | Pipecat in-process、輕 unmount：offline 時 `cancel()` 拆掉 Pipecat 管線，路由切回既有 `run_turn_audio`＋`asyncio.Lock`，既有碼零改。 |
| ④框架只當**傳輸/中斷水管**、非 agent 編排 | ✅ | 僅用 Pipecat 的 transport＋VAD/中斷＋STT/TTS service 殼；不使用其任何 agent/LLM 編排語意。 |

> LiveKit 亦逐項相容（同表邏輯），差別僅在 mount/unmount 需管 Go daemon（③較重但仍可行）。**故無論最終 spike 選 Pipecat 或 LiveKit，四裁決都不被牴觸。**

---

## 5. 最終推薦 + 風險 + 必過的 A2-1 spike 驗證項

**推薦**：以 **Pipecat 為 A2-1 spike 的主受測框架**，**LiveKit 為並列備援**（若 Pipecat 在句級中止 sherpa 或 `LocalAudioTransport` 上撞牆則切 LiveKit `StreamAdapter` 路線）；自建僅保底。定案以 spike 結果為準（本檔為桌面研究，非上機實測）。

**風險（誠實）**：
1. **AEC 在裸麥 SBC 須 BYO**（所有候選皆同，非 Pipecat 特有）——`research/12` §2 第 2 列 AEC lane 仍待補，Genio 520 上板需硬體 DSP 或 libwebrtc APM，瀏覽器 AEC 不自動移轉。**未驗證。**
2. **sherpa-onnx 句級中止能否乾淨嵌入 Pipecat `InterruptibleTTSService`**——`OfflineTts.generate(callback)->int` 回非 0 中止為本機已驗；但嵌入 Pipecat TTS 基類的 frame/interruption 生命週期**未驗證**，是本 spike 核心。
3. **Genio 520 footprint**——Pipecat 純 Python 好搬，但 SenseVoiceSmall＋sherpa＋VAD 三件套在 NPU 端到端延遲**無 Genio 實測**（`research/10`/`12` 既有註記）。**未驗證。**
4. **Pipecat 部分 transport 的 barge-in 邊角** issue（#2460/#3985）——須在所選 transport 上實測 barge-in。

**A2-1 spike 必過驗收項（沿用 `research/12` §5，具名化為 Pipecat）**：
- [ ] 用 `pipecat-ai[funasr]` 的 `FunASRSTTService`（`iic/SenseVoiceSmall`）在 `SmallWebRTCTransport` 或 `LocalAudioTransport` 上，能把一段語音批次轉出整段文字。
- [ ] 該整段文字**一次**餵進**未改動**的 `pipeline._process_text(text)`，取得 reply（證明契約不動）。
- [ ] reply 以句界切段，逐句經 sherpa-onnx `generate(callback)` 串流播放；VAD 偵測使用者開口→`interrupt_flag`→下一次 callback 回非 0 中止＋client <10ms 靜音（證明句級 barge-in）。
- [ ] 斷網事件觸發 Pipecat 管線 `cancel()` 拆除、路由切回既有 `run_turn_audio`＋`Lock`，既有碼零改（證明離線乾淨卸載）。
- [ ] 上四項任一在 Pipecat 撞牆，改用 LiveKit `StreamAdapter(SenseVoiceSTT, silero.VAD)` 重跑同劇本比較。

**過關才展開 A2-2（串流輪管理器＋可中止 TTS）。**

---

## 6. 來源彙整（主張佐證）

- **Pipecat**：官方 repo `github.com/pipecat-ai/pipecat`（`src/pipecat/services/funasr/stt.py`＝`FunASRSTTService(SegmentedSTTService)` 預設 SenseVoiceSmall、`src/pipecat/services/stt_service.py`、`src/pipecat/processors/frame_processor.py`、`tests/test_funasr_stt.py`、GitHub releases/commits/license API）、`docs.pipecat.ai`（supported-services、pipeline guide、smart-turn）、Daily blog `daily.co/blog/smart-turn-v2-...`、`huggingface.co/pipecat-ai/smart-turn-v3`。
- **LiveKit**：`github.com/livekit/agents`（`livekit-agents/livekit/agents/stt/stt.py`＋`stt/stream_adapter.py`＝`StreamAdapter`/`_recognize_impl`/`STTCapabilities(streaming=False)`、`tts/tts.py`）、`github.com/livekit/livekit`（server Apache-2.0/Go）、`docs.livekit.io`（models/stt、turn-detector、self-hosting、noise-cancellation）、`livekit.com/blog/solving-end-of-turn-detection`、PyPI `livekit-agents`、issue #2930。
- **TEN**：`github.com/TEN-framework/ten-framework`（releases、`LICENSE` raw ＝ Apache-2.0＋Agora 附加、clause 1(i)）、`theten.ai/docs`（extension dev）、DeepWiki getting-started、Plivo `plivo.com/blog/...livekit-pipecat-ten...`、`github.com/TEN-framework/ten-turn-detection`、`ten-vad`。
- **Vocode**：`github.com/vocodedev/vocode-core`（commits/releases API＝last commit 2024-11-15、`pyproject.toml`、`streaming_conversation.py`、`base_transcriber.py`、`base_synthesizer.py`）、PyPI `vocode` v0.1.113、`docs.vocode.dev` python-quickstart。
- **aiortc（自建）**：`github.com/aiortc/aiortc`、`aiortc.readthedocs.io/en/latest/changelog.html`（Py3.14）、PyPI `aiortc`。

> **信心**：候選的維護/授權/原始碼類別事實＝**高**（官方 repo/API/原始碼直查）；「Pipecat 對本專案最貼合」的**選型判斷＝中高**（證據紮實但未上機）；sherpa 句級中止嵌入、Genio 520 footprint、裸麥 AEC＝**未驗證，待 A2-1 spike/上板**。
