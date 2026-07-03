# 「說說學伴」28 天決賽衝刺 — 最小可行 MVP 規劃書

> 版本：v1.0｜規劃日：2026-07-02｜決賽倒數：28 天
> 前提：Genio 520 開發板 5 天後到手；團隊具備嵌入式/Python 工程能力；目標為決賽現場可操作、可展示之實體原型。

---

## 0. 一頁摘要（TL;DR）

**MVP 一句話定義**：一隻**無螢幕企鵝語伴玩偶**，跑在 **MediaTek Genio 520** 上，**離線**就能完成「聽（ASR）→ 想（LLM）→ 說（TTS）」的中英雙語鷹架帶讀，並在連線時把互動資料同步到**教師儀表板**。

**三個關鍵策略轉變**（相對於原 SPEC）：

1. **模型與加分脫鉤（重要澄清）**：決賽「國產晶片加分（至多 2 分）」看的是**開發板核心晶片**——Genio 520 由聯發科（我國依法登記之單位）自主研發、擁有 IP，**已符合「自主研發晶片」資格，與 LLM 選什麼無關**。因此邊緣 LLM 以記憶體/延遲務實選 **Qwen2.5-1.5B**（4GB 板友善）即可；繁中品質有餘力再升級 MediaTek Breeze 系列（`Llama-Breeze2-3B`／`Breeze-ASR-25`／`BreezyVoice`）。ASR/TTS 透過 **NeuroPilot Public 上 NPU**，讓近端原型紮實用到國產晶片，加分佐證更強。**雲端線上**則用 **Hermes Agent 框架**（Nous Research agent runtime）作「反思教學 AI 助教」大腦，**推論接 AWS Bedrock（Claude）**。
2. **Edge-first、CPU 優先**：即時對話迴路一律在邊緣 CPU 完成（用 Pipecat + llama.cpp 全離線管線）；雲端 Hermes Agent 改為**非同步增強**（深度診斷、教師報表、長期記憶）。NPU 加速列為決賽後 stretch goal，不擋 MVP。
3. **大幅收斂範圍**：砍掉三源 RAG、雙雲 LLM、自建 Yocto、音素級發音評分——這些在 28 天內是風險黑洞，且決賽不需要。

**時間軸**：Day1–5 在筆電/x86 把軟體管線跑通 → Day6–13 板子到手移植上 ARM → Day14–21 雙模＋教師閉環 → Day22–26 實體整合＋調優＋錄備援影片 → Day27–28 彩排與緩衝。

---

## 1. 策略轉變：為什麼這樣改

### 1.1 模型：Hermes → MediaTek Breeze 三件套

原本規劃的 `NousResearch/hermes-agent`（Hermes 3B 是 Llama 3.2 3B 的**英文導向**微調）繁中偏弱，對一個**臺灣國小雙語**產品是硬傷。聯發科創新基地（MediaTek Research）已開源一整套台灣在地、且同為國產的模型，剛好組成完整語音管線：

| 管線層 | 原方案 | 改用（建議） | 為什麼 |
| :-- | :-- | :-- | :-- |
| ASR | Whisper / 泛用 | **Breeze-ASR-25** | Whisper-large-v2 微調，專攻台灣華語＋中英夾雜；官方數據較原生 Whisper 準確率高約 10%、夾雜情境高 56%。Apache 2.0。直擊阿明「中英夾雜」場景。 |
| LLM | Hermes 3B | **Llama-Breeze2-3B-Instruct** | 繁中特化、Llama 3.2 3B 基礎、支援 function calling，3B 專為行動/邊緣設計。Llama 3.2 社群授權。 |
| TTS | Piper | **BreezyVoice**（主）/ Piper（備） | 台灣口音語音合成，聽感在地化。Piper 保留為輕量備援。 |

> **加分邏輯**：用**聯發科的模型**跑在**聯發科的晶片**上，「基於國產晶片開發平台」加分（至多 2 分）與「主題切合度（在地化/普惠）」同時最大化。

