# 說說學伴 TalkyBuddy — A 軸 A2（傳輸層）AEC 回音消除技術路線補研究

> 日期：2026-07-07
> 用途：補齊 `research/12_A2_全雙工barge-in_設計交接.md` §2 第 2 列、§1「AEC」列、§7 #3 長期掛空的 AEC 專屬研究。前兩輪自動化流程 AEC schema 重試 5 次回傳純佔位（`"test"`）、對抗式 verdict 判 REJECT、未交付；本檔以具名、附來源、可量化的 prose 補上。
> 母脈絡：先讀 `research/12`（全雙工設計交接）、`research/10`（選型總結）。本檔只回答「線上全雙工玩偶喇叭外放時，自體 TTS 回授如何壓到 barge-in 門檻以下，且不吃掉使用者插話」這一題。
> 邊界問題：離線 PTT 停播才收音、**不需 AEC**（已定，本檔不翻）。

> **⚠️ 研究完整度（誠實標註）**：三條路線的**架構取捨、關鍵風險、移轉缺口**信心「中高」（有具名產品＋官方文件＋多來源交叉）。所有**絕對數值**（ERLE dB、延遲 ms、CER、成本）為**廠商文件／第三方文章值，非本專案實測**——尤其兒童高音語音、繁中、企鵝機殼外放這三個變因**全數未實測**，凡引用數字皆標來源與信心。最尖銳的 double-talk（消回音 vs 不吃插話）在 §4 專節處理，屬**必須落地實測**項（A2-3）。

---

## 0. 一句話結論

- **PC 原型階段（A2-3）**：用**瀏覽器路徑**——`getUserMedia({ audio: { echoCancellation: true }})`（Chrome/WebRTC APM＝AEC3），**前提是 TTS 必須透過瀏覽器自己的音訊輸出播放**（`<audio>`／Web Audio graph → destination），否則瀏覽器 AEC 拿不到 far-end 參考、形同未開。零額外依賴、當天可落地，但 double-talk 積極度與兒童高音表現**必須實測調校**。
- **上板產品階段（A2-6）**：瀏覽器 AEC **完全不移轉**（Genio 520 上不跑 Chrome）。首選**外掛 XMOS 型硬體語音前端**（如 XVF3800，AEC＋beamforming＋NS 一顆搞定，~US$50/顆）；次選在 SoC CPU 上跑**軟體 libwebrtc APM（AEC3）**做上行 AEC。**關鍵新事實：Genio 520 本身沒有 HiFi audio DSP**（同系列 510/700 才有 Cadence Tensilica HiFi 5），故「板上 DSP 直接做 AEC」的原假設不成立，須外掛硬體或吃 CPU。

> 換言之：**PC 原型與上板是兩條物理不同的 AEC 堆疊，不能共用一份實作**，只能共用「VAD 門檻／barge-in 積極度」這層調校經驗。這正是 research/12 §6 #5 點名、被低估的「移轉缺口」。

---

## 1. 三路線 × 各維度比較大表

