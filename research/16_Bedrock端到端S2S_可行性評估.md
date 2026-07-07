# 說說學伴 TalkyBuddy — 架構評估：端到端語音大模型（Speech-to-Speech）在 Bedrock 生態的可行性

> 日期：2026-07-08
> 用途：回應一個架構翻轉提問——**把現有 ASR→LLM→TTS 三段管線，改成「端到端語音大模型／全雙工實時音訊流」（模型直接吃音訊、直接吐音訊，<500ms、原生打斷、擬真情感、中英夾雜）**，並確認是否 100% 符合決賽「指定使用 Amazon Bedrock 基礎模型（Foundation Models）」規範。
> 母脈絡：先讀 `research/10`（雙網混成母脈絡）、`research/12`（A2 全雙工設計交接）、`research/13`（framework bake-off＝Pipecat 首選）、`research/15`（雲端編排下 Pipecat 仍勝）、`research/12_B軸研究規劃`（雙 Agent 教學閉環）。本檔只回答「端到端 S2S vs 現有管線」的選型，不重跑 A1／AEC／傳輸拓撲。
> 現況 code 基準：`server/pipeline.py`（`_process_text:188`、`run_turn_audio:134`、`network_mode:120` `edge|cloud`、LLM 注入 `directive:216`、`DIRECTIVE_REFRESH_EVERY:33` 背景導師）、`server/llm.py`（本地 llama.cpp）、`server/diagnose.py`（導師四維診斷）。
> **誠實原則**：所有主張標來源；未實測者標「未驗證」；成本為數量級估算，決賽前以 AWS 官方 pricing 頁複核。本檔為桌面研究＋官方 docs 查證，非上機。

---

## 0. 一句話結論

**端到端 S2S 的「概念」完全正確且誘人，但在 Bedrock 生態對「中文」不可得**——2026 年 Bedrock 上唯一的端到端 S2S 基礎模型是 **Amazon Nova Sonic / Nova 2 Sonic，而它不支援中文**（僅英/法/德/義/西/葡/印地語）。因此對「臺灣國小中英雙語」這個核心情境，端到端 S2S 只能用於**英文子場景**。

**要同時滿足「① Bedrock 合規 ② 中文即時對話 ③ 全雙工體驗 ④ 保住 B 軸教學智慧 ⑤ Genio 520 離線降級」，唯一路徑是「組裝式串流」**：**Bedrock 基礎模型當大腦**（Claude／Nova text）＋ 中文 STT ＋ 中文 TTS，用 **Pipecat** 串接（AWS 官方 blog 背書）。這正是 `research/12`～`15` 已收斂的 **A2 路線**——本提案不是打掉重練，而是**收編並強化 A2**：把本地 llama.cpp 大腦換成 Bedrock FM，就一石二鳥地拿下合規與雲端智慧。

> **裁定：採路徑 C（混成）**。中文主體＝組裝式串流（Bedrock FM 大腦，收編 A2）；英文口說／帶讀子場景＝Nova Sonic 端到端當 demo 亮點；斷網＝現有本地半雙工降級。端到端「純 S2S 取代整條管線」對本專案**否決**（中文不支援＋架空 B 軸）。

---

## 1. 背景：提案的訴求 vs 專案的核心

提案訴求（皆合理）：極致低延遲（<500ms）、擬真語氣情感、中英夾雜、隨時可打斷（barge-in）。

專案不可退讓的核心約束：

| 約束 | 內容 | 為何不可退讓 |
|---|---|---|
| **合規** | 決賽指定使用 Amazon Bedrock 基礎模型 | 評分硬條件 |
| **中文核心** | 臺灣國小雙語，小朋友**主要說中文**、英文帶讀 | 產品定位；放棄＝換題 |
| **B 軸教學智慧** | 雙 Agent 閉環（陪聊＋導師）、四維診斷、CEFR 鷹架、教師端雷達圖——**全部依賴逐字文字** | 這是教學價值與差異化，非聊天玩具 |
| **離線降級** | Genio 520 斷網仍能本地互動 | 雙網混成的招牌優雅性 |

**端到端 S2S 與後兩者天生衝突**：純 S2S 無文字中介 → 架空 B 軸；純雲端 → 無離線。

---

## 2. 事實地基：2026 年 Bedrock 對「中文語音」到底有什麼