### 1.2 運算落點：CPU 優先，NPU 為 stretch goal

- **Pipecat 可做完全離線管線**（內建本地 Whisper STT、Piper TTS 服務，LLM 走 Ollama/llama.cpp）——但 LLM 會跑在 **ARM CPU（2×Cortex-A78）**，**用不到那顆 9 TOPS NPU**。
- 要真的吃到 NPU，得走 MediaTek **NeuroPilot GenAI** 工具鏈，模型轉換/相容性複雜度高，28 天內是高風險。
- **決策**：MVP 用 CPU 路線（穩、快、可交付）。**即使跑 CPU，仍完全符合「基於國產晶片開發平台」的加分定義**。NPU 加速做為 Demo 之外的加分彩蛋，有餘力再攻。

### 1.3 資料流：Edge-first，雲端非同步增強

原 SPEC 把雲端當「高品質主路徑」、邊緣當「降級路徑」，但雲端經 API Gateway → Lambda → Bedrock → TTS 回傳，實測輕鬆 2–5 秒，體感遲鈍，反而是最差的即時體驗。**翻轉為**：

- **邊緣永遠負責即時對話**（首音 <300ms 目標），預設離線就能運作 → 普惠/偏鄉故事天然成立。
- **雲端只做非同步增強**：深度學習診斷、RAG 補充教案、教師報表。回得來就加值、回不來不影響孩子。狀態機因此大幅簡化（見 SPEC v2）。

---

## 2. MVP 範圍界定

### 2.1 必做（Must-have，決賽勝負手）

1. **實體板上離線對話**：Genio 520 上完成 ASR→LLM→TTS 全離線一輪中英雙語鷹架帶讀。
2. **現場斷網橋段**：Demo 中實際切飛航模式，玩偶照常回應 —— 全場最有記憶點的畫面。
3. **教師儀表板**：由真實互動資料（哪怕少量）驅動，呈現學生四維評分、弱項與差異化教學建議。
4. **國產晶片佐證**：板子實機、on-device 推論、模型來源皆為聯發科的說明頁。

### 2.2 明確砍除（MVP 不做）

- **三源 RAG**（Cool English＋均一＋國教院）全建 → 改為單一小型精選教材集（1–2 個單元），其餘用 mock。
- **雙雲 LLM**（Claude＋Llama 70B）→ 雲端若要做，只留一顆。
- **自建 Yocto Linux** → 用官方 BSP 映像（Yocto/Ubuntu 現成），不自己編 OS。
- **音素級發音評分**（/æ/ 這種）→ LLM 做不到；MVP 先做「流暢度＋詞彙＋句型」的整體評語，發音評測列為未來工作。
- **裝置端多用戶身份管理** → Demo 固定單一學生 profile。

### 2.3 Stretch goals（有餘力才碰）

- NeuroPilot GenAI 讓 LLM 或 ASR 跑上 NPU（效能與省電加分）。
- 雲端非同步深度診斷（Bedrock/Claude）真的接起來。
- 跨世代「長者口述→雙語繪本」小 demo。

---

## 3. 目標技術棧（最終選型表）

> **SDK 權限前提（已釐清）**：團隊**無 NDA**，只能用 **NeuroPilot Public（免 NDA）**。因此：**ASR/TTS** 走公開的 **TFLite + Neuron Delegate**（模型轉 `.tflite` INT8）上 **NPU 硬體加速**；**LLM** 避開需 NDA 的 **GAI Toolkit**，改用開源 **llama.cpp（GGUF）跑在 8 核 CPU（Cortex-A78）**。這條「NPU 管感知、CPU 管生成」的分工，既合法（免 NDA）又真的用到國產晶片 NPU，加分論述完整。

