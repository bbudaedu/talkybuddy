# 邊緣 ASR 技術選型研究報告——「說說學伴」

研究範圍：臺灣華語＋中英夾雜、兒童語音、MediaTek Genio 520（8 核 ARM aarch64、NPU ~9-10 TOPS、4GB RAM、Yocto Linux）
研究日期：2026-07
現況基線：自建 PC 原型（FastAPI + faster-whisper + llama.cpp Qwen2.5-1.5B + Piper）

---

## 1. 總覽表（先看結論）

| 候選 | 授權 | ARM/aarch64 | 中英夾雜 | 延遲特性 | 活躍度 | 二開難易 | 結論 |
|---|---|---|---|---|---|---|---|
| **sherpa-onnx + SenseVoice-Small** | Apache-2.0（sherpa-onnx）；SenseVoice 權重 NOASSERTION（近 Apache 精神，商用需自行覆核條款） | 原生支援，含 RK NPU/Axera NPU/嵌入式範例 | 訓練時本就是中英日韓粵混合語料，號稱夾雜辨識強 | 非自回歸，單次前向；RK3588 A76 單執行緒 RTF ≈0.099（4 緒 0.049） | 極活躍，2026-07-03 剛有 push | C API/Python/C++ 綁定齊全，接線少 | **採用（主力）** |
| **sherpa-onnx streaming zipformer 雙語** | Apache-2.0 | 原生支援 | 專門雙語（zh-en）transducer 模型 | 串流、chunk 160-1920ms 可調，端點延遲最低 | 隨 sherpa-onnx 主庫活躍 | 同上框架，換模型即可 | **備選（若需要「邊聽邊出字」體驗再切）** |
| whisper.cpp | MIT | 原生 ARM NEON，量化完整 | 靠 whisper 原生多語能力，非針對夾雜優化 | tiny/base 即時，small 較吃力（Pi 5 等級） | 極活躍（2026-07-01 push，5.1萬星） | 程式碼精簡、C/C++、插樁容易 | 備選（NPU/串流不如 sherpa-onnx 生態完整） |
| faster-whisper (CTranslate2) | MIT | 有 aarch64 wheel（ctranslate2 4.8.0, 2026-06） | 依賴 Whisper 原生能力 | INT8 CPU 相對快，但仍是自回歸 decoder | **趨緩**：faster-whisper 主庫最後 push 2025-11-19（距今約 7.5 個月） | Python 生態最熟悉（團隊現有基線） | 現有基線可留作 fallback，**不建議作為長期主力** |
| FunASR Paraformer-zh | MIT | 官方主打 x86，ARM 需自行驗證/交叉編譯 | 中文為主，夾雜能力不如 SenseVoice 專門設計 | 非流式 ~600ms、流式 ~300ms（官方數字，非 ARM 板實測） | 活躍（2026-06-30 push） | funasr-onnx 可脫離 PyTorch，但 ARM 實務案例少 | 備選（有 sherpa-onnx 路徑更成熟時，優先度較低） |
| MediaTek Breeze-ASR-25 | Apache-2.0 | 基於 whisper-large-v2，2B 參數 | 專門優化臺灣華語＋中英夾雜，官方稱夾雜辨識較 Whisper 提升 56% | 2B 參數，BF16，邊緣 4GB RAM 推理／NPU 部署皆吃緊 | 論文 2025-06，仍在下載使用（近月下載 1.3萬+） | 標準 Whisper 架構，理論上可走 faster-whisper/whisper.cpp，但無官方量化到可上板的小尺寸 | **淘汰於邊緣層；列為雲端助教層／benchmark 標竿候選** |
| MediaTek Breeze-ASR-26 | Apache-2.0 | 同上，2B 參數 | 專攻**台語（閩南語）**，非國語＋英文夾雜場景 | 同上 | 論文 2026-02，新 | 與本案核心場景（國語+英文夾雜）不符 | 不適用，僅供未來若要支援台語時參考 |
| Vosk | Apache-2.0 | 支援，CPU-only、輕量 | 弱，非 Transformer，中文模型較舊 | 極快、極省資源 | 表面看 repo 仍有 push，但核心語言模型/聲學模型數年未更新，準確率明顯落後 Transformer 世代 | 簡單但天花板低 | **淘汰**：準確率不足以支撐兒童教學情境需求 |

