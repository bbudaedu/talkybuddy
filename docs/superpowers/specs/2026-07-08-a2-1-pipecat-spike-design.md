# A2-1 Pipecat 整合 Spike 設計書（拋棄式 go/no-go）

- 日期：2026-07-08
- 範圍：獨立拋棄式 spike，落點 `talkybuddy/spike/a2_pipecat/`，**不動** `talkybuddy/server/`
- 依據：A 軸 A2（線上全雙工 barge-in 傳輸層）。研究定案見 `research/12`（設計交接）、`research/13`（framework bake-off：Pipecat 首選）、`research/15`（TEN vs Pipecat 雲端佈署：維持 Pipecat 裝置端編排）。
- 定位：**拋棄式 go/no-go 驗證**（使用者簽核）。先答可行性，過關再決定是否留用；驗收線為**二元淨判**（能乾淨中止即 pass），不做延遲量測。

## 目標（單一問句）

回答一個 go/no-go 問題：

> **Pipecat 能不能「同時」容納「批次 SenseVoice 當 STT service」＋「句級可中止 sherpa 當 TTS service」，被程式化 barge-in 打斷，且完全不碰 `pipeline._process_text`？**

這是整條線上全雙工路線的**最高未驗證風險**（`research/12` §5 A2-1）。多數串流傳輸框架的 STT plugin 偏串流導向，而本專案 SenseVoice 是**批次**；且本專案 `tts.py` 現行用 `voice.generate(text)`（**無 callback 形式**），sherpa 的句級中止 callback **從未在本專案接過**——這兩點是 spike 要一次驗掉的核心未知。

## 非目標（本次不做）

- 真麥克風、AEC、`LocalAudioTransport`、Silero VAD、真人 barge-in（AEC 留 A2-3，見 `research/14`）。
- 延遲量測 / benchmark（驗收線是二元淨判，不量數字）。
- Genio 520 footprint、上板移植（A2-6）。
- 網路狀態機、線上↔離線切換（A2-0/A2-4）。
- 聲音品質 / TTS 選型（自然情緒台灣聲另案，見交接 task 聲音選型）。
- 整進正式 `server/`（此 spike 不改任何既有檔）。
- 接 Anthropic 真 LLM（用 stub 取代）。

## 架構定案前提（本 spike 繼承，不重議）

- **框架＝Pipecat（BSD-2）**，LiveKit 為備援（`research/13`）。
- **拓撲＝裝置端編排**（Pipecat in-process；`research/12` §7 #10，使用者 2026-07-08 簽核）。
- **四項既定裁決**（本 spike 全程遵守）：①Anthropic 直呼、不引 LangGraph/AutoGen（此 spike LLM 為 stub，天然相容）；②新增元件、**不改 `_process_text`**；③離線重用 `run_turn_audio`（此 spike 不碰）；④框架只當水管。

## 二元 Pass 判準（全中才 PASS）

1. Pipecat pipeline 跑得起來、不 crash。
2. 餵一段 canned wav → `FunASRSTTService` 能把它**當一整段**轉出文字（證明批次 STT 塞得進 Pipecat 的 frame 拉取模型）。
3. stub LLM 把該文字變成一段**多句**回覆 → sherpa **逐句**合成。
4. 在**第 1 句播放中**注入程式化 interruption → sherpa 的**下一句 callback 回中止值、合成在句界乾淨停住**（不是等整段合成完才停）。
5. 全程**未 import／未呼叫** `_process_text`。

任一項未達 → FAIL（並記錄卡在哪、原因、是否有 workaround）。

## 元件與資料流

一支獨立腳本，**不 import 也不改動 `talkybuddy/server/` 任何模組**（sherpa voice 載入邏輯採**複製** `tts.py` 的最小片段到 spike 內自帶，不 import、不動 `server/tts.py`，避免耦合正式 server）：

```
canned_child.wav ─► [FunASRSTTService]      ← Pipecat 原生，批次 SenseVoiceSmall
     (audio frames)      │ transcript
                         ▼
                   [StubLLMService]          ← 把 transcript 映成固定多句 zh 回覆
                         │ text frames
                         ▼
        [SherpaInterruptibleTTSService]      ← 手寫，subclass Pipecat TTS 基類
                         │  逐句 generate(text, callback)
                         │  callback 讀 self._interrupted → 回中止值
                         ▼
                   [OutputSink]              ← 丟棄/log，不需真喇叭

  [InterruptDriver] ──(計時後 push interruption frame)──► pipeline
```

### 元件職責

