# PLAN.md 對齊紀錄（align 任務）

> 對照對象：`/home/budaedu/hackathon/PLAN.md`（Genio 520 決賽 MVP 計畫）
> 現況：`/home/budaedu/hackathon/talkybuddy`（PC 原型，忠實鏡射未來 Genio 架構，見 CONTRACTS.md）
> 原則：PC 原型與 Genio 板是兩個不同執行環境，PLAN.md 有部分項目「本來就只在 Genio 板上才成立」，
> 這類項目在 PC 原型上刻意不改，列為移植階段 TODO；其餘可在 PC 原型落地的項目本次已對齊。

## 本次實際變更

| 檔案 | 變更 | 依據 |
|---|---|---|
| `server/config.py` | `ASR_CONF_THRESHOLD` 0.45 → 0.5 | PLAN §3：「若 ASR 信心度低於 0.5（雜音干擾），直接播放兜底話術」 |
| `server/llm.py` | `n_ctx=1024` 保留數值不變，加註解說明 Genio 板上應降為 512 | PLAN §1.1：「LLM 跑在 CPU（限制 Context 512 tokens 以防 OOM）」；PC 記憶體充足，暫不動數值以免影響現有測試/行為 |
| `server/pipeline.py` | `_webm_to_wav` docstring 加註解，說明已實測 soundfile 無法解 webm 容器、為何保留 ffmpeg、Genio 板上如何移除 ffmpeg | PLAN §1.1：「捨棄 FFmpeg，改用 soundfile 記憶體解碼」；見下方「實測結果」 |

## 實測結果：soundfile 能否讀 MediaRecorder 的 webm/opus

```
.venv/bin/python -c "import soundfile as sf; print(sf.__libsndfile_version__); print(sf.available_formats())"
```

結果：libsndfile 1.2.2，`available_formats()` 回傳的容器清單（AIFF/AU/AVR/CAF/FLAC/HTK/SVX/MAT4/MAT5/
MPC2K/OGG/PAF/PVF/RAW/RF64/SD2/SDS/IRCAM/VOC/W64/WAV/NIST/WAVEX/WVE/XI）**不含 WEBM**。
即 soundfile／libsndfile 完全沒有 WebM 容器解碼器，無法直接讀取瀏覽器
`MediaRecorder({mimeType:"audio/webm;codecs=opus"})` 錄出的音檔。

另外確認：`web/index.html`（第 606-616 行）目前優先選用
`audio/webm;codecs=opus`，其次 `audio/ogg;codecs=opus` / `audio/webm`，
**前端目前一律送 webm/ogg 容器，從未送過裸 wav**，因此契約提到的「若前端已送 wav，確認走
soundfile」在現有程式碼中不適用（沒有這條路徑），本次未新增假設中的 wav 分支，避免無測試覆蓋的推測式改動。

**結論（依「不破壞現有可運行狀態」為最高原則）**：
- PC 原型維持 `ffmpeg subprocess` 轉檔為主要路徑，邏輯不變。
- 僅在 `_webm_to_wav()` docstring 加註解說明實測結論與移植方向。
- 真正捨棄 ffmpeg 的時機是 Genio 板：改用 ALSA 直接錄 16kHz mono wav（跳過瀏覽器
  MediaRecorder/webm 這一段前端錄音管線），屆時 ASR 輸入本就是 wav，
  可以直接用 `soundfile.read()` 讀進記憶體餵給 faster-whisper，不需要 ffmpeg。

## PLAN.md 各要點 vs 現有實作

### 已對齊（本次或先前已完成，PC 原型可驗證）