---

## 2. 逐項評估

### 2.1 faster-whisper（CTranslate2）small/base INT8 於 aarch64

- **定位**：CTranslate2 是通用 Transformer 推理引擎，faster-whisper 是其上的 Whisper 封裝，團隊目前 PC 原型正在用它。
- **授權**：MIT（[SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper)、[OpenNMT/CTranslate2](https://github.com/OpenNMT/CTranslate2)）。
- **離線/ARM**：CTranslate2 官方支援 x86-64 與 AArch64/ARM64，整合 Intel MKL / oneDNN / OpenBLAS / Ruy / Apple Accelerate 等後端；PyPI 上 ctranslate2 4.8.0 已提供 ARM64 wheel（2026-06 上傳）。INT8 在 CPU 上是最快選項，推論時間可降到原本的約 1/4。（[ctranslate2 · PyPI](https://pypi.org/project/ctranslate2/)、[whisper-ctranslate2 · PyPI](https://pypi.org/project/whisper-ctranslate2/)）
- **中英夾雜**：靠 Whisper 原生多語能力，無專門優化。
- **延遲特性**：INT8 CPU 快，但仍是「encoder + 自回歸 decoder」架構，逐 token 解碼在低核心數 ARM 上有天花板；small 模型在 4 大核以下的 ARM（A55 為主的 Genio 520）預期仍偏慢。
- **活躍度**：⚠️ 主 repo 最後一次 push 為 **2025-11-19**，距今（2026-07）已約 7.5 個月沒有新提交，相對於 whisper.cpp/sherpa-onnx 幾乎每週都有更新，活躍度明顯趨緩，需留意長期維護風險。
- **二開難易**：團隊已熟悉，是目前 PC 原型的一部分，遷移成本為零，但這是「熟悉」而非「最適配邊緣」的理由。
- **結論**：**現有 PC 原型的基線可保留作為雲端層 / CI 對照組**，但不建議作為 Genio 520 邊緣層的長期主力——ARM 端效能與生態成熟度都不如 sherpa-onnx / whisper.cpp。

### 2.2 whisper.cpp——ARM NEON 優化、量化、串流

- **定位**：OpenAI Whisper 的 C/C++ 移植，[ggml-org/whisper.cpp](https://github.com/ggml-org/whisper.cpp)，5.1 萬星，2026-07-01 仍有 push，是目前最活躍的邊緣 Whisper 實作。
- **授權**：MIT。
- **離線/ARM**：NEON SIMD 在 ARM64 build 上預設開啟；支援 GGML 整數量化（q5_0 等）降低記憶體與提升速度。社群報告 Pi 5（四核 A76）上 tiny 可輕鬆超即時、base 約即時（需 `-t 4`）、small 雖可跑但「需要耐心」（[whisper.cpp Real-time transcription討論](https://github.com/ggml-org/whisper.cpp/discussions/166)、[2026 Whisper.cpp vs faster-whisper 比較](https://www.promptquorum.com/power-local-llm/local-whisper-stt-comparison-2026)）。Genio 520 的 2×A78+6×A55 理論上優於純 A76 四核，但仍屬同一效能量級（<2s 單輪目標下 small 有風險）。
- **中英夾雜**：靠 Whisper 原生能力，非專門優化，不如 SenseVoice。
- **延遲特性**：自回歸 decoder，串流靠切窗（sliding window）模擬，非真正低延遲 streaming transducer。
- **活躍度**：非常活躍，量化/後端持續更新。
- **二開難易**：程式碼量精簡（純 C/C++，無深度框架依賴），插樁點清楚（`whisper_full`、callback 可拿中間結果），最容易做 NDK/Yocto 交叉編譯。
- **結論**：**備選**。作為 sherpa-onnx 路徑失敗時的高可靠度 fallback，或用於離線 demo 兜底（tiny/base 模型體積小、幾乎零依賴，適合斷網現場備援）。

### 2.3 sherpa-onnx + SenseVoice-Small（重點候選）

- **定位**：[k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) 是 Next-gen Kaldi 團隊出品的推理框架（純 ONNX Runtime，無 PyTorch 依賴），支援 ASR/TTS/VAD/說話人分離等；[SenseVoice-Small](https://huggingface.co/FunAudioLLM/SenseVoiceSmall) 是阿里 FunAudioLLM 團隊的多語音理解模型（ASR + 語言辨識 + 情緒辨識 + 音訊事件偵測）。
- **授權**：sherpa-onnx 為 Apache-2.0；SenseVoiceSmall 權重卡標示為 NOASSERTION（無明確 license 欄位標記，但模型卡與論文皆以開放模型自居），**商用前建議團隊法務快速覆核模型卡**（風險相對低但不是零，且黑客松展示用途通常無虞）。
- **離線/ARM**：官方 README 明確列出 embedded systems、Android、iOS、HarmonyOS、**Raspberry Pi、RISC-V、RK NPU、Axera NPU、Ascend NPU** 等 12+ 種語言綁定與平台（[GitHub repo](https://github.com/k2-fsa/sherpa-onnx)）。RK3588（同為 A55/A76 系列 ARM SoC，可視為 Genio 520 的效能代理指標）int8 量化模型 RTF：**A76 單執行緒 0.099、4 執行緒 0.049；A55 單執行緒 0.436、4 執行緒 0.175**（[sherpa 官方 pretrained 文件](https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html)）。RTF < 0.5 代表比即時快兩倍以上，即使全跑在 A55 小核也有餘裕；Genio 520 的 2×A78 大核預期比 A76 更快。int8 模型檔案僅 **226-228MB**（float32 為 894MB），對 4GB RAM 預算非常友善。
- **中英夾雜**：訓練語料本身就是中/英/粵/日/韓混合（`sherpa-onnx-sense-voice-zh-en-ja-ko-yue`），FunAudioLLM 論文宣稱識別延遲 <80ms、比 Whisper-Small 快 5 倍以上、比 Whisper-Large 快 15 倍以上（[arXiv 2407.04051](https://arxiv.org/html/2407.04051v1)），且是**非自回歸**架構（一次前向產生全部輸出），天生比 Whisper 系列的自回歸 decoder 更適合低延遲邊緣場景。
- **⚠️ 簡繁問題（核心判斷題之一）**：SenseVoice 模型卡明確說明訓練時「all Chinese characters were converted into the simplified Chinese version」，即**輸出固定為簡體中文**，沒有原生切換到繁體的選項（[HuggingFace README](https://huggingface.co/FunAudioLLM/SenseVoiceSmall)）。這點與 Whisper 系列相同（Whisper 官方 discussion 也確認 Whisper 中文輸出同樣傾向簡體，[openai/whisper#277](https://github.com/openai/whisper/discussions/277)）——**這不是 SenseVoice 獨有的缺點，而是這一代中文 ASR 的共通現象**。
- **活躍度**：sherpa-onnx 2026-07-03（幾乎是查詢當下）仍有 push，13.3k 星、594 open issues（維護中而非棄坑訊號，issue多代表使用者多）；SenseVoice repo 2026-06-29 有 push，8.7k 星。均為目前生態中最活躍的中文 ASR 專案之一。
- **二開難易**：C API / Python / C++ / Android / iOS 綁定齊全，官方就有 RK NPU / Axera NPU 範例可參照改寫給 MediaTek NPU；相較 whisper.cpp 需要自己刻串流邏輯，sherpa-onnx 對「串流 vs 非串流」「VAD 整合」都有現成範例，插樁成本低。
- **結論**：**採用（主力）**。效能與生態成熟度目前是邊緣中英夾雜 ASR 的最佳解，唯一需要工程補的是「簡轉繁」這一層。

### 2.4 sherpa-onnx streaming zipformer 中英雙語模型

- **定位**：`sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20`，transducer 架構，真正的串流（chunk-based）辨識，同樣跑在 sherpa-onnx 框架下（[Zipformer pretrained models](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-transducer/zipformer-transducer-models.html)）。
- **授權**：隨 sherpa-onnx，Apache-2.0。
- **離線/ARM**：與 SenseVoice 共用同一套推理框架與部署路徑，ARM 支援度相同。
- **中英夾雜**：模型本身即針對 zh+en 雙語訓練，且支援多種 chunk size（160/480/960/1920ms）可依延遲需求調整（另有社群模型 [X-ASR-zh-en](https://huggingface.co/GilgameshWind/X-ASR-zh-en) 可參考）。
- **延遲特性**：**串流架構的天生優勢**——可以邊講邊出字，端點延遲可壓到 chunk size 等級（數百毫秒級），比 SenseVoice「等一段音訊再一次前向」更適合需要「即時反饋」的互動設計（例如打斷偵測、半雙工搶話）。
- **活躍度**：隨 sherpa-onnx 主庫活躍。
- **二開難易**：與 SenseVoice 同框架，換模型設定檔即可切換，成本很低。
- **結論**：**備選**，建議先用 SenseVoice-Small 把管線跑通（非串流、實作簡單、準確率高），若之後發現「使用者說完才開始辨識」造成的延遲在真人測試中無法接受，或想做「講話中即時字幕」效果，再切到 streaming zipformer——兩者切換成本低，是同一套框架內的模型替換。

### 2.5 FunASR Paraformer-zh

- **定位**：達摩院/ModelScope 出品，[modelscope/FunASR](https://github.com/modelscope/FunASR)，中文語音辨識的另一條主流路線（Paraformer 為非自回歸架構）。
- **授權**：MIT。
- **離線/ARM**：有 `funasr-onnx`（[PyPI](https://pypi.org/project/funasr-onnx/)）可脫離 PyTorch 依賴；2026-06 開始有 llama.cpp/GGUF runtime 選項可跑 Paraformer（單一 self-contained binary，含內建 FSMN-VAD，無需 Python）。但官方效能數字（600ms 非流式／300ms 流式）是**非 ARM 板實測**，且社群在 aarch64 上的實際部署案例遠少於 sherpa-onnx。
- **中英夾雜**：以中文為主設計，夾雜能力不如 SenseVoice 專門針對多語混合訓練。
- **活躍度**：活躍（2026-06-30 push，1.88 萬星）。
- **二開難易**：中規中矩，但目前生態工具鏈（尤其 ARM 交叉編譯與 NPU 範例）不如 sherpa-onnx 完整。
- **結論**：**備選**，技術上可行但沒有明顯優於 SenseVoice-Small + sherpa-onnx 的理由，除非未來遇到 SenseVoice 在特定語料上表現不佳，才值得投入時間驗證。

### 2.6 MediaTek Breeze-ASR-25 / 26

- **定位**：MediaTek Research 出品，基於 Whisper-large-v2 微調，[Breeze-ASR-25](https://huggingface.co/MediaTek-Research/Breeze-ASR-25) 專攻臺灣華語＋中英夾雜，官方稱準確率較原生 Whisper 提升近 10%、夾雜場景提升 56%（[DIGITIMES 報導](https://www.digitimes.com/news/a20250701PD240/mediatek-ai-language-model-openai-taiwan.html)）；[Breeze-ASR-26](https://huggingface.co/MediaTek-Research/Breeze-ASR-26) 則是 2026-02 新發布、專攻**台語（閩南語）**版本。
- **授權**：兩者皆 Apache-2.0。
- **離線/ARM**：**核心問題——參數量 2B（BF16）**，屬於 Whisper-large-v2 等級規模，模型卡未提供官方邊緣量化/蒸餾版本。在 4GB RAM（扣除 OS/runtime 後約 2.5-3GB 可用、且要同時容納 1.5B LLM）的預算下，同時塞入一個 2B 的 ASR 模型幾乎不可行；即使做到 int4 量化（~1GB+），加上 LLM 與 TTS 模型仍會嚴重擠壓記憶體，且 2B whisper-large-v2 架構的自回歸 decoder 在 ARM CPU 上推論速度也遠不如 SenseVoice-Small 這類專為效率設計的小模型。
- **中英夾雜**：**論文/官方數字上是本清單中「臺灣華語＋夾雜」專門優化最強的模型**，若不受邊緣資源限制，準確率理論上優於 SenseVoice-Small。
- **活躍度**：論文 2025-06（ASR-25）／2026-02（ASR-26），近月下載量 1.3 萬+，屬於仍在被使用的模型，非棄坑專案。
- **二開難易**：標準 Whisper 架構，理論上可用 faster-whisper / whisper.cpp 載入，但缺乏官方小尺寸/量化版本，需自行蒸餾或大幅量化，28 天內風險過高。
- **結論**：**於邊緣即時層淘汰**（尺寸不符 4GB RAM 預算與延遲目標）；建議轉列為：
  1. **雲端助教層**的候選（AWS 上跑 2B 模型完全沒問題，可用於深度診斷/離線報表的高精度轉寫）；
  2. **離線 WER 標竿（benchmark oracle）**——用它產生的轉寫結果，反過來評估 SenseVoice-Small 在臺灣華語＋夾雜語料上的準確率差距，是很好的品質對照組。

### 2.7 Vosk（淘汰確認）

- **定位**：[alphacep/vosk-api](https://github.com/alphacep/vosk-api)，早期主流的輕量離線 ASR 工具包（Kaldi 系）。
- **授權**：Apache-2.0。
- **離線/ARM**：CPU-only、模型極小、資源需求低，這點在純嵌入式（如 MCU 等級）場景仍有優勢。
- **中英夾雜**：**弱**——非 Transformer 架構，中文模型陳舊，混合語碼場景表現明顯落後新一代模型。
- **延遲特性**：極快極省資源，但這是用準確率換來的。
- **活躍度**：repo 表面上仍有 commit push（2026-07-02），但這類 push 多為周邊工具/文件維護，**核心聲學模型與語言模型數年未有實質更新**，社群評測普遍認為「在有 GPU 或能跑 Whisper 系列時，Whisper/新一代模型準確率明顯更好」（[Vosk vs Whisper 2026 評測](https://www.sinologic.net/en/2026-05/vosk-vs-whisper-local-the-ultimate-2026-guide-to-self-hosted-speech-recognition-stt.html)）。
- **結論**：**淘汰**。兒童語音本身辨識難度就高於成人（發音不穩定、語速快慢不一），Vosk 的準確率天花板明顯不足以支撐教學鷹架場景中「聽懂學生說什麼」這個核心需求，資源省下來的空間遠不足以彌補準確率損失。

### 2.8 NPU 路徑：.tflite INT8 經 Neuron Delegate 的可行性

- **官方工具鏈**：MediaTek NeuroPilot 提供 `mtk_pytorch_converter`（PyTorch → TFLite）與 Neuron SDK 的 `ncc-tflite`（TFLite → DLA 私有格式），Genio 510/700 支援線上（Neuron Stable Delegate）與離線（Neuron SDK 編譯 DLA）兩種推論路徑（[Genio 510/700-EVK Yocto 文件](https://mediatek.gitlab.io/aiot/doc/aiot-dev-guide/master/sw/yocto/ml-guide/ml-g700-evk.html)）。
- **⚠️ 已知失敗案例（Whisper）**：MediaTek 官方社群論壇上有使用者實測嘗試把 Whisper 部署到 Genio 510 NPU（MDLA），結果是：
  - **Whisper 的 encoder+decoder 無法轉換成單一 TFLite 檔案，decoder 目前不支援完整 TFLite 轉換**（[NPU Deployment Issue 討論串](https://genio-community.mediatek.com/t/npu-deployment-issue-whisper-model-genio-510/1430)）；
  - 另有使用者回報執行 TFLite Whisper 模型在 NPU 上出現 **apusys 記憶體錯誤**（[Executing TFLite Whisper Model on NPU 討論串](https://genio-community.mediatek.com/t/executing-tflite-whisper-model-on-npu-leads-to-apusys-memory-error/629)）；
  - MediaTek 官方代表明確回覆：MDLA 應用層目前**僅支援 C/C++ 的 NeuroPilot Runtime API，尚未提供穩定的 Python API**，未來才會補上。
  - 討論串中未見任何「成功將完整 Whisper 模型跑在 Genio NPU 上」的案例，反而有 "garbage transcription"（亂碼輸出）等其他問題回報。
- **結構性原因**：Whisper 類自回歸 encoder-decoder 架構對 NPU 編譯器不友善（decoder 逐 token 迴圈、KV cache、動態長度輸出），這是 whisper 系「轉 NPU 難」的根本原因，不是單一 bug。
- **更可行的方向——非自回歸/串流 encoder-only 架構**：SenseVoice-Small（非自回歸單次前向）與 zipformer（transducer，encoder 部分是純前向網路）在結構上遠比 Whisper decoder 更接近「靜態計算圖」，較有機會透過 `onnx2tf` / `onnx2tflite` 做 int8 全整數量化後上板（社群工具鏈已存在，見 [onnx2tflite 討論](https://community.nxp.com/t5/i-MX-Processors/tflite-compatibility-with-NPU/td-p/1982303)），但**截至目前查證，沒有找到「sherpa-onnx 模型成功轉 .tflite 並跑在 MediaTek Neuron Delegate/NPU 上」的公開案例**——這是全新的轉換路徑，需要團隊自己嘗試 encoder 單獨轉換（decoder/joiner 部分可能仍需留在 CPU）。
- **結論**：**28 天內不建議把 NPU 化 ASR 排進關鍵路徑**。NPU 路徑目前有兩層風險：(1) Whisper 系已有官方社群驗證過的失敗案例（decoder 不可轉、記憶體錯誤）；(2) SenseVoice/zipformer 雖架構更友善，但沒有先例可循，屬於「研發性」而非「整合性」工作。建議：ASR 全部先用 **CPU 上 sherpa-onnx（ONNX Runtime）**跑，把 NPU 預算留給 LLM 推論（若時間允許）或列為決賽後續優化項目；若團隊有餘力，可用 3-5 天做「zipformer encoder 轉 .tflite 上 NPU」的 spike，但要設好停損點（不影響 CPU 版本的可用 demo）。

---

## 3. 核心判斷題

### 3.1 SenseVoice-Small vs Whisper-small：兒童＋夾雜場景的準確率/延遲 trade-off

| 面向 | SenseVoice-Small | Whisper-small（含 faster-whisper/whisper.cpp 實作） |
|---|---|---|
| 架構 | 非自回歸，單次前向 | 自回歸 encoder-decoder，逐 token 解碼 |
| 官方延遲宣稱 | <80ms 辨識延遲；比 Whisper-Small 快 5 倍、比 Whisper-Large 快 15 倍（[FunAudioLLM 論文](https://arxiv.org/html/2407.04051v1)） | 需完整跑完 decoder 迴圈，長度越長延遲越高，且在低核心 ARM 上 small 模型「需要耐心」（社群觀察） |
| 中英夾雜訓練 | 訓練語料本就是 zh/en/yue/ja/ko 混合，專門設計給多語/夾雜場景 | 靠 Whisper 原生 99 語言的通用能力，非針對中英夾雜特化 |
| ARM 實測數據 | RK3588 A76 單緒 RTF 0.099（有官方數字） | 社群零散報告（Pi 5 tiny/base 可用、small 吃緊），無官方 ARM RTF 表 |
| 兒童語音 | 無官方兒童語音專門評測數據（本次查證未找到） | 同樣無官方兒童語音專門評測數據 |
| 模型大小（int8） | 226-228MB | small int8 約 250MB 量級（同一數量級） |

**判斷**：兩者官方都沒有針對「兒童語音」的專門評測數字（這是本報告誠實揭露的資訊缺口，建議團隊用自己的兒童語音樣本各跑一輪小規模 A/B 測試，此判斷不能只靠文獻）。但從**架構層面**看，SenseVoice-Small 的非自回歸設計在 Genio 520 這種中低算力 ARM 板上有結構性延遲優勢（不受輸出長度拖累、無 decoder 迴圈開銷），且訓練語料本身涵蓋中英混合，比通用 Whisper 更貼近「中英夾雜」這個核心場景設計目標。在 <2s 單輪、<300ms TTS 首音的嚴苛延遲預算下，SenseVoice-Small 是風險更低的選擇。**建議：以 SenseVoice-Small 為主力實作，同時在 Day 1-3 的 spike 中用同一批测試語音（含兒童錄音）跑 SenseVoice-Small 與 whisper.cpp-small 的並排比較，用實測結果而非文獻確認最終選擇**。

### 3.2 簡體輸出轉繁（OpenCC）是否可接受？

- SenseVoice-Small **與 Whisper 系列一樣**，中文輸出固定為簡體（模型卡明確聲明訓練時做過簡轉繁→簡的正規化），這不是 SenseVoice 獨有的缺點。
- [OpenCC](https://github.com/BYVoid/OpenCC)（含 Python/多語言封裝如 `opencc-py`）是成熟、廣泛使用的簡繁轉換函式庫，提供 `s2t`（簡轉繁）、`s2twp`（簡轉繁＋臺灣慣用詞）等設定檔，字級/詞級轉換規則完整。
- **可接受性判斷**：OpenCC 的簡轉繁在**通用文本**（新聞、書面語）準確率很高，常見風險點在於「一簡對多繁」的歧義字（如「后/後」「里/裡」）與臺灣特有詞彙（「軟體 vs 软件」）。針對本專案：
  - 這是**教學對話場景**，語句短、口語化、詞彙集中在國小課綱範圍，一簡對多繁歧義字出現機率相對低；
  - 建議直接用 `s2twp.json`（簡體→繁體＋臺灣慣用詞）設定檔，而非陽春的 `s2t`，可同時處理詞彙在地化；
  - ASR 輸出的簡體/繁體差異**主要影響「顯示給老師看的逐字稿」與「寫入 SQLite 的歷程記錄」**，並不直接影響 LLM 理解（LLM 對簡繁都能理解）與 TTS 输出（TTS 走自己的繁體語料），所以就算 OpenCC 轉換有極少數字誤判，對核心對話體驗**沒有影響**，只影響書面記錄的美觀度與教師儀表板的可讀性。
- **結論：可接受**。加一層 OpenCC `s2twp` 轉換是低成本、低風險的工程解法（純函式庫呼叫，無需訓練/微調），不構成阻礙採用 SenseVoice-Small 的理由。

---

## 4. 排序推薦表

| 順位 | 方案 | 定位 | 決策理由 |
|---|---|---|---|
| 1 | **sherpa-onnx + SenseVoice-Small（int8）+ OpenCC s2twp** | 邊緣即時層主力 ASR | 生態最活躍、ARM 官方支援最完整、非自回歸架構延遲優勢明顯、訓練語料天生涵蓋中英夾雜、簡繁問題有低成本工程解 |
| 2 | **whisper.cpp（tiny/base，量化）** | 離線兜底 / 高可靠度 fallback | 依賴最少、程式碼最精簡、社群驗證最久，斷網現場 demo 的保險 |
| 3 | **sherpa-onnx streaming zipformer 雙語模型** | 若需要真串流體驗時的升級路徑 | 與方案 1 同框架，可低成本切換；用於解決「等整句講完才辨識」的體感延遲問題 |
| 4 | faster-whisper（現有 PC 原型） | 雲端層/CI 對照組，非邊緣主力 | 團隊熟悉但 ARM 端表現與生態活躍度均不如方案 1/2，且主庫近 7.5 個月無更新 |
| 5 | FunASR Paraformer-zh | 觀察名單 | 技術可行但 ARM 生態不如 sherpa-onnx 成熟，非必要不投入 |
| 6 | Breeze-ASR-25/26 | 雲端助教層 / 品質標竿，不用於邊緣 | 2B 參數超出邊緣 RAM 預算，但適合當雲端高精度轉寫或本地評測 oracle |
| 淘汰 | Vosk | — | 準確率天花板不足以支撐兒童教學鷹架場景 |
| 暫緩 | NPU (.tflite + Neuron Delegate) 化 ASR | — | Whisper 系已有社群驗證的失敗案例（decoder 不可轉、記憶體錯誤、僅支援 C/C++）；SenseVoice/zipformer 轉 NPU 無公開先例，屬研發性工作，不排入關鍵路徑 |

---

## 5. 28 天內的整合路徑

**Day 1-3｜Spike & 決策驗證**
- 在 Genio 520 板子（或最接近的 ARM 開發板）上安裝 sherpa-onnx，跑通 SenseVoice-Small int8 官方 demo。
- 同時跑 whisper.cpp base/small 作為並排比較。
- 用團隊自錄的**兒童中英夾雜語音樣本**（哪怕只有 20-30 句）分別測 SenseVoice-Small 與 whisper.cpp-small 的準確率與端到端延遲，產出實測數字取代文獻推論（呼應 3.1 節的資訊缺口）。
- 決策點：確認 SenseVoice-Small 是否維持第一名；若實測明顯翻盤，切到方案 2（whisper.cpp）不會有架構性風險，因為兩者都是獨立可替換的 ASR 模組。

**Day 4-7｜管線整合**
- 把 sherpa-onnx SenseVoice-Small 接入現有 FastAPI 管線，取代/並存 faster-whisper 路徑（保留 faster-whisper 作 feature flag 可切換的 fallback，降低風險）。
- 接上 OpenCC `s2twp`，在 ASR 輸出後立刻做簡轉繁，寫入 SQLite 前確保是繁體。
- 建立基本的 VAD → ASR 串接的整合測試（含中英夾雜語句）。

**Day 8-14｜延遲打磨與兜底**
- 量測端到端延遲，確認 ASR 環節是否吃掉太多 <2s 單輪預算；若 SenseVoice-Small 的「等講完才辨識」造成體感延遲問題，此時評估切換/並行導入方案 3（streaming zipformer）。
- whisper.cpp tiny/base 作為離線兜底路徑，確保斷網 demo 時有 100% 可運作的備援（即使準確率略降）。
- 針對兒童常見的停頓、語氣詞、重複語句做 edge case 測試，必要時調整 VAD 靜音閾值。

**Day 15-21｜與 LLM/TTS 全鏈路整合**
- ASR → LLM 鷹架生成 → TTS 全鏈路壓測，確認記憶體峰值在 4GB 預算內（尤其 SenseVoice-Small + Qwen2.5-1.5B + Piper 同時載入的實際佔用）。
- 若記憶體或延遲仍有餘裕，可用 2-3 天做「NPU spike」：嘗試把 zipformer encoder 或 SenseVoice encoder 單獨轉 .tflite 上 Neuron Delegate（設停損點，CPU 版本作為保底不可被取代）。

**Day 22-28｜穩定化與 Demo 排練**
- 凍結 ASR 模型與參數（不再換模型），只做參數調優（VAD 閾值、beam/decode 參數、OpenCC 詞庫補充教學情境常用詞）。
- 準備斷網 demo 腳本與兜底話術，確認 whisper.cpp fallback 路徑在完全斷網環境下可正常啟動。
- 收集決賽現場最可能出現的中英夾雜語句樣本做最後一輪回歸測試。
