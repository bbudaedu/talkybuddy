# 說說學伴 TalkyBuddy — PC 原型

**在 PC 上「真正可運行」的邊緣三層架構原型，忠實鏡射未來 MediaTek Genio 520 開發板架構**：ASR 用 sherpa-onnx + SenseVoice-Small（int8，OpenCC s2twp；faster-whisper 為 flag 可切換 fallback）真跑語音辨識、LLM 用 llama.cpp 真跑 Qwen2.5-1.5B-Instruct 對話生成、TTS 用 sherpa-onnx（Apache-2.0，載入 Piper 格式聲音檔）真跑中英雙語語音合成 —— 沒有任何一段是假資料或錄好的展示影片。

## 一句話定位

一隻會聽、會想、會說的「英語學伴玩偶」，邊緣端（瀏覽器模擬玩偶的麥克風/喇叭 ⇄ 本機 FastAPI 伺服器模擬 Genio 520 開發板）100% 離線可運作，雲端端（mock Hermes Agent + Bedrock Claude）在有網路時才補上長期學習診斷。

## 架構圖

```
┌─────────────────────────────┐       ┌────────────────────────────────────────────────┐
│   瀏覽器（= 玩偶 I/O）        │  WS   │   FastAPI 伺服器（= Genio 520 邊緣板）           │
│                              │◄─────►│                                                  │
│  index.html 學生端            │/ws/talk│  ASR (sherpa-onnx + SenseVoice-Small int8,       │
│                              │       │       OpenCC s2twp；faster-whisper 為 flag        │
│                              │       │       可切換 fallback)                            │
│   - 押肚子 push-to-talk       │       │       │                                          │
│   - 快速語句按鈕（麥克風降級） │       │       ▼                                          │
│   - LED 呼吸燈狀態機          │       │  鷹架引擎 scaffold.py（規則式，永遠可用，主幹）  │
│   - 飛航模式開關              │       │       │                                          │
│   - TTS 播放 / speechSynthesis│       │       ▼（LLM 可用時加值，逾時 8s 降級）           │
│    降級                      │       │  EdgeLLM llama.cpp（Qwen2.5-1.5B GGUF）          │
│                              │       │       │                                          │
│  teacher.html 教師端          │ HTTP  │       ▼                                          │
│   - 四維雷達圖 / 14天趨勢      │◄─────►│  TTS sherpa-onnx (zh/en) → SQLite (interactions/ │
│   - AI 診斷卡 / 互動紀錄表     │ poll  │       diagnoses)                                │
│   - 5 秒輪詢                  │       │       │                                          │
└─────────────────────────────┘       │       ▼ 網路模式切到 cloud 時                     │
                                       │  diagnose.py（mock Hermes Agent + Bedrock Claude │
                                       │  雲端層；有 ANTHROPIC_API_KEY 時可走真 API）      │
                                       └────────────────────────────────────────────────┘
```

## 安裝

```bash
cd /home/budaedu/hackathon/talkybuddy
bash scripts/setup_env.sh
```

會建立 `.venv`、安裝 FastAPI/faster-whisper/sherpa-onnx/opencc/llama-cpp-python 等套件，並下載模型到 `models/`（Qwen2.5-1.5B GGUF、faster-whisper small、Piper 格式 zh/en 聲音檔，交由 sherpa-onnx 載入合成）。ASR 需 `opencc`，首次安裝會下載 SenseVoice int8 模型（~226MB）。`llama-cpp-python` 編譯較久且可能失敗，失敗不擋整體流程，系統會自動降級為純規則鷹架引擎（見下方降級行為表）。完成後會產生 `.venv_ready` 標記檔。

## 啟動

```bash
bash scripts/run.sh
```

實際執行 `\.venv/bin/python -m uvicorn server.app:app --host 0.0.0.0 --port 8787`：

- 學生端：http://localhost:8787
- 教師端：http://localhost:8787/teacher
- 手機同網段：`http://<本機區網 IP>:8787`（例如 `http://192.168.100.200:8787`；同 Wi-Fi 下用 `hostname -I` 查本機 IP）

伺服器啟動時（lifespan）會 `init_db()` + `seed_demo()`（首次啟動灌 14 天診斷 + 20 筆歷史互動），並把三顆引擎的預熱丟到背景 daemon thread，不會擋住伺服器啟動——即使模型還在載入，網頁也能立刻打開。

## 決賽 4 分鐘 Demo 操作對照

> 伺服器預設以「邊緣模式」開機（`network_mode = "edge"`），對應學生端「✈️ 飛航模式」開關預設為 **ON**（離線優先，符合玩偶「先求可用、雲端錦上添花」的設計哲學）。

| 時間 | 操作 | 對應路由/事件 | 預期畫面 |
|---|---|---|---|
| 0:00–1:00 | 開啟學生端 `/`，用**快速語句**按鈕連續互動 2–3 句（或按住企鵝肚子錄音） | `WS /ws/talk` `text_input` / binary → `state`→`asr_result`→`reply`→`tts_audio` | LED 呼吸燈依 idle/listen/think/talk 變色、對話氣泡即時顯示、sherpa-onnx 語音播放、分數即時顯示 |
| 1:00–2:00 | 點擊「✈️ 飛航模式」開關（確保切到/維持邊緣模式）再繼續互動幾句 | `POST /api/network_mode {"mode":"edge"}` | 狀態列 badge 顯示「edge」、待同步筆數持續累加，回覆依然即時（ASR/LLM/TTS 全在本機跑，斷網也不影響對話） |
| 2:00–3:00 | 再點一次「✈️ 飛航模式」關閉（切回雲端） | `POST /api/network_mode {"mode":"cloud"}` → `mark_all_synced()` + `generate_diagnosis()` | Toast「☁️ 已同步 N 筆｜Hermes Agent 已產出最新診斷」、待同步筆數歸零 |
| 3:00–4:00 | 切到教師端 `/teacher` | `GET /api/diagnoses`、`GET /api/interactions`（5 秒輪詢） | 四維能力雷達圖（最新 vs 14 天前）、14 天四線趨勢折線圖、最新 AI 診斷卡（strengths/weaknesses/emotional_status/instructions，標示 Hermes Agent + Bedrock Claude）、互動紀錄表（分數 meter、edge/cloud badge） |

