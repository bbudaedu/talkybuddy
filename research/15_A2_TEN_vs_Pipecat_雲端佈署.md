# 說說學伴 TalkyBuddy — A2 補研究：雲端編排下 TEN Framework 是否翻盤勝過 Pipecat？

> 日期：2026-07-07
> 用途：補研究，回應一個具體提問——**若把線上全雙工「編排」改成跑在雲端伺服器（拓撲二），而非裝置端（拓撲一），`research/13` 因「裝置端」兩理由剔除 TEN 的判定是否翻盤？TEN 是否反而比 Pipecat 更適合？**
> 母脈絡：先讀 `research/13`（framework bake-off，Pipecat 首選、TEN 剔除）、`research/12`（A2 設計交接、四裁決、拓撲）、`research/10`（雙網混成母脈絡）。本檔只回答「雲端編排下 TEN vs Pipecat」，不重跑其他候選、不碰 A1／B 軸。
> 現況 code 基準：`server/pipeline.py`（`_process_text:188`、`run_turn_audio:134`、`network_mode:120` `edge|cloud`）、`server/app.py`（`ws_talk:208` 本地 WS server）。
> **誠實原則**：所有主張標來源；LICENSE 條文引原文；未實測者標「未驗證」。本檔為桌面研究＋官方 repo/docs／LICENSE 原文查證，非上機。

---

## 0. 一句話結論（分兩層，務必分開讀）

**第一層（單就「雲端佈署」這個維度）**：雲端解除了 `research/13` 剔除 TEN 的**兩個理由中較硬的那一個**——LICENSE clause 1(i)「不得 host 於 End User devices」對雲端伺服器（非 End User device）**不適用**，且 TEN 本就是**為雲端 real-time 而生**（Docker/K8s/Cloud Run、RTC/WS/SIP 傳輸、原生全雙工 turn-detection、水平擴容）。所以「TEN 在雲端不可行」的說法**被推翻**，TEN 從「剔除」升為「雲端下的合格候選」。**但「合格」不等於「翻盤勝出」**：即使純看雲端，TEN 仍**沒有明確勝過 Pipecat**——它仍拖著 Rust+Go+Node+C++ 多 runtime 的肥映像、綁 Agora（付費 RTC＋competitive clause）、且**它本身就是 agent 編排框架（graph＋extension），會擠壓四裁決 ④「框架只當水管」**。Pipecat 在雲端一樣有 GA 的 **Pipecat Cloud**（容器化、autoscaling、warm pool），純 Python 薄映像反而是雲端優勢，且原生 `FunASRSTTService` 仍在。**故第一層結論：TEN 在雲端「翻案為可行」，但「未翻盤」——Pipecat 仍小幅領先。**

**第二層（把雙網混成／離線降級一起算）**：拓撲二（雲端編排）**破壞**本專案雙網混成的核心優雅性。編排一旦搬上雲，斷網時整條線上路徑連同編排邏輯一起消失，離線只能走一套**完全獨立的 on-device 系統**（既有 `run_turn_audio`＋sherpa 半雙工）——雙網混成退化成「**兩套毫無共用的系統**」，違背 `research/12` 拓撲一「線上全雙工元件在 online 掛載、offline 乾淨卸載回同一顆 `_process_text`」的設計。拓撲一（Pipecat 裝置端 in-process）能讓線上／離線**共用同一顆裝置端 `_process_text`、共用同一台機器**，斷網只是 `cancel()` 拆管、路由切回，這正是雙網混成最省成本的形狀。**故第二層結論：一旦納入離線降級，天平明顯倒回 Pipecat 裝置端編排。**

> **合併裁定**：**維持 Pipecat 裝置端編排（拓撲一）**。TEN 的雲端翻案為真，但不足以推翻 Pipecat；而雲端編排本身（不論用誰）對雙網混成是淨損。A2-1 spike **不需改動**。

---

## 1. 兩個拓撲的定義（先釘清楚，避免混淆）

| | **拓撲一＝裝置端編排**（`research/12` 既定） | **拓撲二＝雲端編排**（本次提問） |
|---|---|---|
| 編排跑在哪 | 玩偶／Genio 520 **本機 in-process**（Pipecat asyncio 管線） | **雲端伺服器**（玩偶只把音訊串上雲，框架在雲端跑管線） |
| 音訊流 | 麥克風→本機 VAD/STT/LLM/TTS→喇叭 | 麥克風→(RTC/WS 上雲)→雲端 VAD/STT/LLM/TTS→(下行)→喇叭 |
| 線上 `_process_text` | **裝置端本機**這一顆 | **雲端**那一顆（裝置端另留一顆給離線） |
| 離線降級 | 同一台機、`cancel()` 拆管、切回 `run_turn_audio` | 雲端路徑整條消失，落回**獨立**的 on-device fallback |
| 傳輸依賴 | 無強制（`LocalAudioTransport` 免網） | **強制**一條雲端 real-time 音訊通道（RTC/WS） |

