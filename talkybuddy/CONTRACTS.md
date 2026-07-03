# 說說學伴 PC 原型 — 模組契約（所有子代理必須遵守，欄位名逐字一致）

> 目標：在 PC 上「真正可運行」的邊緣管線原型，忠實鏡射未來 Genio 520 架構：
> 瀏覽器(麥克風/喇叭 = 玩偶 I/O) ⇄ WebSocket ⇄ FastAPI 伺服器(= 邊緣板)：ASR(faster-whisper) → 鷹架引擎/LLM(llama.cpp Qwen2.5-1.5B) → TTS(piper zh/en) → SQLite → mock Hermes 診斷(= 雲端層)。

## 目錄

```
talkybuddy/
  .venv/                  # setup_env.sh 建立
  models/                 # qwen2.5-1.5b-instruct-q4_k_m.gguf, piper *.onnx(+.json)
  data/talkybuddy.db      # SQLite
  server/  app.py pipeline.py asr.py llm.py tts.py scaffold.py store.py diagnose.py config.py
  web/     index.html teacher.html
  tests/   test_scaffold.py test_store.py test_pipeline.py test_e2e.py
  scripts/ setup_env.sh run.sh
```

- Python 3.12，全部 import 走套件相對層級：模組彼此 `from server import scaffold` 形式（app 由 repo 根目錄以 `.venv/bin/python -m uvicorn server.app:app` 啟動）。`server/__init__.py`、`tests/__init__.py` 需存在。
- **所有重依賴（faster_whisper、llama_cpp、piper）一律 lazy import + try/except**，模組必須提供 `available()`，不可在 import 期就炸掉——沒模型也要能啟動伺服器（降級路徑）。

## config.py

```python
BASE_DIR: Path            # talkybuddy 根目錄
MODELS_DIR, DATA_DIR, DB_PATH
LLM_GGUF = MODELS_DIR / "qwen2.5-1.5b-instruct-q4_k_m.gguf"
PIPER_ZH = MODELS_DIR / "zh_CN-huayan-medium.onnx"
PIPER_EN = MODELS_DIR / "en_US-lessac-medium.onnx"
ASR_MODEL = "small"       # faster-whisper
DEVICE_ID = "GENIO-520-X992"; STUDENT_ID = "STUDENT-AMING-004"
ASR_CONF_THRESHOLD = 0.45
```

## scaffold.py（規則式雙語鷹架引擎＝確定性主幹，永遠可用）

```python
@dataclass
class ScaffoldResult:
    reply_text: str                    # 顯示用完整回應（繁中+英文）
    tts_segments: list[tuple[str,str]] # [("zh","你說得很棒！"),("en","I want to eat an apple.")] lang ∈ {"zh","en"}
    scores: dict                       # {"fluency":0-100,"vocabulary":0-100,"grammar":0-100}
    safety_triggered: bool
    target_sentence: str | None        # 要學生跟讀的英文句

def respond(student_text: str) -> ScaffoldResult
def split_tts_segments(text: str) -> list[tuple[str,str]]   # 中英混句切段（供 LLM 回覆重用）
def compute_scores(student_text: str) -> dict
def safety_check(text: str) -> bool                          # True=命中禁詞
FALLBACK_LINES: list[str]   # ≥3 句兜底話術
```

規格：詞庫 ≥30 個國小詞（中→英，含分類）；處理 a/an；中英夾雜→替換中文詞產完整英文句；純中文→鼓勵＋整句英文；純英文→簡單文法修正(a/an、複數、大小寫)或稱讚＋延伸問句；禁詞→安撫話術。純標準函式庫，零第三方依賴，可獨立測試。

## asr.py

```python
class ASREngine:
    def available(self) -> bool
    def transcribe(self, wav_path: str) -> tuple[str, float]  # (text, confidence 0-1)
```
faster-whisper `small`、`compute_type="int8"`、`language=None` 自動偵測（中英夾雜）。confidence 用平均 `avg_logprob` 映射到 0-1（exp(avg_logprob) 夾 0-1）。單例懶載入。

