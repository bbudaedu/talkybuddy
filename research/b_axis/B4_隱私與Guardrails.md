# 說說學伴 TalkyBuddy — B4：Guardrails / 隱私（COPPA，橫切）

> 日期：2026-07-07
> 軸線：B 軸（智慧/教學層）子題 B4 —— 內容安全與兒童隱私合規（橫切）。
> 定位：兒童玩具的內容 guardrails + 隱私最小化，落地成「本專案 demo 可執行」的清單。
> 母脈絡：先讀 `research/11_B軸智慧教學層_設計交接.md`。本檔只談 B4，不碰 A 軸音訊傳輸、不改 B1 閉環語意。

---

## 0. 一頁摘要與裁決

**核心洞察：本專案架構在隱私上「先天體質不錯」，缺的是把既有事實變成明文政策與補上雲端側的護欄。**

三個「現有 code 已經做對」的事實（誠實標示，非本次新增）：

1. **語音音檔不落地**：pipeline 轉檔後 `os.unlink(wav_path)`（`pipeline.py:150-152`），webm 暫存檔 `finally` 清除（`pipeline.py:99-105`），DB 只存 ASR 後的**文字**、不存 audio。這正好符合 COPPA 2025 新增「audio file exception」的核心要求——**回應後立即刪除語音**。
2. **LLM 輸出已過安全檢查**：`EdgeLLM.generate` 產出後會再跑一次 `scaffold.safety_check`，命中即回 `None` 降級（`llm.py:136-142`）。這已是「不讓 LLM 自我審查、用外部規則過濾輸出」的雛形。
3. **雲端診斷已最小化＋斷網不外洩**：`diagnose._call_anthropic_api` 只送 `student_text`/`ai_response_text`（各截斷 200 字）＋`asr_confidence`＋`scores`，**不送 `device_id`/`student_id`**（`diagnose.py:362-370`）；任何失敗靜默 fallback 本地規則式（`diagnose.py:437`）。

四個「需新增」的缺口：

- **A. Cloud 陪聊輸出無護欄**：目前只有 edge `llm.py` 的輸出過 `safety_check`。B1 若引入雲端 provider 生成陪聊回覆，該輸出**尚未**經過同一道過濾。
- **B. system prompt 無明文兒童安全條款**：`_SYSTEM_PROMPT`（`llm.py:44`）只規範格式（繁中+英文、無 markdown/emoji、≤60 字），**沒有**「不談暴力/成人/自傷、遇情緒危機轉安撫」的明文護欄。
- **C. 禁詞是 regex 子字串比對、只有 27 條**（`scaffold.py:113-121`）：擋得住明文關鍵字，擋不住間接誘導與情緒危機語意；無自傷/情緒的語意層偵測。
- **D. 無留存政策、無家長同意 gate**：interactions 永久留存無 TTL；`student_text` 原文（可能含人名/地名）直送雲端未去識別化；無 consent 旗標。

**裁決**：demo 階段用「防禦縱深三層（既有兩層 + 補 system prompt 明文）＋ 資料分類最小化 ＋ 去識別化 scrub」即可交付可展示、可解釋的合規故事；家長同意與正式留存政策屬**法遵顧問層級**，工程只做「留好接口與旗標」。

---

## 1. 內容 Guardrails：防禦縱深（defense in depth）

業界共識是**分層過濾**（pre-filter 輸入 → system prompt 約束 → post-filter 輸出），**不依賴 LLM 自我審查**，每層獨立、任一層漏掉下一層還能擋（見文末來源 Datadog / Palo Alto Unit 42 / LLMGateway）。本專案已具備第 1、3 層雛形，缺第 2 層明文與雲端側覆蓋。

### 1.1 現況盤點（誠實對照）

| 層 | 位置 | 現況 | 缺口 |
|---|---|---|---|
| L1 輸入前置過濾 | `scaffold.safety_check`（`scaffold.py:288-293`），於 `pipeline._process_text` 呼叫（`pipeline.py:194`）＋ `respond` 內再檢查（`scaffold.py:448`） | **已有**：命中禁詞回安撫話術 `_SAFETY_REPLY_ZH/EN`（`scaffold.py:123-127`），不給學習目標句 | 禁詞僅 27 條、regex 子字串/詞邊界；無語意分類 |
| L2 system prompt 約束 | `EdgeLLM._SYSTEM_PROMPT`（`llm.py:44-52`） | 部分：格式類護欄齊全 | **無兒童安全明文**（暴力/成人/自傷/個資/情緒危機處置） |
| L3 輸出後置過濾 | edge：`llm.py:136-142` 已對 LLM 輸出跑 `safety_check` | edge **已有** | cloud 陪聊輸出**未覆蓋**（缺口 A） |

