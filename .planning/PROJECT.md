# TalkyBuddy（說說學伴）

## What This Is

TalkyBuddy（說說學伴）是一款給兒童使用的語音 AI 繁體中文（台灣）語言學習夥伴。孩子用口說喚醒它、進行自然對話，並在對話中同時獲得教學內容與發音評估。系統由 Python（FastAPI + Uvicorn + WebSocket，port 8787）後端與 vanilla-JS（Web Audio API）瀏覽器前端組成，目前原型在 PC/筆電（LAN）與雲端 VM 上運行，未來生產目標是搭載 NPU 的 MediaTek Genio 520 開發板。

系統刻意維持**雙模式共存（dual-mode coexistence）**：兩條對話路徑皆為一等公民，各自獨立成需求與階段，不合併。

- **Path 1 — 自架串流回合式（turn-based）**：Pipecat + FunASR STT + sherpa-onnx TTS + Silero VAD（StreamingTurnManager / barge-in）；Porcupine 或 sherpa-onnx 喚醒；ElevenLabs / edge TTS；雲端 LLM 走 Bedrock Converse 或自架 Anthropic-compatible relay。
- **Path 2 — 即時 Nova Sonic 原生 S2S（live）**：native VAD / barge-in / 語音生成；sherpa-onnx KWS 喚醒詞「說說學伴」。

## Core Value

孩子能喊出「說說學伴」，進行一段自然、可即時打斷（barge-in）的口說繁體中文對話——這段對話同時能教學並評估其語言能力——而且**無論走自架串流路徑或即時 Nova Sonic 路徑都能完整達成**。若其他都失敗，這件事必須成立。

## Business Context

- **Customer**: 學齡兒童（國小級英語 / 華語口說學習者）與其家長 / 教師。
- **Revenue model**: 尚未商業化（原型 / 研究階段）。
- **Success metric**（north-star）: 兩條對話路徑皆能跑完整迴圈——wake → converse → spoken reply with barge-in——並由 B1/B3 教學內容驅動對話、發音評估產出分數，全程在家長同意（parental consent）的隱私護欄下運作。
- **Strategy notes**: 由既有 `docs/superpowers/specs/*` 設計文件與 `docs/PRIVACY.md`、`docs/DEPLOY_CLOUD.md` 匯集而成。

## Requirements

完整可勾選需求見 `.planning/REQUIREMENTS.md`。以下為高層摘要。

### Validated

<!-- 需 ship 後確認；目前 codebase 已大量實作，但尚未在此工作流下驗收。 -->

(None yet — ship to validate)

### Active

- [ ] 繁中 ASR 基礎：sherpa-onnx SenseVoice-Small + OpenCC s2twp，faster-whisper 為 feature-flag fallback
- [ ] 喚醒層：Porcupine tap-to-toggle（Path 1 客戶端）與 sherpa-onnx KWS「說說學伴」（Path 2 客戶端），各綁定其模式
- [ ] Path 1 自架全雙工串流對話（StreamingTurnManager + SpeechGate barge-in，真實麥克風 / 喇叭）
- [ ] 雲端大腦與情感語音：Bedrock Converse / 自架 relay + ElevenLabs 情感中文 TTS，含降級鏈
- [ ] Path 2 即時 Nova Sonic S2S 全雙工 hands-free 對話 + 喚醒進入 / 告別離開
- [ ] B1/B3 教學內容串接與本地聲學發音評估（route A，/ws/live）
- [ ] 隱私護欄：音檔不落地、上雲前去識別化、家長同意閘門（跨所有雲端路徑）
- [ ] 跨平台雲端 VM 部署（TLS/WSS、pipeline profiles、edge doll sync）

### Out of Scope

- MediaTek Genio 520 NPU 實機部署 — 未來生產目標，尚未部署，本輪不涵蓋（僅保留 `TALKYBUDDY_PIPELINE_PROFILE` edge 側預留）。
- 多裝置跨機同步壓力測試 — PC 原型為單機；多 Genio 520 情境延後。
- 教師儀表板即時推播（取代 5 秒輪詢）— 現況輪詢可接受，延後。
- 商業化 / 帳務 / 家長 / 學校帳號系統 — 研究原型階段不需要。

