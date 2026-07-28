# 原生邊緣 KWS 規劃：讓裝置自己聽

> 2026-07-28。起因：查證方案 3 時發現喚醒層目前跑在**瀏覽器**，不在裝置上。
> 使用者判斷「原生在邊緣超棒」——這個判斷是對的，而且理由比 NPU 加速強得多。

---

## 1. 為什麼這件事比「KWS 上 NPU」重要

### 現況：裝置不是獨立裝置

| 元件 | 目前跑在哪 | 證據 |
|---|---|---|
| 喚醒詞（Path 1） | **瀏覽器** Porcupine WASM | `web/porcupine-engine.js` |
| 喚醒詞（Path 2） | **瀏覽器** sherpa-onnx KWS WASM | `config.py:70` `WAKE_SHERPA_BASE_URL=/static/vendor/sherpa-kws/` |
| 麥克風擷取 | **瀏覽器** getUserMedia | 同上 |
| ASR / LLM / TTS | 裝置 | `server/` on Genio 520 |

**意思是：現在必須有一台電腦、開著瀏覽器頁面、對著它的麥克風講話，
Genio 520 才會動。** 裝置本身是個「後端」，不是「玩偶」。

### 這對三件事都是硬傷

1. **產品形態**：提案書寫的是「**無螢幕實體伴讀裝置**」
   （`臺灣普惠科技應用痛點研究.md` §3.1）。現況需要螢幕與瀏覽器，**與提案不符**。
2. **普惠論述**：`docs/NEEDS_EVIDENCE.md` §C2 指出數位發展萌動區
   **每 6 戶就有 1 戶沒有網路**。若還要求家裡有一台電腦開瀏覽器，
   「低收入家庭負擔得起」的論述會被評審一句話打穿。
3. **隱私論述**：`docs/PRIVACY.md` §1 主張「語音音檔絕不落地、絕不上雲」。
   現況音訊先進瀏覽器再送裝置——**多了一段可被質疑的路徑**。

### 換句話說

**原生 KWS 不是效能優化，是把「這是一台獨立裝置」這句話變成真的。**
NPU 加速只值 0 分（加分條件已滿足），但這件事同時修補產品形態、普惠論述與隱私論述。

---

## 2. 已具備的條件（2026-07-28 實測確認）

| 條件 | 狀態 |
|---|---|
| 裝置端 `sherpa-onnx` | ✅ **1.13.4 已安裝**（`provision_device.sh` 已含） |
| ALSA 擷取裝置 | ✅ `hw:0,2`／`hw:0,3`／`hw:0,4`（mt8391evk） |
| ALSA 播放裝置 | ✅ `hw:0,0`／`hw:0,1`（DL0／DL1） |
| 16kHz mono 擷取 | ✅ `arecord -f S16_LE -r 16000 -c 1` 可產生正確規格檔案 |
| pipeline 吃 16k mono WAV | ✅ RIFF-sniff fast path 直讀，零 ffmpeg（`DEPLOY_EDGE.md` §5） |
| KWS 模型 | ❌ **repo 內沒有** |
| **麥克風實際收音** | ✅ **已解決，見 §5** |

> 好消息是引擎與音訊管線都已就位。**唯一還缺的是 KWS 模型與接線。**

---

## 3. 實作規劃

### ~~階段 0：解除麥克風阻擋~~ → ✅ **已完成（2026-07-28）**

USB 麥克風 `plughw:1,0` 收音正常、3.5mm 喇叭 `plughw:0,0` 播放經使用者聽測確認，
mixer 設定已 `alsactl store` 持久化。完整組態與兩個坑見 §5。**此阻擋已解除。**

### 階段 1：取得中文 KWS 模型並在裝置上跑通（半天）

sherpa-onnx 提供預訓練中文 KWS 模型（wenetspeech 系）。喚醒詞「說說學伴」以
Pinyin 標音指定，不需自行訓練——`config.py:68` 的註解顯示既有設計已走這條路。

```bash
# 裝置端
cd /root/talkybuddy/models
# 下載 sherpa-onnx 中文 KWS 模型（kws-zipformer wenetspeech）
# 喚醒詞檔以 pinyin 指定：s h uo1 s h uo1 x ve2 b an4
```

驗收：`sherpa-onnx` 的 KWS API 對著麥克風串流，喊「說說學伴」能觸發，
靜置 60 秒不誤觸。

### 階段 2：常駐喚醒服務（1 天）

新增 `edge/runtime/wake_listener.py`：

```
ALSA 串流擷取 (16k mono)
  → sherpa-onnx KeywordSpotter（常駐、低成本）
  → 命中「說說學伴」
  → 開始錄音直到 VAD 判定語句結束
  → 呼叫既有 pipeline（localhost，不經瀏覽器）
  → TTS 輸出經 aplay 播放
```

**設計要點**：

- 掛進 `run_edge.sh` 啟動序列，與 llama-server 同級的背景服務
- 喚醒引擎**常駐但輕量**——KWS 模型小，CPU 佔用需實測確認不影響 LLM 的 6 執行緒
- **保留瀏覽器路徑**：不刪除現有 web 喚醒，兩者並存。
  決賽前不動已驗證的路徑，原生路徑走獨立入口
- 音訊全程不落地（比照 `pipeline.py:98,106,159` 的既有 unlink 紀律）

### 階段 3（可選）：KWS 上 NPU

**只有在階段 2 實測顯示 KWS 常駐確實吃掉可觀 CPU 時才做。**

