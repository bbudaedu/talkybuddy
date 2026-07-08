# A2-2 StreamingTurnManager + Barge-in 迴路 設計

- 日期：2026-07-08（複審後修訂：釘死 criterion #2 淨判、補中斷輪 `TurnResult` 產物、補 barge-in 併發順序）
- 分支：`feat/dualnet-hybrid`
- 母脈絡：`research/12_A2_全雙工barge-in_設計交接.md`（§核心契約、A2-2 定義 line 146-147）、
  `spike/a2_pipecat/SPIKE-RESULT.md`（A2-1 spike＝GO，五判準全過）
- 前置：A2-1 spike 已驗 sherpa 句界中止（未知#1）＋ Pipecat 中斷接法（未知#2）＋批次 SenseVoice（判準#2）

## 目標（一句話）

在**不改動** `_process_text` / `run_turn_audio` 的前提下，新增一條**線上串流全雙工迴路**：
Silero VAD 偵測開口即 barge-in → 取消進行中的回覆 → 靜音逾時判輪結束 → 逐句合成可隨時句界乾淨中止。
交付「能打斷的玩偶」的伺服器端核心，用 canned 音訊 harness 二元淨判驗收。

## 範圍（使用者已簽核 2026-07-08）

**In scope（伺服器端迴路 + 測試 harness）**：
- 新套件 `server/streaming/`，把 spike 驗過的 `interruptible_synth.py` **畢業搬入**當基石。
- `StreamingTurnManager`（輪邊界 + barge-in 取消 + 串接）、`ReplySource` 可抽換介面、Silero VAD 封裝。
- canned wav harness + pytest 二元淨判。

**Out of scope（明確不做，YAGNI）**：
- ❌ 瀏覽器全雙工傳輸 / client 端即時靜音 → **端到端 demo 另案**。
- ❌ 真串流 LLM / Bedrock FM 接線 → 本 spec 只定 `ReplySource` 介面 + Stub + Batch 包裝；
  真串流各自立案（**不與 research/16 大腦決策撞期**）。
- ❌ AEC（→ research/12 A2-3）、A2-0 網路狀態機接線（→ A2-4）。

## 架構定位與不變式

1. **離線半雙工零改動**：`VoicePipeline.run_turn_audio` / `_process_text` 一行不動，續當斷網 fallback。
   A2-2 是**平行新路徑**，不 import 也不改既有 pipeline 熱路徑（grep 驗）。
2. **落點 `server/streaming/`**（非 `spike/`，spike 拋棄式）。
3. **產物仍是同一個 `TurnResult`**（`server/pipeline.py:36` dataclass），供日後接 DB / B 軸診斷；
   本 spec 只填 `asr_text` / `reply_text` / `state_events` / `latency_ms`，DB 寫入留 A2-4 整合。
   **中斷輪產物**：`reply_text` = 中斷前已合成的部分句（可半段）、`state_events` 記 `barge_in`；
   錯誤降級輪 `reply_text` = 空字串、`state_events` 記降級原因。B 軸日後靠 `state_events` 區辨三態。
4. **框架＝Pipecat**（與 A1-spike 已驗路徑一致，原生 `SileroVADAnalyzer` + `FunASRSTTService`）。
   核心 `InterruptibleSynth` 保持**框架無關**（純 threading.Event + sherpa callback），日後可抽離 Pipecat。

## 元件邊界（各一職責、可獨立測試）

### `server/streaming/reply_source.py` — 可抽換回覆句流介面（⚠️ 大腦免疫接縫）
```python
class ReplySource(Protocol):
    async def stream(self, asr_text: str, ctx: ReplyContext) -> AsyncIterator[str]:
        """逐句 yield 回覆文字（一次一句，供 InterruptibleSynth 立即合成）。"""
```
- `StubReplySource`：yield 固定多句中文（harness 用，確保有句界可打斷）。
- `BatchReplySource`：包現有 `scaffold.respond()` + 批次 `llm.generate()`，**一次吐完再 `split_sentences` 切句** yield。
  過渡實作，讓非串流大腦也能掛上此迴路。
- `ReplyContext`：帶 `directive` / `scaffold_result` 等（沿用現有欄位），不含框架型別。
- **免疫落點**：日後本地串流 EdgeLLM、雲端 Bedrock FM 各實作一個 `ReplySource`，`StreamingTurnManager` 不動。

### `server/streaming/interruptible_synth.py` — 句界可中止合成核心（從 spike 搬入）
- 內容＝ `spike/a2_pipecat/interruptible_synth.py` 原封搬入（已 5 pytest 驗過）：
  `interrupt()` / `reset()` / `synth_one()` / `synth_sentences()`，sherpa callback 回非 0 句內/句界乾淨停。
- **不改邏輯**，只搬位置 + 補模組 docstring 指明來源。

### `server/streaming/vad.py` — Silero VAD 封裝
- 薄封裝 Pipecat `SileroVADAnalyzer`，於連續音訊上吐 `VADUserStartedSpeakingFrame` / `VADUserStoppedSpeakingFrame`。
- 參數（start_secs / stop_secs / 靜音逾時）集中此處，便於日後上機調校（research/12：繁中門檻待實測）。