> **關鍵澄清**：框架選型（TEN vs Pipecat）與拓撲選型（雲端 vs 裝置）**是兩件事**。Pipecat 也能跑在雲端（Pipecat Cloud／自架容器），TEN 也宣稱能跑 ESP32 邊緣。本次提問把兩件事綁在一起問（「改雲端編排是否讓 TEN 勝出」），所以下面**先分開評、再合起來裁**。

---

## 2. TEN vs Pipecat（雲端佈署）× 各維度比較大表

評分：✅ 強／佳、🟡 可但有摩擦、⚠️ 明顯風險、❌ 不可行或裁決級缺陷。

| # 維度 | **TEN Framework（雲端）** | **Pipecat（雲端）** | 勝方 |
|---|---|---|---|
| **1 LICENSE 於雲端** | ✅ clause 1(i)「不得 host 於 **End User devices**」對雲端伺服器**不適用**（見 §3）；但 clause 1(ii) 禁「與 Agora offerings 競爭」仍在 | ✅ BSD-2-Clause，雲端零限制 | 平（TEN 解禁但仍帶 Agora 附加條款陰影） |
| **2 是否為雲端 real-time 而生** | ✅✅ **本來就是**：TEN Agent 是 Agora 的雲端 real-time 產品，Docker/K8s/Cloud Run/ECS/Fly 皆同碼跑；RTC＋WS＋SIP 傳輸、congestion control、自動 fallback | ✅ 可佈雲：**Pipecat Cloud GA**（2025）容器化、autoscaling、warm pool；自架亦可 K8s HPA | 🟡 TEN 略勝（雲端 real-time 是其原生強項） |
| **3 全雙工／turn-detection／VAD** | ✅✅ TEN Turn Detection＋TEN VAD 原生全雙工、旗艦強項 | ✅ VAD 驅動中斷＋Smart Turn v2/v3（本地 ONNX、含中文）；barge-in 內建 | 🟡 TEN 略勝（成熟度），Pipecat 足用 |
| **4 水平擴容／多 session** | ✅ 每 agent 一 graph instance，容器化水平擴；Agora RTC 扛傳輸擴展 | ✅ Pipecat Cloud warm pool（beta 上限 50/deployment）＋autoscale buffer；自架 K8s 無此上限 | 平 |
| **5 傳輸與計費** | ⚠️ **綁 Agora RTC**：$0.99/1k 音訊分鐘（~$0.001/min）、10k min/月免費；但 **Conversational AI 另計 $0.10/min**（貴 100×）＋另一條 300 min 試用 | ✅ **傳輸可選**：`SmallWebRTCTransport`(P2P 自架、零傳輸費)／`FastAPIWebSocketTransport`／付費 `DailyTransport`；不綁單一供應商 | ✅ Pipecat 勝（成本可控、不鎖供應商） |
| **6 部署複雜度／映像** | ⚠️ 多 runtime（Rust `tman`＋Go＋Node18＋**Py3.10 唯一**＋C++）；雲端「一次 build 進映像」**降低了 runtime 負擔**，但**映像肥、冷啟慢、CI/CD 重、Python 版本被鎖 3.10** | ✅ 純 Python 薄映像；`pip install pipecat-ai[funasr,webrtc]`；冷啟快、CI/CD 輕、Py 版本彈性 | ✅ Pipecat 勝 |
| **7 批次 SenseVoice 整合** | 🟡 逆流：streaming graph 內自建 buffer/VAD 分段再批次推論；無現成 SenseVoice extension（需自寫） | ✅✅ 原生內建 `FunASRSTTService(SegmentedSTTService)` 預設 `iic/SenseVoiceSmall` | ✅ Pipecat 勝 |
| **8 對四裁決 ④「僅當水管」** | ⚠️ **TEN 本身即 agent 編排框架**（graph＋LLM extension＋整合式 Realtime Agent）；用它易被拉進它的編排語意，或至少要繞開一大半功能只當傳輸——阻抗高 | ✅ Pipecat 是 pipeline/transport 框架，非 agent 編排；LLM 節點可自寫、腦仍自呼 Anthropic | ✅ Pipecat 勝 |
| **9 雲端 neural TTS 包裝**（TTS 聲音選型另案未定，雲端可能改雲端 neural） | ✅ 有多家 TTS extension；但仍在其 graph 內 | ✅ 大量 TTS service（含雲端 neural）即插即用；亦可自寫 `InterruptibleTTSService` | 🟡 平／Pipecat 略順 |

