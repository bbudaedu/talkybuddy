# 任務：接通 Path 1 自架串流 barge-in（G2 缺口），用 TDD

## 先做這件事

```bash
cd /home/budaedu/talkybuddy
git worktree add ../talkybuddy-path1 -b gsd/path1-realwire
cd ../talkybuddy-path1
```

**之後所有工作都在 `../talkybuddy-path1`。** 主工作區 `/home/budaedu/talkybuddy` 有另一個
session 在做決賽演示準備，不要動它。

## 背景（決賽 2026-08-01，剩 2 天）

`.planning/ROADMAP.md` 把 Phase 2（Path 1）標為 **DELIVERED w/ gap**：
「`run_realwire.py` 漏接 `BargeInGate`，真機 barge-in 不觸發；無實機執行證據」。
這就是 G2。你的任務是關掉它。

## 已查證的事實（不要重查，也不要相信與此矛盾的文件）

1. **Path 1 的入口是 `server/streaming/run_realwire.py`**，一支跑本機 pipecat pipeline
   （`LocalAudioTransport` 裸麥/喇叭）的 CLI，**不是 WebSocket 端點**。
   ⚠️ `edge/UAT_FINDINGS_2026-07-29.md:21` 說 Path 1 是 `/ws/live` —— **那是錯的**。
   `server/app.py:659` 的 `/ws/live` 是 Nova Sonic S2S（Path 2）。順手把該行修正。

2. **缺口的確切位置**：`run_realwire.py:52`

   ```python
   return [transport.input(), stt, manager, tts, transport.output()]
   ```

   鏈上沒有 `BargeInGate`。

3. **兩端都是好的，只差中間沒接**：
   - `server/streaming/barge_in_gate.py` 已實作，會把 `InputAudioRawFrame` 餵給
     `SpeechGate`，偵測到就往 DOWNSTREAM 發 `BargeInDetectedFrame`（已有測試）
   - `server/streaming/turn_manager.py:48` 的 `StreamingTurnManager` 已經會消費
     `BargeInDetectedFrame`（push `InterruptionFrame` + cancel reply task）

4. **開發機依賴只差 `pyaudio`**。實跑 `check_prerequisites()` 的結果：pipecat 1.5.0、
   torch、sherpa_onnx、funasr、SenseVoiceSmall cache、espeak-ng-data、sherpa zh onnx
   **全部就緒**，唯一缺項是：

   ```
   sudo apt install portaudio19-dev && .venv/bin/pip install pyaudio
   ```

5. **這條線不上裝置**。Genio 520 刻意未裝 torch/pipecat（`edge/deploy/README.md:57`），
   記憶體 2,038MB/3,794MB 也塞不下。目標是**在開發機上跑通並留下實機證據**，
   不是部署到玩偶。**完全不要 ssh 或 rsync 到 192.168.31.78** —— 那台由另一個
   session 獨佔。

## TDD 流程

測試跑法（streaming 測試需要 `.venv`，有獨立 `pytest.ini`）：

```bash
./run_tests.sh          # 主 suite + streaming suite
.venv/bin/python -m pytest server/streaming/tests/ -v    # 只跑 streaming
```

### 第一個紅燈已經替你準備好了

`server/streaming/tests/test_run_realwire.py:26` 現在硬編碼：

```python
assert len(procs) == 5
```

接上 `BargeInGate` 後會變 6 個，這條測試**會紅**。這正是你的起點：
先改測試表達新的期望（鏈上必須有 `BargeInGate`、且位置正確），看它紅，
再改 `build_processors` 讓它綠。**不要先改實作。**

### 建議的測試順序

1. **紅**：`test_build_processors_includes_barge_in_gate` —— 斷言鏈中存在
   `BargeInGate` 實例，且**位置在 `transport.input()` 之後、`StreamingTurnManager` 之前**。
   用型別斷言，不要只斷言長度（長度是脆弱斷言，這次就是它擋路）。
2. **綠**：修 `build_processors`。
3. **紅**：一條端到端 frame-level 測試 —— 用假 transport 灌
   `TranscriptionFrame` → 若干 `InputAudioRawFrame`（含人聲），
   斷言 `StreamingTurnManager.result.state_events` 含 `"barge_in"`。
   這條是真正證明「接通了」的測試，不是只證明「物件在鏈上」。
   參考既有的 `test_barge_in_gate.py` 與 `test_turn_manager.py` 怎麼造 frame。
4. **綠**，然後重構。

### 一個要你自己判斷的設計決策

`BargeInGate` 該插在 `transport.input()` 與 `stt` 之間，還是 `stt` 與 `manager` 之間？

- `BargeInGate` 會原封轉發所有 frame，兩個位置都「能動」
- 但 STT 服務可能消費或轉換 `InputAudioRawFrame`，放在 `stt` 之後有拿不到音訊的風險

**用測試證明你的選擇，別用推理。** 在 commit message 裡寫下你驗證的方式。

## 驗收標準

- [ ] `./run_tests.sh` 全綠（包含既有 89 條遊戲測試與 streaming suite，不得為了過關而放寬既有測試）
- [ ] 新增的測試在未修 `build_processors` 前確實會紅（附上你看到紅燈的證據）
- [ ] `run_realwire.py` 的 docstring 更新：手動驗收第 2 條現在真的成立
- [ ] `edge/UAT_FINDINGS_2026-07-29.md:21` 的 `/ws/live` 錯誤已修正
- [ ] **實機證據**：裝好 pyaudio 後真的跑一次 `.venv/bin/python -m server.streaming.run_realwire`，
      對麥克風講話、在回覆播放中插話，記錄結果到 `edge/PATH1_REALWIRE_EVIDENCE.md`
      （二元判定：回覆有沒有在句界乾淨停下）
- [ ] `.planning/ROADMAP.md` 的 Phase 2 gap 標記更新為實際狀態

## 紅線

- **未實測不得寫數字或宣稱通過。** 記 `blocked` 比填一個合理的猜測有價值 ——
  這個專案已經因為「用 TTS 合成音驗 KWS」拿到過假信心，別再來一次。
- 沒有 pyaudio 就沒有實機證據。若 `portaudio19-dev` 裝不起來，**如實回報卡在哪**，
  把單元測試層級的成果交出來，不要假裝跑過。
- 不碰 `server/games.py`、`server/pipeline.py` 的遊戲區塊、`server/app.py` 的 `/api/game`
  —— 那是另一個 session 的活。
- 提交用小而可回退的 atomic commit。
