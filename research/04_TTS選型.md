# 邊緣 TTS 選型研究報告——說說學伴（Genio 520 / 中英夾雜 / 首音 <300ms）

研究範圍：邊緣即時層 TTS。目標硬體 MediaTek Genio 520（2×A78+6×A55、~9-10 TOPS NPU、4GB RAM、Yocto aarch64），現況已有 `server/tts.py` 用 **piper-tts**（zh/en 雙 voice、逐段合成後串接、段間插 150ms 靜音）在跑。本報告驗證這條路線是否該繼續、有沒有更適合二開的基底，並處理一個目前程式碼裡還沒被注意到的授權風險。

---

## 0. 先講一個關鍵發現：piper-tts 這個 pip 套件的授權已經變了

專案現有 `server/tts.py` 是 `from piper import PiperVoice`，即 pip 套件 `piper-tts`。

- 原始 repo `rhasspy/piper` 是 **MIT** 授權，但已於 **2025-10-06 被 owner 存檔（archived，唯讀）**，README 明講「Development has moved to https://github.com/OHF-Voice/piper1-gpl」。[[rhasspy/piper]](https://github.com/rhasspy/piper)
- 新的開發主線 `OHF-Voice/piper1-gpl` 是 **GPL-3.0**，目前活躍（最新 release v1.4.2，2026-04-02）。[[OHF-Voice/piper1-gpl]](https://github.com/OHF-Voice/piper1-gpl)
- PyPI 上的 `piper-tts` 套件**現在指向的是 GPL-3.0 的新引擎**（v1.4.2, 2026-04-02）。[[piper-tts · PyPI]](https://pypi.org/project/piper-tts/) [[Local TTS Licenses 2026]](https://www.promptquorum.com/power-local-llm/local-tts-voice-cloning-piper-coqui-xtts)

**影響**：如果專案的 `requirements.txt` 沒有釘死版本，`pip install piper-tts` 很可能已經在拉 GPL-3.0 的程式碼，這跟原本以為的「MIT、可自由二開」認知不一致，對之後商業化/決賽評審若涉及授權合規會是個地雷。**這不是要不要用 Piper 的問題（聲音模型 .onnx 本身授權沒變），而是「用哪個引擎去跑這些 .onnx」的問題。**

兩個乾淨的解法（都不影響現有 voice 模型檔）：
1. 釘死 `piper-tts` 在 GPL 化之前的舊版（MIT，約對應 rhasspy 存檔前的最後 release），風險是舊版之後不會再修 bug。
2. **改用 sherpa-onnx（Apache-2.0）載入同一批 Piper `.onnx` 聲音模型**——sherpa-onnx 原生支援 Piper VITS 格式，不需要 `piper` 這個 Python 套件本身，等於繞開 GPL 依賴，同時拿到更成熟的 streaming API。這是本報告建議的路線，見下方 sherpa-onnx 段落。

---

## 1. Piper（rhasspy/piper，聲音模型本身）

**定位**：輕量 VITS-based CPU TTS，Rhasspy/Home Assistant 生態的標準離線 TTS，是專案目前正在用的基底。

- **授權**：模型檔（voice `.onnx` + config）授權各自標示，多為 MIT/CC 系；**引擎程式碼**如上節所述，舊 MIT／新 GPL-3.0 分裂，需注意。
- **離線/ARM**：原生設計就是給 Raspberry Pi 等 ARM 邊緣裝置用，medium 品質模型在 RPi4 上 RTF 約 0.61（即時可用），非常適合 4GB RAM、無 NPU 加持的 CPU-only 場景。[[Piper Setup 2026]](https://localaimaster.com/blog/piper-tts-setup-guide) [[sherpa-ncnn piper 範例]](https://github.com/k2-fsa/sherpa-ncnn/blob/master/python-api-examples/tts-piper-chinese-single-speaker.py)
- **中英/zh-TW**：官方中文聲音**只有 `zh_CN-huayan`（簡體/中國腔）**，沒有官方 `zh_TW` voice，Taiwan 口音缺席是確認的事實。[[MODEL_CARD zh_CN-huayan]](https://huggingface.co/rhasspy/piper-voices/blob/main/zh/zh_CN/huayan/medium/MODEL_CARD) 社群也有人在 issue 要求「更自然的中文聲音」。[[Piper issue #278]](https://github.com/rhasspy/piper/issues/278)
- **混句處理的缺點（已在現有程式碼中驗證到）**：Piper 一個 voice 只認一種語言的音素集，做不到「同一個 voice 唸中文時夾雜英文單字」。專案現況 `server/tts.py` 的作法是把句子先按語言切段（`[("zh","你說得很棒！"),("en","I want an apple.")]`），zh 段用 zh voice、en 段用 en voice **各自獨立合成**，再用 150ms 靜音串接。這會造成：
  - 語者音色/韻律在中英切換處不連續（聽起來像兩個人接話）；
  - 段間固定靜音是人工插入、不是自然停頓，句子越破碎（中英夾雜越密集）聽感越卡；
  - 每個語言段都要重新過一次模型 forward，段數變多時總合成時間會比單一長句高。
- **活躍度**：原 repo 已 archived；新主線 piper1-gpl 活躍（2026-04 仍有 release）。
- **二開難易**：程式碼量小、C++/Python 都有現成 binding，專案已經在用，二開成本最低。
- **結論：主線核心引擎，繼續用，但（a）換成 sherpa-onnx 承載以解決授權風險，（b）改善分段串接的縫合方式（見第 8 節落地做法）。**

---

## 2. MeloTTS（MyShell）

**定位**：VITS/VITS2/Bert-VITS2 系的多語 TTS，賣點是**中文 voice 原生能唸英文單字**（不用切模型），對「中文句夾英文單詞」這種真正的 code-switching 很對題。

- **授權**：MIT，商用友善。[[MeloTTS repo]](https://github.com/myshell-ai/MeloTTS)
- **離線/ARM**：官方本體是 PyTorch，重、不適合直接上邊緣；但有 ONNX 匯出路徑，社群專案 `MeloTTS-ONNX` 明確標榜「CPU real-time inference + 中英混合 TTS + ARM/QNN（Qualcomm）加速」。[[MeloTTS-ONNX]](https://github.com/201831771214/MeloTTS-ONNX) 官方 repo 也有人在問「能不能匯出 onnx」的 issue，代表這不是官方一等公民路徑，是社群補的。[[MeloTTS issue #98]](https://github.com/myshell-ai/MeloTTS/issues/98)
- **中英夾雜**：官方文件明講「The Chinese speaker supports mixed Chinese and English」，這是唯一一個把「同句中英混讀」當作原生特性寫進文件的候選。[[MeloTTS README]](https://github.com/myshell-ai/MeloTTS/blob/main/README.md)
- **延遲特性（重要疑慮）**：MeloTTS 因為用 BERT 做韻律/G2P 預測，模型比純 VITS 重。sherpa-onnx 把它轉成 onnx 後的 `vits-melo-tts-zh_en`，在 **Raspberry Pi 4 上實測 RTF 是 1 thread=6.727、2 threads=3.877、3 threads=2.914、4 threads=2.518**——**全部大於 1，代表在 RPi4 上完全不到即時**，4 執行緒全開都要 2.5 倍時間才能生完對應長度的語音。[[sherpa vits pretrained models]](https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/vits.html) Genio 520 的 2 顆 A78 效能比 RPi4 的 A72 好一截，但這個 RTF 差距太大，**不能假設換板子就會自動達標**，必須在 Genio 520 上實測才能下結論。
- **活躍度**：官方 repo 7.5k star，但最後 release 是 **2024-03-01**，之後主線基本停滯，活躍度中等偏弱（社群 fork 較活）。[[MeloTTS repo]](https://github.com/myshell-ai/MeloTTS)
- **二開難易**：原生 PyTorch 訓練/推論程式碼中等規模；若走 ONNX 路徑等於依賴社群非官方轉檔，可維護性打折扣。
- **結論：備選，且優先順位視 Genio 520 實測 RTF 而定**。中英混讀原生支援是最大亮點，但 RTF 疑慮不小，建議排進 28 天內的早期 spike（見第 9 節），而不是直接當主線賭上去。

---

## 3. sherpa-onnx TTS（k2-fsa）——推薦的「執行框架」而非單一聲音

**定位**：這不是一個聲音模型，是一個**推論框架**，可以載入 VITS（含 Piper 格式）、Matcha、Kokoro、MeloTTS-converted 等多種 onnx TTS 模型，並提供統一的 streaming/embedded API。是本報告認為「最適合二開、不用從 0 開始」的**基底候選**。

- **授權**：Apache-2.0，商用/二開都乾淨，不像 piper-tts 有 GPL 疑慮。[[k2-fsa/sherpa-onnx]](https://github.com/k2-fsa/sherpa-onnx)
- **活躍度**：13.4k star，累積 1,947 commits，最新 release **v1.13.3（2026-06-15）**，非常活躍、還在快速迭代。[[k2-fsa/sherpa-onnx]](https://github.com/k2-fsa/sherpa-onnx)
- **離線/ARM**：明確支援 **arm64/aarch64、arm32、RISC-V**，官方列名支援 Raspberry Pi、RK3588、Jetson 等嵌入式裝置，並有 RKNN/QNN/Ascend/Axera 等 NPU delegate（**沒有列出 MediaTek NPU/Neuron Delegate**，NPU 加速這塊仍要靠 CPU 執行，等同和 Piper 現況一樣走 CPU-only）。[[k2-fsa/sherpa-onnx]](https://github.com/k2-fsa/sherpa-onnx)
- **中英/多語**：可直接載入現有的 `zh_CN-huayan`/`en` Piper voices（相容），也提供 `vits-melo-tts-zh_en`（MeloTTS 轉檔，163MB，44100Hz，單語者，中英混讀原生能力繼承自 MeloTTS）與 `kokoro-multi-lang-v1_1`（zh+en，103 speakers）等現成中英雙語模型。[[sherpa pretrained models]](https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/index.html) [[vits.html]](https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/vits.html)
- **延遲特性**：官方強調支援 incremental/streaming 生成，可邊生成邊播放，降低 time-to-first-byte，這正是達成「首音 <300ms」需要的架構能力（實際數字取決於底層模型，vits-melo-tts-zh_en 本身偏重，見上節 RTF 數字）。[[react-native-sherpa-onnx streaming docs]](https://mintlify.wiki/xdcobra/react-native-sherpa-onnx/features/streaming-tts)
- **二開難易**：12 種語言 binding（含 Python/C++），文件完整、範例多（Android/iOS/樹莓派/桌面都有現成 script），外掛點清楚（換模型只是換設定檔路徑）。比自己維護 piper 的 GPL 依賴更省心。
- **結論：採用（作為執行層）**。用 sherpa-onnx 取代目前直接呼叫 `piper` pip 套件的方式，模型仍先用現有的 `zh_CN-huayan` + `en` Piper voices（零聲音品質風險、零額外訓練成本），拿到：(a) Apache-2.0 乾淨授權，(b) streaming 首音優化的框架能力，(c) 之後若要換/加測 `vits-melo-tts-zh_en` 或 kokoro 模型，只是換一個模型設定，架構不用重寫。

---

## 4. Kokoro-82M

**定位**：82M 參數、StyleTTS2 架構、輕量、在 TTS Arena 類評測上英文表現亮眼，是「小模型高音質」路線的代表。

- **授權**：Apache-2.0。[[hexgrad/Kokoro-82M]](https://huggingface.co/hexgrad/Kokoro-82M)
- **中文/混句支援**：v1.0（2025-01-27）號稱支援 8 語言含中文（`misaki[zh]` 音素化），但**語言代碼驅動音素生成、沒有原生 code-switching 設計**，中文是靠獨立的 phonemizer 掛上去，不是像 MeloTTS 那樣把中英混讀當一等公民做。[[Kokoro voice codes]](https://soniqo.audio/guides/kokoro) [[Kokoro-82M HF]](https://huggingface.co/hexgrad/Kokoro-82M)
- **品質疑慮**：社群回報中文有「產生中文語音亂碼/不管輸入什麼都唸成中文」的 bug（M4 Max 上），且整體社群評價提到「語調怪、有些辨識度低的機械感」，中文並非其強項語言。[[HF discussion #42]](https://huggingface.co/hexgrad/Kokoro-82M/discussions/42) [[Kokoro Review]](https://reviewnexa.com/kokoro-tts-review/)
- **活躍度**：sherpa-onnx 已把 Kokoro multi-lang 收進去（`kokoro-multi-lang-v1_1`），可透過同一套框架測試。
- **結論：備選但優先度低**。zh 支援不成熟、沒有針對中英混句設計，若要用也建議只透過 sherpa-onnx 順手測一下當 A/B 對照，不建議投入主力工時。

---

## 5. MediaTek BreezyVoice（台灣口音，GPT-SoVITS/CosyVoice 系）

**定位**：MediaTek Research 官方釋出、專門為台灣華語調校的 TTS，論文重點是「注音輔助的多音字消歧」，聽感上最貼近「親切的台灣口音」這個訴求。[[BreezyVoice paper]](https://arxiv.org/abs/2501.17790)

- **授權**：Apache-2.0，商用友善。[[BreezyVoice HF]](https://huggingface.co/MediaTek-Research/BreezyVoice)
- **模型架構/量級**：基於 **CosyVoice** 架構（S³ tokenizer + LLM + OT-CFM flow matching + G2P），這一系模型的共通特性是**帶自回歸 LLM 元件**，不是純 VITS 的輕量 decoder。同系 CosyVoice 官方文件本身就寫「CPU-only 推論建議 16GB+ RAM，且比 GPU 慢 10-50 倍」。[[CosyVoice guide]](https://dev.to/czmilo/cosyvoice-2025-complete-guide-the-ultimate-multi-lingual-text-to-speech-solution-4l39) BreezyVoice README 只說「可在 CPU 跑、無 GPU 時把 onnxruntime-gpu 換成 onnxruntime」，**沒有提供任何延遲/RTF 數字**，也沒有 TFLite/NPU 量化版本。[[mtkresearch/BreezyVoice]](https://github.com/mtkresearch/BreezyVoice)
- **量化/加速現況**：官方目前只有 ONNX（GPU 導向）版本，**沒有看到針對 NeuroPilot/Neuron Delegate 的移植或量化說明**，跟「跑在 NPU 上」是兩件事——目前沒有證據顯示這模型能被 NeuroPilot Public 工具鏈直接吃下去跑到 NPU 上。
- **活躍度**：GitHub 只有 **325 stars、17 commits、沒有任何 release**，屬於研究釋出型專案，非工程化產品。[[mtkresearch/BreezyVoice]](https://github.com/mtkresearch/BreezyVoice)
- **二開難易**：程式碼量不大，但要嫁接到 4GB RAM / ARM CPU-only 的邊緣即時層，等於要自己做「CosyVoice 系模型的邊緣量化/加速」這件目前業界都還在啃的硬骨頭，28 天內風險極高。
- **結論：邊緣即時層淘汰**。板上跑不動（沒有 NPU 加速路徑、CPU 推論按同系模型經驗會遠超 <2s 單輪預算，且沒有官方延遲數據可佐證能達標）。**可以留作雲端/展示用**：例如決賽 demo 若想秀「台灣腔特別好」的亮點，可用雲端 BreezyVoice 事先合成一批固定的高頻教學語句/開場白，快取成 audio 檔放到裝置端播放（非即時生成），這樣完全不佔邊緣層的 4GB RAM/延遲預算，也不用擔心斷網。

---

## 6. OpenUtau / EmotiVoice（快篩）

- **OpenUtau**：UTAU 系「歌聲合成」編輯器，領域是唱歌聲音轉換/合成，不是語音助理式 TTS，**與本專案需求不符，直接淘汰**。[[OpenUtau/OpenUtau]](https://github.com/openutau/OpenUtau)
- **EmotiVoice**（NetEase Youdao）：中英雙語、2000+ 音色、情緒可控，MIT 系授權，社群熱度高（首週 4.2k star），仍活躍維護。[[EmotiVoice repo]](https://github.com/netease-youdao/EmotiVoice) [[EmotiVoice 介紹]](https://blog.brightcoding.dev/2025/08/30/emotivoice-the-open-source-tts-engine-with-2-000+-voices-and-emotion-control) 但這是 PyTorch/伺服器導向的專案，設計目標是雲端多音色服務，**沒有 ARM 邊緣部署的官方路徑或案例**，量級也偏大。**邊緣即時層淘汰，可考慮放在雲端助教層**（例如週報/回饋語音用更豐富情緒的雲端 TTS 念給家長聽），但那是另一個題目的範圍。

---

## 7. 核心判斷題：「親切台灣口音」vs「首音 <300ms」怎麼取捨

**結論：不取捨——用架構把兩者解耦，而不是拿延遲去換口音。**

理由：
1. 產品定義的體感底線是**首音 <300ms、單輪 <2s**，這是端到端可用性的硬門檻；一旦破功，「親不親切」根本不會被感知到（孩子會覺得娃娃當機/沒反應，直接跳出互動）。BreezyVoice 這類 CosyVoice 系模型在沒有 NPU 加速、且官方自己都承認 CPU 推論慢一個數量級的情況下，**拿它做即時生成幾乎確定跳票**，不是「犧牲一點延遲換口音」，是「直接不可用」。
2. 但「台灣口音」不是只有「用台灣口音模型」一條路。**把它拆成兩個子問題**：
   - (a) 音色/腔調本身：目前務實選項就是 `zh_CN-huayan`（中國腔），這是延遲/穩定性換來的代價，短期內接受它，但可以用 SSML/前端文字正規化去修正最違和的部分（兒化音習慣詞替換、輕聲標註、常見台灣用語的發音校正字典）。
   - (b) 多音字/用詞準確度：BreezyVoice 論文的核心貢獻其實是「注音輔助的多音字消歧」這個 idea，這件事不需要跑整個 CosyVoice 模型才能做——可以在文字前處理層自建一個小型多音字校正表（針對國小課本高頻字），套用在餵給 Piper/sherpa-onnx 的文字上，用很低的成本拿到「唸對音」帶來的親切感提升，不用去扛 BreezyVoice 的延遲風險。
3. 高風險、高辨識度的「台灣腔」需求（例如開場白、鼓勵語、角色自我介紹）用**離線快取的雲端 BreezyVoice 預錄音檔**解決，這些是可預期、重複率高的固定短語，天然適合預錄；即時生成的部分（回應學生當下說的話）留給 Piper/sherpa-onnx 保延遲。

---

## 8. 中英夾雜（混句）落地做法

1. **短期（延續現況）**：維持 LLM 輸出時就標好語言分段 `[(lang,text), ...]`（現有架構已經這樣做），但改善合成端：
   - 用 sherpa-onnx 承載，取代直接呼叫 `piper` pip 套件，解決 GPL 依賴問題。
   - 段間不要用固定靜音，改用**極短 crossfade（如 30-50ms 音量交叉淡入淡出）**取代硬切靜音，聽感上縫合感會明顯降低，成本幾乎為零。
   - 語言分段前先做「合併相鄰同語言段」，减少不必要的模型切換次數（例如「這個是 apple 對不對」不要切成 3 段，中文-英文-中文各自過模型，儘量只在真正的語言邊界切）。
2. **中期（若 spike 驗證 vits-melo-tts-zh_en 在 Genio 520 上 RTF < 1）**：改用 MeloTTS zh 模型（透過 sherpa-onnx），因為它的中文 voice **原生能唸英文單字**，可以把「中文句子夾少量英文單詞」這種最常見的教學場景（例如「這個叫做 apple」）**整句丟給同一個 voice**，完全不用切段、不用縫合，音色/韻律天然連貫，是最乾淨的解法。純英文整句或整段對話仍可切到 en voice。
3. **不建議**的路線：用 BreezyVoice/CosyVoice 系模型做即時中英混句生成——延遲與資源都不現實。

---

## 9. 排序推薦表

| 優先序 | 候選 | 授權 | 角色 | 理由 |
|---|---|---|---|---|
| 1（主線執行框架） | **sherpa-onnx** | Apache-2.0 | 取代直接呼叫 piper pip 套件的執行層 | 授權乾淨、活躍度最高（13.4k star，2026-06 仍在發版）、原生支援 Piper voice 格式與 streaming、ARM/aarch64 官方支援、之後要換模型只是換設定 |
| 2（主線聲音，現況延續） | **Piper zh_CN-huayan + en**（透過 sherpa-onnx 載入） | 模型另計，引擎繞開 GPL | 邊緣即時層預設聲音 | 已驗證 RTF 可即時（~0.6 on RPi4）、零額外訓練成本、風險最低，是 28 天內能穩定交付的底線方案 |
| 3（重點 spike，若過關可升級為主線） | **vits-melo-tts-zh_en**（sherpa-onnx 載入） | MIT（MeloTTS 源模型） | 混句音質升級選項 | 原生中英混讀免切換 voice，但 RPi4 上 RTF 2.5-6.7（不即時），**必須在 Genio 520 實測**才能決定是否採用；不確定性高故排第三 |
| 4（備援，快速 A/B） | **Kokoro-82M（kokoro-multi-lang）**（sherpa-onnx 載入） | Apache-2.0 | 備援音質選項 | zh 支援不成熟、無原生 code-switch 設計、社群回報中文 bug，順手測但不投入主力 |
| 5（雲端/預錄專用，邊緣淘汰） | **MediaTek BreezyVoice** | Apache-2.0 | 固定短語預錄快取 | 台灣口音最道地，但 CosyVoice 系架構在無 NPU 加速、CPU-only、4GB RAM 條件下延遲不可控，且官方無延遲數據佐證；只適合雲端預先生成、快取播放，不進即時生成路徑 |
| 6（淘汰） | EmotiVoice | 開源（MIT系） | 雲端助教層可另評估 | 伺服器/PyTorch 導向，無邊緣部署路徑，超出本題範圍 |
| 7（淘汰） | OpenUtau | GPL 系 | 不適用 | 歌聲合成，非語音助理 TTS，領域不符 |

---

## 10. 28 天內的整合路徑

- **Day 1-2**：在目標硬體（Genio 520 EVK 或最接近的 aarch64 板）上跑通現有 `server/tts.py` 的 Piper pipeline，量測實際首音延遲與 RTF 作為 baseline；同時盤點 `requirements.txt` 目前釘的 `piper-tts` 版本，確認是否已受 GPL-3.0 影響。
- **Day 3-4**：把執行層從直接呼叫 `piper` pip 套件遷移到 **sherpa-onnx**，載入同一批 `zh_CN-huayan`/en Piper voice 檔，驗證輸出音質/延遲與現況一致或更好（Apache-2.0 授權風險解除）。
- **Day 5-7**：**關鍵 spike（go/no-go）**——在 Genio 520 上實測 `vits-melo-tts-zh_en`（透過 sherpa-onnx）的 RTF 與首音延遲，用真實教學句型（含中英夾雜）測試。若 RTF < 0.8 且首音 < 400ms → 排入 Day 11-14 換成主力聲音；否則維持 Piper 雙 voice 路線。同一週可順手用 sherpa-onnx 跑 Kokoro multi-lang 做音質 A/B（低優先，時間允許才做）。
- **Day 8-10**：串流化——改成 clause-level 分段合成＋邊生成邊播放（sherpa-onnx streaming API），把「等整句合成完才播」的延遲砍掉，逼近 <300ms 首音目標；同時做執行緒數/量化參數在 Genio 520 上的調參。
- **Day 11-14**：依 Day 7 的 go/no-go 結果，收斂混句合成方案（MeloTTS 整句直出 或 Piper 雙 voice + crossfade 縫合），並把 LLM 輸出的語言分段邏輯改成「相鄰同語言合併、只在真實語言邊界切」。
- **Day 15-18**：文字前處理層加入國小課本高頻多音字校正表 + 輕聲/兒化音替換規則，用低成本方式提升 zh_CN-huayan 的「台灣親切感」；同時用雲端 BreezyVoice 預錄一批高頻固定語句（開場白/鼓勵語/角色自介），快取進裝置端音檔庫。
- **Day 19-21**：4GB RAM 資源競爭壓力測試（ASR+LLM+TTS 同時跑），驗證 TTS 模型常駐記憶體是否影響 LLM 可用配額；準備兜底話術對應的離線預錄音檔（TTS engine 失敗時的 fallback）。
- **Day 22-24**：離線 demo 硬化——確認全流程零網路呼叫、模型檔打進 Yocto image、優化冷啟動（模型預先常駐/warm start）以避免決賽現場第一句話延遲暴增。
- **Day 25-26**：Buffer，修 bug、補測試（自動化量測首音延遲/RTF 的迴歸測試腳本）。
- **Day 27-28**：決賽彩排、凍結版本。

---

## 參考來源

- [rhasspy/piper (archived, MIT)](https://github.com/rhasspy/piper)
- [OHF-Voice/piper1-gpl (active, GPL-3.0)](https://github.com/OHF-Voice/piper1-gpl)
- [piper-tts on PyPI](https://pypi.org/project/piper-tts/)
- [Local TTS & Voice Cloning Licenses 2026](https://www.promptquorum.com/power-local-llm/local-tts-voice-cloning-piper-coqui-xtts)
- [Piper voice MODEL_CARD: zh_CN-huayan](https://huggingface.co/rhasspy/piper-voices/blob/main/zh/zh_CN/huayan/medium/MODEL_CARD)
- [Piper issue #278: More natural Chinese voice, Please](https://github.com/rhasspy/piper/issues/278)
- [Piper TTS Setup 2026 (RTF ~0.61 on RPi4)](https://localaimaster.com/blog/piper-tts-setup-guide)
- [myshell-ai/MeloTTS](https://github.com/myshell-ai/MeloTTS)
- [MeloTTS README: mixed Chinese/English](https://github.com/myshell-ai/MeloTTS/blob/main/README.md)
- [MeloTTS issue #98: onnx export](https://github.com/myshell-ai/MeloTTS/issues/98)
- [MeloTTS-ONNX (community, ARM/QNN)](https://github.com/201831771214/MeloTTS-ONNX)
- [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)
- [sherpa-onnx pretrained TTS models](https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/index.html)
- [sherpa-onnx VITS models incl. vits-melo-tts-zh_en RTF table](https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/vits.html)
- [sherpa-onnx streaming TTS docs](https://mintlify.wiki/xdcobra/react-native-sherpa-onnx/features/streaming-tts)
- [hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M)
- [Kokoro voice/language codes](https://soniqo.audio/guides/kokoro)
- [Kokoro HF discussion #42: Chinese gibberish bug](https://huggingface.co/hexgrad/Kokoro-82M/discussions/42)
- [Kokoro TTS Review 2026](https://reviewnexa.com/kokoro-tts-review/)
- [MediaTek-Research/BreezyVoice (HF)](https://huggingface.co/MediaTek-Research/BreezyVoice)
- [mtkresearch/BreezyVoice (GitHub)](https://github.com/mtkresearch/BreezyVoice)
- [BreezyVoice paper (arXiv 2501.17790)](https://arxiv.org/abs/2501.17790)
- [CosyVoice CPU inference notes (16GB+ RAM, 10-50x slower)](https://dev.to/czmilo/cosyvoice-2025-complete-guide-the-ultimate-multi-lingual-text-to-speech-solution-4l39)
- [netease-youdao/EmotiVoice](https://github.com/netease-youdao/EmotiVoice)
- [EmotiVoice intro](https://blog.brightcoding.dev/2025/08/30/emotivoice-the-open-source-tts-engine-with-2-000+-voices-and-emotion-control)
- [openutau/OpenUtau](https://github.com/openutau/OpenUtau)
- [MediaTek Genio 520 NPU/TFLite Neuron Delegate](https://mediatek.gitlab.io/genio/doc/tao/npu_acceleration.html)
- [MediaTek Genio 520 product page (2x A78 + 6x A55, ~10 TOPS)](https://genio.mediatek.com/genio-520)

（另補充：本報告寫作時同步讀取了專案現有 `server/tts.py` 的實作作為現況基線，路徑：`/home/budaedu/hackathon/talkybuddy/server/tts.py`。）
