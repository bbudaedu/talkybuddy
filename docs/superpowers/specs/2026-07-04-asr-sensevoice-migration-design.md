# ASR 換 sherpa-onnx + SenseVoice-Small + OpenCC 設計書

- 日期：2026-07-04
- 範圍：`talkybuddy/` PC 原型的 ASR 引擎層
- 依據：`research/03_ASR選型.md`（裁決：sherpa-onnx + SenseVoice-Small int8 + OpenCC s2twp 為邊緣主力 ASR）
- 現況基線：`server/asr.py` = faster-whisper "small" int8

## 目標

把 ASR 從 faster-whisper 換成 sherpa-onnx + SenseVoice-Small（int8）+ OpenCC 簡轉繁，
並保留 faster-whisper 作為 feature flag 可切換的 fallback，**不破壞任何現有可運行狀態**。

## 非目標（本次不做）

- NPU（.tflite + Neuron Delegate）化 ASR —— 研究列為研發性工作，不排入關鍵路徑。
- streaming zipformer 串流辨識 —— 備選升級路徑，先用非串流 SenseVoice 把管線跑通。
- 改動 `pipeline.py` 狀態機、`app.py` 以外的呼叫端契約。
- 移除 espeak-ng-data GPL-3.0 殘留風險（屬 TTS 既有議題，非本次範圍）。

## 契約（維持不變）

`pipeline.py` 對 ASR 的呼叫完全不動，沿用 CONTRACTS.md：

```
class ASREngine
  available() -> bool
  transcribe(wav_path: str) -> tuple[str, float]   # (text, confidence 0-1)
```

- `transcribe` 收 16kHz mono wav 路徑（ffmpeg 已在 `pipeline._webm_to_wav` 轉好）。
- 任何失敗回 `("", 0.0)`，import 期不可炸（lazy import + try/except）。
- 降級安全：`available()=False` 時 pipeline 走 `FALLBACK_LINES` 兜底，行為不變。

## 架構：分檔

現有單一 `server/asr.py` 拆為：

| 檔案 | 職責 |
|---|---|
| `server/asr_base.py` | 定義共同介面說明 + `get_asr_engine()` 工廠（讀 `config.ASR_BACKEND` 回對應實例） |
| `server/asr_whisper.py` | 現有 faster-whisper 實作原封搬入，保留為 fallback |
| `server/asr_sensevoice.py` | 新增：sherpa-onnx `OfflineRecognizer`（SenseVoice）+ OpenCC 簡轉繁 |
| `server/asr.py` | 薄 shim：`ASREngine = get_asr_engine()` 之類，維持 `from server.asr import ASREngine` import 相容 |

分檔理由：單檔塞兩套後端會變大且雜；拆開後每檔單一職責、可獨立測試，符合「小而可回退」。

實作前先確認 `app.py` 建立 ASR 實例的方式（`from server.asr import ASREngine` 直接實例化，
或其他），確保 shim 對外符號相容、不需改 `app.py`（若需最小改動則改為呼叫工廠）。

## SenseVoice 引擎（`asr_sensevoice.py`）

- **模型**：`sherpa-onnx-sense-voice-zh-en-ja-ko-yue`（int8，約 226MB），置於
  `models/sherpa-onnx-sense-voice/`。由 `setup_env.sh` 從 k2-fsa 官方 release 下載並解壓。
- **載入**：`sherpa_onnx.OfflineRecognizer.from_sense_voice(model=..., tokens=..., use_itn=True)`，
  單例懶載入 + `threading.Lock`，鏡射 `tts.py` 既有降級安全範式。
- **available()**：`sherpa_onnx` 可 import 且模型檔存在 → True；載入失敗過 → False；
  尚未載入 → 只探測 import + 檔案存在，不觸發載入。