**來源（維度表）**：TEN — `github.com/ten-framework/ten-framework`（README、`LICENSE` raw）、`docs.agora.io/en/ten-agent/*`（quickstart、docker-setup、core-concepts）、Oracle `docs.oracle.com/en/solutions/ai-with-ten-framework`、Seeed Wiki（reSpeaker XVF3800＋TEN edge client）；Agora 定價 — `agora.io/en/pricing`、`docs.agora.io/en/voice-calling/overview/pricing`、trtc.io/forasoft 2026 分析。Pipecat — `docs.pipecat.ai/deployment/pipecat-cloud/*`（scaling）、`daily.co/products/pipecat-cloud`、`daily.co/blog/pipecat-cloud-is-now-generally-available`、`github.com/daily-co/pipecat-cloud`、AWS Bedrock AgentCore blog；`research/13`（Pipecat 原生 `FunASRSTTService`、傳輸清單）。

---

## 3. LICENSE 雲端解禁與否（引條文專節）

**這是本補研究最硬的一節，也是 TEN 翻案的關鍵。**

TEN Framework `LICENSE`（© 2025 Agora，Apache-2.0＋Agora 附加條款）的核心限制在 **Condition/Clause 1**，逐字節錄查得如下（來源：`raw.githubusercontent.com/TEN-framework/ten-framework/main/LICENSE`）：

> **Clause 1(i)**：「You may not **(i) host the TEN Framework or the Derivative Works on any End User devices, including but not limited to any mobile terminal devices**」
>
> **Clause 1(ii)**：「…**(ii) Deploy the TEN Framework in a way that competes with Agora's offerings and/or that allows others to compete with Agora's offerings**」
>
> **Condition 2（正向許可）**：「Deploy the TEN Framework **solely to create and enable deployment of your Application(s) solely for your benefit**.」

**逐項判定**：

1. **clause 1(i) 在雲端是否解禁？→ 是，解禁。** 條文限制的標的是「**End User devices**（含 mobile terminal devices）」。**雲端伺服器（VM／容器／K8s node）在任何合理解讀下都不是 End User device**——它不是交到使用者手中的終端。`research/13` 剔除 TEN 的**第一硬理由（上板玩偶＝被禁的 End User device）在拓撲二下不成立**。這是 TEN 翻案的法理基礎，且是**條文原文支撐、非臆測**。
   - 反面：拓撲一（把 TEN 跑在 Genio 520 玩偶上）**仍然違約**——玩偶正是 mobile terminal device。所以 `research/13` 對拓撲一的剔除**依然正確**，只有拓撲二解禁。

2. **clause 1(ii)「不得與 Agora offerings 競爭」是否還卡雲端？→ 部分卡，但對本專案風險低。** TalkyBuddy 是兒童語伴玩偶，**不是**要做一個對外賣的 real-time 通訊／CPaaS 平台去和 Agora 競爭，落在 Condition 2「solely for your benefit」的許可內。故 1(ii) 對本專案**大致不觸雷**，但它是一條**開放式、由 Agora 自由詮釋**的條款，仍是長期法務不確定性（尤其若日後產品化對外提供 API）。

3. **是否有其他雲端商用門檻？** LICENSE 未見固定的營收／規模 gating 數字（不像某些 source-available 授權設 ARR 門檻）；商用被 Condition 2 允許（自用 App）。但**綁 Agora RTC 的實質商業耦合**（見 §2 #5 定價）才是真正的「隱性門檻」——技術上你很難把 TEN 從 Agora RTC 剝離乾淨。

**本節結論**：**單就 LICENSE，雲端佈署使 TEN 從「clause 1(i) 剔除」變為「可用」**（信心：高，條文原文直查）。但 clause 1(ii)＋Agora RTC 耦合是**殘留的軟風險**（信心：中，屬詮釋性條款）。

---

## 4. 拓撲二對「離線降級／雙網混成」的衝擊（專節）

