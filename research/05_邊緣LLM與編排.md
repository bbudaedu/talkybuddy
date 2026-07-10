# 邊緣 LLM 與教學鷹架編排方式研究報告

研究範圍：edge-llm（模型選型 / 推理框架 / 編排層架構判斷）
產品脈絡：說說學伴——MediaTek Genio 520（2×A78+6×A55、~9-10 TOPS NPU、4GB RAM、Yocto Linux，NeuroPilot Public、LLM 走 CPU llama.cpp）、28 天黑客松決賽、現有 PC 原型（FastAPI + faster-whisper + llama.cpp Qwen2.5-1.5B + Piper）
報告日期：2026-07-04

---

## TL;DR（先講結論）

1. **模型**：現有的 Qwen2.5-Taiwan-1.5B-Instruct 基底可以留著當「保底」，但應優先花 1 天做 A/B 實測，讓 **Qwen3-1.7B(-Instruct)** 取代它成為主力——同量級記憶體/延遲下，Qwen3-1.7B 在推理與多語言（119 語言含中文）上明顯優於 Qwen2.5，且官方已提供 GGUF。TAIDE 系列沒有 1–2B 規格（最小 7B/8B），Llama-Breeze2-3B 官方標示需 ~8GB，兩者都超出 4GB 板子預算，**邊緣層淘汰**；Gemma-3n E2B 的「~2GB 記憶體」宣稱是 Google 自家 PLE caching 機制下的數字，llama.cpp 目前對 Gemma-3n 僅支援 text-only 推理且是近期才穩定，**磁碟/實際佔用是否真的比 Qwen 系列小需要實測驗證**，列為觀察備選，不建議壓在 demo 主線上。
2. **推理框架**：**llama.cpp 維持採用**（原型已在用，ARM CPU 優化最成熟、100k+ star、GBNF grammar 可解決結構化輸出問題）。Ollama 只適合開發機除錯（daemon 常駐帶來 10–30% 額外開銷），正式板子上沒有理由多包一層。mlc-llm 的 aarch64 CPU 路徑歷來不穩定且需要 TVM 編譯鏈，28 天內風險過高，淘汰。ExecuTorch 與 Arm/MediaTek 生態系整合度高、值得作為黑客松後把部分負載搬上 NPU 的中期方向，但現在換血成本不划算。
3. **編排層（核心判斷題）**：**邊緣即時層不要用 LangGraph、不要用 PydanticAI 包 LLM 的 tool-calling**。正確策略是「規則式教學鷹架引擎為主幹，LLM 只做潤飾（slot-filling 式短生成）」的混合式架構：規則引擎決定「下一個教學動作是什麼」，LLM 只負責把動作轉成一句自然、雙語的話，用 llama.cpp 的 **GBNF grammar 約束解碼**保證格式合法，並疊加關鍵字/長度黑名單做語意安全檢查。LangGraph 的 checkpoint/狀態持久化開銷（社群已回報疊加即時語音會把延遲從 2–3 秒推到 5–8 秒）留給非即時的**雲端助教層**用（正是它「long-running, stateful agent」的設計初衷）；PydanticAI 也留給雲端層接 Bedrock Claude（大模型 tool-calling 才可靠），因為 1.5–1.7B 模型的原生 function calling 成功率在 58–65% 區間、且一致性差，不適合承擔安全攸關的判斷。

---

## 一、模型評估

### 1. Qwen2.5-1.5B-Instruct（現有基底）/ Qwen2.5-Taiwan-1.5B-Instruct