## llm.py

```python
class EdgeLLM:
    def available(self) -> bool
    def generate(self, student_text: str, scaffold: "ScaffoldResult") -> str | None
```
llama-cpp-python 載 `LLM_GGUF`（n_ctx=1024, n_threads=4）。System prompt：台灣國小英語鷹架家教，回覆 ≤2 句繁中鼓勵＋1 句目標英文句（把 scaffold.target_sentence 塞進 prompt 要求圍繞它），輸出禁 markdown/emoji。逾時（>8s）或未載入回 None——**pipeline 以 scaffold.reply_text 為準，LLM 只是加值**。生成後仍過 `scaffold.safety_check`，命中回 None。

## tts.py

```python
class TTSEngine:
    def available(self) -> bool
    def synth(self, segments: list[tuple[str,str]]) -> bytes | None  # 完整 WAV (16-bit PCM mono)
```
piper-tts Python API：zh 段用 PIPER_ZH、en 段用 PIPER_EN，各段合成後重取樣到 22050Hz 串接成單一 WAV bytes。任一 voice 缺→只合成可用語言段；全缺→None（前端降級用瀏覽器 speechSynthesis）。

## store.py（SQLite，欄位名 = 決賽契約）

```python
def init_db() -> None
def add_interaction(d: dict) -> int   # 回傳 seq；seq 自動 = max+1
def list_interactions(limit=50) -> list[dict]
def pending_count() -> int            # synced=0 筆數
def mark_all_synced() -> list[dict]   # 標記並回傳剛同步的紀錄
def add_diagnosis(d: dict) -> None
def list_diagnoses() -> list[dict]    # 依 date 升冪
def seed_demo() -> None               # 首次啟動灌 14 天診斷 + 20 筆歷史互動（僅當兩表皆空）
```

interaction dict（API/DB 一致）：
```json
{"seq":1,"device_id":"GENIO-520-X992","student_id":"STUDENT-AMING-004",
 "ts":"2026-07-03T10:24:13+08:00","network_mode":"edge",
 "student_text":"...","asr_confidence":0.92,"ai_response_text":"...",
 "scores":{"fluency":55,"vocabulary":60,"grammar":50},
 "latency_ms":{"asr":320,"llm":900,"tts_first":280,"round_total":1450},
 "synced":false}
```
diagnosis dict：
```json
{"date":"2026-07-03",
 "scores":{"pronunciation":62,"fluency":58,"vocabulary":65,"grammar":54},
 "strengths":["..."],"weaknesses":["..."],
 "emotional_status":"...",
 "instructions":{"classroom":"...","device":"...","peer":"..."}}
```
DB 表：`interactions`(seq INTEGER PRIMARY KEY, payload TEXT/JSON, synced INTEGER)、`diagnoses`(date TEXT PRIMARY KEY, payload TEXT)。`sqlite3` + `check_same_thread=False` + threading.Lock。

## diagnose.py（mock Hermes Agent + Bedrock Claude 雲端層）

```python
def generate_diagnosis(interactions: list[dict], prev: dict | None) -> dict
```
規則式依近期互動動態產出（平均分數→四維、缺 a/an→weaknesses 寫冠詞、中文比例高→夾雜偏高、分數趨勢→emotional_status），instructions 三欄給老師可讀的具體建議。輸出嚴格符合 diagnosis dict。若環境變數 `ANTHROPIC_API_KEY` 存在可走真 API（model=claude-sonnet-5，失敗 fallback mock）——mock 為預設與 demo 主線。

## pipeline.py（狀態機，SPEC v2 §5.1）