**這一節是把結論從第一層拉回第二層的樞紐。**

`research/12` 的雙網混成設計美在**一個不變量**：線上與離線**共用同一顆 `pipeline._process_text()`**，全雙工元件只在 online「掛載」、offline「乾淨卸載」回既有 `run_turn_audio`＋`asyncio.Lock`。拓撲一（Pipecat 裝置端 in-process）天然滿足這個不變量——編排、線上路徑、離線路徑**都在同一台機器、同一個 Python process**，斷網只是 `cancel()` 拆掉 Pipecat 管線、把路由切回同一顆本機 `_process_text`。

**拓撲二打破這個不變量**，具體衝擊：

1. **線上路徑整條在雲端，斷網即完全消失。** 編排、VAD、turn-detection、STT/TTS 若都在雲端跑，斷網瞬間**不是降級、是整條蒸發**。裝置端手上沒有任何線上編排可「卸載回本機」——因為本機從一開始就沒有那套編排。

2. **離線只能是一套完全獨立的 on-device 系統。** 斷網後只剩既有 `run_turn_audio`＋sherpa 半雙工＋本機 Qwen2.5-0.5B（`research/10`）。它與雲端那套**不共用編排、不共用 `_process_text`（雲端一顆、裝置一顆）、不共用傳輸**。雙網混成因此退化成「**兩套毫無交集、各自維護的系統**」——線上＝雲端 TEN/Pipecat graph，離線＝裝置端自建薄骨架。這正是 `research/10` §二一路想避免的（「network-aware routing 無框架內建、引框架徒增抽象」）。

3. **狀態機與 UX 斷點更痛。** 拓撲一斷網切換是本機管線 in-process 熱切；拓撲二斷網要處理「雲端連線斷→偵測→拉起本機獨立系統→冷啟本機模型」，切換延遲與失敗面更大，且**線上全雙工 vs 離線半雙工 PTT 的行為落差完全落在使用者感受上**（本來就存在，但拓撲二讓它更硬、無中間態）。

4. **裝置上的模型並不會因為上雲而省掉。** 為了離線降級，Genio 520 上**仍必須**帶 SenseVoice＋sherpa＋本機 LLM（否則斷網完全啞掉）。所以拓撲二**沒有換到「裝置變瘦」的好處**——雲端那套是「多養一套」，不是「取代本機那套」。這讓拓撲二的成本效益在本專案特別差。

**本節結論**：拓撲二對雙網混成是**淨損**——它把「一套系統、兩種掛載」變成「兩套系統、各自為政」，且**省不掉裝置端模型**。這個衝擊**與框架是 TEN 或 Pipecat 無關**（Pipecat 跑雲端一樣中招），是**拓撲層級**的結論。**信心：高**（純架構推理，基於既有 code 契約與 `research/10/12` 定案）。

> **值得保留的雲端用法（非拓撲二）**：把**個別重元件**（如雲端 neural TTS、條件式 Azure zh-TW 串流 STT）當成**被裝置端 Pipecat 管線呼叫的雲端服務**，編排仍留在裝置端。這是 `research/12` §5 A2-5 已預留的「條件式雲端旁路」，**不是把編排搬上雲**，不觸發上述衝擊。這才是本專案該走的「用雲」方式。

---

## 5. 四項既定裁決相容檢核（雲端拓撲下）

| 既定裁決（`research/12`） | TEN（雲端） | Pipecat（雲端／裝置） | 說明 |
|---|---|---|---|
| ①Anthropic Messages API 直呼、不引 LangGraph/AutoGen | 🟡 可（自寫 LLM extension 直呼），但要抗拒 TEN 內建 Realtime/LLM 整合的誘導 | ✅ LLM 節點自寫、腦自呼 Anthropic | TEN 相容但阻抗較高 |
| ②不改 `_process_text` 契約 | ⚠️ **拓撲二下 `_process_text` 分裂成兩顆**：雲端一顆給線上、裝置一顆給離線。契約「不改」勉強成立（簽章不動），但**「同一顆共用」的原設計精神被破壞**——需釐清雲端路徑另建一顆等價邏輯，B 軸（依賴 `_process_text`）要對雲端那顆也生效，複雜度上升 | ✅ 拓撲一維持單一顆共用 | **這是拓撲二最尷尬處**：見下方註 |
| ③離線重用 `run_turn_audio` | ✅ 離線本就獨立，重用成立 | ✅ 同 | 兩者皆可，但拓撲二讓「重用」變成「唯一的離線出路」而非「降級分支」 |
| ④框架只當水管、腦仍自呼 Anthropic | ⚠️ **TEN 本身是 agent 編排框架**，最易違反此裁決——要刻意繞開它的 graph/LLM 編排只用傳輸層，事倍功半 | ✅ Pipecat 非 agent 編排，天然只當水管 | **Pipecat 明顯較安全** |

