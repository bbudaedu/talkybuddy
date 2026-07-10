# 自然情緒 TTS 研究——雲端／聲音克隆／邊緣提升三軸（說說學伴 TalkyBuddy）

研究日期：2026-07-08。母脈絡：`research/04_TTS選型.md`（原邊緣 CPU-only 前提）、`research/16_Bedrock端到端S2S_可行性評估.md`（裁定路徑C混成）。

## 0. 為什麼重研究：前提變了

`04` 假設「邊緣 CPU-only 死守低延遲、台灣腔/情緒模型只能拿去雲端預錄」。但架構已裁定 **路徑C混成**：**線上主場景可走雲端、離線才降級本地**。這讓「雲端即時情緒/克隆 TTS」從被否決重新變成認真候選。

**現況痛點**：`server/tts.py` 用 sherpa-onnx 載入 Piper `zh_CN-huayan-medium` VITS，聽感是大陸腔、平板、無情緒。

**使用者範圍決策**：三種佈署都要看（雲端即時＋邊緣即時＋自選人聲克隆）；**優先序：自然情緒 > 台灣腔**（腔調可妥協）。

## 1. 最重要的結論（先講）

> **「自然、有情緒」在 Genio 520（4GB RAM、CPU-only、無 NPU delegate）是模型家族層級的天花板，不是優化能解決的差距。**
>
> 邊緣即時這一整個「非自回歸並行解碼」家族（VITS / MeloTTS / Kokoro / Matcha）**沒有任何一個內建情緒 embedding 或大規模情緒語料訓練**。調到極致，聽感只能從「大陸腔平板」進步到「口齒清楚、韻律自然但情緒平淡」，**跨不過情緒表達那道牆**。
>
> 真正的情緒表達（CosyVoice2 / Fish-Speech / GPT-SoVITS / ElevenLabs / MiniMax）全都仰賴 GPU 級算力或 12GB+ RAM/VRAM——**在 4GB RAM 邊緣確定摸不到，常常連裝進 RAM 都有困難**。
>
> **所有聲音克隆模型都跑不動 4GB ARM 邊緣**。想要克隆自選人聲，克隆只能在雲端做。

→ 這與 `research/16` 已裁定的「路徑C混成」完全一致：**若情緒是硬需求，把 TTS 這塊也納入雲端混成，離線降級回退到邊緣 Matcha/Piper。**

## 2. 三種佈署的可行性判定

### 佈署 A：雲端即時 TTS（線上主場景）——✅ GO，這是拿到「自然情緒＋克隆」的唯一現實路徑

兩條子路：**商用 API**（快、省事、有情緒＋克隆）或 **自架雲端 GPU 開源模型**（無每字元成本、可控、Apache-2.0）。

### 佈署 B：邊緣即時 TTS（Genio 520 離線）——⚠️ 僅能「降級可用」，情緒天花板明確

只能做離線 fallback。最佳低成本升級＝把 huayan 換成 **Matcha-TTS zh**（見 §5）。**不要**指望邊緣做出情緒。

### 佈署 C：自選人聲克隆——✅ 雲端 GO／❌ 邊緣 NO-GO

克隆模型全都太重，邊緣裝不下。克隆一律走雲端；離線 fallback 用不可克隆的固定音色模型（Matcha/Kokoro）。

## 3. 雲端商用 API 候選比較（情緒＋克隆）