### 1.2 設計：三層強化（低侵入、維持降級韌性）

**L1 輸入（維持現況，可選增補）**
- 保留 `safety_check` 作為第一道；demo 不需擴充清單即可展示。
- 之後可做：把 `FORBIDDEN_ZH/EN` 抽成外部設定檔並分級（暴力/成人/自傷/個資），命中「自傷/情緒危機」類時走**專屬升級話術**（引導找老師/家人），而非通用安撫。

**L2 system prompt 明文護欄（本次重點、最小改動）**
- 在 `llm.py:44 _SYSTEM_PROMPT` 尾端追加一段固定護欄句（不影響現有格式規則），例如：
  > 「五、你的對象是國小兒童：不得談論暴力、血腥、成人、藥物、恐怖內容；不得索取或覆述姓名、住址、電話、學校等個人資料。六、若學生表達難過、害怕、想傷害自己或被欺負，先用繁體中文溫柔安撫，鼓勵他告訴老師或家人，不要追問細節、不要給處置建議。」
- 同一段護欄字串**同步用於 B1 雲端 provider**（若引入），避免雲/邊護欄不一致。建議把這段抽成 `scaffold.py` 或新 `guardrails.py` 的常數 `CHILD_SAFETY_CLAUSE`，`llm.py` 與雲端 provider 共用同一份。

**L3 輸出後置過濾（補雲端覆蓋，關閉缺口 A）**
- 現有 edge 路徑（`llm.py:136-142`）保持不變。
- **關鍵接點**：B1 若在 `pipeline._process_text`（`pipeline.py:200-214`）加入雲端陪聊生成，**該雲端輸出必須在採用前也跑 `scaffold.safety_check`**，命中即丟棄、fallback 回 `sc.reply_text`（scaffold 規則輸出是確定性、來自固定詞庫，本質安全）。建議把「LLM 文字 → safety_check → 採用或降級」收斂成一個 helper（例如把 `llm.py` 內的檢查邏輯上抽為 `_passes_guardrail(text) -> bool`），edge/cloud 共用。

### 1.3 誠實限制
- regex 禁詞**擋不住**：拼音/諧音規避、間接誘導、情境式不當內容。demo 可接受（對象是國小帶讀、輸入受限於鷹架情境）；正式產品需接語意分類器（本地小模型或雲端 moderation endpoint），屬 B4「之後做」。
- self-harm 偵測用關鍵字**會漏**真實情緒危機。這是**法遵/兒少保護顧問層級**議題，工程只能做到「偵測到就轉安撫並提示找大人」，不可宣稱能識別或處置危機。

---

## 2. 兒童隱私：COPPA 精神 + 台灣情境

### 2.1 法規對照（重點事實）

**COPPA 2025 修正（美國，FTC；2025-06-23 生效、2026-04-22 前須遵循）**——本專案非美國服務，但採其**精神**作為兒童玩具的自律基準：
- **新增 audio file exception**：可在**不取得可驗證家長同意（VPC）**下蒐集含兒童聲音的音檔，**但**須：僅為回應兒童當次請求、不蒐集其他個資、**回應後立即刪除音檔**、不對第三方揭露、不作他用；且須在隱私政策揭露此用途與立即刪除。→ **本專案 pipeline 的 wav/webm 用後即刪（`pipeline.py:150-152`, `99-105`）＋ DB 不存 audio，正好落在此例外內。**
- 資料**最小化**、**留存期限**限制、限制以兒童資料變現。
- 擴充 VPC 方法：knowledge-based auth、臉部比對、text-plus。

**台灣個資法（PDPA）**：
- 未滿 7 歲：由**法定代理人（家長）**代為意思表示；滿 7 歲以上原則須法代允許。
- 蒐集/處理/利用須符合**特定目的**、不逾越必要範圍、與目的有正當合理關聯（最小化原則）。
- 學校情境：屬教育目的，仍須告知與同意；語音無專法明文，回歸一般個資規範。

（本專案硬體目標 Genio 520、對象台灣國小生，故以「PDPA 為主、COPPA 精神為輔」定位。）

### 2.2 資料分類：可上雲 vs 只留本地（落地表）

以「敏感度 × 是否為完成功能所必需」分類。**預設本地優先，上雲須去識別化＋最小化。**