> **註（②的釐清，回應提問第 6 點）**：拓撲二下要維持「不改 `_process_text` 契約」，只能是——**裝置端 `_process_text` 原封不動留給離線**，**雲端另建一顆功能等價的 text-in→reply-out**（含 scaffold＋Anthropic 直呼＋B 軸 directive）。這在字面上沒改既有契約，但代價是**同一套對話邏輯要在雲端重寫／搬移一份、並讓 B 軸的記憶/診斷（`_refresh_directive`、`store`、`diagnose`）也在雲端跑得起來**。這等於把 B 軸一起拖上雲，遠超出 A2 傳輸層的邊界，**與「A2 只在呼叫時機這一點對接」的乾淨切面相悖**。故拓撲二在裁決 ②④ 上都比拓撲一劣。

---

## 6. 若真要改雲端編排，A2-1 spike 該如何調整（以及為何建議不改）

**建議：A2-1 spike 維持 `research/13`/`research/12` 既定版本，不因本補研究改動。** 理由是上面第二層與 §4/§5 的結論——雲端編排對雙網混成淨損、且拖 B 軸上雲。

但為完整回答「若改雲端編排該怎麼調 spike」，列出**假設性**調整（僅備查，非建議執行）：

- **原 A2-1**（拓撲一）：驗 Pipecat 原生 `FunASRSTTService`＋句級中止 sherpa 嵌入 `InterruptibleTTSService`＋`LocalAudioTransport` 裸麥＋Genio footprint，全程不改本機 `_process_text`。
- **若改拓撲二**，spike 需改成驗：
  1. 雲端容器（TEN graph 或 Pipecat Cloud）跑 real-time 全雙工，玩偶端 RTC/WS 上下行延遲（**新增網路 RTT＋抖動**，barge-in <200ms 目標更難達）。
  2. **雲端那顆等價 `_process_text`**（含 Anthropic 直呼＋scaffold＋B 軸 directive）的重建與正確性——**這一項等於把 B 軸拉進 A2 spike，範圍爆炸**。
  3. **斷網切換**：雲端連線斷→裝置端獨立 fallback 冷啟（模型 warm 策略）→UX 斷點量測。
  4. TEN 專屬：確認只用其傳輸/turn-detection、未被拉進 graph agent 編排（守 ④）；Agora RTC 計費在多 session 下的成本模型。
  5. 若用 Pipecat Cloud：warm pool（beta 上限 50）與冷啟對「玩偶隨時可講話」的體驗是否夠。

> 光是第 2 項（雲端重建 `_process_text`＋B 軸上雲）就足以說明：拓撲二不是「A2 傳輸層的調整」，而是**整個系統架構的重寫**。這超出補研究要回答的範圍，且與專案雙網混成定案衝突。

---

## 7. 兩層問題的明確回答（提問核心）

**Q1：單就雲端佈署，TEN 是否翻盤比 Pipecat 適合？**
→ **翻案為「可行」，但未翻盤為「更適合」。** 雲端解除 clause 1(i)（TEN 最硬的剔除理由消失），且 TEN 本就是雲端 real-time 原生、全雙工旗艦——這幾點 TEN 甚至略勝。**但**綜合傳輸鎖定＋付費（Agora）、多 runtime 肥映像、批次 SenseVoice 無現成整合、以及**TEN 本身是 agent 編排框架會擠壓裁決 ④**，Pipecat（含 GA 的 Pipecat Cloud、純 Python 薄映像、原生 `FunASRSTTService`、天然只當水管）**在雲端仍小幅領先**。所以答案是「TEN 不再出局，但 Pipecat 仍勝」。

**Q2：把雙網混成／離線降級一起考慮，結論是否改變？**
→ **改變且更強烈地倒向 Pipecat 裝置端編排（拓撲一）。** 雲端編排（不論 TEN 或 Pipecat）會把雙網混成從「一套系統、線上掛載/離線卸載」打成「兩套毫無交集的系統」，且**省不掉裝置端模型**、還把 B 軸拖上雲。裝置端 in-process 的 Pipecat 才能維持 `research/12` 的核心不變量（單一顆共用 `_process_text`、`cancel()` 熱切）。

