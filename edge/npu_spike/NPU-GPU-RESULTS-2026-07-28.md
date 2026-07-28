# 方案 1／2／3 實測結果（2026-07-28，裝置 `root@192.168.31.78`）

> 承 `NPU-NEXT-TARGET-ASSESSMENT.md` §4 的四個方案。本檔記錄當日真機實測結果。
> 一句話總結：**方案 1（GPU）已否證、方案 2（TTS vocoder 上 NPU）成功 8.0×、
> 方案 3（KWS）發現架構前提有誤，需重新規劃。**

---

## 方案 1：GPU Vulkan 打 LLM —— ❌ 已否證

### 建置（開發機，全部可重現）

板上原有 binary 沒編進 GPU 後端，需重編。三個前置件皆自行取得，
**刻意不用 `dpkg --add-architecture arm64`**，避免永久改動開發機 apt 設定：

| 前置件 | 取得方式 |
|---|---|
| `glslc`（host shader 編譯器） | `apt install glslc`（shaderc 2023.8） |
| Vulkan headers | Khronos `Vulkan-Headers` v1.3.275，安裝到本地 sysroot |
| **aarch64 `libvulkan.so`** | Khronos `Vulkan-Loader` v1.3.275 自行交叉編譯 |
| SPIRV-Headers | Khronos repo，安裝到同一 sysroot |

版本刻意對齊裝置：裝置端為 `/usr/lib/libvulkan.so.1.3.275`。

```bash
cmake -B build-vulkan-aarch64 -S . \
  -DCMAKE_SYSTEM_NAME=Linux -DCMAKE_SYSTEM_PROCESSOR=aarch64 \
  -DCMAKE_C_COMPILER=aarch64-linux-gnu-gcc -DCMAKE_CXX_COMPILER=aarch64-linux-gnu-g++ \
  -DGGML_NATIVE=OFF -DCMAKE_C_FLAGS="-march=armv8.2-a+dotprod" \
  -DCMAKE_CXX_FLAGS="-march=armv8.2-a+dotprod" -DGGML_OPENMP=OFF \
  -DCMAKE_BUILD_RPATH='$ORIGIN' -DGGML_VULKAN=ON \
  -DVulkan_INCLUDE_DIR="$SYSROOT/include" -DVulkan_LIBRARY="$SYSROOT/lib/libvulkan.so" \
  -DVulkan_GLSLC_EXECUTABLE=/usr/bin/glslc -DCMAKE_PREFIX_PATH="$SYSROOT"
```

`-march` 沿用 D-02 修正版（**只有 `+dotprod`，絕不加 `+i8mm`**，見 `DEPLOY_EDGE.md` §4a）。
llama.cpp 自動偵測 cross-compile 並以 host toolchain 另建 `vulkan-shaders-gen`，無需手動處理。
產物推到裝置 **`/root/talkybuddy/edge/deploy/bin-vulkan/`**，
**刻意與現用的 CPU 部署 `bin/` 分開，不動既有可用堆疊。**

### GPU 被正確辨識

```
ggml_vulkan: Found 1 Vulkan devices:
ggml_vulkan: 0 = Mali-G57 (Mali-G57) | uma: 1 | fp16: 1 | bf16: 0 | fp4: 0
             | warp size: 16 | shared memory: 32768 | int dot: 0 | matrix cores: none
Available devices:
  Vulkan0: Mali-G57 (3794 MiB, 3794 MiB free)
```

`server/config.py:144` 註解「Genio 520 弱腦、無 GPU」確實是錯的——GPU 存在且 Vulkan 可用。

### 實測：GPU 遠慢於 CPU

`llama-bench -m qwen2.5-1.5b-instruct-q4_k_m.gguf -p 128 -n 32 -r 2 -ngl 99`

| 測試 | Mali-G57 (Vulkan) | CPU 基線（Phase 8） | 倍率 |
|---|---|---|---|
| prompt | **1.35 ± 0.00 tok/s** | 39.06 tok/s | **慢 28.9×** |
| generation | **3.37 ± 0.01 tok/s** | 12.35 tok/s | **慢 3.7×** |

### 為什麼慢——原因寫在能力字串裡

- **`int dot: 0`** —— 無整數點積加速。q4_K_M 量化推論高度仰賴此項。
- **`matrix cores: none`** —— 無矩陣運算單元。
- **`uma: 1`** —— 與 CPU 共用系統記憶體，頻寬互搶。
- Mali-G57 **僅 2 核**，warp size 16、shared memory 32KB。

> ⚠️ **誠實標示**：CPU 欄為 Phase 8 以**不同版本** llama.cpp 量得。
> 未以本次同一顆 binary 補測 `-ngl 0` 的 CPU 對照。
> 但差距達 28.9×／3.7×，遠超過版本差異可解釋的範圍，結論不受影響。

**GPU_PATH_DECISION: NO-GO（Mali-G57 缺 int dot 與 matrix cores，量化推論遠慢於 CPU）**

投入成本約半天，與事前估計一致。這條「唯一未被否證、且不需 NDA」的路徑至此關閉。

---

## 方案 2：Piper TTS vocoder 上 MDLA —— ✅ 成功，8.0×

### 切圖依據

完整 Piper VITS 圖（2,755 節點）不可能上 MDLA：12 `NonZero`、30 `ScatterND`、
2 `RandomNormalLike`、輸入輸出全動態。但依節點名稱前綴分群後，架構邊界清楚：

