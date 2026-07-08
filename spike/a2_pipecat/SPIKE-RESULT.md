# A2-1 Pipecat 整合 Spike 結果

- 日期：2026-07-08
- 定位：拋棄式 go/no-go、二元淨判、不量延遲
- 環境：獨立 venv、**Pipecat 1.5.0**、**sherpa-onnx 1.13.4**、Python 3.12、torch 2.12.1+cpu

## 結論：**GO**（五項判準全數實測通過）

核心 go/no-go 問句——「批次 SenseVoice 當 STT、句級可中止 sherpa 當 TTS，能否共存於
Pipecat 並被程式化 barge-in 句界乾淨打斷，且不碰 `pipeline._process_text`」——
**全數成立**：sherpa callback 中止（未知 #1）與 Pipecat 中斷接法（未知 #2）皆一手驗證，
pipeline 跑得起來、批次 SenseVoice 整段轉錄、多句逐句合成、barge-in 句界乾淨停。
判準 #2 於 SenseVoiceSmall（936MB）下載完成後補跑通過：真模型把 `zh.wav`
整段轉出 `开放时间早上9点至下午5点。`，經 StubLLM 觸發固定回覆並由 sherpa 合成
（frames=4），**全鏈 STT→LLM→TTS 一次跑通**。

| 判準 | 結果 | 證據 |
|---|---|---|
| #1 pipeline 不 crash | ✅ PASS | run_spike.py 走完判準觀察行 |
| #2 批次 STT 整段轉錄 | ✅ PASS | 真 SenseVoiceSmall 把 `zh.wav` 整段轉出 `开放时间早上9点至下午5点。`（`run_stt` log + `[StubLLM] got transcript` 非空）|
| #3 多句逐句合成 | ✅ PASS | 未中止 OutputSink frames=**4**, bytes≈282k |
| #4 句界乾淨中止 | ✅ PASS | 中止 frames=**1** ≪ N_full=**4**（1.5s barge-in→只產第 1 句） |
| #5 未碰 _process_text | ✅ PASS | grep `_process_text`=0 命中；`import server`=0（唯一命中為註解中文字） |

## 三個未知的實測答案

1. **sherpa callback 中止語意**（一手驗證，sherpa-onnx **1.13.4**）：
   `OfflineTts.generate(text, sid=0, speed=1.0, callback=None) -> GeneratedAudio`；
   `callback(samples: np.ndarray[float32], progress: float) -> int`，**回非 0 即提早中止合成**。
   `OfflineTts.sample_rate=22050`。Task 3 的 5 個 pytest（含真合成）全過 → 中止語意成立。

2. **Pipecat 中斷機制**（Pipecat 1.5.0，probe 實測）：
   - TTS 基類＝**`pipecat.services.tts_service.TTSService`**（本地同步 TTS 用；`InterruptibleTTSService`
     繼承 `WebsocketTTSService`，是串流 websocket 系，不適用）。
   - 覆寫方法＝**`async def run_tts(self, text, context_id) -> AsyncGenerator[Frame|None]`**，
     yield `TTSAudioRawFrame(audio, sample_rate, num_channels, context_id)`（base 的
     `tts_process_generator` 會把非 None frame 併入 audio context；start/stop frame base 自理）。
   - interruption frame＝**`InterruptionFrame`**（SystemFrame；plan 早期假設的 `StartInterruptionFrame`
     在 1.5.0 **不存在**）。base `TTSService.process_frame` 收到即走 `_handle_interruption` 並取消
     run_tts task。**攔截點**＝覆寫 `process_frame`，在 `super()` 之前令 `InterruptibleSynth.interrupt()`。

3. **批次 STT 在 frame 拉取下**（真模型實測，已更正）：`FunASRSTTService(*, device='cpu', settings=None)`＝
   `SegmentedSTTService`，預設 `iic/SenseVoiceSmall`。框段方式＝
   **`VADUserStartedSpeakingFrame` → `InputAudioRawFrame` chunks → `VADUserStoppedSpeakingFrame`**。
   ⚠️ 關鍵更正：段落轉錄**只由 VAD 前綴 frame 觸發**——`SegmentedSTTService.process_frame` 僅在
   `VADUserStoppedSpeakingFrame` 呼叫 `run_stt`；非 VAD 版 `UserStartedSpeaking/UserStoppedSpeaking`
   不會啟動轉錄，且因 `_user_speaking` 未置真、audio buffer 被裁到 1s，`run_stt` 從不觸發
   （第一版用非 VAD frame 實測：無 `Transcription:` log、OutputSink frames=0）。
   改用 VAD 前綴 frame 後乾淨吃一整段轉錄成功（run_spike.py 非 no-STT 路徑）。

## workaround / 卡點

- **torch 未隨 `pipecat-ai[funasr]` 帶入**：`funasr 1.3.14` 有裝但 torch 缺，SenseVoice 無法載入。
  補裝 `torch/torchaudio` CPU wheel（`--index-url https://download.pytorch.org/whl/cpu`）後 import 恢復。
  → 若留用，requirements 須顯式列 torch。
- **barge-in 需模擬播放節奏才可觀察**：sherpa 合成遠快於即時播放，若 run_tts 不 pace，4 句在
  1.5s barge-in 前就全合成完、frames 不變（第一版實測 4→4）。於 run_tts 每句後加
  `await asyncio.sleep(len(samples)/sample_rate)` 模擬播放，barge-in 才落在播放中→句界停
  （4→1）。真實系統本就以即時播放 pace，此為 spike 擬真、非缺陷。
- **TTSSettings NOT_GIVEN 警告**（model/voice/language）：非致命，合成正常；留用時於子類 __init__
  帶入 settings 可消警。
- **判準 #2 補跑（已解決）**：SenseVoiceSmall 936MB 於後續 session 下載完成（cache
  `~/.cache/modelscope/models/iic--SenseVoiceSmall/`），補跑一次即通過。過程中揪出 spike harness
  用錯 frame 型別（見未知 #3 更正）：SegmentedSTTService 需 **VAD 前綴** frame 才觸發 `run_stt`，
  改用 `VADUserStartedSpeakingFrame/VADUserStoppedSpeakingFrame` 後轉錄成功。
- **SenseVoice 輸出為簡體大陸腔用詞**（如「开放时间」）：符合 memory 記載的 mainland-accent 特性，
  判準 #2 只驗「整段非空」不受影響；正式留用時若需繁體/台灣腔，屬 B 軸/後處理範疇，非本 spike 阻塞。

## 若留用的一句話銜接

- **STT**：把 native `FunASRSTTService` 換成包 `server/asr.py`（SenseVoice）的 `SegmentedSTTService`
  子類，介面相同、框段方式不變。
- **TTS**：`SherpaInterruptibleTTSService` 內的 `InterruptibleSynth` 中止介面，即
  `StreamingTurnManager`（A2-2）在 `_process_text` **之外**組出 `TurnResult` 後、驅動句級中止所需的核心；
  barge-in 由 Silero VAD（A2-3）取代本 spike 的 `InterruptDriver`＋播放節奏。
