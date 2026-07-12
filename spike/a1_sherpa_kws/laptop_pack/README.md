# 筆電真人聲喚醒詞測試包（sherpa-onnx KWS）

對麥克風講「哈囉學伴 / 說說學伴 / 嗨學伴」看即時偵測。免 key、離線、裝置端。

## 用法（Win / Mac / Linux 通用）

把整個 `laptop_pack/` 資料夾複製到有麥克風的筆電，然後：

```bash
# 1) 準備環境（建 venv + 裝套件 + 下載模型，約 40MB）
python bootstrap.py

# 2) 依畫面提示執行即時測試，例如：
#    Mac/Linux:
.venv/bin/python live_mic.py
#    Windows:
.venv\Scripts\python live_mic.py
```

對麥克風清楚講出喚醒詞，看到 `🔴 偵測` 就是成功。Ctrl+C 結束。

## 調門檻（這是這次測試的重點）

```bash
.venv/bin/python live_mic.py --threshold 0.25
```

- 門檻**越低越靈敏**，但太低會誤觸發。合成音需要 0.05；**真人聲預期更高**。
- 建議流程：
  1. 先用 `--threshold 0.30` 講喚醒詞，若不太觸發就往下調（0.25 → 0.20 → 0.15）。
  2. 找到「講喚醒詞會觸發」的值後，**改成正常聊天、念文章、放音樂**，看會不會亂叫（誤觸發）。
  3. 目標：**喚醒詞穩定觸發 + 正常講話不誤觸發** 的門檻，就是要帶進產品的值。
- 記下每個門檻的真人觸發率與誤觸發感受，回報給我。

## 疑難排解

- **Linux 若報 PortAudio 錯**：`sudo apt install libportaudio2`（Win/Mac 的 sounddevice wheel 已內含，免裝）。
- **抓錯麥克風**：程式啟動會印出「預設輸入裝置」；如需指定，跟我說我幫你加裝置選擇。
- **完全不觸發**：先確認麥克風有進聲音（系統設定），再把 `--threshold` 降到 0.1 試。

## 內容物

- `bootstrap.py` — 跨平台環境準備
- `live_mic.py` — 即時麥克風偵測
- `keywords.txt` — 三個候選喚醒詞（拼音 token）
- 模型於 bootstrap 後下載到 `sherpa-onnx-kws-zipformer-.../`