| 候選 | 情緒控制 | 克隆樣本 | 延遲 | 中文/zh-TW | 定價 | 商用/合規 | 判定 |
|---|---|---|---|---|---|---|---|
| **ElevenLabs v3** | ⭐最強：inline audio tags `[excited]`/`[whispers]`/`[calm]` | Instant 30s–2min；Pro 需 30min+ | Flash v2.5 ~75ms、端到端 <500ms | 支援 Mandarin，zh-TW 腔需試聽 | Starter $6/月起含商用授權 | 需聲音當事人書面同意；無兒童專屬條款 | **Demo 首選** |
| **MiniMax 海螺 Speech-02** | API 原生 `emotion` 參數 | ⭐最低 5–10s | 未查到 TTFA 數據 | 中文母語強；zh-TW 未專門優化 | ⭐最便宜（第三方 ~$0.05–0.1/千字） | ⚠️中國廠商，跨境資料/COPPA/台灣個資法須法遵審查 | 成本敏感備選 |
| **Azure Neural TTS** | ❌ zh-TW **無情緒 style**；zh-CN 才有（含童聲 `UnisoundXiaoAi`） | Custom Neural Voice＝企業審批制 | Neural HD Flash 即時優化（無 ms 數據） | zh-TW 三個聲音無情緒 | Neural $16／HD $22 每百萬字元 | ✅教育用途明確核准，但 CNV 走人工審批、須及早申請 | 情緒需退回大陸腔童聲 |
| **Google Chirp3-HD** | 稱 30 styles，但文件說不支援 SSML，控制方式矛盾 | Instant Custom Voice 僅 10s | 未查到 | Mandarin 有；zh-TW locale 待確認 | Chirp3-HD $30／Instant Voice $60 每百萬字元；每月百萬字免費 | 未查到兒童專屬限制 | 待實測 |
| **OpenAI gpt-4o-mini-tts** | prompt 式語氣引導 | ❌ **無克隆** | ~0.5s | 非英語品質不均 | ⭐最便宜 ~$0.015/分 | — | ❌ 無克隆，出局 |
| **Cartesia Sonic 3.5** | 隨對話校準（無標籤） | Professional clone（樣本長度未知） | 宣稱 40–90ms；第三方實測 P50 188ms | 有中文產品頁，zh-TW 適配未知 | ~$35/百萬字（credit 制） | 未查到兒童條款 | 低延遲備選 |

### 雲端商用小結
- **要情緒＋快速克隆＋馬上驚艷** → **ElevenLabs**（情緒粒度最細、克隆門檻低、延遲夠即時）。
- **要最便宜＋中文母語＋最短克隆樣本** → **MiniMax 海螺**，但**必做 zh-TW 試聽＋跨境合規審查**再定案。
- Azure/Google 在 zh-TW 情緒/克隆自助性明顯落後；OpenAI 無克隆直接淘汰。

## 4. 雲端自架開源克隆候選比較

| 候選 | 授權（可商用？） | 克隆樣本 | 情緒 | 中英混 | 延遲/RTF | 邊緣可行 | 判定 |
|---|---|---|---|---|---|---|---|
| **CosyVoice2-0.5B** | ✅ Apache-2.0 | 幾秒 zero-shot | ✅ instruct 模式控情緒/方言/語速 | ✅ 9 語＋18 方言，最成熟 | 官方 ~150ms 串流首包（**GPU**）；TensorRT-LLM 加速 | ❌ CPU 需 16GB+ RAM | **自架首選** |
| **GPT-SoVITS** | ✅ MIT（最寬鬆） | 5s zero-shot／1min few-shot | 有限 | ✅ 中/英/日/韓，中文最道地 | GPU RTF 0.01–0.03；**M4 CPU 0.526** | ❌ 弱 ARM 推測 RTF>1、RAM 超標 | 自架次選（中文最道地） |
| **IndexTTS2** | ⚠️ Apache＋bilibili Model License 矛盾，商用需書面申請 | zero-shot | ⭐罕見：情緒與音色**解耦**，8 維情緒向量／文字描述／情緒參考音 | ✅ 中文＋拼音 | 無 RTF 數據 | ❌ 無邊緣資料 | 情緒克隆最強但授權待清 |
| **Fish-Speech / OpenAudio S1** | ❌ 權重非商用（Fish Audio Research License / CC-BY-NC-SA） | 10–30s | 有情緒標記 | 80+ 語 | H200 RTF 0.195 | ❌ 4B 太重 | ❌ 商用阻擋 |
| **F5-TTS** | ❌ 權重 CC-BY-NC（訓練資料非商用） | 3–10s | — | ✅ 中英 code-switch 音色一致 | GPU RTF 0.03–0.04（也有人測到 ~3） | ❌ flow-matching 偏重 | ❌ 商用阻擋 |
| **XTTS-v2（Coqui）** | ❌ CPML 非商用；Coqui 2024/1 倒閉，授權管道消失 | 6s zero-shot | — | 17 語含中文（較弱） | GPU ~0.15 | ❌ | ❌ 停更＋非商用 |
| MeloTTS | ✅ MIT | ❌ 不支援克隆 | ❌ | ✅ | CPU 可即時 | 邊緣可（無克隆） | 對照 |
| Kokoro-82M | ✅ Apache-2.0 | ❌ 不支援克隆 | ❌ | 有 | CPU 遠超即時 | 邊緣最強（無克隆） | 對照 |