- **`FunASRSTTService`（Pipecat 原生）**：直接用 Pipecat 內建 service（預設 `iic/SenseVoiceSmall`）。此選擇是刻意的——用近零程式碼證明「批次 SenseVoice 在 Pipecat 裡跑得動」（風險①）。**注意**：這是 Pipecat 自己載的 SenseVoice，非專案 `asr.py` 那顆；go/no-go 只問「模式行不行」，故可接受。日後留用時把 STT 換成包 `asr.py` 只是換一個 service 子類。
- **`StubLLMService`（手寫最小）**：不接 Anthropic，把輸入 transcript 映成**寫死的一段 3–4 句中文回覆**，確保有「句與句之間」可供打斷。
- **`SherpaInterruptibleTTSService`（手寫，spike 唯一真未知）**：subclass Pipecat 的可中止 TTS 基類；把回覆切句，逐句呼叫 sherpa `OfflineTts.generate(text, callback)`，callback 讀 `self._interrupted` flag，被打斷時回中止值使合成停止並 break 逐句迴圈。**介面設計為 engine-agnostic**（換後端＝換此子類，不動 pipeline 其餘）。
- **`InterruptDriver`（手寫）**：不用真 VAD，計時到（例如第 1 句開播後 X ms）就 push 一個 interruption frame，模擬「使用者開口打斷」。
- **`OutputSink`**：把 TTS 音訊 frame 丟棄或 log；不需真喇叭。

## Spike 要發現的未知（產出，不預設答案）

1. **sherpa-onnx callback 中止語意**：已裝版本 `OfflineTts.generate(text, callback)` 的 callback 簽章與回傳語意（回 0 停還是非 0 停？收哪些參數？）。本專案現行 `tts.py:226` 用無 callback 形式，此為未驗。
2. **Pipecat interruption 機制**：已裝版本注入程式化 interruption 的正確 frame（`StartInterruptionFrame` / `BotInterruptionFrame` / push `UserStartedSpeakingFrame`）＋ TTS service 該 subclass 哪個基類（`InterruptibleTTSService` vs `TTSService`）。
3. **批次 STT 在 frame 拉取下的行為**：`FunASRSTTService`（`SegmentedSTTService`）在 Pipecat 的 pull 模型下是否乾淨吃「一整段音訊 → 一段轉錄」。

以上不預設答案；spike 的價值就是把這三個實測出來。

## 環境前置（Step 0，本身即 go/no-go 訊號）

1. 建**獨立 venv**（`talkybuddy/spike/a2_pipecat/.venv`，與正式 `.venv` 隔離；使用者 2026-07-08 簽核），在其中驗 `pip install pipecat-ai[funasr]` 裝得起來、import 乾淨；**記錄 Pipecat 版本**。隔離理由：`pipecat-ai[funasr]` 依賴重，避免污染現有可運行 PC 原型的正式環境；spike 拋棄時整個 venv 一併刪。
2. 注意 `FunASRSTTService` 可能拉 FunASR 依賴＋下載 `SenseVoiceSmall`（~1GB）；sherpa-onnx 在此獨立 venv 需**另裝一次**（`tts.py` 用的是正式 `.venv` 那顆，spike 不共用）。
3. **若 Pipecat 在此 Python 裝不起來／import 就炸 → 這本身就是 no-go 訊號**，記錄後止血，不強推。

## 落點與交付物

- **落點**：`talkybuddy/spike/a2_pipecat/`（新目錄，明確拋棄式，與既有 `bench/` 分開）。
- **交付物**：`talkybuddy/spike/a2_pipecat/SPIKE-RESULT.md`——含 **PASS/FAIL**、上述**三個未知的實測答案**、任何 workaround、以及「若留用，STT 從 native 換成包 `asr.py`／TTS 介面如何長成正式 `StreamingTurnManager` 的一部分」的一句話銜接建議。
- 程式碼過關後**可留可刪**（拋棄式定位；留用與否 spike 完成後另判）。

## 風險與止血

- **Pipecat 裝不起來 / 版本不合 Python** → Step 0 即擋，記錄為 no-go 訊號；備援路線＝改驗 LiveKit `StreamAdapter`（`research/13`）。
- **sherpa callback 中止在句內無效**（只能句間停）→ 對本專案**可接受**：`research/12` §2 第5列已載明「非自迴歸單句幾乎一次前向合成完，中止即時效益主要在句與句之間」。判準第 4 項要求的正是「句界乾淨停」，非句內即停。
- **`FunASRSTTService` 下載/相依過重** → 記錄為留用時的 footprint 風險（呼應 `research/12` §7 #10 的 Genio 板上開銷待驗）。
- 全程**不動 `server/`**，故 spike 失敗不影響現有可運行原型。

## 完成後的下一步

spike PASS → 回到正式 A2 spec：以驗證過的 `SherpaInterruptibleTTSService` 介面與 interruption 機制為基礎，設計 `StreamingTurnManager`（A2-2）如何在 `_process_text` **之外**組出 `TurnResult` 並呼叫它。spike FAIL → 依卡點切 LiveKit 備援或修正假設後重驗。
