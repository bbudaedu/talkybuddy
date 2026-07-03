# 「說說學伴」技術規格書 v2（決賽 MVP 修訂版）

> 相對 v1 的主要修正：①更正 Genio 520 硬體規格；②改為 Edge-first 三層架構；③導入 NeuroPilot Public 免 NDA 的 NPU/CPU 分工；④雲端改用 Hermes Agent＋Bedrock；⑤修正雙模狀態機的競態與時序；⑥補上內容安全、裝置認證、資料治理；⑦對齊診斷 JSON 與 DB schema。

---

## 1. 硬體規格（更正）

| 項目 | v1（錯誤） | v2（正確） |
| :-- | :-- | :-- |
| SoC | MT8365 | Genio 520（6nm） |
| CPU | 4×Cortex-A53@2.0GHz | **8 核：2×Cortex-A78 + 6×Cortex-A55** |
| NPU | 1.2 TOPS APU | **總算力 10 TOPS（NPU 約 9 TOPS，第 8 代 MediaTek NPU）** |
| GPU | — | Arm Mali-G57 MC2 |
| RAM | 未明 | 4GB（本 MVP 目標配置） |

> v1 把 Genio 520 誤植為 MT8365/1.2 TOPS，導致 KPI 與記憶體推算失真。v2 全數更正。

---

## 2. SDK 權限與運算落點（免 NDA 前提）

> **國產晶片加分（釐清）**：決賽加分（至多 2 分）看的是**開發板核心晶片**是否為國產。Genio 520 由聯發科（我國依法登記之單位）自主研發、擁有 IP，符合「自主研發晶片」條件——**加分與 LLM 選型無關**。用 NeuroPilot Public 讓 ASR/TTS 上 NPU，等於近端原型真正運用國產晶片算力，佐證更完整。

團隊**無 NDA**，僅能使用 **NeuroPilot Public**。運算落點依此設計：

- **感知（ASR / TTS）→ NPU**：模型轉 `.tflite` INT8，透過**公開的 TFLite + Neuron Delegate** 上 NPU 硬體加速（免 NDA）。
- **生成（LLM）→ CPU**：避開需 NDA 的 **GAI Toolkit**，改用開源 **llama.cpp（GGUF Q4）** 跑在 2×Cortex-A78。
- **待驗證**：`.tflite` 的部分算子可能不被 Neuron Delegate 支援而 fallback 回 CPU；板到手第一天即測算子相容性與實際加速比。

---

## 3. 系統架構（Edge-first 三層）

### 3.1 三層職責

1. **邊緣即時層（Genio 520，離線可用，永遠負責即時對話）**
   麥克風 → VAD →（NPU）whisper ASR →（CPU）Qwen2.5-1.5B 鷹架生成 →（NPU/CPU）Piper TTS → 喇叭。互動寫本地 SQLite。目標首音 <300ms、單輪 <2s。
2. **雲端助教層（線上，非同步增強）— Hermes Agent + Bedrock**
   每位學生一個 **Hermes Agent**（持久記憶 runtime，MIT），推論**接 AWS Bedrock（Claude）**；負責深度學習診斷、RAG 教材對齊、cron 排程每日報表、tool 調用。
3. **教師端層**
   儀表板讀取雲端診斷結果，呈現四維評分、弱項、差異化教學建議與長期趨勢。

### 3.2 資料流（文字描述）

即時語音永遠在邊緣閉環，不依賴網路。連線時，邊緣把**衍生文字＋分數（非原始音訊）**經帶憑證 HTTPS 上傳；Hermes Agent 以該學生的持久記憶為脈絡，呼叫 Bedrock 生成結構化診斷，寫入 RDS，教師端渲染。斷網時全部快取於 SQLite，復連背景補送。

### 3.3 為何用 Hermes Agent 當助教大腦

