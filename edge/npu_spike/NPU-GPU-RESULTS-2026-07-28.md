# 方案 1／2／3 實測結果（2026-07-28，裝置 `root@192.168.31.78`）

> 承 `NPU-NEXT-TARGET-ASSESSMENT.md` §4 的四個方案。本檔記錄當日真機實測結果。
> 一句話總結：**方案 1（GPU）經一次撤回與重測後確認 NO-GO（真實差距 6.5×，非首次宣稱的 28.9×）；
> 方案 2（TTS vocoder 上 NPU）成功 8.0×；方案 3（KWS）發現架構前提有誤，需重新規劃。**

---

## 方案 1：GPU Vulkan 打 LLM —— ❌ NO-GO（**經公平比較後確認**）

> **本節經歷一次結論撤回與重測。** 首次量測的 build 缺少整數點積 shader 路徑，
> 導致差距被灌水為 28.9×。修正後重測，**真實差距為 6.5×（prefill）／3.7×（decode）——
> 方向不變但幅度更正，NO-GO 結論成立。** 完整經過見本節後半，保留以供追溯。

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

### 首次實測（**build 有缺陷，數字已作廢，保留供追溯**）

`llama-bench -m qwen2.5-1.5b-instruct-q4_k_m.gguf -p 128 -n 32 -r 2 -ngl 99`

| 測試 | Mali-G57 (Vulkan) | CPU 基線（Phase 8） | 倍率 |
|---|---|---|---|
| prompt | 1.35 ± 0.00 tok/s | 39.06 tok/s | 慢 28.9× |
| generation | 3.37 ± 0.01 tok/s | 12.35 tok/s | 慢 3.7× |

> ⚠️ **此表的 28.9× 已由重測推翻**，因該 build 缺整數點積路徑。正確數字見下方「重測完成」。

### 更正：`int dot: 0` 是**我的 build 造成的，不是硬體限制**

初次分析把慢速歸因於能力字串的 `int dot: 0` 與 `matrix cores: none`。
**前者是錯的。** 向裝置查詢實際能力：

```
integerDotProduct8BitUnsignedAccelerated         = true
integerDotProduct8BitSignedAccelerated           = true
integerDotProduct4x8BitPackedUnsignedAccelerated = true
integerDotProduct4x8BitPackedSignedAccelerated   = true
shaderIntegerDotProduct                          = true
VK_KHR_shader_integer_dot_product : extension revision 1
```

**硬體完全支援，且為硬體加速。** ggml 印出 `int dot: 0` 是因為建置時的
`glslc`（Ubuntu noble 的 shaderc 2023.8）編不出該 shader：

```
integer_dot.comp:3: error: '#extension' : extension not supported: GL_EXT_integer_dot_product
```

configure 階段其實印過 `-- GL_EXT_integer_dot_product not supported by glslc`，
但當時未意識到其嚴重性。

**這使該次量測成為不公平比較**：CPU build 帶 `-march=armv8.2-a+dotprod`
（正是為了拿 ARM SDOT 整數點積指令），GPU build 卻被迫走「逐一解包 4-bit 權重
再轉浮點」的慢路徑。**等於讓一邊用專用指令、另一邊用通用 ALU，然後宣布後者不行。**

### 佐證：prefill／decode 的不對稱本身就指向這個原因

| | 倍率 | 性質 |
|---|---|---|
| prompt（prefill） | 慢 28.9× | **計算受限**——反量化 ALU 成本主導 |
| generation（decode） | 慢 3.7× | **記憶體受限**——`uma: 1` 下兩者共用同一條 LPDDR，頻寬本就打平 |

計算受限的那一項慘烈得多，正說明瓶頸在反量化路徑——**而整數點積要修的就是它**。

### 仍然成立的部分

- **`matrix cores: none` 是真的。** 已查證裝置**不存在** `VK_KHR_cooperative_matrix`
  擴充，Mali-G57 確實沒有類 tensor-core 的矩陣單元，此項無法補救。
- `uma: 1`（無獨立 VRAM）、僅 2 核、warp size 16、shared memory 32KB 皆為事實。
  llama.cpp 的 Vulkan shader 多針對 warp 32/64 調校，16 會較不理想；
  32KB shared memory 也限制 tiled matmul 的 tile 大小。

### 重測狀態

已自原始碼建置新 `glslc`（**shaderc v2026.4-dev**，對比原本的 2023.8），
確認可編譯 `integer_dot.comp`，並以 `-DVulkan_GLSLC_EXECUTABLE` 指向它重建
（configure 印出 `-- GL_EXT_integer_dot_product supported by glslc`）。
產物位於 `third_party/llama.cpp/build-vk2-aarch64/`。