| 維度 | ①瀏覽器路徑（AEC3 in Chrome） | ②伺服器路徑（libwebrtc APM / speexdsp） | ③Genio 520 上板硬體（XMOS 型 前端） |
|---|---|---|---|
| **AEC 演算法** | WebRTC AEC3（線性濾波＋殘響抑制器）[S1][S8] | 同一份 libwebrtc AEC3 原始碼，server 端跑；或 speexdsp echo canceller [S2][S3] | XMOS XVF3800 專用 DSP 韌體：AEC＋beamforming＋de-reverb＋NS＋AGC [S5][S6] |
| **抑制量（ERLE）** | 線性濾波典型 **20–40 dB**，<10 dB 代表未收斂 [S1][S8]（廠商值，非本專案實測） | 同 AEC3（同碼）；speexdsp 一則 n=1 論壇報告稱在 8k/16k「表現良好」而 AEC3「處理不佳」[S2]（軼事、須自測） | 廠商宣稱清晰遠場 5m 拾音、含 AGC 60dB [S6]（廠商值） |
| **far-end 參考需求** | 瀏覽器**自動**用其音訊輸出 render 流當參考——**但僅限瀏覽器知道的播放**（`<audio>`/WebAudio）；手動解 PCM 自播則 AEC 收不到、失效 [S4] | **須自行提供 loopback 參考**：把送給喇叭的 TTS PCM 對齊時間戳餵給 APM 的 `ProcessReverseStream`，同步難度高 [S3] | 硬體在**類比/前級**就地消，喇叭參考走板內電氣回路，**整合最乾淨**、無軟體同步問題 [S5] |
| **double-talk（插話不被吃）** | AEC3 偵測雙講時**降/停濾波器自適應**保護係數；殘響抑制器過激會把近端語音削成「空洞/水下音」[S1][S8] | 同 AEC3 行為；speexdsp 雙講保護較弱、需搭 VAD | 硬體 pipeline 有獨立雙講偵測，宣稱可全雙工；仍須實測繁中兒童 |
| **延遲** | AEC3 每 block **4ms@16k**（64 samples），收斂 1–2s；行動裝置回路延遲 20–200ms [S8] | 同 AEC3 演算延遲，**加**上行送 server 的網路 RTT＋參考對齊 buffer（新增數十 ms） | 硬體 pipeline 固定低延遲、no 網路 hop；最利 <200ms 預算 |
| **兒童高音/音量不穩** | **未知/未實測**：AEC3 為成人電話語音調校，高基頻＋忽大忽小易觸發殘響抑制器過抑（吃掉童聲）[S1] | 同瀏覽器（同碼） | XVF3800 宣稱噪環清晰拾音，但**無兒童語音公開數據** |
| **整合難度** | **最低**：改一行 constraint（前提 TTS 走瀏覽器播放） | **中高**：需 Python 綁定 `webrtc-audio-processing` [S2]＋loopback 參考對齊＋上行改造 | **中**（硬體）：多一顆料、走 USB/I2S 進板、韌體設定；**но省軟體 AEC** |
| **Python 綁定現況** | 不適用（瀏覽器內建） | `webrtc-audio-processing`（PyPI，`xiongyihui`）／`aec-audio-processing`；AEC 支援度歷史上有坑 [S2] | 不適用（韌體） |
| **成本** | US$0 | US$0（吃 CPU；Genio 520 無 DSP 卸載，佔 A55/A78 週期） | ~**US$50/顆**（reSpeaker XVF3800 板 US$49.99–56）[S5][S7]＋BOM/機構 |
| **移轉性** | **不移轉到上板**（Genio 不跑 Chrome）[research/12 §6#5] | **可移轉**：同一份 libwebrtc 可 cross-compile 上 aarch64 | 產品階段原生方案；PC 原型階段無關 |
| **適用階段** | ✅ **PC 原型（A2-3）首選** | 備援／上板軟體 fallback | ✅ **上板產品（A2-6）首選** |

---

## 2. 三路線具名評述（含來源與數據）

### 2.1 路線①：瀏覽器路徑 — `getUserMedia` `echoCancellation:true`（AEC3）

**現況缺口**：前端 `web/index.html:626` 目前 `getUserMedia({ audio: true })`，未顯式指定 `echoCancellation`，吃瀏覽器預設（Chrome 預設為 `true`，但**不保證**、且無法確認參考來源正確）。全雙工必須**顯式**開並驗證。

**AEC3 是什麼、多強**：AEC3 是 WebRTC 第三代回音消除（2017–2018 取代舊 AEC/AECM）[S1]。核心是**線性自適應濾波器**估回音，典型移除 **20–40 dB（ERLE）**，低於 10 dB 代表濾波器未收斂 [S1][S8]；後接**殘響抑制器**清掉線性濾波消不掉的殘量。以 research/12 推估的「回音需壓到 <-10dB 才不誤觸 barge-in VAD」為門檻，AEC3 的 20–40 dB **理論上綽綽有餘**——但這是**廠商/文章值、非本專案實測**，且高音量喇叭的**非線性失真**（喇叭破音、限幅、動態壓縮）超出線性濾波能力 [S8]，殘量全靠殘響抑制器，這正是童聲被吃的風險源。

**關鍵陷阱（決定成敗）**：瀏覽器 AEC 有個「內建假設」——far-end 音訊必須是**瀏覽器自己知道的播放**（`<audio>` 元素或 Web Audio API → 輸出）；瀏覽器用它送到喇叭的 render 流當參考自動對齊消回音 [S4]。**若 TTS 是「WebSocket 收 PCM chunk → 手動 decode → 自播」，瀏覽器 AEC 收不到參考、等於沒開**，AI 會聽到自己、陷入「打斷自己→回應打斷→再聽到→session 崩」的回授迴圈（一篇 sub-500ms 語音 AI 實作明確踩到，最後靠 close code 1011 斷線暴露）[S4]。
→ **落地約束**：TalkyBuddy 的 sherpa-onnx TTS 音訊在前端**必須經 `<audio>`／Web Audio graph 播到 destination**，瀏覽器才能把它當參考消掉；不可用旁路自訂播放。這是 A2-3 spec 的硬條件。

**收斂延遲**：AEC3 需 **1–2 秒**適應房間聲學/裝置路徑，期間漏少量回音 [S1][S8]；每 block 4ms@16k [S8]。對「一開機講第一句」的頭 1–2s 要容忍殘漏。

**務實備援（若瀏覽器 AEC 不足）**：可疊加**「代理發話期間硬門控＋播放結束 cooldown」**——一篇實作用 two-tier RMS gate：發話中全擋上行、停播後 1.5s 冷卻期用較低 RMS 門檻（0.03 vs 常態 0.05）吸收「房間殘響衰減」[S4]。此法**犧牲部分 barge-in 靈敏度換防回授**，可作 AEC 調不好時的保底（但與「真 barge-in」有張力，見 §4）。

**信心**：架構「中高」；ERLE/延遲數字「中」（廠商值）；兒童繁中外放表現「低（未實測）」。

### 2.2 路線②：伺服器路徑 — libwebrtc APM（AEC3）vs speexdsp

**動機**：若上行改為連續串流（全雙工必然），可在 server 端對上行音訊做 AEC，好處是不依賴瀏覽器行為、可控、且**上板後同一份 code 可 cross-compile**（移轉性優於路線①）。

**AEC3 vs speexdsp**：
- **libwebrtc AudioProcessing（APM/AEC3）**：品質標竿（同路線①的碼），但 server 端要**自己餵 far-end 參考**——把送往喇叭的 TTS PCM 以正確時間戳呼叫 `ProcessReverseStream`，再對上行呼叫 `ProcessStream`。**難點是同步對齊**：near/far 兩流的取樣率、frame 對齊、播放實際發聲時間（喇叭 buffer 延遲）都要估準，估歪 AEC 直接失效 [S3]。
- **speexdsp echo canceller**：更輕、老牌；一則 discuss-webrtc 論壇報告（**n=1 軼事**）稱 speexdsp 在 8k/16k「規矩地消掉了 render 訊號」，而 AEC3「對 8k/16k 處理不佳」[S2]——此說**與 AEC3 官方支援 8/16/32/48k 矛盾**，很可能是該開發者整合/參考對齊問題，**不足以定論**，但提示 speexdsp 在低取樣率整合較省心。speexdsp 雙講保護弱、須自己搭 VAD。

**Python 綁定現況**：PyPI 有 `webrtc-audio-processing`（`xiongyihui/python-webrtc-audio-processing`）與 `aec-audio-processing` 等；**但 AEC 功能的可用性歷史上有坑**（該 repo issue #18「Add acoustic echo cancellation」長期是討論題）[S2]，pip wheel 對 aarch64（piwheels 有建置）與參考流 API 完整度須先 spike 驗證，不可假設「pip install 就能 AEC」。

**延遲代價**：AEC3 演算本身 4ms/block；server 路線**額外**吃「上行送到 server 的 RTT」＋「參考對齊 buffer」，對 <200ms barge-in 預算是淨增負擔，且把 AEC 放遠端也意味著**barge-in 判斷（本地 VAD）與回音消除（遠端）分處兩地**，本地 VAD 仍會先看到未消的回音——**這條路線對「防自體誤觸發」其實不理想**（VAD 在前端、AEC 在後端），除非 barge-in 判斷也移到 server。

**信心**：技術可行「中高」；作為 PC 原型主線「不建議」（整合重、對本地 VAD 誤觸發幫助有限）；作為上板軟體 fallback「中」。

### 2.3 路線③：Genio 520 上板硬體 — XMOS 型語音前端

**核爆級新事實（推翻原假設）**：research/12 §2 第 2 列與 §6 #5 假設「Genio 520 上板用板上 XMOS 型 DSP 做 AEC＋beamforming」。**查證結果：MediaTek Genio 520 本身沒有 HiFi audio DSP**——MediaTek 官方 audio DSP 平台清單為 Genio 1200(HiFi4)、700(HiFi5)、510(HiFi5)、350(HiFi4)，**520 不在列**[S9]；另一來源明言「Genio 520 沒有內建 HiFi audio DSP，同系列 510/700 才配 Cadence Tensilica HiFi 5」[S9b]。
→ 意涵：**Genio 520 上「板載 DSP 直接卸載 AEC」的路走不通**（信心中高，但建議以 520 正式 datasheet 最終覆核，因同系列差異可能隨 SKU 變動）。上板做 AEC 只剩兩條：**(a) 外掛獨立硬體語音前端晶片**，或 **(b) 吃 A78/A55 CPU 跑軟體 APM（路線②的 aarch64 版）**。

**外掛硬體首選：XMOS XVF3800**（VocalFusion 4-mic）[S5][S6]：
- 一顆晶片內含 **AEC＋beamforming＋de-reverberation＋DoA＋動態 NS＋VAD＋60dB AGC**，360° 遠場拾音達 5m [S6]。
- 這正是 research/02:80 描述的「ESP32-S3＋XMOS 雙處理器、XMOS 做硬體加速語音前處理（回音消除/波束成形）」的同族方案 [research/02][S6]。
- 開發評估用 **XK-VOICE-SQ66** 4-mic 套件；量產可用 reSpeaker XVF3800 模組，**US$49.99–56/顆**（Seeed/AliExpress，2025-07 上市）[S5][S7]。透過 USB 或 I2S 接 Genio 520。
- 好處：AEC 在類比/前級**就地**完成，喇叭參考走板內電氣回路，**無軟體 loopback 同步問題**、延遲最低、不佔 NPU/CPU、對 <200ms 預算最友善。

**取捨**：外掛硬體多一顆 ~US$50 料＋機構整合，但省掉「在無 DSP 的 520 上用 CPU 跑 AEC」的 CPU 週期與同步工程；對量產玩偶（BOM 敏感）是**成本 vs 工程**的商業決策，非純技術題。

**信心**：Genio 520 無 DSP「中高（待 datasheet 覆核）」；XVF3800 能力「中高（廠商文件＋第三方）」；實際上板延遲/繁中兒童表現「未實測」。

---

## 3. double-talk / barge-in 相容性（最尖銳張力，專節）

**張力定義**：全雙工的核心矛盾——AEC 必須把**自體 TTS 回音**消到本地 VAD 的 barge-in 門檻（<-10dB）以下，**同時不能**把**使用者的插話語音**（正是 barge-in 要偵測的訊號）一起削掉。這兩個目標在物理上直接對立，因為插話發生時正是 double-talk（近端使用者＋遠端 TTS 同時出聲）。

**各方案在 double-talk 的行為**：
- **AEC3（路線①②共用）**：偵測到雙講時**降低或暫停線性濾波器自適應**以保護係數不被近端語音污染 [S8]；但真正吃掉插話的是**後段殘響抑制器**——它「過激會把近端語音削成空洞/水下音」，「太保守則留可聞回音」[S1][S8]。這是一條**必須調的積極度旋鈕**，非開關。
- **speexdsp**：雙講保護較弱，更依賴外部 VAD 把關。
- **XVF3800**：內建雙講偵測與全雙工設計，但無繁中兒童公開數據。

**落地調校策略（A2-3 實測項）**：
1. **分工**：AEC 負責「壓自體回音」，**barge-in 觸發交給 VAD＋門檻**，兩者分開調。AEC 積極度調到「回音壓過門檻即可、不追求極致」，把削語音風險留給殘響抑制器最小化。
2. **VAD 門檻抬高＋能量差**：barge-in VAD 門檻設在「AEC 殘漏回音之上、真人插話之下」——因插話者近麥（企鵝機上麥克風離孩子嘴近），SNR 通常高於殘漏回音，可用能量差開窗。
3. **保底門控**（AEC 調不動時）：路線①的「發話中軟門控＋停播 cooldown」[S4] 可當保險，但它**本質上犧牲 barge-in 靈敏度**（發話中壓低上行敏感度），與「真 barge-in」有取捨——demo 需在「防回授」與「可隨時插話」間拉一條可調滑桿，實測定甜蜜點。
4. **句間中止優先**：research/12 §2 第 5 列已指出 sherpa 中止效益主要在「句與句之間」；配合 double-talk，把 barge-in 判斷放在句界附近（TTS 短句間隙回音最弱）可降低誤觸與吃字。

**一句話**：double-talk 沒有「設定即解決」的方案，AEC3/XVF3800 都提供雙講保護但**都可能吃掉童聲插話**；這是 **A2-3 必須實機錄音、拉積極度／VAD 門檻滑桿實測**的項，不能靠查資料定案。

---

## 4. 跨路線關鍵議題

### 4.1 移轉缺口（research/12 §6#5 的核心）
- **瀏覽器 AEC 不會自動移轉到 Genio 520**：PC 原型跑 Chrome，AEC3 在瀏覽器內、自動拿 render 流當參考；上板不跑 Chrome（Yocto Linux aarch64），這層**整個消失**，且 520 又無 DSP 可頂替 [S9]。
- **PC 原型 vs 上板該用哪條**：
  - PC 原型（A2-3）：路線①瀏覽器 AEC（一行 constraint＋TTS 走瀏覽器播放）。
  - 上板（A2-6）：路線③外掛 XVF3800（首選）或路線②軟體 APM 跑 CPU（fallback）。
- **如何銜接**：兩階段**唯一可共用的是「VAD 門檻／barge-in 積極度」調校經驗**與「AEC 分工原則」（AEC 只壓回音、觸發交 VAD）。實作碼不共用。research/12 的 `NetworkFSM`「AEC/VAD 掛載開關」設計要能容納「兩種完全不同的 AEC 後端」，介面要抽象在「回音是否已壓過門檻」這層，不綁瀏覽器 API。

### 4.2 兒童語音
- 高基頻、音量忽大忽小是**所有軟體 AEC 的已知弱點**：AEC3 為成人電話語音調校，殘響抑制器易把不穩的高音童聲判成殘餘回音而過抑 [S1]。**無任一方案有公開兒童數據**，全屬未實測。
- 對策：實測時**用真實兒童錄音**（research/10 §六#1 已列「錄真實兒童語音補測」為待辦），調殘響抑制器積極度時以「童聲插話不被削」為驗收線。

### 4.3 物理/聲學
- **喇叭-麥克風距離／機殼遮蔽**：企鵝玩偶外放，喇叭與麥克風同機近距，回音強——這放大 AEC 負擔，也提高非線性失真（小喇叭高音量易破音，超出線性濾波）[S8]。
- **機構隔離仍有價值**：research/12 §7#9 已點名。即使 AEC 到位，**麥克風與喇叭實體隔開／加聲學阻尼／麥克風指向背離喇叭**能先物理降低回音 10+ dB，等於替 AEC 減負、直接改善 double-talk 餘裕。外放 vs 耳機：若最終允許耳機則回授問題幾乎消失，但玩偶產品定位是外放，故 AEC＋機構隔離**兩者都要**。
- XVF3800 的 **beamforming（波束成形）**在此有額外價值：空間上對準使用者、抑制喇叭方向，是純軟體 AEC（單麥）給不了的物理增益——這是上板選硬體前端的隱性理由。

### 4.4 延遲預算（barge-in 端到端 <200ms 目標）
- **AEC 本身很便宜**：AEC3 每 block 4ms@16k [S8]，硬體前端固定低延遲。AEC 不是 200ms 預算的大戶。
- **大戶在別處**：research/12 §2#5 已誠實標註「200ms 為工程目標未實測」；一篇實作實測端到端 p50 ~1.5–2s、VAD turn detection 就吃 1–2.5s [S4]——**真正的延遲風險是 VAD turn 判定＋LLM 首 token＋TTS 首音，不是 AEC**。
- **路線間差異**：路線②server AEC 額外加 RTT＋參考對齊 buffer（數十 ms），且把 AEC 與本地 VAD 分處兩地；路線①③ AEC 在近端、對預算最友善。

---

## 5. 移轉缺口與分階段建議

| 階段 | 子專案 | AEC 方案 | 動作 | 驗收 |
|---|---|---|---|---|
| **PC 原型** | **A2-3** | **路線① 瀏覽器 AEC3** | ①`getUserMedia({audio:{echoCancellation:true, noiseSuppression:true, autoGainControl:true}})` 顯式開；②**確認 TTS 經 `<audio>`/WebAudio 播到 destination**（否則 AEC 拿不到參考）；③喇叭外放實測自體回授是否誤觸 VAD；④拉「AEC 積極度 / VAD 門檻」滑桿；⑤用**真實兒童錄音**測插話不被削 | 自體 TTS 不誤觸 barge-in（回音殘量 <VAD 門檻）**且**孩子能隨時插話打斷（童聲不被吃）；備援：two-tier RMS gate [S4] |
| **上板產品** | **A2-6（另案）** | **路線③ XVF3800 硬體前端**（首選）／路線② 軟體 APM 跑 CPU（fallback） | 取得 Genio 520 板後：先確認 datasheet「無 HiFi DSP」[S9]；評估外掛 XVF3800（~US$50，AEC＋beamforming＋NS）vs CPU 跑 libwebrtc APM；量測上板端到端 barge-in 延遲 | 上板回授壓過門檻、延遲可接受；beamforming 增益驗證 |

**NetworkFSM 介面建議**：AEC 掛載開關抽象在「回音是否已壓過 barge-in 門檻」語意層，**不綁定** `getUserMedia` 或任一硬體 API，讓 PC 原型（瀏覽器）與上板（硬體/軟體 APM）兩種後端可替換——這是把 research/12 §6#5 移轉缺口在架構上封住的關鍵。

---

## 6. 待驗證（誠實保留）

1. **所有 dB/ms/US$ 皆為外部值**：ERLE 20–40dB [S1][S8]、AEC3 4ms/block [S8]、XVF3800 US$49.99–56 [S5][S7] 均為廠商/第三方，**本專案零實測**。
2. **Genio 520 無 HiFi DSP**：兩來源交叉 [S9][S9b]，但建議以 Genio 520 正式 datasheet／MediaTek FAE 最終覆核（SKU 差異）。
3. **兒童繁中外放 double-talk**：三路線**全未實測**——A2-3 的核心驗收，必須實機錄音。
4. **`webrtc-audio-processing` pip 的 AEC＋參考流 API 完整度＋aarch64 wheel**：須 spike（歷史有坑 [S2]）。
5. **speexdsp vs AEC3 於 16k**：僅 n=1 論壇軼事 [S2]，不足定論。
6. **XVF3800 接 Genio 520 的實際整合**（USB vs I2S、Yocto 驅動）未查證。

---

## 7. 來源清單

- [S1] Switchboard Audio — *How WebRTC AEC3 Works*：AEC3 世代、ERLE 20–40dB、<10dB 未收斂、double-talk 保護、殘響抑制器過激「空洞/水下音」。https://switchboard.audio/hub/how-webrtc-aec3-works/
- [S2] PyPI `webrtc-audio-processing` ＋ GitHub `xiongyihui/python-webrtc-audio-processing` issue #18（Add AEC）＋ discuss-webrtc「AEC3 flat output」討論（speexdsp vs AEC3 於 8k/16k，n=1）。https://pypi.org/project/webrtc-audio-processing/ ；https://github.com/xiongyihui/python-webrtc-audio-processing/issues/18 ；https://groups.google.com/g/discuss-webrtc/c/T0W8m5Wy7RM
- [S3] PJSIP AEC 文件（far-end 參考／ProcessReverseStream 同步概念）＋ GStreamer `webrtcdsp`。https://docs.pjsip.org/en/latest/specific-guides/audio/aec.html ；https://gstreamer.freedesktop.org/documentation/webrtcdsp/webrtcdsp.html
- [S4] dev.to / GoNoGo — *I Built a Voice AI with Sub-500ms Latency… Echo Cancellation Problem*：瀏覽器 AEC 只認 `<audio>`/WebAudio far-end、手動 PCM 自播失效、回授崩潰、two-tier RMS gate（0.03/0.05、1.5s cooldown）、端到端 p50 1.5–2s、VAD 1–2.5s。https://dev.to/remi_etien/i-built-a-voice-ai-with-sub-500ms-latency-heres-the-echo-cancellation-problem-nobody-talks-about-14la
- [S5] CNX-Software / Seeed — ReSpeaker XMOS XVF3800 4-mic 板（US$49.99–56、2025-07 上市、AEC/beamforming/NS）。https://www.cnx-software.com/2025/07/29/respeaker-xmos-xvf3800-4-mic-array-board-features-esp32-s3-module-works-over-usb/ ；https://www.seeedstudio.com/ReSpeaker-XVF3800-USB-4-Mic-Array-With-Case-p-6490.html
- [S6] XMOS 官方 XVF3800（VocalFusion 4-mic、AEC＋beamforming＋DoA＋NS＋VAD＋60dB AGC、5m 遠場、audio pipeline datasheet）。https://www.xmos.com/xvf3800 ；https://www.xmos.com/documentation/XM-014888-PC/html/modules/fwk_xvf/doc/datasheet/03_audio_pipeline.html
- [S7] Amazon reSpeaker XVF3800 USB 4-mic 產品頁（規格/售價交叉）。https://www.amazon.com/ReSpeaker-Microphone-Cancellation-Far-Field-Assistants/dp/B0FKGFXQQ5
- [S8] getUserMedia 音訊約束（addpipe）＋ AEC3 技術細節（4ms/block@16k、收斂 1–2s、回路 20–200ms、非線性失真、latency constraint）。https://blog.addpipe.com/getusermedia-audio-constraints/ ；（AEC3 細節同 [S1]）
- [S9] MediaTek Genio 官方 blog — *Unlocking Advanced Audio Processing: HiFi DSP and SOF*：HiFi DSP 平台清單＝Genio 1200/700/510/350，**520 不在列**；DSP 做 filtering/NS/AEC。https://genio.mediatek.com/blog/unlocking-advanced-audio-processing-leveraging-hifi-dsp-and-sof-on-the-mediatek-genio-platform
- [S9b] Genio 平台比較（WebSearch 摘要）：「Genio 520 無內建 HiFi audio DSP；Genio 700/510 配 Cadence Tensilica HiFi 5」；Genio 520＝8th-gen NPU ~10 TOPS。https://genio.mediatek.com/genio-520
- [S10] 本專案 `research/02_整機語音玩具二開專案.md:80`（ESP32-S3＋XMOS 雙處理器、XMOS 做硬體 AEC/beamforming 之架構借鏡）；`research/10`、`research/12`。
