# 說說學伴 TalkyBuddy — 隱私與資料最小化說明

> 對象：台灣國小學生語音學習玩具。定位：**PDPA（個資法）為主、COPPA 精神為輔**。
> 本文件為 B4 落地清單 §2.2/§4.1 的明文政策；工程事實對應原始碼行號，可稽核。

## 1. 語音音檔：絕不落地、絕不上雲（已實作事實）

系統**不保存任何語音音檔**。錄音在完成當次回應後立即刪除，資料庫只存 ASR 之後的**文字**：

- webm 暫存檔於 `finally` 區塊清除：`server/pipeline.py:106`
- 轉檔後的 wav 立即 `os.unlink`：`server/pipeline.py:98`、`server/pipeline.py:159`
- 資料庫 `interactions` 表**不含 audio 欄位**，只存 `student_text` / `ai_response_text` 等文字

此設計正落在 **COPPA 2025「audio file exception」** 的核心要求內：僅為回應兒童當次請求而蒐集語音、**回應後立即刪除**、不揭露第三方、不作他用。

## 2. 內容安全護欄：防禦縱深三層

不依賴 LLM 自我審查，分層過濾、任一層漏掉下一層仍能擋（`server/guardrails.py`）：

- **L1 輸入前置**：`scaffold.safety_check` 禁詞比對，命中回安撫話術
- **L2 system prompt 明文**：`guardrails.CHILD_SAFETY_CLAUSE` — 不談暴力/成人/自傷/個資；遇情緒危機先溫柔安撫、鼓勵告訴老師或家人、不追問細節（接進 `EdgeLLM._SYSTEM_PROMPT`）
- **L3 輸出後置**：`guardrails.passes_guardrail()` — LLM 輸出採用前必過此關，不安全即丟棄、降級回確定性 scaffold 輸出（edge 現用、雲端陪聊未來必經）

**誠實限制**：regex 禁詞擋不住諧音/拼音規避與間接誘導；自傷偵測用關鍵字**會漏**真實情緒危機。此屬**法遵／兒少保護顧問層級**，工程只做「偵測→轉安撫→提示找大人」，不宣稱能可靠識別或處置危機。

## 3. 資料上雲最小化與去識別化

- **即時陪聊**：edge 本地生成，**不上雲**。
- **導師診斷（異步）**：有 `ANTHROPIC_API_KEY` 時送近 10 筆 brief，內容僅 `student_text`（去識別化後截 200 字）、`ai_response_text`（截 200 字）、`asr_confidence`、`scores`。**不送** `device_id` / `student_id` / 時間戳。
- **去識別化**：上雲前 `guardrails.deidentify()` 遮罩明顯人名（→`[名字]`）、三位以上連續數字（電話/門牌，→`[數字]`）、中文個資詞（住址/身分證/密碼，→`[個資]`），同時**保留詞庫學習詞**以不影響診斷品質（`server/diagnose.py:_call_anthropic_api`）。
- **斷網 fallback**：無金鑰或雲端失敗 → 本地規則式診斷，資料完全不出境。

| 資料 | 政策 |
|---|---|
| 原始語音音檔 | 絕不落地、絕不上雲（用後即刪） |
| `student_text` 原話 | 本地存；上雲前去識別化 |
| `ai_response_text` / `scores` / `asr_confidence` | 本地存；上雲可（已最小化） |
| `student_id` / `device_id` | 不上雲（本地映射另存） |
| 家長/學生真實姓名、聯絡方式 | **不蒐集** |

## 4. 家長同意（consent）

- demo 階段留工程接口 `consent_granted`（旗標式 gate），啟用雲端路徑前檢查；正式版走**學校/家長書面同意書**（校務系統或紙本）。
- 同意書之法律效力與文案屬**法遵／律師層級**，工程不獨力決定。

## 5. 非工程可獨力解決（誠實標示）

家長同意的法律效力、兒童情緒危機的識別與處置、語音是否屬生物辨識個資與跨境合規、COPPA 是否適用、語意 moderation 模型的偏誤與覆蓋率——皆須**法遵／兒少保護／獨立紅隊**層級，非一次性工程交付。

---
_對應規劃：`research/b_axis/B4_隱私與Guardrails.md`。程式碼行號以 commit 當下為準，重構後請重新核對。_