| PLAN 要點 | 現況 |
|---|---|
| ASR 信心門檻 0.5 兜底 | `config.ASR_CONF_THRESHOLD=0.5`，`pipeline._process_text` 已依此值走 `FALLBACK_LINES` |
| VAD silence timeout 放寬至 1500ms 對兒童停頓容錯 | 屬前端 MediaRecorder / push-to-talk 互動細節，PC 原型採「按住講話、放開送出」手動觸發（見 `web/index.html` push-to-talk），無自動 VAD silence timeout 的需求，此項目與 Genio 板「自動偵測結束說話」的語音管線設計相關，列為移植階段 TODO（見下） |
| Web 監控面板技術棧（vanilla JS + 手刻 SVG，非 React/Tailwind/Recharts，5 秒輪詢非 WebSocket） | `web/teacher.html`、`web/index.html` 已是零建置 vanilla JS + SVG，`teacher.html` 以 5 秒輪詢 `/api/diagnoses`、`/api/interactions`，`index.html` 走 `/ws/talk` WebSocket 僅用於單一對話 session 的即時語音互動（非監控面板輪詢），與 PLAN §1.2 描述一致 |
| 雲端中介 FastAPI + SQLite | `server/app.py` + `server/store.py` 已完整實作，欄位名與 CONTRACTS.md 逐字一致 |
| 離線同步（seq、device_id、冪等去重） | `store.add_interaction` 以 `seq` 自增 primary key、`synced` 欄位標記；`/api/network_mode` 切到 cloud 時 `mark_all_synced()` 補同步，欄位與 PLAN §1.3 一致 |
| ASR 信心度單元測試 | **實測發現落差**：`tests/` 目錄實際只有 `__init__.py`，CONTRACTS.md 描述的 `test_scaffold.py`/`test_store.py`/`test_pipeline.py`/`test_e2e.py` 尚未建立（`.venv/bin/python -m pytest tests/ -x -q` → `no tests ran`）。PLAN §3「針對 VAD 與 ASR 信心度編寫單元測試」目前並未達成，非本次 align 任務範圍（僅改設定值），但應盡快補上，否則本次 `ASR_CONF_THRESHOLD` 0.45→0.5 的行為變更完全沒有測試保護 |

### Genio 板專屬，PC 原型不適用，列為移植階段 TODO

| PLAN 要點 | 說明 |
|---|---|
| `hardware_agent.py` / `voice_agent.py` 軟硬解耦架構 | PC 原型無 GPIO 按鈕、無 LED，`server/pipeline.py` 直接是單一 `VoicePipeline` 狀態機，前端用滑鼠/觸控按住講話取代 GPIO Pinch-to-talk。移植時需拆出 `hardware_agent.py`（GPIO+LED）與 `voice_agent.py`（現有 pipeline 邏輯可大致沿用），中間加安全隊列解耦 |
| Whisper-base ONNX / Piper TTS ONNX 預載 `talkybuddy/models/` + NeuroPilot NPU (TFLite Neuron Delegate) | PC 原型用 `faster-whisper`（PyTorch/CTranslate2 後端）+ `piper-tts`（onnxruntime CPU），非 Genio 板指定的 TFLite + Neuron Delegate NPU 路徑。移植時 ASR/TTS 模型格式與推論後端都要換，屬於 Day1 階段 2-3（PLAN §2 階段一）的實測項目，非本次 align 範圍 |
| LLM n_ctx 限制 512 防 OOM | PC 原型記憶體足夠，`server/llm.py` 保留 `n_ctx=1024`（本次已加註解），Genio 板實測時再降為 512 |
| ALSA 直接錄音、捨棄 ffmpeg | 見上方「實測結果」，PC 原型前端走瀏覽器 MediaRecorder→webm，只能靠 ffmpeg 或改前端錄音格式；Genio 板走 ALSA 原生錄 wav，才能真正省掉 ffmpeg |
| 單調遞增 `seq` + `device_id` 冪等去重（跨裝置/斷網重連情境） | PC 原型單一 session、單一 `device_id`（`config.DEVICE_ID` 固定值），`seq` 已存在但「跨裝置去重」邏輯未觸發測試（目前只有一台虛擬裝置）。真正的斷網重連壓力測試（PLAN §2 階段三）需在 Genio 板 + 實體網路環境驗證 |
| GPIO 捏肚子按鈕綁定 ASR 錄音觸發 | 同上，PC 原型用瀏覽器按鈕/觸控事件模擬，非真實 GPIO |
| Neuron Delegate 算子相容性盲點紀錄 | 需在 Genio 板實測後才能產出，PC 原型無 NPU 可測 |

### 刻意不改及理由

| 項目 | 理由 |
|---|---|
| `pipeline.py` 的 ffmpeg webm→wav 轉檔邏輯本身 | 已實測 soundfile 無法解 webm 容器（見上）；若強行拆掉 ffmpeg 但前端仍送 webm，會直接讓語音輸入路徑整個失效，違反「不破壞現有可運行狀態」最高原則。改為僅加註解說明實測結論與未來方向 |
| `llm.py` 的 `n_ctx` 數值 | PLAN 明確說「512 tokens」是為了 Genio 板 CPU 防 OOM 的限制，PC 端記憶體充足、且改動 n_ctx 可能影響現有 LLM 回覆品質與既有測試假設（若 test 有針對 prompt 長度假設），風險與效益不對等，故只加註解不改值 |
| `web/*.html` 的 VAD silence timeout 相關邏輯 | PC 原型採手動 push-to-talk（按住/放開），本就沒有自動偵測靜音結束這一段狀態機，PLAN 此項描述的是 Genio 板連續聆聽情境，不屬於本次「小幅對齊」範圍，也避免無對應測試覆蓋下新增行為 |
| `store.py` 跨裝置冪等去重的擴充（目前僅單一 `device_id`） | 現有欄位與 API 已符合 CONTRACTS.md 定義，PLAN 描述的「網路復連補送冪等去重」場景需要多裝置/多次斷線重連的整合測試才能驗證正確性，屬於階段三（Day3 軟硬串接）的範圍，非本次程式碼小改動能安全覆蓋 |