| 模組 | 節點數 | 性質 |
|---|---|---|
| `dp`（duration predictor） | 1,455 | 含隨機取樣 |
| `enc_p`（text encoder） | 865 | transformer |
| `flow` | 196 | |
| **`dec`（vocoder）** | **67** | **純 CNN** |

`dec` 算子組成：**24 Add / 20 Conv / 16 LeakyRelu / 3 ConvTranspose / 3 Div / 1 Tanh**
——零 MatMul、零 BatchMatMul、零動態形狀算子。邊界乾淨：
單一輸入 `/Mul_7_output_0`、單一輸出 `/dec/Tanh_output_0`。

流程已寫成可重現腳本 **`edge/np8/extract_piper_vocoder.py`**，含兩個撞到才發現的陷阱
（`extract_model` 殘留 `unk__` value_info 會讓 mtk_converter 拋 AssertionError；
清 output shape 不能整段刪除否則 rank 不符）。

釘 T=200 frames → 輸入 `[1,192,200]`、輸出 `[1,1,51200]`（**2.32 秒音訊 @22050Hz**）。
`mtk_converter` 8.13.0 轉出 **6.4 MB** TFLite，115 算子，不到 1 秒。

### PreOpCheck：整圖被完整接收

```
INFO: Explicitly applied STABLE_DELEGATE delegate, and the model graph
      will be completely executed by the delegate.
INFO: Initialized session in 1437.46ms.
INFO: Inference (avg): 30386.6 us
INFO: Memory footprint delta (MB): init=51.2109 overall=51.2109
```

**拒收算子數：0。** 原始輸出：`edge/npu_spike/VOCODER-NPU-PREOPCHECK-RAW.txt`。

### 加速倍率（同一 TFLite、同一工具、同一裝置）

| Backend | Inference (avg) | 對 NPU 倍率 |
|---|---|---|
| CPU 1 thread | 663.8 ms | 21.8× |
| CPU 6 threads（與 llama-server 同設定） | 243.7 ms | **8.0×** |
| **MDLA NPU** | **30.4 ms** | — |

2.32 秒音訊只需 30.4ms 計算 = **76× 即時率**，記憶體僅 +51MB。

**這是本專案第一個跑在真實產品管線元件上的 NPU 加速**（先前只有 mobilenet_v2 的
12.9× 屬合成基準）。

### 尚未完成、不得粉飾

- **未接進產品。** 目前只證明 vocoder 子圖可在 MDLA 上跑並量到延遲。
  要真正省到時間，必須把 piper 的單次 ONNX 推論改成兩段式
  （enc_p+dp+flow 走 CPU → vocoder 走 NPU），**這步尚未做，且是本方案最大風險**。
- **未驗證輸出正確性。** 只量了延遲，沒有比對 NPU 與 CPU 的波形輸出是否一致。
  **接線前必須先做數值比對**，否則可能得到很快但錯誤的音訊。
- **未量測 vocoder 佔 `tts_first` 的實際比例。** 已知 `tts_first`=1209ms
  （Phase 8 冷啟動），但其中多少屬 vocoder 未拆解。
  **省下的時間上限取決於這個比例，目前未知。**
- 固定 T=200（2.32 秒）。實際語句長度不一，需決定 padding／分段策略。
- 未量化（`quantize=False`）。量化需真實 mel 校準資料。

---

## 方案 3：KWS 上 NPU —— ⚠️ 前提有誤，需重新規劃

原評估假設 KWS 跑在裝置端。**實際查證後為誤**：

- Path 1 用 **Porcupine Web**（`web/porcupine-engine.js`，瀏覽器 WASM）
- Path 2 用 **sherpa-onnx KWS Web**（`WAKE_SHERPA_BASE_URL=/static/vendor/sherpa-kws/`）
- **repo 內沒有任何 KWS 模型檔**

因此「把 KWS 放上 NPU」不是模型轉檔問題，而是**先要把喚醒層從瀏覽器搬到裝置原生**。
這反而是個比 NPU 加速更有價值的改動——見 `edge/NATIVE_KWS_PLAN.md`。

---

## 附帶發現：裝置麥克風目前收不到聲音

使用者於 2026-07-28 接上 3.5mm 麥克風與喇叭後實測：

| 擷取裝置 | peak | rms |
|---|---|---|
| `hw:0,2`（UL0） | 0.0006 | 0.00007 |
| `hw:0,3`（UL1） | 0.0000 | 0.00003 |
| `hw:0,4`（UL2） | 0.0000 | 0.00002 |

三個裝置都錄得到檔案（16kHz、48000 samples）但**全是靜音**。

已檢查與嘗試：

- `UL0_CH1 ADDA_UL_CH1`（numid=15）原為 **off**，已 `cset on`（此變更留在裝置上）
- 開啟後重測仍為靜音（peak 0.0006）
- **`Headset Mic Jack`（numid=353）= off** —— 板子偵測不到耳麥插入

**未解，需實體確認**（已試 3 次，不再盲猜）：麥克風插在哪個孔？
是 3-pole 還是 4-pole TRRS？EVK 的 mic 輸入是否走獨立的 line-in 而非 headset jack？
`ADC_L_Mux`/`ADC_R_Mux` 目前皆為 2，需確認該值對應哪個實體輸入。

> Phase 8 的紀錄稱「3.5mm 已驗證可用」。若當時確實可用，
> 差異可能來自重開機後 mixer 設定未持久化——值得優先確認，
> 因為**這直接擋住 Phase 11 真機彩排與原生 KWS**。