需要重新展示時可在教師端點「重置示範資料」（`POST /api/seed_reset`）清空重灌種子資料。

## 測試

```bash
.venv/bin/python -m pytest tests/ -q
```

> 測試套件已建置：`test_scaffold.py`／`test_store.py`／`test_pipeline.py`／`test_e2e.py`／`test_asr_backend.py`，共 **50 passed**（另有 1 個既有的 `StarletteDeprecationWarning`，與本專案改動無關），server/ 覆蓋率約 **64%**（超過 PLAN §3 的 50% 目標）。test_pipeline 以 Stub 注入引擎測狀態機（正常回合、低信心兜底、LLM 逾時降級、半雙工 busy），test_e2e 以 httpx ASGITransport 打 API、Starlette TestClient 測 WebSocket，皆不需載入真模型。

## 降級行為表

| 情境 | 觸發條件 | 降級行為 |
|---|---|---|
| 無麥克風權限 / 非 HTTPS | `micAvailable === false` | 學生端自動提示「麥克風不可用，請用下方快速語句」，隱藏 push-to-talk 提示，改用**快速語句**按鈕（≥6 句）送出 `text_input` |
| ASR 信心過低 / 無模型 | `asr_engine.available()` 為 false，或辨識信心 `< ASR_CONF_THRESHOLD`（0.5）、空文字。SenseVoice 無 logprob，改以「空辨識結果」判斷兜底 | 不進 LLM，直接播放 `FALLBACK_LINES` 兜底話術（≥3 句輪替），不寫入低分紀錄 |
| 無 LLM（llama-cpp-python 未安裝成功 / 模型檔缺失） | `llm_engine.available()` 為 false，或生成逾時 > 8 秒，或生成內容命中 `safety_check` 禁詞 | 直接採用 `scaffold.respond()` 的規則式鷹架引擎回覆（詞庫 ≥30 個國小詞、a/an 處理、中英夾雜替換、文法修正）—— **scaffold 永遠是主幹，LLM 只是加值**，零第三方依賴、必定可用 |
| 無 TTS（Piper 格式聲音檔缺失 / sherpa-onnx 未安裝） | `tts_engine.available()` 為 false，或該語言 voice 缺失 | 伺服器回 `{"type":"tts_unavailable"}`；前端改用瀏覽器內建 `speechSynthesis`，中英分段朗讀；若瀏覽器也不支援則僅顯示文字氣泡 |
| 無網路 / 手動開飛航模式 | `network_mode == "edge"` | 互動照常進行並寫入 SQLite（`synced=false`），僅待同步筆數累加；不呼叫 `diagnose.py`，教師端診斷維持上次結果 |

## 已知限制

- 本專案為 **x86 PC 軟體先行原型**，尚未實際部署於 Genio 520 開發板／NeuroPilot NPU，NPU 算子相容性未經驗證。
- 引擎本體（asr/llm/tts）的測試以 Stub 注入為主，未在 CI 強制載入真模型；真模型的端到端行為以手動冒煙測試驗證（見下方 demo 步驟）。
- webm → wav 轉檔目前仍走系統 `ffmpeg` subprocess（`-loglevel error`，逾時 10 秒），需環境已安裝 `ffmpeg` 執行檔；PLAN.md 規劃日後改用 Python `soundfile` 記憶體內解碼以消除此依賴。
- 半雙工限制：單一 WebSocket session 同一時間只能跑一輪對話（`asyncio.Lock`），重入會收到 `{"type":"busy"}`。
- `diagnose.py` 預設為規則式 mock 邏輯；僅當環境變數 `ANTHROPIC_API_KEY` 存在時才會嘗試呼叫真正 Claude API（`model=claude-sonnet-5`），失敗會自動 fallback 回 mock，mock 為 demo 主線。
- `llama-cpp-python` 屬原始碼編譯安裝，若目標環境編譯失敗，LLM 加值層會整層停用（不影響核心可用性，見降級表）。
- SQLite 為單檔案本機資料庫，`device_id`/`student_id` 為 demo 固定值，未實作多裝置/多學生的真實帳號與衝突解決機制。
- TTS 合成後端已由 piper-tts（GPL-3.0）改為 sherpa-onnx（Apache-2.0），聲音檔沿用既有 Piper 格式 `.onnx`（zh_CN-huayan-medium / en_US-lessac-medium）；但語音合成仍需的 espeak-ng-data 音素化資料，其上游授權為 espeak-ng 的 GPL-3.0（`scripts/setup_env.sh` 目前仍安裝 piper-tts 僅為取得其內附的 espeak-ng-data，並未用於實際合成），此為已知殘留授權風險，待找到 Apache/MIT 授權替代來源後可再移除。
- 教師端投影可視性（教室後排字級對比度）、錯誤狀態細分（暫時離線顯示快取 vs 請求失敗）、無障礙逐項檢查（鍵盤導覽、SVG `aria-label` 完整度）尚未完整驗證，詳見 `/home/budaedu/hackathon/PLAN.md` 待補設計決策清單。
- SenseVoice 權重授權為 NOASSERTION（近 Apache），黑客松展示無虞，商用前建議法務覆核；OpenCC s2twp 一簡對多繁歧義字為已知極少數風險，僅影響書面逐字稿。