| 資料 | 現存位置 | 敏感度 | 政策裁決 |
|---|---|---|---|
| 原始語音音檔（webm/wav） | 暫存檔，用後即刪 | 高（生物辨識風險） | **絕不落地、絕不上雲**（現況已符合，維持） |
| `student_text`（ASR 後原話） | `store` interactions（`store.py:116`） | 中高（可能含人名/地名） | 本地存；**上雲前須 PII scrub**（見 §3） |
| `ai_response_text` | 同上 | 低 | 本地存；上雲可（已截斷 200 字） |
| `scores` 四維分數 | 同上 | 低（去識別後為抽象數值） | 上雲可 |
| `asr_confidence`/`latency_ms` | 同上 | 低 | 上雲可 |
| `student_id`（化名 `STUDENT-AMING-004`） | `store.py:34` | 中（化名，非真名） | **不上雲**（diagnose 已不送，維持）；本地映射表另存 |
| `device_id`（`GENIO-520-X992`） | `store.py:33` | 中 | **不上雲**（diagnose 已不送，維持） |
| 診斷結果 `diagnoses` | `store` diagnoses 表 | 低 | 本地存；老師端呈現 |
| 家長/學生真實姓名、聯絡方式 | **目前 code 完全未蒐集** | 高 | **維持不蒐集**；若未來加入須 consent gate |

**結論**：目前 code 上雲的只有 diagnose 的 `student_text`＋`ai_response_text`＋分數，且不含 `student_id`/`device_id`——**已接近最小化**，只差 `student_text` 的去識別化。

### 2.3 家長同意（consent）

- demo 階段：**不做真正的 VPC**（屬法遵層級），但工程留接口：`store` 或 config 加一個 `consent_granted: bool` / `consent_ts` 欄位，pipeline 在**啟用雲端路徑前**檢查此旗標（未同意則強制 edge-only）。
- 台灣落地：實務走**學校/家長書面同意書**（紙本或校務系統），非 app 內流程；工程端只需「有無同意」的布林 gate，不需自建 VPC。此為**法遵顧問層級**，工程不獨力決定同意文案與法律效力。

---

## 3. 雙網混成資料流：online 送什麼、如何去識別化

### 3.1 現況資料流（已驗證）

- **陪聊即時路徑**：edge LLM（`llm.py`）本地生成，**不上雲**（現況）；B1 若加雲端 provider，屬新增資料流，須套用本節規則。
- **導師異步路徑**：`generate_diagnosis`（`diagnose.py:427`）→ 有 `ANTHROPIC_API_KEY` 時 `_call_anthropic_api`（`diagnose.py:355`）送**近 10 筆 brief** 到 Anthropic Messages API。brief 內容（`diagnose.py:362-370`）：`student_text`（截 200）、`ai_response_text`（截 200）、`asr_confidence`、`scores`。**不含** `device_id`/`student_id`/`ts`。`prev`（前次診斷）也送，內含分數與文字建議，無直接 PII。

### 3.2 風險與最小化設計（接點明確）

**風險**：`student_text` 是兒童原話，示範資料就出現「她叫 Mimi」（`store.py:456`）這類**人名**；真實使用可能出現同學名、地名、學校名。直送雲端 = 兒童個資出境。

**設計：上雲前去識別化 scrub（新增，最小改動）**
- 在 `diagnose._call_anthropic_api` 組 `brief` 前（`diagnose.py:361` 之前）加一道 `_deidentify(text) -> str`：
  - 移除/遮罩：明顯人名（詞庫外的中文專有名詞、英文大寫專名如 `Mimi` → `[名字]`）、數字串（電話/門牌）、`scaffold.FORBIDDEN_ZH` 中的個資類詞（住址/電話號碼/身分證/密碼）。
  - 保留學習訊號：食物/動物/顏色等詞庫詞（`scaffold.VOCAB`）不動，才不影響診斷品質。
- 這道 scrub 純字串處理、零依賴，與 diagnose「純標準函式庫」原則一致；失敗不影響主流程（沿用現有 try/except fallback）。
- **量的最小化**：目前送近 10 筆已是上限；可再降為「只送分數＋去識別文字摘要」，但 demo 保留 10 筆文字以維持診斷品質，取捨標示清楚即可。

**斷網 fallback（現況已對，維持）**：無金鑰或雲端失敗 → 本地規則式診斷（`diagnose.py:437-440`），資料不出境。這本身就是最強的隱私保護：**online 才送、且只送去識別化摘要；offline 全本地**。

---

