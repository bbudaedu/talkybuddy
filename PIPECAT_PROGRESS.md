# pipecat edge — 夜間進度（2026-07-31 02:20–02:45）

## 先看這段：你的板子沒被動過

決賽今天。我全程只讀不寫地對待決賽路徑：

- `talkybuddy-live-client.service`、`talkybuddy-server.service` **從頭到尾 active running**，沒有 stop/restart/reload
- `/root/talkybuddy/` 底下**零修改**（含 `.venv`、`models`）
- 新東西全部隔離在板子的 `/root/pipecat-lab/`（獨立 venv），刪掉即完全復原
- 主 repo `gsd/2-genio-520-edge-mvp` 沒動、沒 push；本檔在 worktree 分支 `feat/pipecat-edge`
- 板子磁碟：8.5G → 9.4G（pipecat 佔約 600MB），剩 **4.0G**，離 2G 紅線還遠

備份（已實際還原驗證過，不是「應該可以」）：`~/backups/talkybuddy-20260731-022002/`
含 `RESTORE.md`。板子端另有 `/root/backup-20260731/`（venv tar + 三個 unit + `.env`）。

---

## 決賽當天可能用得上的一個發現

`a874141` 查到裝置的 `ANTHROPIC_BASE_URL=http://127.0.0.1:8317` 指向板子自己，
沒人在聽，所以 CloudLLM 每輪 `ConnectionRefused` 降級回邊緣 LLM——**所謂 cloud 模式
跑的一直是 edge LLM**。

而 Ubuntu-AI-Server（192.168.100.200）的 **8317 一直有 `cli-proxy-api` 在跑**
（`LISTEN *:8317 users:(("cli-proxy-api",pid=858))`）。

LLM 佔 round_total 的 78%（3859ms / 5025ms）。改一個環境變數指過去就可能把它換掉。
**但前提是決賽現場連得到那台機器**——實測 edge→server RTT 116ms、頻寬約 550kB/s，
而且你說現場要靠 tunnel。這件事我沒有動，留給你判斷。

---

## 做完的事

### 1. 原本以為要自寫 4 個 adapter，實測後只需要 1 個

第一次判斷是用 `pip download --platform manylinux2014_aarch64` 在開發機模擬的，
結論「onnxruntime 無 aarch64 wheel、`[silero]` 不可行」——**那是假陰性**，
platform tag 訂太舊。真的在板子上 `pip install` 就裝起來了。

**教訓：platform tag 模擬只能證明「可行」，不能證明「不可行」。**

| 元件 | 板子實測 | 結論 |
|---|---|---|
| `pipecat-ai` 1.6.0 核心 | ✅ 裝好，`import pipecat` 0.27s | 直接用 |
| `SileroVADAnalyzer` | ✅ onnxruntime 1.24.4 CPU provider 可用 | 直接用，**不必自寫** |
| `LocalAudioTransport` (pyaudio) | ❌ `Failed to build installable wheels` | **自寫** ← 唯一要做的 |
| 本地 LLM | llama-server 是 OpenAI 相容 | `OpenAILLMService` 指過去即可 |
| 本地 STT / TTS | sense-voice、piper 模型都在板子上 | 待接（下一輪） |

### 2. Silero VAD 在 Genio 520 上的實測數字

- 模型是 pipecat **內建**的，不必另外下載
- 實例化 0.10s、行程 RSS 143MB
- 端到端 `analyze_audio()` **1.90ms/窗**，每窗 512 samples = 32ms 音訊 → **即時率約 6%**，板子綽綽有餘
- 靜音 confidence 0.024、類語音 0.61、`VADState.QUIET` 判斷正確

**兩個會咬人的行為（已寫進 `pipecat_adapters/__init__.py`）**：
1. 建構後 `sample_rate` 是 0、`num_frames_required()` 回 256（8kHz 的窗）；
   要等 pipeline 送 `StartFrame` 觸發 `set_sample_rate(16000)` 才變成正確的 512。
   **任何在 `__init__` 裡就依賴 `self.sample_rate` 的子類別都會拿到 0** ——
   我自寫的 sherpa VAD 版本就是踩到這個才被實測抓出來（該檔已刪，因為官方版可用）。
2. `voice_confidence()` 註記 `-> float` 但實際回 shape `(1,)` 的 ndarray；
   numpy 2.x 對它呼叫 `float()` 會 `TypeError`。

### 3. `alsa_transport.py` — 唯一需要自寫的 adapter

`edge/runtime/pipecat_adapters/alsa_transport.py`，用 arecord/aplay 子行程取代 pyaudio。

- argv 一律 import `live_client.build_arecord_argv` / `build_aplay_argv`，
  **不另寫一份會漂移的 ALSA 參數**（尤其 `--buffer-time 2000000` 那個
  2026-07-30「斷斷續續」的實機教訓）
- 用 `asyncio.create_subprocess_exec` 而非 `Popen`+執行緒，因為 pipecat 是 asyncio 世界
- stderr 一律不吞：arecord 起不來時唯一的線索在那裡
- teardown 會 terminate→逾時 kill，確保**麥克風一定釋放**（不釋放的症狀跟麥克風壞掉
  一模一樣，見 `38aa261`）