### 自架小結
- **可商用＋情緒＋克隆＋中英混** 同時滿足者：只有 **CosyVoice2-0.5B（Apache-2.0）**。
- **中文最道地＋授權最寬鬆（MIT）**：**GPT-SoVITS**。
- Fish-Speech / F5-TTS / XTTS-v2 **權重非商用，商用兒童玩具直接阻擋**（除非付費授權或自行重訓）。
- IndexTTS2 的「情緒/音色解耦克隆」技術最先進，但授權界線不明，商用前務必書面確認。

## 5. 台灣本土優化模型

| 候選 | 授權 | 克隆 | 情緒 | 邊緣即時 | 活躍度 | 判定 |
|---|---|---|---|---|---|---|
| **MediaTek BreezyVoice** | Apache-2.0（底層 CosyVoice 商用範圍仍建議法務確認） | ✅ zero-shot（樣本秒數未公布） | ❌ 未提供 | ❌ 無 RTF 數據、CosyVoice 系偏重 | ⚠️ 326★、0 release、一年多未更新 | 台灣腔道地但無情緒＋即時性存疑＋近停滯 |
| **雅婷 Yating** | 封閉雲端 API | ❌ 不支援 | ❌ 僅語速/音調/能量參數 | ❌ 無法自架 | 商用服務 | 風險最低雲端備援，但不能克隆、情緒弱 |
| 中研院/工研院/國網 | 技轉或研究態 | — | — | — | — | 無現成中文 TTS，多在做台語 |

補充：MediaTek 2026/2 發表 **Breeze 3／BreezyVoice 26**，但聚焦**台語（閩南語）**、僅 LINE 體驗、是否開源未確認，不直接適用中英教學。

**台灣本土小結**：因「優先序＝自然情緒 > 台灣腔」，台灣本土模型的最大賣點（腔調）恰好是可妥協項，而它們在情緒與即時性上都不及國際方案。**BreezyVoice 可留作「想要台灣腔克隆」時的雲端自架備選**，但不是主線。

## 6. 邊緣路線：不換架構下 CP 值最高的自然度提升（供離線 fallback 用）

sherpa-onnx 可載入、RPi4 實測 RTF：

| 模型 | RPi4 RTF（1/2/3/4 緒） | 大小 | 中英混 | 即時？ |
|---|---|---|---|---|
| **matcha-icefall-zh-baker**（Matcha-TTS，flow-matching） | **0.892 / 0.536 / 0.432 / 0.391** | 72MB | 否 | ✅ **唯一 RPi4 實測 <1** |
| vits-melo-tts-zh_en | 6.7 / 3.9 / 2.9 / 2.5 | 163MB | ✅ 原生 | ❌ 需 Genio 520 實測 |
| kokoro-multi-lang | 7.6 / 4.5 / 3.4 / 3.2 | ~310MB | 有 | ❌ |

**三招（排序）**：
1. **換聲學模型：Matcha-TTS zh 取代 Piper huayan**。唯一有實測即時證據＋架構上比純 VITS 少合成瑕疵；同在 sherpa-onnx 下，遷移成本低（換模型檔）。風險：仍非台灣腔、無直接 vs huayan 聽感實測 → **需上機盲測拍板**。
2. **推理期參數調優**（`noise_scale` / `noise_w` / `length_scale` + sentence-silence）。近零成本降低機械感，但**不產生真正情緒**，天花板低。
3. **文字前處理**（g2pW 多音字消歧 + 台灣慣用發音自建字典 + 句子切分）。修「唸錯字」這種小孩立刻聽出的硬傷；g2pW 是小 BERT 要算力，須納入 <300ms 首音預算。