| 能力 | Bedrock 現況（2026） | 對本專案 |
|---|---|---|
| **端到端 S2S** | 只有 **Nova Sonic／Nova 2 Sonic**；語言＝英/法/德/義/西/葡/印地語，**無中文**；polyglot／code-switching 僅在支援語言間 | 中文主場景 ❌；英文子場景 ✅ |
| **直接吃音訊的 LLM** | **Gemini 不在 Bedrock**（AWS 官方：可能永遠不會；僅開源 Gemma 在）；Claude 4.x 吃文字＋影像、**不吃即時語音**；Nova Pro 偏 image/video NLU | 沒有「中文語音直接進 LLM」這條路 |
| **中文語音轉錄／分析** | **Bedrock Data Automation (BDA)** 2025-11 起支援中文（含**繁中／粵語**）語音分析＋GenAI 摘要 | 屬**批次/分析**、非即時串流；可作課後診斷素材，不適合即時互動 |
| **組裝式即時語音** | AWS **官方 blog：Building intelligent AI voice agents with Pipecat and Amazon Bedrock** | ✅ 官方背書；正好對上 A2 已定案的 Pipecat |
| **雲端中文 TTS** | Amazon **Polly**（Neural，含中文；繁中童聲品質待驗） | 可當雲端 TTS 選項 |
| **中文 STT（AWS）** | Amazon **Transcribe** streaming（含中文）；或本地 **SenseVoice**（已驗證繁中 CER 2.8%） | 兩者皆可；非「Bedrock FM」但屬 AWS 生態 |

**核心事實**：Bedrock **沒有**中文端到端語音模型。中文即時對話只能組裝，且「大腦」用 Bedrock FM 即滿足合規主軸。

---

## 3. 三條路徑對比

評分：✅ 佳／滿足 · 🟡 可但有摩擦 · ⚠️ 明顯風險 · ❌ 不可行或致命缺陷

| 維度 | **A. 純端到端 S2S** | **B. 組裝式串流（底座）** | **C. 混成（推薦）** |
|---|---|---|---|
| 大腦 | Nova Sonic | Bedrock Claude/Nova text | 中文走 B、英文走 A |
| **Bedrock 合規** | ✅ 是 FM | ✅ 大腦是 FM | ✅ |
| **中文核心** | ❌ 不支援 | ✅ STT+LLM+TTS | ✅ |
| **B 軸（需文字）** | ❌ 架空 | ✅ 保留文字中介 | ✅ |
| **延遲（首音）** | ✅ ~1.09s、部分 <500ms | 🟡 ~0.8–1.5s | 分場景 |
| **barge-in／情感** | ✅ 原生 | 🟡 靠本地 VAD 組（A2 已研究） | 英文原生、中文組 |
| **Genio 520 離線** | ❌ 純雲端 | ✅ 可降回本地半雙工 | ✅ |
| **對既有投資** | ⚠️ 全推翻 A2/B 軸 | ✅ 完全相容 A2/B1 | ✅ 收編強化 |
| **demo 驚艷度** | ✅✅ 真端到端 | 🟡 良好 | ✅ 英文段落亮眼 |

**A 否決**：中文不支援＋架空 B 軸，兩個致命缺陷。**B 是務實底座、C 在 B 之上加英文亮點**。

---

## 4. 成本與延遲估算（數量級，決賽前複核）

**延遲（使用者說完 → 首個語音回應）**

| 路徑 | 估算 | 依據 |
|---|---|---|
| Nova Sonic（英文） | ~1.09s，部分實作 <500ms | AWS 官方 |
| 組裝式中文（雲 LLM） | STT ~0.1–0.5s ＋ Bedrock LLM TTFT ~0.3–0.6s ＋ TTS 首段 ~0.1–0.3s ≈ **0.8–1.5s**；barge-in 靠本地 VAD ≈ 即時 | 概估／未驗證 |
| 本地離線（半雙工批次） | 現有實測單輪 ~1.9s | 專案實測 |

**成本（per 1M tokens，in/out；Bedrock，數量級）**

| 模型／服務 | 概估 | 角色 |
|---|---|---|
| Nova Lite | ~$0.06 / $0.24 | 陪聊候選（便宜快） |
| Claude Haiku 4.x | ~$1 / $5 | 陪聊候選（品質） |
| Claude Opus 4.x | ~$15 / $75 | 導師（每 5 輪一次、讀 10 筆，量小） |
| Nova Sonic | $3 / $12（speech tokens，~$0.015/min） | 英文子場景 |
| Amazon Polly Neural | ~$16 / 1M chars | 雲端 TTS 選項 |
| Amazon Transcribe streaming | ~$0.02–0.03/min | 雲端 STT 選項 |

