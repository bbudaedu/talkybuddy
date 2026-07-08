# 雲端情緒 TTS（ElevenLabs）最小落地設計

日期：2026-07-08　狀態：設計待實作　分支：feat/dualnet-hybrid
母脈絡：`research/17_自然情緒TTS_雲端克隆與邊緣提升.md`、`research/16`（路徑C混成）

## 1. 目標與非目標

**目標**：線上場景（`network_mode == "cloud"`）改用雲端 ElevenLabs 合成，拿到自然、有情緒的中文語音，取代邊緣 Piper `zh_CN-huayan` 的大陸腔平板聽感。與 A1/A2/B 軸正交，是最靠近決賽 Demo 的一小步。

**非目標（YAGNI，本步不做）**：
- streaming 首音優化（本步走單一請求→完整 WAV，比照現有非串流契約）。
- audio-tag 情緒標註（需 LLM 產標籤，另立案）。
- 聲音克隆上傳流程（先用 provider 內建溫柔中文 voice_id；克隆自選人聲後僅換 `ELEVENLABS_VOICE_ID`，架構不動）。
- 邊緣 Matcha-TTS 升級（獨立工作項，見 research/17）。

## 2. 使用者決策（2026-07-08，已確認）
- Provider＝**ElevenLabs 先行**。
- Demo 聲音＝**先用內建自然聲，克隆後補**。
- 降級＝**雲端失敗/離線 → 靜默降級回邊緣 Piper**。
- 路由放法＝**方案 A：路由放 pipeline**（network_mode 已住在 pipeline，改動最小、TTSEngine 契約一字不改）。

## 3. 架構

```
_synth_tts(segments):
  if network_mode == "cloud" and cloud_tts.available():
      wav = cloud_tts.synth(segments)     # 試雲端
      if wav is None:                      # 逾時/錯誤/斷網
          wav = edge_tts.synth(segments)   # 靜默降級回邊緣 Piper
  else:
      wav = edge_tts.synth(segments)       # edge 模式只走邊緣
```

- 現有邊緣 `TTSEngine`（`server/tts.py`）**完全不動**，仍是 `self.tts`。
- 新增 `self.cloud_tts`（`VoicePipeline.__init__` 多收一個 `cloud_tts=None` 參數，預設 None → 現有測試/呼叫端向後相容）。

## 4. 元件

### 4.1 新模組 `server/cloud_tts.py`
`class CloudTTS`，**與 TTSEngine 同契約**：

- `available() -> bool`：`config.ELEVENLABS_API_KEY` 非空 **且** HTTP 客戶端（httpx 或 requests，lazy import）可用。import 期絕不可炸（比照 tts.py/llm.py：重依賴一律 lazy import + try/except）。
- `synth(segments: list[tuple[str, str]]) -> bytes | None`：
  1. 把 `segments` 各 `text` 依序併成單一字串（ElevenLabs 單一 voice **原生中英混讀** → 免切段、免 crossfade 縫合）。空字串 → 回 None。
  2. 呼叫 ElevenLabs TTS API（POST `/v1/text-to-speech/{voice_id}`），帶 `model_id=config.ELEVENLABS_MODEL`、`output_format=pcm_22050`、逾時 `config.CLOUD_TTS_TIMEOUT_S`。
  3. 回傳的 raw PCM（22050Hz/16-bit/mono）**包成 WAV bytes**（沿用 tts.py 的 `TARGET_RATE=22050`、wave 寫檔邏輯 → 前端零改動）。
  4. 逾時、非 2xx、空 body、任何例外 → **回 None**（交由 pipeline 降級）。不 raise。

### 4.2 `server/config.py` 新增（全走環境變數、不進 repo，比照 `PICOVOICE_ACCESS_KEY` 慣例）
- `ELEVENLABS_API_KEY: str = os.environ.get("ELEVENLABS_API_KEY", "")`
- `ELEVENLABS_VOICE_ID: str`（預設一個溫柔中文女聲的 voice_id）
- `ELEVENLABS_MODEL: str`（預設 `eleven_flash_v2_5` 保低延遲；可切 `eleven_v3`）
- `CLOUD_TTS_TIMEOUT_S: float`（預設 6.0）

### 4.3 `server/pipeline.py`
- `__init__(self, asr, llm, tts, cloud_tts=None)`：新增 `self.cloud_tts = cloud_tts`。
- `_synth_tts`：依 §3 路由邏輯改寫（cloud 試→None 則 fall back edge；edge 模式不變）。以 `asyncio.to_thread` 執行（同現況）。latency 記錄維持 `tts_first`。

### 4.4 `server/app.py`
- `cloud_tts_engine = CloudTTS()`；`pipeline = VoicePipeline(asr_engine, llm_engine, tts_engine, cloud_tts=cloud_tts_engine)`。
- `/api/status` 可選增 `"cloud_tts": bool(cloud_tts_engine.available())`（非必要，便於 Demo 除錯）。

## 5. 資料流與相容性
- segments 型別、TurnResult、WS `tts_audio`/`tts_unavailable` 協定**全部不變**。
- WAV 規格（22050Hz/16-bit/mono）與邊緣一致 → 前端播放零改動。
- 雲端與邊緣輸出同格式，降級對前端透明。

## 6. 錯誤處理與降級
| 情境 | 行為 |
|---|---|
| 無金鑰 / HTTP 客戶端缺 | `cloud_tts.available()=False` → pipeline 直接走 edge |
| network_mode=edge | 只走 edge，完全不碰雲端 |
| 雲端逾時/非2xx/斷網/空body | `synth` 回 None → pipeline 靜默 fall back edge `synth` |
| edge 也失敗 | `tts_wav=None` → WS `tts_unavailable`（現有行為） |

降級全程不 raise、不阻斷回覆（Demo 韌性優先，比照現有 DB 寫入失敗處理）。

## 7. 測試
**單元（pytest）**：
- `CloudTTS.available()`：無金鑰→False；有金鑰且客戶端可 import→True（monkeypatch env）。
- `CloudTTS.synth()`：monkeypatch HTTP 呼叫回假 PCM bytes → 驗證回傳是合法 WAV（wave 可讀、22050Hz/mono/16-bit）；HTTP 拋例外或非2xx→回 None；空 segments→回 None。
- `pipeline._synth_tts` 路由：注入 stub `cloud_tts`（synth 回 None）+ stub `tts`（回假 WAV）、`network_mode="cloud"` → 斷言最終 `tts_wav` 來自 edge stub（驗證 fall back）；stub cloud 回 WAV → 斷言用雲端結果；`network_mode="edge"` → 斷言完全沒呼叫 cloud stub。

**手動 verify**：帶真 `ELEVENLABS_API_KEY` 啟動 → UI 切 cloud → 說一句 → 聽到自然情緒中文；故意用錯金鑰/拔網 → 自動降級回 Piper 聲音、無中斷。

## 8. 風險
- ElevenLabs `output_format` / API 回傳格式細節需實作時對照官方文件確認（pcm_22050 是否需 header、header 是否已含）。
- 雲端網路往返延遲：線上可接受；本步不做 streaming，若 Demo 覺得慢再另立 streaming 案。
- 金鑰管理：僅走 env，絕不 commit；`.gitignore` 已排除敏感檔（實作時確認）。
- 商用/兒童內容：ElevenLabs 無兒童專屬條款；克隆他人聲音須書面同意（本步用內建 voice 不涉及）。
