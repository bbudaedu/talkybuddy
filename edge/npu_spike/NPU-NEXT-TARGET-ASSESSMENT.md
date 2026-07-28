# NPU 下一個標的評估：SLM 上 NPU 可行嗎？

> 2026-07-28。起因：使用者詢問「找小模型 SLM 給 NPU 跑」是否可行
> （引子為 Tom's Hardware 報導 28.9M 參數模型跑在 $10 ESP32-S3 上）。
>
> 本檔在裝置離線狀態下完成，所有結論來自 **既有真機實測** 與 **本機模型算子普查**，
> 無任何新的真機量測。需要真機驗證的項目已明確標示。

---

## 1. 結論先講：「找小模型」這個方向本身有誤

**MDLA 卡的不是模型大小，是兩件與大小無關的事：**

1. **`BatchMatMul` 被拒收。** 這是 transformer attention 的核心運算
   （Q@Kᵀ 與 scores@V —— 兩個運算元都是 activation，因此映射為 `BATCH_MATMUL`
   而非 `FULLY_CONNECTED`）。
2. **動態形狀。** 自迴歸解碼的 KV cache 每產一個 token 就長一次，MDLA 要求固定形狀。

**一顆 28.9M 參數的 transformer 會以與 1.5B 完全相同的方式失敗。**
把模型換小不會讓上面任何一條消失。

### 支撐證據（皆為既有實測，非推論）

| 證據 | 內容 | 出處 |
|---|---|---|
| `toy_matmul` target report | EDMA／GPU／MDLA **三者皆 unsupported** | `REOPEN-OP-TRIAGE.md` §對 NPU-02 的直接後果 |
| SenseVoice PreOpCheck | `BATCH_MATMUL` **×140** 被拒收 | `ADR-npu-path.md` §9.1、`PREOPCHECK-SENSEVOICE-FLOAT-RAW.txt` |
| MediaTek 官方 | `NEURON_FLAG_USE_FP16` **強制**，「the NPU does not support FP32 execution」 | `ADR-npu-path.md` §8.4 |
| GAI Toolkit 存在本身 | MediaTek 把 LLM 部署鎖在 **NDA-gated** 的獨立工具鏈，公開 NeuroPilot 層不提供 | `ADR-npu-path.md` §8、`HANDOFF-2026-07-27.md` |

> 最後一項是最強的旁證：**如果公開層跑得動 LLM，就不需要另做一套 NDA 工具鏈。**

### 順帶：ESP32-S3 那個專案本身不適用

作者 README 原文：**"It will not answer questions, follow instructions, write code, or know facts."**
訓練資料為 TinyStories（英文、3–4 歲詞彙）。我們的教學迴圈整個建立在指令遵循上
（固定回覆格式、鷹架、三層 guardrails），且需要繁中。另 ESP32-S3 為樂鑫（上海）晶片，
非國產晶片，換平台會失去 Phase 8 已取得的加分。

---

## 2. 真正的分項延遲：TTS 比想像中重要得多

`實測`（Phase 8，`edge/EDGE_TURN_LOOP_VALIDATION.md:57`，冷啟動回合）：

```
latency_ms: {'asr': 405, 'llm': 4170, 'tts_first': 1209, 'round_total': 5852}
```

`推導`（穩態回合 2,960ms，以 asr／tts 不受 KV-cache 暖機影響為前提反推 LLM）：

| 階段 | 冷啟動 | 佔比 | 穩態（推導） | 佔比 |
|---|---|---|---|---|
| ASR | 405 ms | 6.9% | 405 ms | **13.7%** |
| LLM | 4,170 ms | 71.3% | ~1,346 ms | **45.5%** |
| TTS（首段） | 1,209 ms | 20.7% | 1,209 ms | **40.8%** |
| 合計 | 5,852 ms | | ~2,960 ms | |

> **TTS 佔穩態回合約 41%** —— 這遠高於先前的印象。
> 先前討論 NPU 時一直聚焦 ASR（實際只佔 13.7%）與 LLM（NDA 擋住），
> **TTS 才是既未被否證、佔比又最大的標的。**
>
> ⚠️ 穩態欄為推導值。`tts_first` 是否在穩態回合維持 1,209ms **未經獨立量測**，
> 動手前應先補一次分項量測確認，不要直接照這張表投入工程。

---

## 3. 逐元件 NPU 可行性

| 元件 | 架構 | 判定 | 依據 |
|---|---|---|---|
| **ASR** SenseVoice | transformer encoder | ❌ **已兩次否證** | ORT-NeuronEP session 崩潰；TFLite 路徑 140 `BATCH_MATMUL` 被拒 |
| **LLM** Qwen2.5-1.5B | decoder transformer + 動態 KV | ❌ **結構性不可行** | 同上算子問題 + 動態形狀；公開層無 LLM 路徑 |
| **TTS** Piper/VITS | Conv 為主，但含動態控制流 | ⚠️ **需要圖手術** | 見 §3.1 |
| **KWS** 喚醒詞 | CNN | ✅ **最有機會** | 未取得模型測試；`ConvStackPlusUnsupported`／`BigConvStack` 探針 PASS 佐證 |

### 3.1 Piper TTS 算子普查（2026-07-28 本機執行，`models/zh_CN-huayan-medium.onnx`）

總節點 2,755，opset 15。

**對 NPU 有利的部分：**

```
129  Conv          3  ConvTranspose      16  LeakyRelu
 17  Tanh         16  Sigmoid             6  Relu
```

**擋路的部分：**

```
 12  NonZero        ← 輸出形狀取決於資料內容，形狀無法靜態化
 30  ScatterND      15  GatherND       21  GatherElements
 46  ConstantOfShape  39  Range        90  Expand      144  Shape
  2  RandomNormalLike ← 圖內隨機取樣（stochastic duration predictor）
 26  MatMul         12  Softmax        ← text encoder 的 attention
```