Hermes Agent 內建**持久記憶、自主技能學習、cron 排程、工具調用、原生 MCP**，可編排任一 LLM。對「反思教學 AI 助教」的價值在於**長期追蹤**：agent 記住每位學生的學習歷程與弱項演變，排程每日/每週自動產出診斷，而非每次無狀態呼叫。推論後端用 Bedrock Claude，透過 OpenAI 相容層 / LiteLLM adapter 接入（Day1–5 驗證）。

---

## 4. 邊緣技術棧與記憶體預算

| 模組 | 選型 | 落點 | 記憶體 |
| :-- | :-- | :-- | :-- |
| VAD | silero/webrtc | CPU | 極輕 |
| ASR | whisper-base/small INT8 `.tflite` | NPU | 80–150MB |
| LLM | Qwen2.5-1.5B GGUF Q4 | CPU | ~1.1GB 權重 + 150–400MB KV cache |
| TTS | Piper（zh/en） | CPU/NPU | 150–300MB（含音訊緩衝） |
| Runtime | Python/Pipecat | CPU | 300–500MB |
| OS | Yocto + 常駐服務 | — | ~600MB |
| **峰值合計** | | | **~2.6–3.1GB（4GB 可行，需控 context/mmap）** |

> 升級路徑（記憶體允許時，**純為繁中品質，與加分無關**）：LLM 換 **Llama-Breeze2-3B**、TTS 換 **BreezyVoice**、ASR 品質版 **Breeze-ASR-25** 放雲端。國產晶片加分由 Genio 520 硬體平台獨立達成（見 §2）。

---

## 5. 雙模狀態機（修正競態與時序）

v1 問題：5 秒輪詢無法保證「3 秒失效降級」；對話中途硬切會產生孤兒請求；冷啟動破壞「無縫」。v2 修正：

- **事件驅動失效偵測**：不靠固定輪詢；當一次雲端請求 timeout（例如 >800ms 無回應）即刻標記降級，並由邊緣即時迴路接手該輪。
- **Session affinity（每輪鎖定）**：一個 utterance 開始即鎖定模式，切換只發生在輪與輪之間，杜絕 in-flight race。
- **遲滯（hysteresis）**：恢復雲端需連續數次健康（延遲 <300ms）才升級，避免抖動。
- **邊緣常駐**：因為邊緣本就是主路徑（Edge-first），不存在「冷啟動才載入」的破功問題。

```
[每輪開始] → 是否有健康雲端連線？
   ├─ 是 → 雲端增強（best-effort，逾時即用邊緣結果）
   └─ 否 → 邊緣即時迴路（一律可用）
[每輪結束] → 背景健康探測 + 佇列補送
```

### 5.1 互動觸發與回合管理（喚醒 / 端點 / 打斷）

v1 只有 VAD，且誤把 VAD 當「喚醒」。VAD 只能判斷「有無語音」，無法判斷「是否對玩偶說話」，在多人/吵雜環境會誤觸發。v2 明確定義互動狀態機：

- **主觸發＝Push-to-talk（捏肚子/按鈕）**：最可靠、實作近乎零成本、符合玩偶情境（抱著捏一下說話），為決賽 MVP 主線。
- **Wake word 為 stretch**：有餘力用輕量 KWS（openWakeWord / microWakeWord，可 CPU 或轉 tflite 上 NPU）做「嗨學伴」喚醒，當完成度亮點；自訂中文喚醒詞需訓練，不進關鍵路徑。
- **VAD 改做端點偵測（endpointing）**：觸發後判斷「講完了沒」再送 ASR。**silence timeout 對兒童放寬**（停頓多、拖長音），可調。
- **回合管理＝半雙工**：玩偶說話時暫停辨識（配合 AEC），避免自我喚醒與 barge-in 亂序。push-to-talk 天然半雙工。
- **兜底話術（graceful fallback）**：ASR 低信心 / 聽不懂 / 沉默逾時 → 玩偶固定友善回應（「你可以再說一次嗎？」），避免卡死。demo 穩定性關鍵。