**⛔ 尚未取得重測數字——裝置於 2026-07-28 稍晚再次失聯（ping 100% 掉包），
rsync 推送失敗。** 待裝置恢復後應跑三組同基準對照：

1. Vulkan **有** integer dot product（新 build）
2. Vulkan 無 int dot（舊 build，已有：1.35／3.37）
3. CPU `-ngl 0`（**同一顆 binary**——先前的 39.06／12.35 來自 Phase 8 的
   不同 llama.cpp 版本，嚴格說不構成同基準比較）

### ✅ 重測完成（2026-07-28 稍晚，公平比較）

新 build 的能力字串確認快速路徑已啟用：**`int dot: 1`**（前次為 `0`）。

四組數字，全部同一台裝置、同一顆模型、同一組參數（`-p 128 -n 32 -r 2`，6 執行緒）：

| 建置／設定 | pp128（prefill） | tg32（decode） |
|---|---|---|
| **CPU-only build（現行 production，`edge/deploy/bin/`）** | **38.68 ± 0.01** | **13.02 ± 0.01** |
| Vulkan build，全 offload `-ngl 99`（**有** int dot） | 5.93 ± 0.00 | 3.53 ± 0.00 |
| Vulkan build，全 offload（**無** int dot，前次） | 1.35 ± 0.00 | 3.37 ± 0.01 |
| Vulkan build，CPU 路徑 `-ngl 0` | 5.73 ± 0.00 | 10.16 ± 0.00 |

補充：原 CPU-only build `-p 512` 為 37.11 tok/s，與 pp128 的 38.68 相近，
且與 Phase 8 記錄的 39.06 吻合——**證明 Phase 8 那組基線數字無誤**，
先前對「不同版本不可比」的疑慮可以排除。

### 結論一：整數點積確實是主因，但不足以翻盤

啟用 int dot 讓 GPU prefill 從 1.35 → **5.93，提升 4.4×**——證實了
「瓶頸在反量化路徑」的診斷。但對照現行 CPU build：

- **prefill：CPU 快 6.5×**（38.68 vs 5.93）
- **decode：CPU 快 3.7×**（13.02 vs 3.53）

**先前宣稱的 28.9× 差距是被我的 build 缺陷灌水的；真實差距是 6.5×。
方向不變，但幅度必須更正。**

剩餘差距的原因是無法補救的那些：`matrix cores: none`（裝置無
`VK_KHR_cooperative_matrix`）、僅 2 個 shader core、`uma: 1` 無獨立記憶體頻寬、
warp size 16（llama.cpp shader 多針對 32/64 調校）、shared memory 僅 32KB。

### 結論二（意外發現，且有實務影響）：開啟 Vulkan 會拖垮 CPU 路徑

Vulkan build 即使指定 `-ngl 0`，prefill 也只有 **5.73**，
相對 CPU-only build 的 38.68 **慢 6.8×**。

**這代表不能採用「編一個 Vulkan build、GPU 不划算時退回 CPU」的策略**——
那會讓產品的 CPU 路徑直接慢 6.8 倍。若未來要用 GPU，必須是**兩個獨立 binary**，
而非單一 build 切換。

（未深究機制。可能是 `-ngl 0` 仍有部分張量落在 Vulkan 後端，或排程器切分不良。
本輪不追此問題，因為結論一已使該路徑無實用價值。）

**GPU_PATH_DECISION: NO-GO（公平比較後仍慢 6.5×／3.7×；且啟用 Vulkan 會使
CPU 路徑慢 6.8×，無法作為可切換的加值選項）**

原始能力字串佐證（新 build）：

```
ggml_vulkan: 0 = Mali-G57 (Mali-G57) | uma: 1 | fp16: 1 | bf16: 0 | fp4: 0
             | warp size: 16 | shared memory: 32768 | int dot: 1 | matrix cores: none
```

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

## 附帶發現：音訊硬體組態（已於同日解決）

使用者接上音訊硬體後實測，過程中發現先前的假設有誤：

- **麥克風是 USB 不是 3.5mm**（`Jieli Technology K`，`ID 4c4a:4155`，列舉為 `card 1`）。
  最初對 card 0 類比輸入的整輪排查方向錯誤。
- **該 USB 麥克風只支援 48kHz**，而 edge pipeline 要 16kHz mono 且刻意不裝 ffmpeg。
  解法為 `plughw:1,0`（ALSA 層重採樣），非 `hw:1,0`。
- **`Lineout` 音量預設 0%**，喇叭完全沒聲音。
- mixer 設定已 `alsactl store` 持久化（決賽當天現場重開機的保命項）。

完整組態、兩個坑的細節與持久化驗證狀態見 **`edge/NATIVE_KWS_PLAN.md` §5**。
