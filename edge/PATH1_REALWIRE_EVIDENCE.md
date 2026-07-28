# Path 1 real-wire barge-in 執行證據

日期：2026-07-29　分支：`gsd/path1-realwire`　對象：Known-Gaps **G2**
入口：`server/streaming/run_realwire.py`（本機 pipecat pipeline CLI，**不是** WebSocket 端點）

---

## 一句話結論

**接線缺口已補上並有自動化證明；真麥克風的二元驗收沒做成 —— 這台開發機沒有任何錄音裝置。**
不是「沒跑」，是「跑了、卡在硬體邊界」。以下是量到的東西，沒有推測的數字。

| 驗收項 | 狀態 | 證據 |
|---|---|---|
| BargeInGate 在鏈上 | ✅ 通過 | `test_build_processors_includes_barge_in_gate` |
| gate 排在 STT 之前（不依賴 STT 轉發音訊） | ✅ 通過 | `test_barge_in_gate_sits_upstream_of_stt` |
| 真實鏈上灌人聲 → 觸發 barge-in、回覆提早收斂 | ✅ 通過 | `test_realwire_chain_bargein_end_to_end` |
| 完整測試 suite 無退步 | ✅ 通過 | `./run_tests.sh` → 1009 passed, 0 failed (9m31s) |
| **① 對真麥講一句 → 聽到回覆** | ❌ **BLOCKED** | 無錄音/播放裝置，見下 |
| **② 回覆播放中插話 → 句界乾淨停** | ❌ **BLOCKED** | 同上 |

---

## 修了什麼

修前（`build_processors`）：

```
transport.input() → STT → StreamingTurnManager → TTS → transport.output()
```

`BargeInGate` 不在鏈上。它會發的 `BargeInDetectedFrame` 沒有來源，於是
`turn_manager.py:48` 那段已經寫好、也有測試的 barge-in 分支在真機上是死碼 ——
兩端都是好的，中間沒接。

修後：

```
transport.input() → BargeInGate → STT → StreamingTurnManager → TTS → transport.output()
```

### 位置決策（量出來的，不是推理）

gate 放 STT 之前或之後都實作過、都跑過端到端測試，**兩種都會過**。原因也量到了：

```
BEFORE_STT: 28 frames, bytes= 178944  distinct(rate,ch,len)= [(16000,1,6144), (16000,1,6400)]
AFTER_STT : 28 frames, bytes= 178944  distinct(rate,ch,len)= [(16000,1,6144), (16000,1,6400)]
IDENTICAL : True
```

`FunASRSTTService` 把 `InputAudioRawFrame` **原封轉發**，所以 gate 排它後面今天也收得到音訊。

選 STT 之前的理由不是「怕它壞」，是「那不是契約」：原封轉發是 pipecat 的實作細節。
上游改版若改成消費或重採樣音訊，barge-in 會**無聲失效** —— 沒有任何測試會紅，
只有使用者會發現插話沒反應。排在 STT 前面直接吃 transport 原始音訊，零成本移除這個相依。

---

## 為什麼真機驗收做不成

`check_prerequisites()` 現在回傳 `[]`（全就緒）：`portaudio19-dev` 已裝、`pyaudio 0.2.14` 已裝、
SenseVoiceSmall cache / espeak-ng-data / sherpa zh onnx 全在。**前置不是卡點。**

實跑 `.venv/bin/python -m server.streaming.run_realwire`，pipeline **組得起來也 link 得起來**，
`StartFrame` 走完整條鏈（`pipeline is now ready`），然後撞上硬體：

```
ERROR | LocalAudioInputTransport#0  exception (pyaudio/__init__.py:441):
        Error processing frame: [Errno -9996] Invalid input device (no default output device)
ERROR | LocalAudioOutputTransport#0 exception (pyaudio/__init__.py:441):
        Error processing frame: [Errno -9996] Invalid output device (no default output device)
```

錯誤是 non-fatal 的 `ErrorFrame`，所以行程不會結束、會一直掛著（實測 200s 未退出）。

### 硬體現況（開發機）

```
$ .venv/bin/python -c "import pyaudio; ..."      # 一般使用者
device_count = 0
default input : ERROR No Default Input Device Available
default output: ERROR No Default Output Device Available

$ sudo .venv/bin/python -c "import pyaudio; ..." # root，排除權限因素
device_count = 4
0 'HDA NVidia: HDMI 0 (hw:1,3)' in= 0 out= 8
1 'HDA NVidia: HDMI 1 (hw:1,7)' in= 0 out= 8
2 'HDA NVidia: HDMI 2 (hw:1,8)' in= 0 out= 8
3 'HDA NVidia: HDMI 3 (hw:1,9)' in= 0 out= 8

$ ls /dev/snd/
controlC0  controlC1  hwC1D0  pcmC1D3p  pcmC1D7p  pcmC1D8p  pcmC1D9p  seq  timer
```

三件事：

1. **完全沒有 capture 節點**（`/dev/snd` 內沒有任何 `pcmC*D*c`）→ 這台機器沒有可用的麥克風。
   即使用 root 列舉，4 個裝置全部 `in= 0`。
2. card0（Intel HDA 類比）只有 `controlC0`，**連一個 pcm 節點都沒有**（`/proc/asound/card0/` 只有 `id`）
   → 類比輸入/輸出（3.5mm）在此機未啟用。
3. 唯一的輸出是 NVidia HDMI，且 `crw-rw---- root:audio` 而目前使用者不在 `audio` group。

所以「講一句」與「插話」這兩個二元判定，**在這台機器上不可能產生**。
依專案紅線（`用 TTS 合成音驗 KWS` 那次假信心的教訓），這裡記 **BLOCKED**，不填合理猜測。

---

## 誰有麥克風就能收尾（完整步驟）

前置已在本 repo 的 `.venv` 備妥，換一台有錄音裝置的機器只需確認 `check_prerequisites()` 回 `[]`。

```bash
cd /home/budaedu/talkybuddy-path1
.venv/bin/python -m server.streaming.run_realwire
# 出現 "[run_realwire] 就緒：對麥克風講話..." 才算裝置開起來了
```

1. **判定 ①**：對麥克風講一句中文 → 有沒有聽到回覆？（有／無）
2. **判定 ②**：回覆播到一半時開口插話 → 回覆有沒有在**句界**停下、系統轉去聽新輸入？（乾淨停／沒停）

無 AEC（A2-3 未做）：外放時 TTS 會被麥收回、可能誤觸發 barge-in。**請戴耳機測**，否則測到的是回授不是插話。

判定完把結果補進上面的表格，G2 才算真正關閉。

---

## 已知待辦（本次未做，僅記錄）

- `check_prerequisites()` 只檢查 `pyaudio` **能不能 import**，不檢查**有沒有裝置**。
  所以在這台機器上它回報「全就緒」，卻在 pipeline 起來後才撞 `-9996` 並無聲掛住。
  加一個「至少要有一個 input device」的檢查，可以把靜默 hang 換成一行清楚訊息。
  未在本分支動手 —— 超出 G2 範圍，留給決策。
- Genio 520 不在這條線上：刻意未裝 torch/pipecat（`edge/deploy/README.md:57`），
  記憶體也塞不下。本檔案的驗收對象是開發機，不是裝置。