方案 2 已證明流程可行（Piper vocoder 整圖被 MDLA 接收、8.0×），
KWS 若為 CNN 型應同樣可轉。但：

- KWS 本來就便宜，省下的 CPU 可能不值得多一層複雜度
- 需先對模型做算子普查，確認無 `BatchMatMul`
  （**zipformer 系是 transformer，很可能不行**；需選 CNN 型 KWS 模型）

> ⚠️ 若選用 zipformer KWS，它是 transformer 骨幹，
> 依 `ADR-npu-path.md` §9 的結論**預期會被 MDLA 拒收**。
> 要上 NPU 需刻意選 CNN 型模型，這是選型時就要決定的事。

---

## 4. 時程與決賽取捨

| 階段 | 估時 | 決賽前該做嗎 |
|---|---|---|
| ~~0 音訊硬體~~ | — | ✅ **已完成 2026-07-28** |
| 1 KWS 模型跑通 | 半天 | ⚠️ 看剩餘時間 |
| 2 常駐喚醒服務 | 1 天 | ❌ **決賽前不建議** |
| 3 KWS 上 NPU | 半天+ | ❌ 不做 |

**建議**：決賽（2026-08-01）剩 4 天，階段 2 會動到啟動序列與音訊路徑，
風險高於收益。階段 1 可作為「若時間有餘」的加分項，階段 2、3 列入 v2。

**階段 0 已完成的附帶價值**：`plughw:1,0` 收音 + `plughw:0,0` 播放這條原生音訊
路徑，同時也是 **Phase 11 真機彩排**與**決賽當天實機演示**的前置，
不論階段 1–3 是否進行都已到手。

**但這件事應該寫進簡報的 roadmap** ——
「目前為裝置＋瀏覽器終端；下一步是喚醒與收音全部原生化，成為真正的無螢幕獨立裝置」
是誠實且有說服力的敘述，比假裝現況已經獨立好得多。

---

## 5. 音訊硬體：已解決（2026-07-28）

**麥克風是 USB，不是 3.5mm。** 先前一整輪對 card 0（`mt8391evk`）類比輸入的排查
方向錯誤，該卡的錄音裝置本來就沒有接東西。

### 實際組態（實測確認）

| 項目 | 值 |
|---|---|
| 麥克風 | USB，`Jieli Technology K`（`ID 4c4a:4155`），列舉為 **`card 1`** |
| 麥克風裝置 | **`plughw:1,0`** |
| 麥克風原生格式 | **僅支援 48000 Hz**、S16_LE、mono（`/proc/asound/card1/stream0`） |
| 喇叭 | 3.5mm Lineout，**`plughw:0,0`**（USB 裝置無播放能力） |
| `snd_usb_audio` | 已載入，模組存在於 kernel 6.6.92-mtk |

### 兩個會咬人的坑

**坑 1：USB 麥克風只有 48kHz，而 edge pipeline 要 16kHz mono。**

`DEPLOY_EDGE.md` §5 記載 edge 端**刻意不裝 ffmpeg**，規格不符會明確拋
`WavSpecMismatchError` 而非靜默降級。直接用 `hw:1,0` 要求 16kHz 會拿到 48kHz
（`arecord` 只印一行 warning 就繼續），送進 pipeline 必然報錯。

**解法：用 `plughw` 而非 `hw`。** ALSA 的 plug plugin 在擷取當下就完成重採樣，
零額外 process、零額外延遲，pipeline 的 RIFF-sniff fast path 直接命中。

```bash
# ✅ 正確
arecord -D plughw:1,0 -f S16_LE -r 16000 -c 1 -d 4 out.wav
# 實測：sr=16000, mono, peak=0.0802 —— 符合 pipeline 規格

# ❌ 錯誤：靜默拿到 48kHz
arecord -D hw:1,0 -f S16_LE -r 16000 -c 1 -d 4 out.wav
```

**評估過但不採用的替代方案：在 edge 裝 ffmpeg。** `plughw` 更乾淨——
不增加每回合的 subprocess 開銷、不動既有程式碼、也不破壞
「規格不符就明確報錯」這個刻意的設計決定（ffmpeg 另已列於
`.planning/codebase/CONCERNS.md` 的既有技術債）。

**坑 2：`Lineout` 音量預設為 0%，喇叭完全沒聲音。**

```bash
amixer -c 0 sset Lineout 12   # 範圍 0-18，12 ≈ 67% ≈ +2.00dB
```

### 持久化（決賽當天現場重開機的保命項）

mixer 設定已存檔：

```bash
alsactl store          # → /var/lib/alsa/asound.state
```

已確認 `alsa-restore.service` 為 `WantedBy=sound.target`，且 `sound.target` 為 active，
**開機會自動還原，不需額外接線**。

> ⚠️ **未經重開機實測驗證。** 決賽前應安排一次完整重開機，確認
> `Lineout` 音量與錄音仍正常——這是唯一能真正證明持久化有效的方式。

### 順帶：card 0 的類比輸入路由

排查過程中曾把 `UL0_CH1 ADDA_UL_CH1`（numid=15）與 `UL0_CH2 ADDA_UL_CH1`（numid=25）
由 `off` 改為 `on`，並已隨 `alsactl store` 一併存檔。
此變更對 USB 麥克風路徑無影響，保留無害；若未來要改用 3.5mm 麥克風會需要它。
`Headset Mic Jack` 偵測仍為 `off`，符合「沒有插 3.5mm 麥克風」的實際狀況。