- **定位**：Alibaba Qwen2.5 系列最小可用等級模型；`benchang1110/Qwen2.5-Taiwan-1.5B-Instruct` 是在此基礎上做「簡轉繁 tokenizer 置換 + LoRA SFT（`lianghsun/tw-instruct-500k`）+ DPO 對齊」的台灣繁中微調版本（[HF 頁面](https://huggingface.co/benchang1110/Qwen2.5-Taiwan-1.5B-Instruct)）。
- **授權**：Apache 2.0（Qwen2.5 系列）。
- **離線/ARM 支援**：官方提供 [GGUF](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF)，llama.cpp 原生支援，已在現有 PC 原型跑通。
- **中英夾雜**：基礎 Qwen2.5 具多語能力，Taiwan 版特別做過繁中 tokenizer 與語料微調，理論上更貼近 zh-TW 用語，但目前**沒有找到公開的量化中英夾雜評測分數**，只能靠團隊自建測試集驗證（見下方 28 天整合路徑 Day 1-3）。
- **延遲特性**：直接測到 Cortex-A78 的資料稀缺；用近似 ARM 平台（Raspberry Pi 5，Cortex-A76 class）的 llama.cpp Q4_K_M 量化基準推算，1.1–1.5B 級模型約 5–15 tok/s（[TinyWeights 實測](https://tinyweights.dev/posts/run-llms-raspberry-pi-5/)、[SBC 學術評測 arXiv:2511.07425](https://arxiv.org/html/2511.07425v1)）；Genio 520 的 A78 單核性能略優於 Pi5 的 A76，估計同量級或略快，但**必須在真實板子上實測**才能對到 <2s 單輪延遲的目標。
- **活躍度**：Qwen2.5 系列本身持續維護；`benchang1110` 個人 fine-tune 倉庫更新頻率較低（個人專案性質），需注意二次 fine-tune 是否有人持續跟進。
- **二開難易**：低——GGUF 直接可用，llama.cpp 呼叫方式與現有原型一致，不需要額外轉換工作。
- **結論**：**採用（保底基底）**。已驗證可跑、風險最低，即使不換模型也能撐住 demo。

### 2. Qwen3-1.7B(-Instruct)

- **定位**：Qwen3 是 2025 年釋出的世代模型，1.7B 是其「稠密邊緣級」規格之一。
- **授權**：Apache 2.0。
- **離線/ARM 支援**：官方直接提供 GGUF（Unsloth 等社群也同步提供量化版，見 [Qwen3.5 Unsloth 文件](https://unsloth.ai/docs/models/qwen3.5)），llama.cpp 原生支援。
- **中英夾雜**：Qwen3-0.6B/1.7B 支援 119 種語言，官方技術報告（[arXiv:2505.09388](https://arxiv.org/pdf/2505.09388)）指出 Qwen3-1.7B-Base 表現接近 Qwen2.5-3B-Base，在 STEM/程式/推理甚至超過更大的 Qwen2.5 模型；翻譯類任務（FLORES+）上也優於 Qwen2.5-1.5B。這代表在同樣的 4GB 記憶體與相近的推論延遲量級下，換成 Qwen3-1.7B 幾乎是「免費」拿到品質提升。**沒有找到針對 zh-TW（繁體）specifically 的評測**，仍需團隊自建中英夾雜測試集驗證是否輸出簡體字混用或用語偏中國大陸腔。
- **延遲特性**：無 Cortex-A78 直接數據，但因參數量（1.7B）與 Qwen2.5-1.5B 相近，預期 token/s 差距在個位數百分比內，記憶體佔用（Q4_K_M 約 1.0–1.2GB）也在同一量級，換模型不會顯著推高延遲或超出 RAM 預算。
- **活躍度**：Qwen 官方持續維護，2025-2026 有多次後續發布（Qwen3.5 等），生態活躍。
- **二開難易**：低——GGUF 生態成熟，可直接替換現有 pipeline 裡的模型檔案做 A/B。
- **結論**：**建議升級為主力候選**。用 1 天做 A/B（token/s、記憶體、20 題繁中/中英夾雜人工評分），若品質/延遲雙贏就切換，若繁中腔調有問題可考慮找/做一版 Taiwan 微調（目前尚未看到現成的 Qwen3-1.7B Taiwan 版本，需另行搜尋或自行 LoRA）。

### 3. Gemma-3n E2B(-it)

- **定位**：Google 2025 年釋出的行動裝置導向多模態模型，E2B 版「原始參數 5B，透過 MatFormer + Per-Layer Embedding (PLE) caching 讓有效運算負載壓到約 2B」（[Google 開發者部落格](https://developers.googleblog.com/en/introducing-gemma-3n-developer-guide/)、[HF blog](https://huggingface.co/blog/gemma3n)）。
- **授權**：Gemma 授權（非 Apache，需注意其使用條款細節，含一些下游限制）。
- **離線/ARM 支援**：有官方/社群 GGUF（[ggml-org/gemma-3n-E2B-it-GGUF](https://huggingface.co/ggml-org/gemma-3n-E2B-it-GGUF)），但**llama.cpp 目前僅支援 text-only 推理**，多模態（圖像/音訊）projector 尚未完整可用；GGUF 格式本身近期也修過 bug（Ollama 版本曾有解析問題）。
- **中英夾雜**：宣稱支援 140 種語言，但未找到 zh-TW 或中英夾雜的具體評測數字。
- **延遲特性/記憶體風險（重要保留意見）**：Google 宣傳的「~2GB 記憶體」是其官方 runtime（如 AI Edge/LiteRT）用 PLE caching 動態換入換出達成的效果；**沒有證據顯示 llama.cpp 的 GGUF 載入路徑實作了同樣的 PLE 換頁機制**——GGUF 通常會把完整張量（對應 5B 原始參數量）map 進記憶體。也就是說在 llama.cpp 上跑，實際磁碟/記憶體佔用很可能遠高於「2GB」宣傳值，接近一個真正 5B 模型的量化後大小，這對 4GB 板子是高風險。**此點需要實測驗證，不能直接採信官方數字。**
- **活躍度**：模型與生態都是 2025 年後的新東西，llama.cpp 支援還在打磨中（近期才修 GGUF bug）。
- **二開難易**：中——GGUF 可跑但生態不如 Qwen 成熟，text-only 限制與潛在記憶體風險增加除錯成本。
- **結論**：**備選/觀察，不建議壓在 28 天 demo 主線**。若時間允許，可安排半天做記憶體實測（`llama-bench` 量測 RSS），若真的能壓進 <1.5GB 且中文品質不輸 Qwen3，值得後續評估；否則優先度低於 Qwen3-1.7B。

### 4. Llama-Breeze2-3B-Instruct（MediaTek 創新中心，台灣繁中）

- **定位**：MediaTek 自家團隊基於 Llama 3.2 3B 微調的繁中/在地知識模型，號稱「適合行動裝置或資源受限情境」（[Medium 介紹](https://medium.com/@simon3458/%E7%B9%81%E4%B8%AD%E6%96%B0%E6%A8%A1%E5%9E%8B-breeze2-2025-%E5%B9%B4%E5%BC%B7%E5%8C%96%E7%B9%81%E9%AB%94%E4%B8%AD%E6%96%87%E7%9A%84%E5%A4%9A%E6%A8%A1%E6%85%8B-llm-%E6%A8%A1%E5%9E%8B-b775135ed85c)）；有社群 GGUF（[mradermacher/Llama-Breeze2-3B-Instruct-Text-GGUF](https://huggingface.co/mradermacher/Llama-Breeze2-3B-Instruct-Text-GGUF)）。
- **離線/ARM**：GGUF 可跑，llama.cpp 支援（基於 Llama 3.2 架構，生態成熟）。
- **中英夾雜/繁中**：專門針對繁中與在地知識優化，理論上是 zh-TW 品質最佳候選之一。
- **記憶體**：3B 參數即使 Q4 量化也落在 ~2GB 上下，官方/社群標示的建議跑法通常抓 8GB 記憶體（如原始題目所述），扣掉 OS + ASR/TTS 模型後，**很可能超出本專案 2.5–3GB 的可用預算**，尤其還要同時載入 faster-whisper 與 Piper TTS。
- **活躍度**：MediaTek 官方背書、2025 年releases，屬於相對新的專案。
- **二開難易**：低（Llama 3.2 架構生態成熟），但受限於記憶體預算。
- **結論**：**邊緣層淘汰**（記憶體超預算風險過高，且與 ASR/TTS 搶記憶體會很緊張）。可列為雲端層或未來硬體升級（若板子換 6-8GB 版本）時的重新評估對象——其「MediaTek 官方 + 繁中優化」的定位其實很貼合本專案調性，值得長期關注。

### 5. TAIDE 系列

- **定位**：國網中心主導的「臺灣可信任生成式 AI」計畫，2025 年 8 月已改以 Gemma 3 為底座重新發布（[TechNews 報導](https://technews.tw/2025/08/27/gemma-3-taide-series-launched/)）。
- **現況規格**：目前公開規格為 Gemma-3-TAIDE-12B-Chat、Llama3.1-TAIDE-LX-8B-Chat、TAIDE-LX-7B/13B 等，**沒有 1B/2B 級的小模型**（搜尋未發現任何官方小型化版本規劃）。
- **結論**：**邊緣層直接淘汰**（規格不合，最小 7-8B 已遠超 4GB 板子可承載範圍）。其訓練語料與「台灣心」安全對齊的方法論，可作為未來若團隊要自己 fine-tune 小模型時的語料/方法參考，但不是 28 天內能用的現成物件。

### 模型小結表

| 模型 | 授權 | GGUF/ARM | 記憶體估算 (Q4) | 繁中/中英夾雜 | 活躍度 | 結論 |
|---|---|---|---|---|---|---|
| Qwen2.5-Taiwan-1.5B-Instruct | Apache 2.0 | 已驗證可跑 | ~1.0GB | 有做繁中微調，未見量化評測 | 個人專案，中 | **採用（保底）** |
| Qwen3-1.7B(-Instruct) | Apache 2.0 | 官方 GGUF | ~1.0-1.2GB | 119 語言，理論品質優於 Qwen2.5 | 官方持續維護，高 | **建議升級主力，需 A/B** |
| Gemma-3n E2B-it | Gemma 授權 | GGUF 有但 text-only 剛穩定 | 官方稱 ~2GB，**llama.cpp 實測未知，有風險** | 宣稱 140 語言，未見驗證 | 新，打磨中 | 備選/觀察 |
| Llama-Breeze2-3B-Instruct | 需查（MTK 條款） | GGUF 可跑 | ~2GB+，官方建議 8GB 環境 | 繁中優化佳 | MTK 官方，中新 | 邊緣淘汰，雲端層可留意 |
| TAIDE 系列 | 開放但無小模型 | 無 1-2B 規格 | N/A（最小 7-8B） | 台灣在地語料佳 | 官方持續，中 | 邊緣淘汰 |

---

## 二、推理框架評估

| 框架 | 定位 | ARM/aarch64 支援 | 板上部署重量 | 活躍度 | 結論 |
|---|---|---|---|---|---|
| **llama.cpp** | C/C++ 純推理引擎，GGUF 生態核心 | 原生 ARM NEON/i8mm 優化最成熟（[Arm 官方 learning path 也用它做示範](https://learn.arm.com/learning-paths/servers-and-cloud-computing/llama-cpu/llama-chatbot/)） | 極輕（單一 binary，無 daemon） | 極高：119K+ GitHub star（2026-07），持續發版 | **採用**（原型已用，維持） |
| Ollama | llama.cpp 之上的模型管理/API 服務層 | 官方發行 `ollama-linux-arm64` 二進位，**確有官方 ARM64 Linux 支援**（早期網路資訊誤指其「缺乏嵌入式/ARM 支援」是不準確的，已用官方文件核實） | 中——常駐 daemon + REST API，社群測得比直接呼叫 llama.cpp 多 10–30% 管理開銷、2–8% token/s 差距 | 高，商業化程度高 | **備選，僅限開發機除錯**；正式板子上不需要多包一層 daemon，尤其 4GB RAM 每一分都要省 |
| mlc-llm | TVM 編譯式跨平台推理引擎，強項在 GPU（含手機 Mali GPU） | Linux aarch64 **CPU** 路徑歷史上一直不穩定：官方 issue 顯示 2024 年時「無 arm64 nightly」，2026 年雖有 commit（最近一次約 2026-01-25）但仍主打 GPU/TVM 編譯鏈，CPU-only ARM board 案例稀少 | 重——需要 TVM 編譯工具鏈，模型要先編譯成特定 target | 中（持續有 commit，但 issue 回應與 ARM CPU 案例都偏少） | **淘汰**：28 天內導入 TVM 編譯鏈的學習/除錯成本不划算，且我們用不到它的強項（GPU） |
| ExecuTorch | PyTorch 官方邊緣推理引擎，12+ 硬體後端含 Arm Cortex-A/Ethos-U，與 Arm/MediaTek 有官方合作（KleidiAI 加速） | 官方支援 aarch64 Linux host，[Arm 官方文章](https://community.arm.com/arm-community-blogs/b/ai-blog/posts/llm-inference-llama-quantized-models-executorch-kleidiai)展示 Llama 3.2 1B/3B 用 KleidiAI 加速、量化模型 prefill 快 20%，部分手機上 400+ tok/s（注意這是峰值場景非通則） | 中——需要把模型導出成 `.pte` 格式，工作流程與 GGUF 生態完全不同 | 高，2025-2026 持續大版本更新（0.5→0.6→1.3.1） | **中期方向，非現在**：與 MediaTek 生態系整合度理論上最高（未來可能是 NPU delegate 的路），但現在換掉 GGUF pipeline 的遷移成本，28 天內不划算 |

**框架小結**：維持 llama.cpp 作為引擎骨幹是對的選擇，這也驗證了「自建 + 選用元件」路線比整套換框架更適合本專案時程。

---

## 三、編排層評判

### a) LangGraph 作全局狀態控制器——對邊緣即時迴路是否過度設計？

- LangGraph 是「low-level orchestration framework for building, managing, and deploying **long-running, stateful agents**」（[官方 repo](https://github.com/langchain-ai/langgraph)，36.4k star，2026-06-30 仍有新版發布，活躍度高）。它的核心賣點——durable execution、checkpoint 持久化、human-in-the-loop——全部是為了「跨會話存活、可回放、可介入」的場景設計，這些正是**雲端助教層**（每學生持久記憶 agent、深度診斷）需要的東西，而不是邊緣即時迴路需要的東西。
- 實測證據：LangChain 官方論壇上有開發者反映，把 LangGraph 接上 LiveKit 做即時語音時，延遲從目標的 2-3 秒被拖到 **5-8 秒**（[討論串](https://forum.langchain.com/t/integrating-reltime-lowletency-voice-with-langgraph-graph-but-the-letency-is-more-than-6-7-seconds-it-should-be-less-than-2-3-seconds/1584)），根本原因通常是每一步的 state 序列化/持久化 I/O 疊加。本專案邊緣層的預算是「單輪 <2s（端到端 <1.5s）」，這比上述案例的目標還要緊，在 4GB RAM、無 GPU 的 ARM 板子上，多一層 Python 圖狀態機的序列化開銷是不必要的風險。
- **結論**：邊緣即時層**不用 LangGraph**——過度設計。一個用 SQLite/記憶體變數維護的簡單狀態機（FSM／決策表）就能表達「目前教學狀態→下一個動作」，寫起來反而更可控、可預測延遲。**雲端助教層則是 LangGraph 合理的落點**（非即時、best-effort，正好吃它「long-running stateful」的優勢）。

### b) PydanticAI——1.5B 小模型結構化輸出可靠度

- PydanticAI（[18.2k star，最新 v2.4.0 於 2026-07-03 發布](https://github.com/pydantic/pydantic-ai)，非常活躍）的核心機制是：呼叫 LLM 供應商的原生 function-calling / tool-use API，再用 Pydantic schema 驗證＋失敗重試。它的可靠度**取決於底層模型本身的 function-calling 訓練品質**，而不是框架本身能無中生有保證正確性。
- 小模型實測數據（[arXiv:2511.22138 TinyLLM 評測](https://arxiv.org/pdf/2511.22138)）：Qwen3-1.7B / Qwen3-0.6B 在 BFCL 類 live accuracy 只有 **58–63%**；另一份研究指出 Qwen 7B 結構化輸出執行成功率也只有 65.0%，Llama 8B 更只有 53.3%，且小模型的一致性可以在重複測試間差到 **17 倍**（[arXiv:2605.02363](https://arxiv.org/pdf/2605.02363)）。也就是說，若在邊緣層讓 1.5-1.7B 模型「自己決定要呼叫什麼工具、填什麼參數」，失敗率高到不能接受，尤其這是面向兒童、需要穩定 demo 的場景。
- 但這不代表邊緣層完全做不到結構化輸出——**llama.cpp 的 GBNF grammar 約束解碼是不同機制**：它在 sampling/logit 層直接遮蔽不合法 token，語法合法性是「構造上保證」而非「訓練出來的能力」（[llama.cpp grammar 文件](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md)、[DeepWiki 說明](https://deepwiki.com/ggml-org/llama.cpp/8.1-grammar-and-structured-output)）。也就是說，格式正確率可以接近 100%，唯一仍需把關的是「語意是否正確/安全」——這正是規則引擎該做的事（見下）。
- **結論**：
  - 邊緣層：**不引入 PydanticAI**（多一層 Python 依賴、且它保證的東西本來就不是小模型的強項）。若邊緣層需要 LLM 吐結構化資料，直接用 **llama.cpp 原生 GBNF grammar** 卡住格式，語意正確性交給規則引擎驗證/兜底。
  - 雲端層：**PydanticAI 是合理選擇**，接 AWS Bedrock（Claude）做診斷報表/工具呼叫，大模型的原生 tool-calling 可靠度足夠支撐它的驗證/重試模型。

### c) 規則式鷹架引擎為主幹、LLM 只做潤飾（混合式）vs 全 LLM 生成

- 學界與業界對「兒少/教育場景」的混合式架構有一致傾向：一篇 2025 年的兒童求助熱線訓練研究（[Hybrid BDI-LLM, arXiv:2509.16784](https://arxiv.org/html/2509.16784)）用規則式 BDI 框架做骨架、LLM 只負責讓對話更自然，並發現使用者評價「更真實、更正面」；另一篇混合聊天機器人研究總結：「連貫的對話流程與可靠的事實抽取受益於明確結構，回應評估與生成多樣性受益於機器學習的模式辨識能力」——兩者分工而非二選一。醫療/心理領域的系統性回顧也指出規則式方案用「較高的前期/維護成本換取高確定性、安全性與系統相容性」（[JMIR 2025](https://www.jmir.org/2025/1/e78186)）。
- 對應到本專案的三個關鍵風險——**兒少內容安全**、**延遲**、**demo 穩定性**——全 LLM 生成（讓 1.5-1.7B 模型自由決定要說什麼、怎麼教）在三項上都是最差選項：安全邊界不可控（小模型對抗性/離題輸入的魯棒性差）、生成長度不可控（直接影響延遲）、決賽現場遇到非預期輸入時容易「答非所問」拖垮觀感。
- **混合式的具體分工建議**：
  1. **規則引擎（主幹，Python 狀態機/決策表，不涉及 LLM）**：根據「ASR 轉寫文字 + 目前教學單元/進度 + 學生歷史正確率」決定**下一個教學動作**（例如：重複較慢／給一個提示／稱讚並進下一題／糾正發音／切換成中文輔助說明／觸發兜底話術）。這一步是確定性的、可測試的、延遲幾乎為零。
  2. **LLM 只做「動作→自然語句」的潤飾**：prompt 固定模板，例如「用溫暖、簡短、適合國小生的中英夾雜口吻說出：[動作=給提示, 內容=xxx]」，並且：
     - **限制 `max_tokens` 在 40-60 token 內**（直接壓住生成延遲上限，這比模型選型更能穩定達成 <2s 目標）；
     - 用 **GBNF grammar** 約束輸出格式（例如必須是單一句子、不能包含特定符號/超連結）；
     - 生成後過一層**輕量安全過�ter**（關鍵字黑名單、長度上限、語言檢測），不合格直接丟棄改用規則引擎預先寫好的固定話術（= 兜底），不重試不等待。
  3. **全 LLM 生成**（自由決定教學策略＋自由生成整段話）**在邊緣層淘汰**，但可以是**雲端助教層**的做法——那裡有時間預算（非同步、best-effort）、接的是 Bedrock Claude（安全對齊與指令遵循能力遠高於 1.5B 模型），適合做深度診斷、個性化報表這類需要更大自由度與更高品質的任務。

---

## 四、核心判斷：28 天決賽 demo，邊緣生成的正確策略

**策略一句話**：邊緣即時層是「規則決策 + LLM 只潤飾一句話」，不是「LLM 決定一切」；不引入 LangGraph／PydanticAI 這類重量編排框架到邊緣迴路，它們的價值在雲端助教層才能兌現。

**Prompt/引擎分工具體建議**：

- 狀態機（規則引擎）輸出一個結構化的「教學指令」（內部資料結構，不經過 LLM）→ 組成短 prompt → llama.cpp 生成（GBNF grammar 鎖格式、`max_tokens` 鎖長度、`temperature` 調低如 0.3-0.5 求穩定而非創意）→ 安全過濾 → 若失敗，直接用規則引擎預存的固定中英雙語話術頂上，**絕不讓生成失敗變成使用者感知的卡頓**。
- 中英夾雜的處理：規則引擎可以直接標注「這句要哪些詞用英文」（例如題目關鍵詞、鼓勵語可切英文），比讓 LLM 自己判斷該不該切換語言更可控，也更貼近「鷹架帶讀」的教學設計初衷（教師可預先設定切換規則）。

---

## 五、排序推薦表

### 模型
| 排名 | 選項 | 決策 |
|---|---|---|
| 1 | Qwen3-1.7B-Instruct（GGUF）| 建議升級主力，1 天內 A/B 驗證後定案 |
| 2 | Qwen2.5-Taiwan-1.5B-Instruct | 保底，已驗證可跑，A/B 輸了就留著當 fallback |
| 3 | Gemma-3n E2B-it | 觀察備選，先花半天做記憶體實測再決定是否投入 |
| 4 | Llama-Breeze2-3B-Instruct | 邊緣淘汰（記憶體超預算），雲端層可留意 |
| 5 | TAIDE 系列 | 邊緣淘汰（無小模型規格） |

### 推理框架
| 排名 | 選項 | 決策 |
|---|---|---|
| 1 | llama.cpp | 採用，維持現有整合 |
| 2 | Ollama | 備選，僅限開發機除錯用 |
| 3 | ExecuTorch | 中期方向（黑客松後評估 NPU offload），非現在 |
| 4 | mlc-llm | 淘汰 |

### 編排層
| 排名 | 選項 | 落點 |
|---|---|---|
| 1 | 規則式鷹架引擎 + LLM 短句潤飾（混合式） | 邊緣即時層（採用） |
| 2 | llama.cpp GBNF grammar 約束解碼 | 邊緣層結構化輸出手段（採用，取代 PydanticAI） |
| 3 | LangGraph | 雲端助教層（備選） |
| 4 | PydanticAI | 雲端助教層接 Bedrock（備選） |
| 5 | 全 LLM 自由生成整段教學決策 | 邊緣層淘汰；雲端層可用 |

---

## 六、28 天內的整合路徑

- **Day 1-3｜模型基準測試**：在 Genio 520（或先用 Cortex-A78/A76 class 的近似板子）跑 `llama-bench`，比較 Qwen2.5-Taiwan-1.5B vs Qwen3-1.7B 的 token/s、RSS 記憶體、以及自建 20 題「國小中英夾雜教學情境」測試集的人工評分，決定主力模型。同時對 Gemma-3n E2B 做半天的記憶體實測（若時間不夠可跳過，不影響主線）。
- **Day 4-7｜規則式鷹架引擎**：定義教學動作集合與決策表（依 ASR 結果、學生進度 SQLite 狀態），先不接 LLM，純規則跑通一輪對話流程，確保狀態機本身的邏輯正確、可測試。
- **Day 8-10｜LLM 潤飾層整合**：把 llama.cpp 接到「動作→短句」的潤飾步驟，導入 GBNF grammar 鎖格式、`max_tokens` 限制、低 temperature；加上關鍵字黑名單/長度檢查的安全過濾層。
- **Day 11-14｜端到端延遲 profiling**：實機量測 VAD→ASR→LLM→TTS 每段耗時，針對 LLM 段做 KV cache 重用、context 長度限制、量化格式比較（Q4_K_M / Q4_0 / IQ4_XS）調到 TTS 首音 <300ms、單輪 <2s。
- **Day 15-18｜兜底與失敗模式**：把「ASR 為空／LLM 逾時／生成未過安全檢查」全部導向規則引擎預存的固定話術，確保任何失敗都在 <500ms 內有回應，不讓使用者感知到卡頓或空白。
- **Day 19-21｜雲端助教層骨架**：用 LangGraph 建每學生的持久記憶 agent（非即時、接 Bedrock Claude），PydanticAI 定義診斷報表的結構化輸出 schema；這層與邊緣層透過 SQLite/非同步上傳解耦，互不影響邊緣延遲。
- **Day 22-24｜語料擴充與校正**：擴大中英夾雜測試集，人工挑錯，補 few-shot 範例；若時間允許可對主力模型做小量 LoRA 微調，否則維持 zero/few-shot prompt 調校。
- **Day 25-26｜全端整合 + 斷網演練**：關閉網路模擬決賽現場情境，跑完整 demo 腳本，記錄延遲數字是否達標，修正邊界案例。
- **Day 27｜緩衝與備援**：修剩餘 bug，錄一支 backup demo 影片以防現場意外（斷網/硬體問題）。
- **Day 28｜決賽**。

---

## 參考來源

- Qwen2.5-Taiwan-1.5B-Instruct: https://huggingface.co/benchang1110/Qwen2.5-Taiwan-1.5B-Instruct
- Qwen2.5-1.5B-Instruct GGUF: https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF
- Qwen3 技術報告: https://arxiv.org/pdf/2505.09388
- Qwen3.5 Unsloth 文件: https://unsloth.ai/docs/models/qwen3.5
- Gemma 3n 開發者指南: https://developers.googleblog.com/en/introducing-gemma-3n-developer-guide/
- Gemma 3n HF blog: https://huggingface.co/blog/gemma3n
- Gemma-3n E2B GGUF: https://huggingface.co/ggml-org/gemma-3n-E2B-it-GGUF
- Llama-Breeze2-3B 介紹: https://medium.com/@simon3458/繁中新模型-breeze2-2025-年強化繁體中文的多模態-llm-模型-b775135ed85c
- Llama-Breeze2-3B GGUF: https://huggingface.co/mradermacher/Llama-Breeze2-3B-Instruct-Text-GGUF
- TAIDE 改用 Gemma 3 報導: https://technews.tw/2025/08/27/gemma-3-taide-series-launched/
- Raspberry Pi 5 LLM 實測: https://tinyweights.dev/posts/run-llms-raspberry-pi-5/
- SBC LLM 學術評測: https://arxiv.org/html/2511.07425v1
- llama.cpp repo: https://github.com/ggml-org/llama.cpp
- Arm 官方 llama.cpp 部署教學: https://learn.arm.com/learning-paths/servers-and-cloud-computing/llama-cpu/llama-chatbot/
- Ollama Linux 文件: https://docs.ollama.com/linux
- Ollama ARM Linux issue: https://github.com/ollama/ollama/issues/5797
- mlc-llm repo: https://github.com/mlc-ai/mlc-llm
- mlc-llm aarch64 issue: https://github.com/mlc-ai/mlc-llm/issues/533
- ExecuTorch repo: https://github.com/pytorch/executorch
- Arm KleidiAI + ExecuTorch: https://community.arm.com/arm-community-blogs/b/ai-blog/posts/llm-inference-llama-quantized-models-executorch-kleidiai
- MediaTek Genio 520/720 發布: https://www.mediatek.com/tek-talk-blogs/mediatek-genio-720-520-launch-at-embedded-world-2025
- MediaTek NPU + LiteRT: https://developers.googleblog.com/mediatek-npu-and-litert-powering-the-next-generation-of-on-device-ai/
- LangGraph repo: https://github.com/langchain-ai/langgraph
- LangGraph 即時語音延遲討論: https://forum.langchain.com/t/integrating-reltime-lowletency-voice-with-langgraph-graph-but-the-letency-is-more-than-6-7-seconds-it-should-be-less-than-2-3-seconds/1584
- PydanticAI repo: https://github.com/pydantic/pydantic-ai
- TinyLLM 小模型 function calling 評測: https://arxiv.org/pdf/2511.22138
- 小模型結構化輸出可靠度研究: https://arxiv.org/pdf/2605.02363
- llama.cpp GBNF grammar 文件: https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md
- llama.cpp grammar/結構化輸出 DeepWiki: https://deepwiki.com/ggml-org/llama.cpp/8.1-grammar-and-structured-output
- 兒童求助熱線 Hybrid BDI-LLM 研究: https://arxiv.org/html/2509.16784
- 規則式 vs LLM 聊天機器人系統性回顧 (JMIR): https://www.jmir.org/2025/1/e78186