> vits-melo-tts-zh_en 有原生中英混讀優點，但 RPi4 RTF>2.5，除非 Genio 520 實機測出 <1，否則有違反 <2s 硬約束風險——列「待驗證」。

## 7. 最終建議（針對「自然情緒優先、腔調可妥協、想克隆自選人聲」）

### 分場景推薦
| 場景 | 推薦 | 理由 |
|---|---|---|
| **決賽 Demo** | **ElevenLabs**（或 MiniMax 海螺） | 最快拿到「有情緒＋克隆自選人聲」的驚艷效果，API 即接、克隆 30s–2min（MiniMax 甚至 5–10s） |
| **正式產品（線上）** | **自架 CosyVoice2-0.5B on 雲端 GPU** | Apache-2.0 無每字元成本、情緒＋中英混＋克隆俱全、~150ms 串流；與路徑C混成天然契合 |
| **離線 fallback** | **Matcha-TTS zh**（升級現有 huayan） | 唯一邊緣實測即時、聽感優於純 VITS；斷網時降級可用 |
| **想要台灣腔克隆** | BreezyVoice 雲端自架（備選） | 台灣腔道地，但無情緒、須自行驗證即時性 |

### 落地順序建議
1. **最靠近 Demo**：先接 **ElevenLabs 或 MiniMax API**，克隆一個溫柔台灣女聲/童聲，線上場景直接用；離線仍走現有 Piper（或先換 Matcha）。與 A1/A2/B 軸正交，改動集中在 pipeline 的 TTS 呼叫端。
2. **正式化**：把雲端 TTS 從商用 API 換成自架 **CosyVoice2-0.5B**（去每字元成本、資料自控、Apache-2.0），與 `research/16` 的雲端大腦（Bedrock FM）同機房佈署。
3. **離線 fallback 升級**：`server/tts.py` 用 sherpa-onnx 載入 Matcha-TTS zh 取代 huayan，做斷網降級；先上機盲測確認聽感確實優於 huayan 再切換。

### 一句話
**情緒要靠雲端（Demo 用 ElevenLabs/MiniMax，正式用自架 CosyVoice2）；邊緣只做「不掉線的降級」，把 huayan 換成 Matcha 是邊緣路線 CP 值最高的一招；克隆一律雲端。這條路和已裁定的路徑C混成完全一致，不需另立架構。**

---

## 參考來源（節錄）
- Matcha-TTS RPi4 RTF：https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/matcha.html
- vits-melo / kokoro RTF：https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/vits.html ／ kokoro.html
- CosyVoice2：https://github.com/FunAudioLLM/CosyVoice ／ https://funaudiollm.github.io/cosyvoice2/
- GPT-SoVITS：https://github.com/RVC-Boss/GPT-SoVITS （M4 CPU RTF：ResearchGate 403739266）
- Fish-Speech LICENSE：https://github.com/fishaudio/fish-speech/blob/main/LICENSE
- F5-TTS：https://github.com/SWivid/F5-TTS
- XTTS-v2 授權：https://huggingface.co/coqui/XTTS-v2/discussions/106
- IndexTTS2：GitHub issue #228（授權爭議）
- ElevenLabs v3／audio tags：https://elevenlabs.io/v3 ／ /docs/overview/models ／ /pricing
- MiniMax：https://platform.minimax.io/docs/guides/speech-voice-clone ／ pricing-speech
- Azure zh-TW／CNV：https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support ／ limited-access
- Google Chirp3-HD／Instant Custom Voice：https://docs.cloud.google.com/text-to-speech/docs/chirp3-hd ／ chirp3-instant-custom-voice
- Cartesia：https://www.cartesia.ai/sonic ／ /languages/chinese ／第三方延遲：coval.ai
- BreezyVoice：https://github.com/mtkresearch/BreezyVoice ／ arXiv 2501.17790
- 雅婷 Yating：https://developer.yating.tw/zh-TW/doc/tts-語音合成