| 層級 | 組件 | 選型（MVP 主線） | 授權 | 在 Genio 520 的落點 | 備援 / 升級 |
| :-- | :-- | :-- | :-- | :-- | :-- |
| 編排 | 語音管線 | **Pipecat**（本地模式） | BSD | CPU/Python | 自寫 async 迴圈 |
| VAD | 語音活動偵測 | silero-vad / webrtcvad | MIT | CPU（極輕） | — |
| ASR（邊緣） | 語音辨識 | **whisper-base/small INT8 `.tflite`** → **NPU（Neuron Delegate）** | MIT | NPU，80–150MB | faster-whisper CPU（若算子 fallback） |
| ASR（雲端/升級） | 高品質辨識 | **Breeze-ASR-25**（台灣華語+中英夾雜） | Apache 2.0 | 雲端 GPU 或 stretch | 線上模式才用，邊緣放不下 |
| LLM（邊緣） | 離線對話 | **Qwen2.5-1.5B-Instruct GGUF Q4** | Apache 2.0 | **CPU（llama.cpp）約 1.1GB** | Llama-Breeze2-3B（繁中品質升級，非加分因素；記憶體允許再換） |
| TTS（邊緣） | 語音合成 | **Piper TTS**（zh/en 輕量、首音快） | MIT | CPU 或轉 tflite 上 NPU | **BreezyVoice**（台灣口音，較重，記憶體允許再換） |
| 儲存 | 遙測快取 | SQLite | 公有領域 | CPU | — |
| OS/SDK | 系統與工具鏈 | **Yocto + NeuroPilot Public** | — | — | Ubuntu（roadmap，較不穩） |
| **雲端 Agent** | **反思教學 AI 助教大腦** | **Hermes Agent runtime + AWS Bedrock（Claude）** | MIT（框架） | 雲端伺服器 | 高擬真 mock 亦可過關 |
| 向量檢索 | RAG | pgvector（複用 RDS） | PostgreSQL | 雲端 | OpenSearch |
| 教師端 | 儀表板 | 靜態 HTML/JS + 圖表 | — | 瀏覽器 | 已有雛形 |

**雲端 Agent 為何用 Hermes Agent**：它是 Nous Research 的 agent runtime（MIT），內建**持久記憶、自主技能學習、cron 排程、工具調用、原生 MCP**，可編排任一 LLM——推論**接 Bedrock 的 Claude**。這正好對應「反思教學 AI 助教」需要的**長期追蹤每個學生**：每位學生一個持久記憶 agent，排程每日生成診斷報表寫入儀表板。**整合待驗證項**：Hermes Agent 接 Bedrock 的 provider 設定（多半透過 OpenAI 相容層 / LiteLLM adapter），Day1–5 在雲端先驗證。

> **三個要 Day1 就驗證的技術風險**：①ASR `.tflite` 上 NPU 的**算子相容性**（不支援的 op 會 fallback 回 CPU 抵銷加速）。②**Breeze-ASR-25（large-v2, ~1.5B）塞不進邊緣**，只能放雲端或當 stretch，邊緣改用小 whisper。③**Qwen1.5B vs Breeze2-3B 取捨純為繁中品質**（與加分無關——加分靠 Genio 520 硬體平台）：1.5B 記憶體安全；Breeze2-3B（Q4 ~2GB）繁中較佳但需實測 4GB 是否容得下。

---

## 4. 系統架構（MVP 版）

**即時迴路（永遠在邊緣，離線可用）**：
麥克風 → VAD →（**NPU**）小 whisper ASR →（**CPU**）Qwen2.5-1.5B llama.cpp 鷹架生成 →（CPU/NPU）Piper TTS → 喇叭。感知（ASR/TTS）吃 NPU 硬體加速、生成（LLM）跑 CPU，兩者並行以壓低延遲。目標首音 <300ms、單輪 <2s。互動紀錄寫入本地 SQLite。

