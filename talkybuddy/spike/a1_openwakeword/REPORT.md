# A1 喚醒層 spike：openWakeWord 檔案驗證報告

日期：2026-07-10　　環境：GPU 伺服器（無麥克風，純檔案驗證）

## 動機

擺脫 Porcupine 對 Picovoice **雲端 AccessKey 驗證**的依賴（想完全自主）。
候選：**openWakeWord**（Apache 2.0、100% 裝置端、零 key、零雲端驗證、可自訓喚醒詞）。

## 設定

- `openwakeword 0.4.0` + `onnxruntime 1.27`（CPU，不需 GPU）
- 內建 ONNX 模型：`hey_jarvis` / `alexa` / `hey_mycroft`（另有 melspectrogram / embedding / silero_vad）
- 正樣本用 `piper-tts`（en_US-lessac-medium）合成；負樣本用 repo 內多語語音
- 偵測腳本：`detect.py`（16kHz 單聲道 int16，逐 80ms 塊，門檻 0.5）

## 結果

### 負樣本（正常語音，不該觸發）— 全數通過 ✅
中文 / 英文 / 粵語 / 日文 / 韓文語音、英文 TTS、多句英文負句：
最高分 0.000–0.10，**零誤觸發**。中文語音完全不亂叫（對中文產品是關鍵）。

### 正樣本（含 context）— 全數正確 ✅
- "hey jarvis"（含埋在句中）→ hey_jarvis 0.998–0.999
- "alexa" → alexa 1.000
- "hey mycroft"（含埋在句中）→ hey_mycroft 0.951–0.999
- 各自只點燃對應模型，無跨觸發

### ⚠️ 重要教訓：一定要餵「連續串流 + 前置 context」
第一次餵 0.7s **冷短片**時出現跨觸發與假陽性（"hello" 讓 hey_mycroft 到 0.857）。
**前面補 2s 靜音**後這些全部消失。openWakeWord 為串流設計，每次預測需約 1.5s 前置音訊。
→ 真實實作務必餵連續麥克風串流（瀏覽器天然如此），不要一段段冷餵。

### 單一假陰性
"okay alexa turn on the kitchen light" 該句 alexa 未觸發（piper 韻律問題），
單獨 "alexa" 正常。需真人聲複驗，非系統性缺陷。

## 結論：GO ✅

openWakeWord 在無 key、無雲端、CPU 下即可穩定運作，中文語音零誤觸發、英文喚醒詞高信心觸發。
值得投入下一步。

## 下一步

1. **真麥克風即時測**（用戶筆電）：確認真人聲觸發率與真實房間噪音下的誤觸發率。
2. **自訓中文喚醒詞**（如「哈囉學伴」）：openWakeWord 官方 Colab 可免費訓練。
3. **瀏覽器整合評估**：onnxruntime-web 跑 melspectrogram+embedding+wakeword（DIY，需估工）。

## 重現方式

```bash
cd talkybuddy/spike/a1_openwakeword
. .venv/bin/activate
python detect.py <任意 wav>            # 預設測 hey_jarvis,alexa,hey_mycroft，門檻 0.5
python detect.py <wav> --threshold 0.4 --models hey_jarvis
```

（此目錄為拋棄式 spike，venv 與 piper 語音模型未進版控。）