```python
class TurnResult:  # dataclass
    state_events: list[str]; asr_text: str; asr_conf: float
    reply_text: str; tts_wav: bytes | None; scores: dict
    latency_ms: dict; fallback: bool; seq: int

class VoicePipeline:
    def __init__(self, asr, llm, tts)          # 依賴注入，測試可傳 stub
    network_mode: str                          # "edge"|"cloud"（由 app 切換）
    async def run_turn_audio(self, webm_bytes: bytes, emit) -> TurnResult
    async def run_turn_text(self, text: str, emit) -> TurnResult
```
`emit(dict)` 為 async callback，逐階段送 `{"type":"state","state":...}` 等事件。流程：webm→ffmpeg 轉 16k wav(subprocess，`-loglevel error`)→ASR→信心 < ASR_CONF_THRESHOLD 或空文字→兜底話術(FALLBACK_LINES 輪替)＋不寫低分紀錄；否則 scaffold.respond→（LLM available 時 asyncio.to_thread 生成，逾時 8s 用 scaffold 結果）→TTS→store.add_interaction(synced = network_mode=="cloud")。所有階段量測毫秒寫入 latency_ms。半雙工：單一 session 同時只跑一輪（asyncio.Lock，重入直接拒絕）。

## app.py（FastAPI）

- `GET /` → web/index.html；`GET /teacher` → web/teacher.html（FileResponse；web/ 也掛 StaticFiles /static）
- `GET /api/status` → `{"asr":bool,"llm":bool,"tts":bool,"network_mode":"edge|cloud","pending":int}`
- `POST /api/network_mode` body `{"mode":"edge"|"cloud"}`；切到 cloud 時：mark_all_synced() → 以近 10 筆互動 generate_diagnosis → add_diagnosis → 回 `{"synced":n,"new_diagnosis":{...}}`
- `GET /api/interactions?limit=50`、`GET /api/diagnoses`
- `POST /api/seed_reset` → 清空重種子（demo 重置）
- `WS /ws/talk`：
  - client→server 文字 frame：`{"type":"text_input","text":"..."}`（快速語句）或 `{"type":"audio_end"}`；binary frame = 一段完整 webm/ogg 錄音（push-to-talk 放開後整包送）
  - server→client：`{"type":"state","state":"asr|thinking|tts|idle"}`、`{"type":"asr_result","text","confidence"}`、`{"type":"reply","text","scores","latency_ms","fallback":bool,"seq":int}`、`{"type":"tts_audio","wav_b64":"..."}`（無 TTS 時 `{"type":"tts_unavailable"}`）、`{"type":"busy"}`
- startup：init_db(); seed_demo(); 預熱引擎放背景 thread，不擋啟動。

## web/（兩頁，零外部資源、繁體中文、RWD 375px）

- `index.html` 學生端：SVG 企鵝＋LED 呼吸燈（狀態變色）、押住肚子 push-to-talk（MediaRecorder→WS binary）、快速語句按鈕 ≥6、對話氣泡串、狀態列（edge/cloud badge、延遲 ms、待同步 N 筆）、「✈️ 飛航模式」開關（呼叫 /api/network_mode；cloud→edge=開飛航）、TTS 播放 wav_b64（Audio element），tts_unavailable 時 fallback speechSynthesis。麥克風不可用（非 https/無權限）→自動提示改用快速語句。
- `teacher.html` 教師端：讀 /api/diagnoses、/api/interactions；四維雷達圖（最新 vs 14 天前）、14 天四線趨勢折線圖（hover tooltip）、最新 AI 診斷卡（strengths/weaknesses/emotional_status/instructions 三欄，標示 Hermes Agent + Bedrock Claude）、互動紀錄表（20 筆，分數 meter、edge/cloud badge、同步狀態）、5 秒 poll、「重置示範資料」。手刻 SVG，深色系（surface #1a1a19、ink #fff/#c3c2b7/#898781、grid #2c2c2a；四維色 pronunciation #3987e5 / fluency #199e70 / vocabulary #c98500 / grammar #9085e9；文字不穿 series 色）。

## 測試

pytest（`.venv/bin/python -m pytest tests/ -x -q`，от repo 根執行）。test_scaffold / test_store 純單元；test_pipeline 用 stub ASR/LLM/TTS 注入測狀態機（含低信心兜底、LLM 逾時降級、半雙工拒絕重入）；test_e2e 用 httpx+ASGITransport 打 API＋WS text_input 全流程（不需真模型）。