**demo 量級成本可忽略**：一場 demo 數十輪對話，Bedrock text 幾分錢、Nova Sonic 幾分鐘約 $0.05、導師 Opus 每次 $0.1 以下。成本不是決策因素，延遲與合規才是。

---

## 5. 推薦架構（路徑 C）與資料流

```
[ 邊緣：說說學伴玩偶 / Genio 520 ]
        │  A1 喚醒（Porcupine，喚醒前音訊不出裝置）
        ▼
   ┌── 線上 (edge→cloud 切換, pipeline.network_mode) ──────────────┐
   │  Pipecat in-process 編排（research/12 拓撲一，裝置端）           │
   │                                                              │
   │  中文主體：本地 VAD(Silero) barge-in                           │
   │    ├ STT: 本地 SenseVoice（繁中 CER 2.8%）／或雲 Transcribe     │
   │    ├ 大腦: ★Bedrock FM★ 陪聊 Nova Lite/Claude Haiku (Converse) │
   │    │        導師 Claude Opus 非同步（diagnose.py→directive 回寫）│
   │    └ TTS: 本地 sherpa-onnx/Kokoro 句級串流／或 Polly           │
   │                                                              │
   │  英文口說子場景：Nova Sonic 端到端（Bedrock 原生 S2S FM）        │
   │    真 <500ms + 原生 barge-in + 情感（demo 亮點；同時回 transcript）│
   └──────────────────────────────────────────────────────────────┘
        │  斷網：cancel() 拆管，路由切回
        ▼
   [ 離線降級：現有 run_turn_audio 本地半雙工，一行不動 ]
```

**三個關鍵設計點：**
1. **大腦＝Bedrock FM**：把 `llm.py` 的本地 llama.cpp，在 `network_mode=cloud` 時換成 Bedrock Converse API（新 `cloud_llm.py`，走 boto3）。這是合規的核心，也是與 A2／B1 **正交**的最小改動。
2. **B 軸原封不動**：組裝式保留逐字文字 → `diagnose.py` 導師、`companion_directive` 回寫、教師端雷達圖全部照跑。Nova Sonic 英文段落用其 bidirectional API 的 **transcript 事件**補回文字，B 軸不斷線。
3. **離線是保險**：決賽現場網路不穩時，`run_turn_audio` 本地半雙工兜底，雙網混成招牌不倒。

---

## 6. 合規答辯策略（修正版，可直接進 PPT）

1. **大腦 100% 在 Bedrock**：陪聊 Agent 與導師 Agent 的推理**全部**經 Bedrock 基礎模型（Claude／Nova）Converse API，滿足「指定使用 Bedrock 基礎模型」。
2. **展示 Bedrock 原生 S2S**：英文口說子場景用 **Nova Sonic**，向評審證明我們用到了 Bedrock **最新的語音基礎模型**，直接讀音訊特徵、保留語調情感容錯——而非只當文字聊天機器人。
3. **Genio 520 綠葉配襯**：聯發科邊緣負責 KWS 喚醒、硬體解碼、斷網本地 Fallback；複雜教學邏輯與雲端語音大腦留在 Bedrock。既捧硬體贊助商，又命中軟體指定題。
4. **兒童隱私（COPPA）＝ B4 橫切**：資料留 AWS 安全邊界；雲端路徑加 consent／去識別化過濾。

> ⚠️ **切勿沿用原 ChatGPT 版答辯**：它以「Gemini on Bedrock 直接吃音訊」為核心賣點，而 **Gemini 不在 Bedrock**，現場會被一句話戳破（見附錄）。

---

## 7. 風險與待人拍板

