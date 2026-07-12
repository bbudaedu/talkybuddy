# A1 喚醒層 spike：sherpa-onnx KWS（中文「哈囉學伴」）

日期：2026-07-10　環境：GPU 伺服器（無麥克風，純檔案驗證）

## 為什麼從 openWakeWord 轉來
openWakeWord 官方**只支援英文**（embedding 英文訓練），中文「哈囉學伴」不在支援範圍；且本機 GPU 目前故障（`nvidia-smi` Unknown Error），本地訓練也不可行。
→ 改用 **sherpa-onnx keyword spotting**：中文原生、**免訓練**（喚醒詞＝一行文字檔）、免 key、裝置端、官方 WASM，且**已在專案技術棧**（ASR 用 SenseVoice 同一家）。

## 設定
- 模型：`sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01`（中文，WenetSpeech 真人訓練）
- sherpa_onnx 1.13.3（已在 `talkybuddy/.venv`）
- 喚醒詞用拼音 token：`sherpa-onnx-cli text2token --tokens-type ppinyin`
  - 哈囉學伴 → `h ā l uō x ué b àn`
- 正樣本 piper（repo 既有 `zh_CN-huayan-medium`）合成；負樣本用中文/粵/英語音
- 腳本：`kws_detect.py`

## 結果
- **腳本正確性**：模型內建 test_wavs 3–6 用內建 keywords 全數正確觸發 ✅
- **「哈囉學伴」**：門檻 0.05 時正確觸發 ✅
- **零誤觸發**：門檻 0.05 下，中文問候/開燈/公園、zh.wav、yue.wav、en.wav **全部不觸發** ✅

## 關鍵注意
1. **需低門檻(0.05)才觸發合成音**：因 piper 合成音相對 WenetSpeech 真人語音是 off-distribution。真人聲預期分數更高、可用更高門檻 → **務必用真麥克風真人聲重調門檻**。
2. **句中埋詞未觸發**（"哈囉學伴 今天天氣如何"）：合成韻律問題，真人聲需複驗。
3. 喚醒詞候選都測了 token：哈囉學伴 / 說說學伴 / 嗨學伴（皆可寫入）。

## 結論：GO ✅
中文喚醒詞免訓練、免 key、免 GPU、零誤觸發即可運作，且與現有 sherpa-onnx 技術棧一致。相較 openWakeWord（英文限定+需 GPU 訓練），這是更佳路徑。

## 下一步
1. **真麥克風真人聲測**（用戶筆電）：重調門檻、量真實房噪誤觸發率。
2. 決定最終喚醒詞（哈囉學伴 / 說說學伴 / 嗨學伴）。
3. 瀏覽器 WASM 整合（sherpa-onnx 官方有 WASM KWS 範例）。

## 重現
```bash
cd talkybuddy/spike/a1_sherpa_kws
PY=../../.venv/bin/python
$PY kws_detect.py kw_multi.txt samples/p_halou_xueban.wav <其他 wav>
# 產新關鍵詞: ../../.venv/bin/sherpa-onnx-cli text2token --tokens <model>/tokens.txt --tokens-type ppinyin raw.txt out.txt
```
（拋棄式 spike；模型/venv/wav 未進版控。）