**非同步增強（連線時，best-effort）— Hermes Agent 助教大腦**：
背景 daemon 在有網路時，把 SQLite 佇列中的互動摘要（**只傳衍生文字與分數，不傳原始音訊**）以帶裝置憑證的 HTTPS 上傳 → 雲端 **Hermes Agent runtime**（每位學生一個持久記憶 agent，推論接 **Bedrock Claude**）做深度診斷、RAG 教材對齊、cron 排程每日報表 → 寫入教師儀表板。回不來不影響孩子當下互動。

**模式判斷**：不做「對話中途硬切」。每一輪對話開始時鎖定模式（session affinity）；失效偵測改為**事件驅動**（單次請求 timeout 立即降級），而非固定輪詢。（細節見 SPEC v2 §狀態機。）

### 4.1 邊緣記憶體預算（4GB RAM，含常被漏算的項目）

| 項目 | 落點 | 估計 |
| :-- | :-- | :-- |
| OS + 服務常駐 | — | ~600MB |
| Python / Pipecat runtime | CPU | ~300–500MB |
| whisper ASR `.tflite` INT8 | NPU | 80–150MB |
| Qwen2.5-1.5B GGUF Q4（權重） | CPU | ~1.1GB |
| LLM **KV cache**（context 1–2K） | CPU | ~150–400MB |
| Piper TTS 模型 + 音訊緩衝 | CPU | ~150–300MB |
| **合計（峰值）** | | **~2.6–3.1GB** |

> 你原本的帳（1.85GB）**漏了 KV cache、TTS 模型、Python runtime**。真實峰值約 2.6–3.1GB，4GB **可行但需留 headroom**：限制 context 長度、用 mmap 載入、避免同時常駐 Breeze2-3B。**這也印證邊緣選 1.5B 而非 3B 是對的。**

---

## 5. 28 天里程碑

> 假設 Day1 = 2026-07-02（今天），板子 Day6 到手。每階段列「目標／任務／驗收（DoD）」。

### Phase 0 — 軟體先行（Day 1–5，x86/筆電，無板）

- **目標**：在筆電上把整條離線語音管線跑通，不等硬體。
- **任務**：
  1. 架 Pipecat 本地管線骨架（VAD→STT→LLM→TTS）。
  2. 驗證 `Qwen2.5-1.5B-Instruct` GGUF Q4＋llama.cpp 可跑；量測 tokens/sec 與記憶體。
  3. 接 whisper-small 與 Piper，先在 CPU 求跑通（NPU delegate 等板子到手再測）。
  4. 寫「雙語鷹架帶讀」system prompt，跑 7 步對話腳本。
  5. **雲端**：架 Hermes Agent runtime，驗證接 **Bedrock（Claude）** 的 provider 設定；建一個「學生 agent + 每日診斷排程」雛形。
  6. 教師儀表板改由真實 SQLite/雲端診斷資料渲染。
- **DoD**：筆電上對麥克風講一句中英夾雜，玩偶語音回覆＋SQLite 有紀錄＋儀表板顯示一筆評分；Hermes Agent 能用 Bedrock 產出一份 JSON 診斷。

### Phase 1 — 硬體移植（Day 6–13）

- **目標**：整條管線搬上 Genio 520，跑在 ARM CPU。
- **任務**：燒官方 Yocto 映像＋NeuroPilot Public SDK → 裝 Python/依賴 → 移植管線 → **把 whisper ASR 轉 `.tflite` INT8、用 Neuron Delegate 上 NPU，實測算子 fallback 比例與加速**（不理想就退回 CPU faster-whisper）→ LLM 用 llama.cpp 跑 CPU → 量測延遲與**峰值記憶體（對照 §4.1 預算）** → 接麥克風/喇叭 → 處理**回聲消除（AEC）**與 barge-in（玩偶邊講邊聽的自我喚醒問題）。
- **DoD**：板子離線完成一輪對話；記錄實測首音延遲、單輪延遲、記憶體佔用（放進 demo 投影片）。

### Phase 2 — 雙模與教師閉環（Day 14–21）