## 驗證

- `.venv/bin/python -c "from server.app import app"` → 通過（import 無錯誤）
- `.venv/bin/python -m pytest tests/ -x -q` → `no tests ran`（`tests/` 目前僅有 `__init__.py`，測試檔案尚未建立，見上方「已對齊」表格說明；本次 `ASR_CONF_THRESHOLD` 改動因此沒有既有測試可驗證，屬已知落差，建議後續補測試任務優先處理）

## 後續變更：TTS 授權修正（獨立於上述 align 任務）

- **TTS 授權修正：piper-tts（GPL-3.0）→ sherpa-onnx（Apache-2.0），聲音檔沿用**。`server/tts.py` 合成後端改為 sherpa-onnx，仍載入既有 `models/zh_CN-huayan-medium.onnx` / `models/en_US-lessac-medium.onnx` 兩個 Piper=VITS 格式聲音檔（未重新下載模型），`TTSEngine.available()`/`synth(segments)` 契約簽名與行為不變。`scripts/setup_env.sh` 同步改為安裝 `sherpa-onnx`/`onnx`。
- 驗證：`.venv/bin/python -m pytest tests/ -q` → **41 passed**；真伺服器冒煙（`uvicorn` 起服務 + WebSocket `/ws/talk` 送 `text_input`）→ `/api/status` 回 `tts:true`，收到 `reply` 與 `tts_audio`（解碼後 wav bytes > 0）。
- **殘留風險（未在本次範圍內解決）**：語音合成仍需的 espeak-ng-data 音素化資料，其上游授權為 espeak-ng 的 GPL-3.0；目前 `server/tts.py` 的 `_resolve_espeak_data_dir()` fallback 仍讀取 `piper-tts` 套件內附的那份資料，`scripts/setup_env.sh` 因此仍保留 `pip install piper-tts`（僅為取得 espeak-ng-data，不用於合成本身）。待找到 Apache/MIT 授權的 espeak-ng-data 替代來源後，可再移除 piper-tts 這個殘留依賴。

## ASR 換 SenseVoice（實測落地紀錄）

> 對應 Task 1-3（分檔架構、SenseVoice 引擎、預設切換）之後的 Task 4：真模型端到端冒煙驗證。
> 環境：`/home/budaedu/hackathon/talkybuddy`，`.venv/bin/python`；模型已預先下載於
> `models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/`（`model.int8.onnx` 239MB、`tokens.txt`），
> 官方 `test_wavs/`（`zh.wav`/`en.wav`/`ja.wav`/`ko.wav`/`yue.wav`，皆 16kHz mono pcm_s16le）已隨模型附帶。

### Step 2：引擎可用性（實測）

```
.venv/bin/python -c "from server.asr import ASREngine; e=ASREngine(); print('available:', e.available()); print('class:', type(e).__name__)"
→ available: True
→ class: SenseVoiceASREngine
```

### Step 3：直接辨識冒煙（真人聲，官方 test_wavs）

- `zh.wav`（冷啟動，含模型載入）：`('開放時間早上9點至下午5點。', 1.0)`，耗時 0.788s
- `zh.wav`（熱啟動，同一 process 第二次呼叫，單輪辨識延遲）：`('開放時間早上9點至下午5點。', 1.0)`，耗時 **0.0828s**
- `en.wav`（熱啟動，中英夾雜/程式碼混讀場景）：`('The tribal chieftain called for the boy and presented him with 50 pieces of code.', 1.0)`，耗時 0.1061s

**簡→繁轉換證據**（繞過 `ASREngine`、直接呼叫 sherpa-onnx recognizer 取原始輸出比對 OpenCC 前後）：
- sherpa-onnx SenseVoice 原始輸出（OpenCC 轉換前）：`开放时间早上9点至下午5点。`（簡體：开放/时间/点）
- 經 `opencc.OpenCC('s2twp').convert()` 後：`開放時間早上9點至下午5點。`（繁體：開放/時間/點）
- `raw_text == converted_text` → `False`，證實轉換確實發生且非恆等操作
- 對 `ASREngine().transcribe()` 的最終輸出再做一次 `s2twp` 轉換（roundtrip），結果與原輸出逐字相同（`True`）→ 證實最終回傳文字已是**完整繁體**，非殘留簡體字