## 4. 落地清單：本專案 demo 階段

### 4.1 必做（demo 前，工程可獨力、低風險、接點明確）

1. **[L2] system prompt 補兒童安全明文**：`llm.py:44 _SYSTEM_PROMPT` 追加暴力/成人/自傷/個資 + 情緒危機安撫條款；抽成共用常數 `CHILD_SAFETY_CLAUSE`（`scaffold.py` 或新 `guardrails.py`）。
2. **[L3] 雲端輸出補過濾接口**：把 `llm.py:136-142` 的「輸出過 safety_check」邏輯上抽為 helper，B1 雲端陪聊輸出採用前**必經**此 helper；命中則 fallback `scaffold.reply_text`。
3. **[§3] diagnose 上雲前去識別化**：`diagnose.py:361` 前加 `_deidentify()`，遮罩人名/電話/住址類，保留詞庫學習詞。
4. **[§2.2] 語音不落地寫成明文政策**：在 README/隱私說明記錄「音檔用後即刪、DB 只存文字」，對應 `pipeline.py:150-152`、`99-105`——**這是已實作事實，只需文件化**。
5. **[§2.3] consent gate 旗標**：config/store 加 `consent_granted` 布林；pipeline 啟用雲端路徑前檢查（未同意→edge-only）。demo 可預設 `True` 並註明「正式版接學校同意書」。

### 4.2 之後做（正式產品、部分需法遵/顧問）

6. 禁詞清單外部化＋分級，自傷/情緒危機走專屬升級話術（找老師/家人）。
7. 語意層 moderation（本地小分類器或雲端 moderation endpoint），補 regex 之不足。
8. 留存政策：interactions 加 TTL / 定期清除機制（如保留 N 天），對應正式留存期限要求。
9. 正式家長同意流程（學校書面/校務系統）與隱私政策揭露——**法遵層級**。
10. 資料主體權利（查詢/刪除/更正）介面——PDPA 當事人權利。

---

## 5. 誠實標示：哪些非工程可獨力解決

- **家長同意的法律效力與文案**：屬**法遵/律師層級**。工程只能提供 `consent_granted` gate 與揭露文字位置，不能自訂具法律效力的同意書。
- **兒童情緒危機/自傷的識別與處置**：屬**兒少保護/心理專業**。工程能做「偵測關鍵字→轉安撫→提示找大人」，**不可宣稱**能可靠識別或處置危機；誤報/漏報皆有真實傷害風險。
- **語音是否屬生物辨識個資、跨境傳輸合規**：屬**法遵層級**。本專案以「音檔不落地、不上雲」規避此風險，但一旦未來上傳任何音檔即須重新評估。
- **COPPA 對本專案是否適用**：本專案定位台灣，主要遵 PDPA；是否觸及 COPPA（若服務觸及美國兒童）須**法律意見**，本檔僅採其精神為自律基準。
- **語意 moderation 模型的偏誤與覆蓋率**：需獨立紅隊測試與持續評估，非一次性工程交付。

---

## 6. 來源

- [FTC Amends COPPA Rule (2025) — Davis Wright Tremaine](https://www.dwt.com/blogs/privacy--security-law-blog/2025/05/coppa-rule-ftc-amended-childrens-privacy)
- [Unpacking the FTC's COPPA amendments — White & Case](https://www.whitecase.com/insight-alert/unpacking-ftcs-coppa-amendments-what-you-need-know)
- [FTC Finalizes Changes to Children's Privacy Rule (2025)](https://www.ftc.gov/news-events/news/press-releases/2025/01/ftc-finalizes-changes-childrens-privacy-rule-limiting-companies-ability-monetize-kids-data)
- [Complying with COPPA: FAQ — FTC](https://www.ftc.gov/business-guidance/resources/complying-coppa-frequently-asked-questions)
- [LLM guardrails best practices — Datadog](https://www.datadoghq.com/blog/llm-guardrails-best-practices/)
- [Comparing LLM Guardrails across GenAI platforms — Palo Alto Unit 42](https://unit42.paloaltonetworks.com/comparing-llm-guardrails-across-genai-platforms/)
- [LLM Guardrails Explained — LLM Gateway](https://llmgateway.io/blog/llm-guardrails-explained)
- [個人資料保護法 — 全國法規資料庫](https://law.moj.gov.tw/LawClass/LawAll.aspx?PCode=I0050021)
- [論我國兒童數位個資之隱私保護 — 立法院](https://www.ly.gov.tw/Pages/Detail.aspx?nodeid=6590&pid=221269)