**圖輸入／輸出全為動態：**

```
input        ['batch_size', 'phonemes']
input_lengths['batch_size']
output       ['batch_size', 'time', 1, 'Unsqueezeoutput_dim_3']
```

**判定：整張 VITS 圖不可能直接上 MDLA。** 但架構上可切：
VITS = text encoder（transformer）+ duration predictor（含隨機取樣）+ flow
+ **vocoder decoder（純 Conv/ConvTranspose）**。
**vocoder 是計算最重的部分，且只有 Conv 系算子。**

把 vocoder 單獨切出來上 NPU 是**理論可行**的，但需要：

1. `onnx.utils.extract_model` 切出 vocoder 子圖
2. 固定時間維度 T（例如釘 T=200，約對應 2.3 秒音訊），輸入補 padding、輸出裁切
3. 轉 FP16（MDLA 強制）
4. 以昨天跑通的 `mtk_converter` 流程轉 TFLite
5. PreOpCheck 確認整圖被接受
6. **把 piper 的單次 ONNX 推論改成兩段式**（前段 CPU、vocoder 段 NPU）— 這步最麻煩

---

## 4. 具體方案（依投報比排序）

### 方案 1 ⭐ GPU Vulkan 打 LLM —— **不是 NPU，但投報比最高**

- **打中**穩態 45.5% 的瓶頸，**而且是唯一能同時改善冷啟動 5.85s 的路徑**
- **吃現有 GGUF，不需任何模型轉檔**、不需 NDA
- 硬體已確認存在：`/dev/mali0` → Mali-G57 2 cores；`vulkaninfo` apiVersion 1.3.274
  （`server/config.py:144` 註解寫「無 GPU」是錯的）
- **成本**：重編 llama.cpp 帶 Vulkan backend + 一次真機 `llama-bench`，約半天
- **風險**：Mali-G57 僅 2 核、與 CPU 共用記憶體頻寬，**實測可能不比 6 執行緒 CPU 快**。
  但這個風險只要半天就能證實或證偽，不需要先投入轉檔工程。

### 方案 2 TTS vocoder 上 MDLA —— NPU 路線的最佳標的

- **打中**穩態 40.8%
- Conv 為主，且 `mtk_converter` 流程昨天剛跑通、mobilenet_v2 已實測 12.9×
- **成本**：1–2 天，含 §3.1 的六個步驟，第 6 步（兩段式推論接線）有實質風險
- **前置**：先補一次穩態分項量測，確認 `tts_first` 在穩態確實仍是 ~1,209ms

### 方案 3 KWS 上 NPU —— 最便宜的「產品裡真的有 NPU」證明

- 純 CNN，最可能整圖 PASS
- **但 KWS 本來就便宜**，省不到有感的時間
- 價值在**展示**而非效能：可以誠實地說「喚醒詞辨識跑在國產晶片的 NPU 上」
- **成本**：半天（需先取得 sherpa-onnx KWS 模型，目前 repo 內沒有）

### 方案 4 SLM 上 NPU —— ❌ 不可行，不要投入

理由見 §1。**換小模型不會讓 `BatchMatMul` 與動態形狀消失。**

---

## 5. 今天工作坊（2026-07-28，明志科大）應加問的一題

現有 `edge/WORKSHOP_QUESTIONS_2026-07-28.md` 已涵蓋 Q2（MDLA 算子表）
與 Q4（不需 NDA 的 LLM-on-NPU 路徑）。建議補：

> **Q7：VITS／HiFi-GAN 這類 vocoder 上 MDLA 有實例嗎？**
> 我們的 TTS 佔穩態回合約 41%，vocoder 部分是純 Conv/ConvTranspose，
> 但輸出長度本質上是動態的（取決於 duration predictor）。
> 請問建議的做法是固定最大長度 + padding/trim，還是有其他處理動態長度輸出的方式？
> 有沒有可參考的 MDLA vocoder 範例？

這一題的答案直接決定方案 2 是「1–2 天」還是「不用做」。

---

## 6. 建議

**決賽前（剩 4 天）：方案 1、2、3 都不要動。**

理由不是技術，是投報：命題文件的國產晶片加分**至多 2 分**，
條件為「近端原型演示」，**Phase 8 已達成、分數已入袋**
（見 `.planning/HANDOFF-2026-07-27.md`）。NPU/GPU 加速不再增加任何分數，
而 Phase 8 那組 2.96–2.99s／2,723MB 的真機數字是逐項量出來的，
任何一項改動都會讓它們全部作廢重測。

**若仍要動**（使用者判斷優先於此建議），順序應為：

1. 先補**穩態分項量測**（確認 TTS 真的佔 41%）— 半小時，零風險
2. 帶 Q7 去工作坊 — 零成本
3. 方案 1（GPU Vulkan）— 半天可證實或證偽，且不動模型
4. 方案 2 才考慮

**不需要裝置就能先做的**：§3.1 的第 1–5 步（切子圖、固定形狀、轉檔、
本機驗證轉檔成功）全部可在開發機完成，只有 PreOpCheck 與最終接線需要真機。

---

## 7. 本檔的限制

- **穩態分項延遲為推導值**，非實測（§2 已標明）
- **KWS 模型未取得**，方案 3 的可行性未經算子普查驗證
- **未做任何新的真機量測**（裝置自 2026-07-27 下午起失聯，`ping` 100% 掉包）
- Piper 算子普查針對 `zh_CN-huayan-medium.onnx`；英文模型
  `en_US-lessac-medium.onnx` 未查，但同為 Piper medium，預期結構相同