### Step 4：`/api/status` + WebSocket 端到端（headless 替代瀏覽器麥克風）

`scripts/run.sh` 之等效指令啟動伺服器後：

```
curl -s http://localhost:8787/api/status
→ {"asr":true,"llm":true,"tts":true,"network_mode":"edge","pending":7}
```

`app.py` 的 `/ws/talk` 協定：binary frame＝完整 webm/ogg 錄音（瀏覽器 MediaRecorder 格式），
搭配 `{"type":"audio_end"}` 觸發整包送進 `pipeline.run_turn_audio`（`ffmpeg` 轉 16kHz mono wav 後
餵給 ASR）。因無法自動化瀏覽器＋實體麥克風，改以 headless WebSocket client 走**同一條** server 管線：
用 `ffmpeg -c:a libopus` 把官方 `zh.wav` 轉成 webm/opus 容器（模擬 MediaRecorder 輸出格式），
送 binary frame + `audio_end`，收集回應序列：

```
state(asr) → state(thinking) → state(tts) → state(idle)
→ asr_result: {"text": "開放時間早上9點至下午5點。", "confidence": 1.0}
→ reply: {"text": "你的說話很流利，繼續說一遍：<How are you today?>",
           "scores": {"fluency":30,"vocabulary":35,"grammar":45},
           "latency_ms": {"asr":159,"llm":1550,"tts_first":231,"round_total":1942},
           "fallback": false, "seq": 25}
→ tts_audio: {"wav_b64": "<205984 base64 字元，約 154KB wav>"}
```

`asr_result.text` 為繁體（與 Step 3 一致），且完整跑到 `reply`（LLM 產生真實回覆，非降級話術，
`fallback:false`）與 `tts_audio`（TTS 正常合成，非 `tts_unavailable`）。**整條 webm→ffmpeg→SenseVoice→
OpenCC→scaffold/LLM→TTS 管線在真模型下端到端驗證通過**。

備註：瀏覽器＋實體麥克風的手動 demo（PLAN 原 Step 4 描述的「按住企鵝肚子錄一句」）本次以
headless WS client 替代，走的是同一個 `/ws/talk` 端點與同一段 pipeline 程式碼，僅錄音來源從
瀏覽器換成預錄 wav 轉 webm；瀏覽器實機操作留給人工 demo 驗收。

### Step 5：flag 切回 whisper 驗證

未修改 `server/config.py`（`git diff server/config.py` 確認為空），改以
`get_asr_engine_class("whisper")` 直接取得類別繞過 config 讀值：

```
from server.asr_base import get_asr_engine_class
get_asr_engine_class("whisper").__name__ → "WhisperASREngine"
WhisperASREngine().available() → True   （faster-whisper 1.2.1 可正常 import）
```

**環境限制（非任務失敗）**：`faster-whisper` 套件已安裝且 `available()` 依契約回傳 `True`
（僅檢查套件可 import，不檢查模型權重是否已下載）；但實際 `small` 模型權重在本環境**尚未下載**
（`~/.cache/whisper` 為空、無 `~/.cache/huggingface/hub` 下 `Systran/faster-whisper-small` 快取），
真正呼叫 `transcribe()` 會觸發首次下載（約數百 MB，非本次 Task 4 前置已核准下載的項目），
故本次**未觸發下載**、亦**未實測 whisper 路徑之辨識延遲**，僅驗證 flag 切換機制本身
（`get_asr_engine_class`／`available()`）正常運作，符合「不自動 fallback、缺依賴時 degrade-safe」
的契約設計。SenseVoice 熱啟動單輪延遲（0.0828s／zh、0.1061s／en）作為基準記錄，
whisper 對照留待有預下載模型的環境中補測。

### 小結

| 檢查項 | 結果 |
|---|---|
| `available()` / class | `True` / `SenseVoiceASREngine` |
| zh.wav 辨識（繁體驗證） | `開放時間早上9點至下午5點。`，簡轉繁證據明確（`开放时间…` → `開放時間…`） |
| en.wav 辨識 | `The tribal chieftain called for the boy and presented him with 50 pieces of code.` |
| 單輪辨識延遲（熱啟動） | zh 0.0828s／en 0.1061s（冷啟動含模型載入 0.788s） |
| `/api/status` | `asr:true, llm:true, tts:true` |
| WS 端到端（headless 替代瀏覽器麥克風） | `asr_result`（繁體）→`reply`（非降級）→`tts_audio` 全部通過 |
| flag 切回 whisper | 機制驗證通過（`WhisperASREngine`／`available()=True`）；模型權重未預下載，未實測辨識延遲，記為環境限制 |
| `server/config.py` 改動殘留 | 無（`git diff` 為空，全程未編輯此檔） |