## Context

- **既有 codebase**：已有 `server/`（`app.py`、`pipeline.py`、`asr*.py`、`llm.py`、`cloud_llm.py`、`tts.py`、`cloud_tts.py`、`nova_sonic.py`、`scaffold.py`、`diagnose.py`、`lesson.py`、`store.py`、`auth.py`、`guardrails.py`）與 `web/`（vanilla JS + Web Audio API）。詳見 `.planning/codebase/`。
- **兩個 WebSocket 端點並存**：`/ws/talk`（回合式 Path 1）與 `/ws/live`（即時 Path 2）。
- **刻意的降級鏈非衝突**：CloudLLM → EdgeLLM → scaffold；ElevenLabs → edge Piper；SenseVoice → faster-whisper。這些是設計上的容錯層，須保留。
- **設計文件演進**：架構由早期自架串流（A2，7 月初）演進到 Nova Sonic 原生 S2S（7 月中）；兩者在文件集中並存，對應同一對話範圍——本專案決定「共存」而非擇一。
- **已知技術債 / 風險**：ffmpeg 子行程音訊轉檔（阻礙 Genio 520 移植）、LLM `n_ctx=1024`（Genio 520 需降到 512）、espeak-ng-data 的 GPL-3.0 殘留、`app.py` 全域單例。詳見 `.planning/codebase/CONCERNS.md`。

## Constraints

- **Tech stack**: Python 3.x（FastAPI + Uvicorn + WebSocket，port 8787）+ vanilla-JS（Web Audio API）。venv-based，無 Docker / pyproject。
- **Runtime targets**: 現況 PC/筆電（LAN）+ 雲端 VM；未來 Genio 520 NPU（未部署）。以 `TALKYBUDDY_PIPELINE_PROFILE`（edge / cloud）切換。
- **Privacy（跨切面硬限制）**: 兒童語音玩具——音檔絕不持久化、上雲前去識別化、需家長同意、遵循 PDPA/COPPA。此為所有雲端路徑（relay / Bedrock / Nova Sonic）的前置條件。見 `docs/PRIVACY.md`。
- **Licensing**: 傾向 Apache-2.0（sherpa-onnx 取代 piper GPL），但仍有 espeak-ng-data GPL 殘留待清除。
- **Dependencies at risk**: llama-cpp-python 需 C++ 編譯、faster-whisper 依賴 CTranslate2、sherpa-onnx WASM 由 CDN 載入。

## Key Decisions

<!-- 下表混合「本輪使用者鎖定決策」與「來源文件內嵌之提案（proposed，未鎖定 ADR）」。 -->

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| 雙模式共存（Path 1 自架串流 + Path 2 Nova Sonic S2S 皆一等公民，不合併） | 使用者對 INGEST-CONFLICTS 4 個競爭變體的裁決；兩者皆為當前意圖，各自 scope 到自己的模式 | ✓ Good（本輪使用者鎖定） |
| 喚醒引擎依客戶端模式分流：Porcupine（Path 1）/ sherpa-onnx KWS（Path 2） | 兩喚醒引擎對應不同客戶端流程，scope 分離而非擇一 | ✓ Good（本輪使用者鎖定） |
| Route A — `/ws/live`（Nova Sonic）為發音評估主線，本地聲學評分掛在其 PCM buffer | 來源文件 user-confirmed 2026-07-14；但無鎖定 ADR frontmatter | — Pending（proposed，未鎖定） |
| 雲端情感 TTS 走 ElevenLabs，靜默降級到 edge Piper | 來源「使用者決策（已確認）」；文件狀態「設計待實作」 | — Pending（proposed，未鎖定） |
| Nova Sonic live S2S Phase 1 vertical slice（`/ws/live` bidi + transcript 持久化） | 來源含決策摘要 + 2026-07-13 修訂；無鎖定 ADR | — Pending（proposed，未鎖定） |

---
*Last updated: 2026-07-18 after new-project-from-ingest bootstrap*
