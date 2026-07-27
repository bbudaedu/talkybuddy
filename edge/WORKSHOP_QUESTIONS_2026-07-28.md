# 帶去工作坊的問題清單（2026-07-28，明志科大）

講師：人工智慧研發部 廖彥欽博士、軟體開發部 謝育琦協理

**我們的環境**：Genio 520 EVK（mt8391）、**IoT Yocto**（非 Android）、ORT 1.20.2、
Python/FastAPI 堆疊。工作坊教材是 Android 路線，範例可能不能直接搬，但以下問題與平台本身有關。

**強烈建議**：把 `edge/npu_spike/DAY1-RAW-OUTPUT.txt`（355KB 完整 ORT verbose log）帶在
筆電裡。給工程師看真實 log，比口頭描述有效十倍。

---

## Q1（最高價值）ORT NeuronEP 在 SenseVoice 上崩潰

**現象**：`Exception during initialization: unordered_map::at`，session 初始化即失敗，
連 graph partition 都到不了。FP32 與 int8 兩版**完全相同**的錯誤。

**已排除**（皆為真機實測，非推測）：
- 量化格式 — FP32 版零量化算子，同樣崩潰
- 12 個形狀/控制流算子單獨成圖 — 無一崩潰，全部乾淨退 CPU
- int64 圖輸入、4 輸入簽章（仿 SenseVoice）— 皆未崩潰
- CPU fallback — 依官方指南加 `--no-cpu-fallback` 重測，仍崩潰
- 圖規模 — 240 節點 Conv 堆疊 PASS

**同一台機器上可用**：`toy_conv`（Conv+Relu）取得整圖 NeuronEP placement + apusys session。

**問**：這個 `unordered_map::at` 是已知問題嗎？有沒有辦法讓 EP 印出是**哪個算子**
查表失敗？（TFLite 路徑有 `PRE_OPERATION_CHECK` 會指名算子，ORT 這邊似乎沒有對應機制）

---

## Q2 MDLA 5.3 支援算子表

線上文件在 `neuropilot-developer.mediatek.com/.../l1_supported_operations/`，
需登入才看得到。想確認三件事：

1. **`BatchMatMul` 是完全不支援，還是有條件支援？** 我們實測 rank-3 FP32 被拒
   （target report：EDMA/GPU/MDLA 三者皆 unsupported）。FP16 或特定 rank 會支援嗎？
   → 這直接決定 transformer 類模型（ASR/LLM）能不能上 MDLA
2. **算子的 tensor rank 限制**：我們實測 rank-2 `[1,8]` 的 Softmax 被拒，
   但 rank-4 `[1,8,16,16]` 的 Softmax 被接受。文件有明訂 rank 限制嗎？
3. 有沒有**單一子圖的算子數量上限**？

---

## Q3 GAI Toolkit 的取得管道（不是要檔案，是問流程）

文件寫明「requires a non-disclosure agreement (NDA) with MediaTek」。

**問**：對**競賽／學術用途**有沒有申請路徑？流程與大約時程？

背景可說明：參加國產晶片競賽，用 Genio 520 做繁中兒童語音學伴。官方 benchmark 顯示
**Qwen2.5-1.5B-Instruct（我們正在跑的同一顆模型）在 G520 上 prompt 269.87 tok/s**，
而我們 CPU 實測只有 39.06 tok/s——差 6.9 倍。這正是我們最想驗證的能力。

**不要**請講師私下複製一份。那是要他違反自己的 NDA，且來源無法在決賽上公開說明。

---

## Q4 有沒有不需 NDA 的 LLM-on-NPU 路徑？

已知 TFLite + Neuron Stable Delegate 是 NDA-free 且**確實可用**
（我們實測 mobilenet_v2：CPU 34.6ms → NPU 2.68ms，12.9×）。

**問**：LLM（GGUF/transformer）有沒有辦法走這條公開路徑？還是一定要 GAI Toolkit？

---

## Q5 mtk_converter 的量化校準

我們已用 `mtk_converter` 8.13.0（NP8 public）成功把 SenseVoice ONNX 轉成 TFLite
（float，937MB，16.8 秒）。但裝置只有 3.7GB、既有引擎已佔 2723MB，**必須量化**。

**問**：語音模型（fbank 特徵輸入，非影像）的 `calibration_data_gen` 該怎麼準備？
有沒有非視覺類的範例？`decompose_batched_matmul_ops` 這個選項能不能讓
MDLA 吃下原本被拒的 BatchMatMul？

---

## Q6（若有時間）`logits` 的 shape 不一致

固定 shape 為 `x=[1,200,560]` 後，ORT 常數摺疊時警告：

```
'logits' source:{1,204,25055} target:{1,200,25055}. Falling back to lenient merge.
```

宣告 200、實際 204（推測是 encoder 內部 padding）。**問**：這種宣告與實際不一致，
在 NPU 編譯時會不會造成問題？