---

## 8. 綜合最終建議

| 面向 | 建議 |
|---|---|
| **拓撲** | **維持拓撲一＝裝置端編排**（Pipecat in-process 跑在玩偶/Genio 520），不改雲端編排。 |
| **框架** | **維持 Pipecat（BSD-2）首選、LiveKit 備援**（`research/13` 不變）。 |
| **TEN 的定位更新** | 從 `research/13` 的「法務級剔除」**修正為**：「拓撲一（上板）下仍因 clause 1(i) 剔除；拓撲二（雲端）下 LICENSE 解禁、技術可行，但即使純雲端也未勝過 Pipecat，且雲端編排對本專案雙網混成淨損——故**整體仍不採用**。」剔除理由由「授權＋過重」精修為「**授權（僅上板）＋雲端編排破壞雙網混成＋擠壓裁決④**」。 |
| **雲的正確用法** | 需要雲端算力時，走 `research/12` A2-5 的**條件式雲端旁路**（雲端 neural TTS／Azure zh-TW 串流 STT 當**被裝置端 Pipecat 呼叫的服務**），**編排留裝置端**。這與「拓撲二雲端編排」是兩回事。 |
| **A2-1 spike** | **不受影響、不需調整**，維持 `research/12`/`research/13` 版本。 |

---

## 9. 來源彙整與信心標註

**來源**：
- **TEN LICENSE（決定性）**：`raw.githubusercontent.com/TEN-framework/ten-framework/main/LICENSE`（clause 1(i) End User devices、1(ii) 競爭條款、Condition 2 self-benefit）。
- **TEN 雲端／real-time**：`github.com/ten-framework/ten-framework`（README）、`docs.agora.io/en/ten-agent`（quickstart／docker-setup／core-concepts＝RTC+WS+SIP、2 CPU/4GB、Node18）、`docs.oracle.com/en/solutions/ai-with-ten-framework`、Seeed Studio Wiki（reSpeaker XVF3800＋TEN edge client）。
- **Agora 定價**：`agora.io/en/pricing`、`docs.agora.io/en/voice-calling/overview/pricing`（$0.99/1k min、10k free、Conv AI $0.10/min）、`trtc.io/blog/details/agora-pricing-2026`、`forasoft.com`（LiveKit vs Agora 2026）。
- **Pipecat 雲端**：`docs.pipecat.ai/deployment/pipecat-cloud/fundamentals/scaling`、`daily.co/products/pipecat-cloud`、`daily.co/blog/pipecat-cloud-is-now-generally-available`（GA、warm pool、beta 上限 50、autoscale buffer）、`github.com/daily-co/pipecat-cloud`、AWS `deploy-voice-agents-with-pipecat-and-amazon-bedrock-agentcore-runtime` blog。
- **Pipecat 原生 SenseVoice／傳輸／四裁決相容**：沿用 `research/13`（`FunASRSTTService(SegmentedSTTService)`＝`iic/SenseVoiceSmall`、`SmallWebRTCTransport`/`FastAPIWebSocketTransport`/`LocalAudioTransport`）。
- **本專案 code 契約**：`server/pipeline.py`（`_process_text:188`、`run_turn_audio:134`、`network_mode`、`_refresh_directive`）、`server/app.py`（`ws_talk:208`）。

**信心標註**：
| 主張 | 信心 | 依據 |
|---|---|---|
| clause 1(i) 對雲端伺服器不適用（TEN 雲端解禁） | **高** | LICENSE 原文直查，End User devices 定義明確 |
| clause 1(ii)＋Agora RTC 耦合為殘留軟風險 | **中** | 詮釋性條款，非固定門檻 |
| TEN 為雲端 real-time 原生、全雙工旗艦 | **高** | Agora 官方 docs／README 多來源 |
| Pipecat 雲端可行（Pipecat Cloud GA、薄映像優勢） | **高** | 官方 docs／GA blog |
| 純雲端下 Pipecat 仍小幅勝 TEN | **中高** | 綜合多維度桌面判斷，未上機 |
| 拓撲二破壞雙網混成、淨損 | **高** | 架構推理，基於既有 code 契約與 `research/10/12` 定案 |
| 最終建議維持 Pipecat 裝置端編排、A2-1 不改 | **高** | 上述綜合 |
| Genio 520 footprint、雲端 RTT 下 barge-in <200ms | **未驗證** | 需上機（沿用 `research/12` 註記） |