### `server/streaming/turn_manager.py` — StreamingTurnManager（核心編排）
- 擁有：輪邊界判定、barge-in 取消、串接 `VAD → 批次 SenseVoice 轉錄 → ReplySource → InterruptibleSynth → sink`。
- Pipecat processor override `process_frame`：偵測 `VADUserStartedSpeakingFrame` 且 bot 正在說 →
  `super()` 前呼 `synth.interrupt()` + cancel reply asyncio task（SPIKE-RESULT 定案攔截點）。
- 產出 `TurnResult`。

## 資料流（線上全雙工一輪）
```
連續音訊 → SileroVAD
  ├─ 講話中 & bot 正在說 → barge-in：synth.interrupt() + cancel reply task（句界停）
  ├─ VADUserStopped + 靜音逾時 → 判「講完」→ SenseVoice 批次轉錄整段（FunASRSTTService）
  └─ 得 asr_text → ReplySource.stream() 逐句 → InterruptibleSynth 逐句合成 → 輸出 sink
                                                    ↑ barge-in 隨時可切
```

## Barge-in 語意（A2-1 spike 已一手驗證）
- sherpa `OfflineTts.generate(text, callback) -> int`，callback 回非 0 即中止當前合成（句內提早停）。
- 句界停＝`synth_sentences` 在下一句開始前檢查 `is_interrupted()` 即 break。
- Pipecat 攔截點＝override `process_frame` 在 `super().process_frame` **之前** `synth.interrupt()`；
  interruption frame＝`InterruptionFrame`（非 `StartInterruptionFrame`，1.5.0 定案）。
- **併發順序保證**（避免競態）：barge-in 觸發時**先** `synth.interrupt()`（threading.Event，令
  sherpa callback 立即回非 0，收斂「已交給 synth 的那半句」），**後** cancel asyncio reply task
  （收斂「尚未 yield 給 synth 的句子」）。兩者職責不重疊：interrupt 管進行中合成、cancel 管上游句流。

## 錯誤處理與降級
- ReplySource / 合成任一階段例外 → 記 log、該輪回退空回覆（不 crash 迴路），對齊現有 pipeline「demo 韌性優先」。
- VAD 靜音逾時參數保守取值（避免把停頓誤判為輪結束），數值待上機（A2-3）調。
- 本 spec **不處理** AEC 自體回授誤觸發（TTS 被麥收回誤當 barge-in）→ 明確留 A2-3。

## 驗收（二元淨判 + harness，比照 spike）
canned wav（`models/sherpa-onnx-sense-voice-.../test_wavs/zh.wav`）驅動 VAD frame，pytest：
1. **無 barge-in** → `StubReplySource` N 句全數合成（sink frames 對應 N 句）。
2. **播放中注入 VAD 開口**（注入點釘在第 2 句合成期間）→ sink frames 對應句數 **≤ 2**
   （句界乾淨停，第 3 句起不再出現），比照 spike 4→1。淨判＝比對確切句數上界，非模糊「變少」。
3. **隔離不變式**：grep 確認 `server/streaming/` 未 import `server.pipeline` 的熱路徑函式、未改 `_process_text`。
4. **`TurnResult` 欄位完整**：無 barge-in 輪 `asr_text` / `reply_text` / `state_events` 非空；
   barge-in 輪 `reply_text` 允許為「已合成的部分句」（可半段）、`state_events` 須含 `barge_in`。
5. 用**真 sherpa 合成 + 真 Silero VAD**，僅 ReplySource 用 Stub（免真 LLM）。

## 測試策略
- `interruptible_synth` 沿用 spike 既有 5 pytest（搬入後路徑更新即可）。
- `turn_manager` 整合測試＝ harness（上述驗收 1/2），真 VAD + 真 sherpa + StubReplySource。
- `reply_source` 單元測試：`StubReplySource` 逐句、`BatchReplySource` 包批次後切句正確。

## 依賴與風險
- 依賴 `spike/a2_pipecat/.venv` 已驗的 Pipecat 1.5.0 / sherpa-onnx 1.13.4 / torch CPU / SenseVoice 模型。
  **正式 `server/streaming/` 是否共用 spike venv 或併入正式 .venv＝實作首個決策**（spike venv 為隔離而生）。
- Silero VAD 繁中兒童語音門檻無實測 → 參數留可調、驗收只驗二元 barge-in 行為、不驗延遲/準確度。
- 大腦決策（research/16）未定 → 以 `ReplySource` 介面隔離，本 spec 不賭大腦形態。

## 後續銜接（非本 spec）
- 真串流 EdgeLLM `ReplySource` / Bedrock FM `ReplySource`（各立案）。
- A2-3 AEC + 兒童繁中 double-talk 實測。
- A2-4 掛 A2-0 網路狀態機、線上↔離線切換、`TurnResult` 寫 DB / 觸發 B 軸。
- 端到端瀏覽器全雙工 demo（傳輸 + client 靜音）。