- **目標**：斷網無縫、資料回流、儀表板閉環。
- **任務**：事件驅動失效偵測＋session affinity；Telemetry 佇列（冪等鍵、重試退避、復連校時）；雲端非同步診斷（真接或高擬真 mock）；儀表板串接真實資料；加 **LLM 兒少內容安全過濾層**。
- **DoD**：現場切飛航模式，對話不中斷；復網後 30 秒內資料進儀表板；儀表板點開學生看到 AI 診斷。

### Phase 3 — 實體整合與調優（Day 22–26）

- **目標**：像個「產品」，且抗現場風險。
- **任務**：玩偶外殼/針織外觀整合、腹部 LED 呼吸燈、噪音環境調降噪、延遲調優；**錄製 60–90 秒備援 demo 影片**（現場硬體出包即切）；準備國產晶片佐證與資料/內容安全投影片。
- **DoD**：完整走查一次；備援影片就緒；所有實測數字上板。

### Phase 4 — 彩排與緩衝（Day 27–28）

- 至少兩次全流程彩排（含斷網橋段）；預留一天 buffer 給突發。

---

## 6. 分工建議（團隊有嵌入式/Python 工程師）

- **嵌入式/系統**：OS 燒錄、驅動、AEC/音訊、板上效能量測、NPU stretch。
- **AI/後端**：Pipecat 管線、模型轉換與量化、prompt、Telemetry、雲端診斷。
- **前端/設計**：教師儀表板、玩偶外觀、demo 投影片與影片。
- **PM/簡報**：評分對照、腳本、實證數據、國產晶片佐證彙整。

---

## 7. 風險登記表

| 風險 | 影響 | 機率 | 緩解 | 備援 |
| :-- | :-- | :-- | :-- | :-- |
| 板子延遲到貨 | 硬體 demo 排程崩 | 中 | Phase 0 先在 x86 跑通，移植面積最小化 | 用 Raspberry Pi 5 或筆電當「邊緣」示範 |
| ASR `.tflite` 算子 fallback 回 CPU | NPU 加速被抵銷、延遲升高 | 中高 | Day6 板到手立即測算子相容性 | 退回 CPU faster-whisper small |
| 4GB 記憶體峰值超標 | OOM、當機 | 中 | 依 §4.1 控 context 長度、mmap、不併載 3B | 降 context、換更小量化（Q3） |
| Hermes Agent 接 Bedrock provider 卡關 | 雲端助教無法推論 | 中 | Day1–5 雲端先驗 OpenAI 相容層/LiteLLM | 直接呼叫 Bedrock API，暫不用 Hermes 記憶層 |
| 邊緣 1.5B 繁中品質不足 | 對話生硬（非加分問題） | 中 | prompt 強化＋雲端 Breeze2/Claude 補品質 | 記憶體允許換 Breeze2-3B Q4 |
| 玩偶自我喚醒（喇叭灌回麥克風） | 對話錯亂 | 高 | 導入 AEC＋播放時暫停辨識 | 按鍵式 push-to-talk |
| 現場網路/斷網不穩 | Demo 翻車 | 中 | 全離線即時迴路本就不依賴網路 | 預錄備援影片 |
| NPU 工具鏈耗時 | 排擠主線 | 高 | 明確列為 stretch，不進 MVP 關鍵路徑 | 直接 CPU |

---

## 8. 落地維運與成本（強化「可行性」評分）

- **單機成本估算**：開發板 + 麥克風陣列 + 喇叭 + 外殼，列出 BOM 概估。
- **維運**：OTA 模型/韌體更新路徑、裝置健康遙測、離線佇列上限與清理策略。
- **資料治理**：兒少語音預設 on-device 處理、只上傳衍生文字/分數、資料落地台灣/AP 區、加密與保存期限、家長同意流程。**這同時是可行性與人本主題的雙重加分點。**

---

## 9. 交叉引用

- 技術細節與規格修正 → **《說說學伴_技術SPEC_v2.md》**
- 決賽五項評分對策與現場分鏡 → **《說說學伴_決賽評分對照與demo腳本.md》**