| # | 決策點 | 選項 | 初步建議 |
|---|---|---|---|
| 1 | 中文 STT | 本地 SenseVoice ／ 雲 Transcribe | **本地 SenseVoice**（已投資、繁中佳、離線共用）；雲端只在合規故事需要「純 AWS」時考慮 |
| 2 | 中文 TTS | 本地 sherpa/Kokoro ／ Polly | **本地**（童聲、離線共用）；Polly 當雲端備援 |
| 3 | 陪聊 LLM | Nova Lite ／ Claude Haiku | 先 **Nova Lite**（便宜快、亦是 Bedrock FM），品質不足再升 Haiku |
| 4 | Nova Sonic 英文子場景 | 進 MVP ／ 列 stretch | **列 stretch demo 亮點**；先確保中文組裝式主線 |
| 5 | 兒童語音上雲隱私 | consent 預設、去識別 | 隨雲端 provider 引入補（B4） |
| 6 | Nova Sonic 區域 | 目前 us-east-1 | 台灣 RTT ~200–300ms，疊在 1.09s 上；英文子場景可接受，勿當中文主線 |
| 7 | 決賽現場網路 | 現場 Wi-Fi 可靠性 | 離線降級務必可 demo（斷網也能玩） |

---

## 8. 建議下一步（若採 C）

**最小合規落地（與 A2／B1 正交，風險最低）**：
- 新增 `server/cloud_llm.py`：`network_mode=cloud` 時，`_process_text` 的 LLM 加值改呼 Bedrock Converse（boto3），逾時／斷網一律降級回 scaffold／本地。保留 `directive` 注入介面不動。
- 這一步**不碰** A1／A2 串流／B 軸，即可讓「大腦在 Bedrock」成真、demo 可展示合規。

**後續疊加（各自 spec→plan）**：
1. A2-1 spike（Pipecat × SenseVoice × 句級 sherpa，串流全雙工）——`research/12` 既定。
2. Nova Sonic 英文口說子場景（Bedrock 原生 S2S 亮點）。
3. B4 Guardrails／COPPA（雲端 provider 引入時）。

> 本評估的產物是**選型決策**，非單一實作 spec。採 C 後，先對「最小合規落地（cloud_llm）」走 brainstorming→spec 最靠近 demo；A2／Nova Sonic 子場景各自獨立立案。

---

## 附錄：原 ChatGPT 版建議的事實更正（存證，避免日後重犯）

| 原建議宣稱 | 事實 | 來源 |
|---|---|---|
| 「Gemini 1.5 Pro/Flash 在 Bedrock，可直接吃音訊」⭐⭐⭐⭐⭐ | ❌ **Gemini 不在 Bedrock**（AWS 官方：可能永遠不會；僅開源 Gemma 在）。整條「最佳路徑」是幻覺 | AWS Bedrock supported models |
| 「Claude 3.5 Sonnet/Haiku」 | ⚠️ 版本過時（2026 已 Claude 4.x）；Claude 在 Bedrock **不吃即時語音**（文字＋影像 frames）。當導師文字分析可，當陪聊直接聽音訊不可 | Bedrock model catalog |
| 「雲端過渡層跑 TEN Framework」 | ❌ A2 研究**已剔除 TEN**（LICENSE clause 1(i) 禁 host 於 End User devices＋無 pip）；framework **已定案 Pipecat** | research/13、15 |
| 「端到端語音」用 Gemini 達成 | ❌ Bedrock 唯一端到端 S2S 是 **Nova Sonic，且無中文** | AWS Nova 2 Sonic 語言支援 |
| 「Amazon Polly TTS」 | ✅ 合規可用，繁中童聲品質待驗；主體已定案本地 sherpa | — |
| 「大腦 100% 在 Bedrock、資料不出 AWS」答辯 | ✅ 邏輯成立——**前提是用真的 Bedrock 模型**，不是 Gemini | — |

## 來源

- Bedrock 支援模型清單：https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html
- Nova 2 Sonic 語言支援：https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-language-support.html
- 介紹 Nova Sonic（端到端 S2S）：https://aws.amazon.com/blogs/aws/introducing-amazon-nova-sonic-human-like-voice-conversations-for-generative-ai-applications/
- 介紹 Nova 2 Sonic：https://aws.amazon.com/blogs/aws/introducing-amazon-nova-2-sonic-next-generation-speech-to-speech-model-for-conversational-ai/
- Pipecat + Bedrock 語音 agent（官方）：https://aws.amazon.com/blogs/machine-learning/building-intelligent-ai-voice-agents-with-pipecat-and-amazon-bedrock-part-1/
- Bedrock Data Automation 中文語音支援：https://aws.amazon.com/about-aws/whats-new/2025/11/amazon-bedrock-data-automation-10-languages/
