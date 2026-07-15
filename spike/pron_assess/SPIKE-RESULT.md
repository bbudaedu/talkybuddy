# 發音評測 Spike 結果：路 A vs 路 B

日期 2026-07-14。分支 `feat/cloud-llm-bedrock`。目標＝把 `diagnose.py:141` 的假 pronunciation 分數換成真聲學評測（read-aloud、reference 已知、B 軸異步、本地評分）。

## 裁定：**選路 A（GO）**。A/B 皆實跑、鑑別力並列（68~70），A 依賴更輕（純 Python、無 GPL 系統二進位）勝出；B 留作後備。

---

## 破案關鍵：不是音檔問題，是音標系統對不上

前次 spike 卡在「爛音檔」（ElevenLabs 克隆 baseline，whisper 幻聽）。本次用專案既有 TTS（`server/tts.py` 的 `en_US-lessac-medium` Piper voice）合成**完美發音**的已知句 `I want to eat an apple.`（存 `clean_en_apple.wav`），暴露出真正的 bug：

- 模型 `vitouphy/wav2vec2-xls-r-300m-timit-phoneme` 的 vocab 其實是 **IPA**（46 token：`ɑ æ ə aʊ aɪ ʧ ð ɾ ɛ ɝ eɪ ʤ ŋ oʊ ɔɪ ɹ ʃ θ ʊ u …`），**不是** ARPAbet。
- g2p_en 產出的 reference 是 **ARPAbet**。兩套音標永遠對不上 → 正確/錯誤 ref 都趴在地板，假差距。
- 另一個 bug：`batch_decode` 會把同一個字內的 IPA 音素黏成一團（`aɪwɑn`），要改用 **CTC argmax → 逐 id 折疊解碼** 才拿得到乾淨音素序列。

## 修法（＝路 A 正式版做法）

1. **hyp**：CTC argmax → 連續同 id 折疊 + 去 PAD → id→token，濾掉 `| / space / UNK / PAD / <s></s>`。得乾淨 IPA 序列。
2. **ref**：g2p_en → ARPAbet（去 stress 數字）→ 一張 **39 條靜態 `ARPA_TO_IPA` 映射表**轉成模型同一套 IPA（TIMIT 縮減集無 `ɔ/ʌ/ʒ`，併到最近者）。
3. edit-distance 對齊 → 命中率 0–100。

## 路 A 驗收數據（`clean_en_apple.wav`）

hyp 解碼：`aɪ w ɑ n t u i t ɛ n æ p l`（13 音素，＝ /aɪ wɑnt tu it æn æpəl/，正確）

| reference | 分數 | 說明 |
|---|---|---|
| I want to eat an apple.（正確） | **80.0** | 基準 |
| I want to eat a napple.（同音） | 80.0 | 與 "an apple" 同音素，理應同分 ✓ |
| I want to **see** an apple. | 66.7 | 1 詞差 |
| I want to eat an **orange**. | 56.2 | 1 詞差 |
| I want a big red apple today.（半對） | 45.0 | |
| The quick brown fox jumps.（全錯） | 15.8 | |
| Hello how are you doing today.（全錯） | 11.1 | |

- **正確 − 全錯 = 69.7 ≫ 20 → GO。**
- **分數隨語音距離單調遞減**（80→66.7→56.2→45→15.8→11.1）＝評分器真的在測發音、不是看字面（同音的 "a napple" 拿滿分證實這點）。

## 路 B 驗收數據（已實跑，用戶同意 `sudo apt install espeak-ng`）

路 B ＝ espeak-ng 1.51（系統）+ phonemizer 3.3.0 + `facebook/wav2vec2-lv-60-espeak-cv-ft`（官方 `Wav2Vec2Processor`，lang=en）＋ phonemizer 同源 espeak 音標。`spike_b.py`。

**同樣踩到 segmentation 坑**：phonemizer 預設逐「詞」黏團（`wɔnt`/`æpəl` 各一 token）→ 與模型逐音素輸出粒度對不上 → 全 0。修法＝`Separator(phone=" ")` 逐音素分隔。

hyp 解碼：`aɪ w ʌ n t t uː iː ɾ ɐ n æ p əl`（14 音素；比路 A 更細，抓到 flap `ɾ`、`ɐ`、`əl` 等 allophone）

| reference | 路 B 分數 | 路 A 分數 |
|---|---|---|
| I want to eat an apple.（正確） | **78.6** | **80.0** |
| I want to see an apple. | 71.4 | 66.7 |
| I want to eat an orange. | 50.0 | 56.2 |
| I want a big red apple today.（半對） | 36.8 | 45.0 |
| The quick brown fox jumps.（全錯） | 10.5 | 15.8 |
| Hello how are you doing today.（全錯） | 5.9 | 11.1 |
| **正確 − 全錯（差距）** | **68.1 → GO** | **69.7 → GO** |

## 正面對決：A 與 B 鑑別力幾乎並列 → 依賴成本定勝負

- **鑑別力**：兩者差距 68~70，都遠超 20 門檻，都單調遞減。實測**打平**（差異在雜訊內）。
- **音素解碼品質**：B 略細（含 allophone），A 是乾淨的 CMU 39 音素集。對「read-aloud 命中率評分」兩者都夠；A 的粗粒度反而更穩、較不會因口音 allophone 誤扣分。
- **依賴成本（決定因素）**：
  - 路 A：**純 Python**（torch/transformers/g2p_en/nltk）+ 39 條靜態映射表。**零系統二進位**。
  - 路 B：多一個 **espeak-ng 系統二進位（GPL-3.0）** + phonemizer + espeak-ng-data。Yocto 嵌入式移植負擔更重（見 tts.py:19 既有殘留風險註記）。

**裁定：鑑別力打平 → 選依賴更輕、無 GPL/無系統二進位的路 A。** 路 B 已證同樣可行，作為 A 若在真人多樣口音上失準時的現成後備（或搭 Kaldi GOP 後驗）。

## 正式接線注意（spike 不碰，spec 處理）

- **音檔生命週期**：`pipeline.py:169` ASR 後立即 `unlink`，且刪在 scaffold 決定 target 之前 → 正式版要「音檔還在手上時就評分」，或延後刪除。
- 定位：B 軸異步診斷層（每 N=5 輪背景 task，不在即時路徑），延遲寬鬆、CPU 可跑（本 spike 已證）。
- 隱私：本地評分，音檔用後即刪、只留分數。
- 模型/映射表建議搬進 `server/pronunciation.py`，TDD 補測（正確 ref 高分、全錯 ref 低分、同音同分三案）。

## 產物

- `spike.py`（已更新為路 A 正解：IPA 同源映射 + CTC 逐 id 解碼）
- `clean_en_apple.wav`（TTS 合成的乾淨英文素材，可重用當測試 fixture）