```
[待機] --捏肚子/按鈕--> [聆聽] --VAD偵測講完--> [ASR] --信心足--> [LLM生成] --> [TTS播放(暫停聆聽)] --> [待機]
                                                    └--信心低/逾時--> [兜底話術] --> [待機]
```

---

## 6. 介面與資料庫（修正對齊）

### 6.1 Telemetry payload（加冪等與版本）

```json
{
  "schema_version": "2.0",
  "device_id": "GENIO-520-X992",
  "student_id": "STUDENT-AMING-004",
  "seq": 10432,                     // 裝置單調遞增序號（冪等鍵之一）
  "client_ts": "2026-07-03T10:24:13Z",
  "network_mode": "edge",
  "payload": {
    "student_text": "I want to eat apple.",
    "asr_confidence": 0.92,
    "ai_response_text": "跟我說一遍：I want to eat an apple.",
    "local_scores": { "fluency": 55, "vocabulary": 60, "grammar": 50 }
  }
}
```

- **冪等鍵**：`(device_id, seq)` 唯一，伺服器去重、至少一次送達。
- **時序**：離線無 NTP 時 `client_ts` 可能漂移，復連先校時再補送；伺服器另記 `server_ts`。
- **隱私**：只送衍生文字與分數，**不送原始音訊**。
- **注意**：v1 宣稱能輸出音素級 `/æ/` 錯誤——1.5B LLM 做不到，v2 邊緣只給流暢度/詞彙/句型整體分；音素評測列為未來工作（需 GOP/強制對齊專用模型）。

### 6.2 資料庫 schema（與診斷 JSON 對齊）

診斷 JSON（Hermes Agent 產出）與 DB 欄位一一對應，避免 v1 的欄位漏接：

| 診斷 JSON 欄位 | DB 欄位 | 說明 |
| :-- | :-- | :-- |
| scores.{pronunciation,fluency,vocabulary,grammar} | speech_interactions.*_score | 四維分數 |
| strengths / weaknesses | ai_diagnoses.strengths/weaknesses | 優勢/弱項 |
| emotional_status | ai_diagnoses.emotional_status | 情緒信心（v1 DB 有但 prompt 漏產，已補） |
| instructions.classroom | ai_diagnoses.sug_classroom | 課堂指引 |
| instructions.device | ai_diagnoses.sug_device | 裝置端主題 |
| instructions.peer | ai_diagnoses.sug_peer | 同儕共學（v1 漏產，已補） |

---

## 7. 安全、認證與資料治理（v1 缺、v2 補）

- **兒少內容安全層**：LLM 輸出前經過濾（禁詞、越界話題、情緒安撫話術白名單），面向兒童的生成式 AI 必備。
- **裝置認證**：Telemetry 端點需裝置憑證 / 簽章 token（mTLS 或簽章），不可裸 HTTPS POST。
- **資料落地**：雲端資源選台灣/AP 區；語音預設 on-device 處理；保存期限與加密（at-rest/in-transit）；家長同意流程。
- **教師端存取控制**：帳號權限，僅能看自己班級學生。

---

## 8. KPI（修正）

1. **延遲（拆解，別混為一談）**：TTS 首音 <300ms；端到端「小孩講完 → 玩偶開口」= ASR 推論 + LLM 首 token + TTS 首音，目標 <1.5s（whisper 為批次 ASR，需等講完才轉，故不能只報 300ms）。
2. 邊緣峰值記憶體 <3.3GB（4GB 板可行）。
3. NPU ASR 相對 CPU 有可量測加速（算子 fallback 比例 <一定門檻）。
4. 斷網時即時迴路 0 中斷（Edge-first，本就不依賴網路）；復連 30 秒內補送、以 `(device_id,seq)` 去重零丟失。
5. 噪音 >60dB 下 ASR 字準 >80%（含 AEC）。