- **transcribe(wav_path)**：
  1. `soundfile.read(wav_path)` 讀 16kHz mono float32。
  2. 建 stream → `recognizer.decode_stream` → 取 `result.text`。
  3. `text.strip()` 為空 → 回 `("", 0.0)`。
  4. 非空 → OpenCC `s2twp` 簡轉繁 → 回 `(繁體text, 1.0)`。
  5. 任何例外 → 回 `("", 0.0)`。

### 信心分數決策

SenseVoice 為非自回歸架構，sherpa-onnx 辨識結果無 `avg_logprob`。
採「**空字串即兜底、非空給 1.0**」：

- 空/純雜音 → SenseVoice 回空字串 → `("", 0.0)` → pipeline 走兜底。
- 有辨識結果 → `confidence=1.0`。
- 放棄 logprob 式雜音過濾，改依 SenseVoice 自身的空結果判斷。
- `ASR_CONF_THRESHOLD=0.5` 常數保留不動（whisper backend 仍會用到）。

### 簡轉繁

- OpenCC `s2twp`（簡體→繁體＋台灣慣用詞），在 `transcribe` 回傳前套用。
- OpenCC 缺失/初始化失敗 → graceful 降級為原文（不 throw），並使該次不影響可用性。
- 新增依賴 `opencc`（`setup_env.sh` 補裝）。

## Feature flag（`config.py`）

```python
ASR_BACKEND = "sensevoice"                       # "sensevoice" | "whisper"
SENSEVOICE_DIR = MODELS_DIR / "sherpa-onnx-sense-voice"
OPENCC_CONFIG = "s2twp"
```

- `get_asr_engine()` 讀 `ASR_BACKEND` 回對應實例。
- **不自動 fallback**：`ASR_BACKEND=sensevoice` 但模型不可用時，`available()=False`，
  pipeline 走兜底話術；不靜默切回 whisper。行為明確、可預期。
- 切回 whisper 只需改一個常數 `ASR_BACKEND="whisper"`。

## 錯誤處理與降級

沿用現有五層降級表，ASR 這層對外行為不變：

- SenseVoice 不可用 → `available()=False` → pipeline 兜底。
- faster-whisper 路徑原樣保留，可用 flag 即時切換 A/B。
- 不破壞任何現有可運行狀態（最高原則）。

## 測試

- `test_pipeline.py`：用 stub 注入 ASR，契約未變，**不受影響**。
- 新增 `test_asr_backend.py`（不載入真模型）：
  - `get_asr_engine()` 依 `ASR_BACKEND` flag 回正確類別。
  - SenseVoice 引擎在模型檔缺失時 `available()` 安全回 False、`transcribe` 回 `("", 0.0)`。
  - OpenCC 簡轉繁正確：`"我爱学习" → "我愛學習"`（或以 stub 驗證轉換有被呼叫，避免硬綁 OpenCC 版本詞庫差異）。
  - 空辨識結果 → `("", 0.0)`；非空 stub 結果 → confidence=1.0 且輸出為繁體。
- 真模型端到端：手動冒煙（下載模型後 `run.sh` + WebSocket `/ws/talk` 送語音，
  確認 `asr_result` 為繁體、單輪延遲可接受）。

## setup_env.sh 變更

- 新增 `pip install opencc`。
- 新增下載 `sherpa-onnx-sense-voice-zh-en-ja-ko-yue`（int8）到 `models/sherpa-onnx-sense-voice/`。
- faster-whisper 安裝與 small 模型下載**保留**（fallback 需要）。

## 風險與待確認

- SenseVoice 權重授權為 NOASSERTION（近 Apache 精神），黑客松展示用途通常無虞，商用前建議法務覆核。
- sherpa-onnx `OfflineRecognizer` 結果物件的實際屬性名（`result.text`）需在實作時對 1.13.3 版 API 覆核。
- OpenCC pip 套件名與 `s2twp` 設定檔在目標環境的可用性需實作時驗證。
- 兒童語音準確率無文獻數據，需以自錄樣本 A/B（研究已註明的資訊缺口，屬移植階段驗證）。
