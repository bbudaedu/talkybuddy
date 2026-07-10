# 說說學伴 — 跨平台雲端登入設計（決賽 MVP 切片）

- 日期：2026-07-10
- 定位：決賽 MVP 可展示切片（求可跑、可秀，非長期產品架構）
- 前身原型：`talkybuddy/`（單機、單使用者 FastAPI + WebSocket 邊緣管線）

## 1. 目標與成功樣貌

讓**導師與學生都能跨平台登入使用**（PC／平板／手機），系統以雲端為唯一真相；
除了玩偶終端（AIOT，Genio 520，具本地 AI）之外，其他終端也支援全語音對話（AI 在雲端）。

**demo 高潮畫面（成功樣貌，同時也是手動驗收腳本）：**

1. 學生對**玩偶**講話練習（本地 AI 即時對話）。
2. 玩偶連線時把互動 `POST /sync` 上雲。
3. 學生拿**手機**登入 → 看到剛剛那筆進度（雲端為真相）。
4. 手機上**全語音**再練一輪（AI 在雲端）。
5. **導師**在 PC／平板登入 → 看到兩邊 session 合併後的時間線與診斷。

## 2. 已確定的設計約束（brainstorming 決策）

| 軸 | 決策 |
|---|---|
| 定位／交付物 | 決賽 MVP 可展示切片 |
| 終端互動 | 瀏覽器要**全語音對話**，體驗與玩偶一致 |
| 身份 | **單一身份跨終端同步**，雲端是學習資料唯一真相 |
| demo 現場 | **玩偶（本地 AI）＋ 瀏覽器（雲端 AI）都要跑**，秀同一學生兩邊同步 |
| 雲端「腦」方案 | **方案 C：同骨架、抽換元件**（config 切換 provider，鷹架引擎共用） |
| 客戶端 | 瀏覽器／PWA（不做原生 App） |
| 登入 | email + 密碼 + JWT（最簡法） |
| 雲端佈署 | 一台常駐 VM 跑同一個 FastAPI（即時語音需長連線，不能 serverless） |

## 3. 系統拓撲

三種角色連到**同一個雲端後端當唯一真相**，但「腦」跑的位置不同。

```
┌── 玩偶終端（Genio 520，邊緣本地 AI）──────────────┐
│  麥克風/喇叭 → 本地 FastAPI 管線                   │
│  ASR(SenseVoice) + 鷹架引擎 + Qwen1.5B + piper    │  ← 離線可用
│  本地 SQLite（interactions.synced=0）             │
└───────────────┬───────────────────────────────────┘
                │ 連線時：POST /sync（帶 student 身份 token）批次補送
                ▼
┌── 雲端後端（唯一真相）────────────────────────────┐
│  ① Auth 服務：登入 → JWT（含 student_id / role）  │
│  ② 雲端即時語音管線（同骨架，載雲端零件）          │  ← 瀏覽器全語音走這
│     ASR + 共用鷹架引擎 + 升級 LLM + ElevenLabs TTS │
│  ③ 雲端 Store（雲端 DB）＝合併後的真相             │
│     interactions / student_profile / diagnoses     │
└───────────────┬───────────────────────────────────┘
        WSS /ws/talk（帶 token）│  HTTPS /api/*（帶 token）
    ┌───────────┼─────────────┬──────────────┐
    ▼           ▼             ▼              ▼
 手機瀏覽器   PC瀏覽器      平板          導師儀表板
 (學生,全語音) (學生)       (學生)        (teacher.html, 讀多學生)
```

- **薄客戶端**：手機／PC／平板都只是「麥克風＋喇叭＋UI」，沿用同一份 WebSocket 契約
  （CONTRACTS.md 的 `text_input` / `audio_end` / `asr_result` / `reply` / `tts_audio`），
  瀏覽器即可（PWA 可加桌面圖示），不需要原生 App。
- **玩偶**：跑本地管線（現況），只多做一件事——把 `synced=0` 的互動 POST 到雲端。
- **雲端後端**：就是現有 FastAPI app，加上 Auth 與雲端 Store，佈署到一台常駐 VM。

## 4. 身份與登入

- **兩種角色**：`student`、`tutor`。demo 用 email + 密碼登入 → 發 **JWT**
  （內含 `sub=student_id`、`role`，玩偶另有 device 綁定）。前端存 token，WS 與 API 都帶著。
- **取代寫死值**：現在 `config.STUDENT_ID` / `DEVICE_ID` 為全域寫死；
  改成**每條連線從 token 解出 `student_id`**。`store.py` 的函式已支援傳入 `student_id`
  （目前預設吃 config），把預設改成「連線身份」即可，改動小。
- **玩偶的身份**：玩偶用「裝置憑證」換 token，綁定所屬 `student_id`（demo 綁阿明）。
  玩偶送上雲的資料自動歸戶。
- **導師視角**：`tutor` token 能讀「旗下 student 清單」的 profile / diagnoses；
  demo 至少一位導師看得到阿明。登入後 `teacher.html` 改成帶 token 打雲端讀真資料
  （現在是 5 秒輪詢 mock）。
- **JWT**：單一 secret 簽發，不做 refresh token 輪替。

### YAGNI 邊界（demo 不做）

- 註冊流程、密碼重設、email 驗證
- 班級／學校階層（導師↔學生用 seed 好的固定綁定）
- 用 seed 帳號：1 導師 + 1 學生 + 1 玩偶裝置

## 5. 雲端即時語音管線（同骨架、抽換元件）

