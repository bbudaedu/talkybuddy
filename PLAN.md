# 說說學伴 (TalkyBuddy) 決賽 MVP 實作計畫

本計畫旨在黑客松（3 天決賽）時限內，使用 MediaTek Genio 520 開發板結合雲端與 Web 監控板，建置可正常運作的兒童英語陪伴玩偶原型。

## 1. 實作範圍與架構

### 1.1 邊緣端 (Genio 520) 語音對話迴路
* **軟硬解耦架構**：將程式拆分為 `hardware_agent.py` (負責 GPIO 按鈕 Pinch-to-talk、LED 狀態燈控制) 與 `voice_agent.py` (語音 Pipeline 狀態機)，兩者透過安全隊列進行事件驅動通訊，以利在一般筆電進行軟體 mock 測試。
* **語音模型預載**：將 Whisper-base ONNX 與 Piper TTS ONNX 模型檔預載入 `talkybuddy/models/`，確保 100% 離線可用，絕不嘗試運行時從網路下載模型。
* **運算落點與加分策略**：ASR (Whisper) 與 TTS (Piper) 利用免 NDA 的 NeuroPilot Public (TFLite Neuron Delegate) 部署至 NPU，LLM (llama.cpp Qwen2.5 1.5B GGUF) 跑在 CPU（限制 Context 512 tokens 以防 OOM 崩潰）。
* **解碼優化**：改用 Python `soundfile` 在記憶體中進行 WebM 到 WAV 的音訊解碼與 stream 送出，完全捨棄 FFmpeg 外部 `subprocess` 呼叫，消除進程切換延遲與 Flash I/O 損耗。

### 1.2 Web 即時投影監控面板 (React + Tailwind)
* 設計精緻的 React 投影大螢幕介面，使用 Recharts 視覺化庫動態更新 ASR 文字、情緒雷達圖、四維評分柱狀圖與 Claude 發音改進建議。

### 1.3 雲端中介伺服器 (FastAPI)
* 接收 Genio 520 送出的衍生文字 Telemetry，利用 SQLite 保存。
* **可靠離線同步**： telemetry Payload 引入單調遞增序號 `seq` 與裝置識別碼 `device_id`。當網路復連補送時，FastAPI 端依此進行冪等去重 (Idempotency check)，確保診斷數據精確。
* 雲端助教大腦：維持使用 Hermes Agent 框架對接 AWS Bedrock (Claude 3.5 Sonnet)，處理長期學生的學習軌跡與非同步診斷生成。
* 提供手動在線/離線狀態切換器，以演示雙模降級。

---

## 2. 開發階段計畫

### 階段一：邊緣端語音管線與硬體 IO 基礎測試 (Day 1)
1. 安裝板載 Ubuntu 與 `pip install sherpa-onnx`。
2. **提早排除音訊衝突**：外接麥克風與喇叭，使用 ALSA 測試錄音與播放，排查 Python 音訊搶佔 (Device Busy) 問題。
3. ** Neuron Delegate 部署實測**：首日保留 4 小時實測 Neuron NPU 編譯部署 Whisper 與 Piper。若相容則使用 NPU 演示；若出現算子不支持，立即降級為 CPU 運行，但在報告中列出算子相容性盲點。

### 階段二：雲端中介與即時監控 Web 實作 (Day 2)
1. 建立 FastAPI 伺服器，對接 AWS Bedrock (Claude 3.5 Sonnet)。
2. 設計評語生成與分數計算 Prompt。
3. 建置 React 前端監控面板，以 WebSockets 連接 FastAPI，動態更新 ASR 文字與分數。

### 階段三：軟硬串接與降級 Demo 測試 (Day 3)
1. 實作 Genio 520 捏肚子按鈕 (GPIO) 與 ASR 錄音觸發綁定。
2. 連接 Genio 520 與 FastAPI Telemetry POST API。
3. 測試拔除網線（或手動切換開關），驗證「斷網時玩偶仍可順暢離線語音互動」之雙模降級效果。
4. 進行整體 Demo 腳本（帶玩偶介紹家鄉的水果與部落）流暢度排練。

---

## 3. 測試與覆蓋率計畫
* **雜音兜底與防禦測試**：針對 VAD 與 ASR 信心度編寫單元測試。若 ASR 信心度低於 0.5（雜音干擾），直接在本地端播放「我沒聽清楚」兜底話術，不送往本地 LLM。
* VAD silence timeout 放寬至 1500ms，對兒童停頓進行容錯測試。
* ASR 與 TTS 首音延遲實測（目標 TTS 首音 <300ms，端到端對話 <1.5s）。
* 本地邏輯單元測試（如語意引導、降級狀態機），目標覆蓋率 50% 以上。

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 4 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**VERDICT:** ENG CLEARED — ready to implement.

NO UNRESOLVED DECISIONS