**測試：`tests/test_pipecat_alsa_transport.py`，10 passed。** 測試刻意不啟動真的
arecord——板子上 live-client 正持有麥克風，測試若真的開會變成第二個搶麥的行程。

---

---

# 第二輪（02:50–03:10）

## 查證了一條專案既有決策，一半推翻一半成立

記憶／`docs/DEPLOY_EDGE.md:136` 記載「Pipecat 串流管線：刻意不裝」，理由之一是
**「pipecat 會升級 numpy，而 sherpa-onnx ASR/TTS 依賴現版」**。這條直接決定我的
STT adapter 能不能成立（它要讓 pipecat 與 sherpa 跑在同一個 process），所以先驗證：

| venv | numpy | sherpa-onnx |
|---|---|---|
| `/root/talkybuddy/.venv`（決賽路徑，只讀） | 2.5.1 | ✅ |
| `/root/pipecat-lab/.venv` | 2.4.6 | ✅ 事後裝入無衝突無降級 |

**numpy 衝突不成立**（方向甚至相反，決賽 venv 的 numpy 還比較新）。同 process
共存實測：`import pipecat`+`import sherpa_onnx` 0.16s、VAD 仍可用、
SenseVoice 載入 1.93s、**2 秒音訊辨識 147ms**。

**但那條記錄的其他理由仍然成立，而且多了一個新數字**：

- 行程 **RSS 664MB**（板子可用僅 1.7G，llama-server 還要吃）← 這是新的風險
- `pyaudio` 確實要編譯（已於第一輪確認）
- **最關鍵的沒變**：接通後 round_total 仍由 LLM 的 3.9s 主宰

順帶一提，餵雜訊給 SenseVoice 得到的辨識結果是 `'그.'`——**正好複現了
`live_client` docstring 記的「把噪音判成韓文字符」**。決賽會場很吵，
「空結果即雜音」那條兜底不能拿掉。

## 兩個 adapter 完成，27 測試全綠

- `sensevoice_stt.py`：重用 `SenseVoiceASREngine._ensure_model()`（`CONTRACTS.md`
  明列的公開契約）共用模型單例，不重複載入吃掉板子 RAM。
  **sherpa 的 decode 是阻塞 native 呼叫，一律 `asyncio.to_thread`**——
  在 pipecat 的單一 event loop 上阻塞會同時凍住 VAD、麥克風讀取與播放。
- `edge_tts.py`：`synth()` 回完整 WAV，這裡用 `wave` 模組解析剝掉 header
  （硬切 44 bytes 不安全，WAV 允許 `data` 前插別的 chunk）。
  **誠實記錄：這條路徑不是串流的**，first-audio 延遲 = 整句合成時間，切 chunk
  不會讓第一個 byte 早一點出來。

## 又踩到同一個坑，這次釘住了

第一輪在 VAD 上踩過的「`sample_rate` 建構後是 0」，**STT 與 TTS 完全一樣**：

| 元件 | 何時才生效 |
|---|---|
| `VADAnalyzer` | `set_sample_rate()` |
| `STTService` | `StartFrame` → `stt_service.py:315` |
| `TTSService` | `StartFrame` → `tts_service.py:549` |

生產路徑是對的（pipeline 啟動時會設），是單元測試繞過 pipeline 才拿到 0。
已加 `_with_rate()` 補這一步，並用
`test_sample_rate_is_zero_until_pipeline_starts` 釘住行為——pipecat 哪天改掉會紅。

---

## 下一輪要做的

1. **降級狀態機**：雲端不可達時切本地，**麥克風所有權不轉移**
   （這是整個設計相對「換掉整個 client」的最大優勢，也直接對應
   `project-edge-deploy` 記的「`Conflicts=` 只停不啟 → 兩個 client 都死 → 玩偶變啞」）
2. **端到端 pipeline 組裝**，在板子上用檔案餵音訊（不搶麥克風）量 round_total
3. 寫 spec 到 `docs/`
4. 量 **完整 pipeline 的 RSS**——單是 pipecat+sherpa 就 664MB，加上 TTS voice
   與 llama-server 是否還塞得進 1.7G，這是目前最大的未知數

## 還沒有答案的問題

- **決賽現場 tunnel 的穩定性**：RTT 116ms 是可接受的，但 tunnel 斷掉的頻率沒有資料
- **pipecat 疊在 llama-server 之上的 CPU 爭用**：VAD 只吃 6% 即時率，但完整 pipeline
  （VAD+STT+TTS 同時跑）還沒量過，板子只有 1.7G 可用 RAM
- **最關鍵的那個沒變**：round_total 由 LLM 的 3.86s 主宰。pipecat 換的是編排，
  **不會讓那 3.86s 變快**。真正的槓桿仍然是把 LLM 換掉（雲端或 GPU），
  這一點從 `LATENCY_2.96S_EXPLAINED.md` 到現在都沒有改變。