一套程式碼，靠 config／環境變數決定載哪組零件。
**鷹架引擎（確定性教學主幹）兩邊逐字共用**，是體驗一致的來源；
差異只在 ASR／LLM／TTS 三個 provider。

| 管線段 | 玩偶（edge profile） | 雲端（cloud profile） | 現況 |
|---|---|---|---|
| ASR | SenseVoice int8（本地） | 同 SenseVoice（雲端 CPU）或雲端 ASR | `ASR_BACKEND` 旗標已存在 |
| 鷹架引擎 | **共用** | **共用** | `scaffold.py` 已是確定性主幹 |
| LLM 潤飾 | Qwen2.5-1.5B（llama.cpp） | 可升級較大模型／雲端 LLM | `llm.py` 已抽象，換 provider |
| TTS | piper（sherpa-onnx） | **ElevenLabs 情緒 TTS** | `cloud_tts.py` ＋ `network_mode` 已接線 |

- **收斂旗標**：把現有零散旗標收斂成一個 `PIPELINE_PROFILE = "edge" | "cloud"`，
  各 profile 綁定一組 provider。雲端啟動 env 設 `cloud`，玩偶設 `edge`。
- **不新增協定**：瀏覽器連 `WSS /ws/talk`，送 `audio_end`、收 `tts_audio`——
  與現在 PC 原型逐字相同，客戶端幾乎不用改。
- **併發（主要雲端化工程點）**：雲端 app 要同時服務多條 WS（多終端）。
  現有 pipeline 有 busy 狀態機，需確認**每連線一個 session 狀態**（而非全域單例）。
  這是 plan 的重點任務。

## 6. 同步與資料模型（雲端＝唯一真相）

三個資料表沿用現有 schema（`interactions` / `student_profile` / `diagnoses`，
皆以 `student_id` 為鍵），雲端版搬到雲端 DB。

- **玩偶 → 雲端（上行）**：玩偶本地 SQLite 的 `synced=0` 互動，連線時 `POST /sync`
  （帶 device token）批次補送；成功後本地 `mark_all_synced()`。**佇列雛型已存在**
  （`store.py` 已有 `synced` 旗標、`pending_count()`、`mark_all_synced()`），
  只需把對象從 mock 改成真雲端端點。斷網照常離線對話，復連補送。
- **瀏覽器 → 雲端（直寫）**：瀏覽器沒有本地推論，直接對雲端 store 讀寫，天生就是真相。
- **合併規則**：同一 `student_id` 的互動用 `(device_id, 時間戳/seq)` 去重後**依時間合併**
  成單一時間線。profile／能力值以雲端為準（玩偶開機時可先 `GET /profile` 拉最新）。
- **導師讀取**：`tutor` token → `GET /api/interactions?student=…`、`/api/diagnoses`，
  讀合併後真相。診斷仍走現有非同步助教層（Bedrock／mock）產生。

## 7. 錯誤處理與降級

- **玩偶離線**：本地管線照跑（不動其離線能力），同步佇列堆積、復連補送。
- **雲端管線掛／慢**：瀏覽器終端顯示「連線中／暫時無法」狀態；
  TTS 失敗沿用現有 `tts_unavailable` 事件（前端已處理）；
  LLM 逾時 → 鷹架引擎預存話術頂上（現有兜底路徑）。
- **Consent gate**：`CONSENT_GRANTED=False` → 強制 edge-only、不上雲（現有旗標，沿用）。
  瀏覽器終端本質需雲端，未同意則不開放語音、只顯示說明。
- **Token 失效**：WS／API 回 401，前端導回登入。
- **併發爆量**（demo 不會，列風險）：單 VM 連線數上限，超過排隊或拒絕。

## 8. 實作順序（小步、可回退，每步可單獨 demo）

1. **參數化身份**：把 `STUDENT_ID` / `DEVICE_ID` 從寫死改為每連線注入（不動管線）。
2. **Auth**：seed 帳號、登入端點、JWT 簽發／驗證、WS 與 API 帶 token。
3. **雲端 profile 佈署**：`PIPELINE_PROFILE=cloud`，FastAPI + 雲端 store 上常駐 VM，
   瀏覽器連 WSS 全語音。
4. **真 `/sync`**：玩偶上行改打真雲端端點，去重合併、profile 以雲端為準。
5. **導師端接雲**：`teacher.html` 帶 token 讀雲端真資料。

## 9. 測試

- **單元**：token → 身份解析；`/sync` 去重合併；profile 以雲端為準。
- **契約**：瀏覽器 WS 流程沿用現有 e2e 測試（協定沒變）。
- **手動驗收**＝§1 的 demo 高潮五步，逐步截圖／錄影。

## 10. 風險與前置

- **金鑰**：上雲前**須先撤銷記憶中外露的舊 ElevenLabs 金鑰**，改由雲端 VM 環境變數注入。
- **每連線 session 化**：pipeline 若有全域單例狀態，多終端併發會互相污染——列為重點任務。
- **VM 常駐成本**：即時語音需長連線，不能 serverless；demo 用單台 VM。
- **台灣腔 TTS**：ElevenLabs 台灣腔須付費／自架克隆（見既有 TTS 研究），非本設計範圍決策。

## 11. 明確排除（YAGNI，demo 不做）

- 註冊／改密碼／email 驗證、班級階層、多租戶隔離
- 水平擴展／負載平衡、refresh token、金鑰輪替機制
- 原生 App
