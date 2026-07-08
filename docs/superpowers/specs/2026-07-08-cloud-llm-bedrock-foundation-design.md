# cloud_llm 地基：Bedrock FM 收編智慧大腦（子專案 A）最小落地設計

日期：2026-07-08　狀態：設計待複審　分支：feat/dualnet-hybrid
母脈絡：`research/16_Bedrock端到端S2S_可行性評估.md`（路徑 C 混成裁定）、`research/06_雲端助教Agent層.md`（Hermes 已淘汰、無框架主線）、`research/11_B軸智慧教學層_設計交接.md`（雙 Agent 閉環）

---

## 0. 這份 spec 在整體藍圖中的位置

TalkyBuddy 的「多 Agent 智慧教學大腦」願景拆成 5 個可獨立交付的子專案，各自 spec→plan→impl：

| 順序 | 子專案 | 定位 | 狀態 |
|---|---|---|---|
| **A（本檔）** | **cloud_llm + B4 安全地板** | 導師/陪聊接真 Bedrock FM，地基 | 本 spec |
| B | 內容/教材生成 agent | 依 CEFR+興趣動態生成內容，取代靜態 VOCAB | roadmap（demo 主線次位） |
| C | 家長/教師週報 agent | diagnoses+profile → 自然語言敘事 | roadmap（時間夠再做） |
| D | 主動關懷/開場 agent | 依 profile 主動發起開場（依賴 A1 喚醒層） | roadmap（跨軸，暫緩） |
| E | 中央編排 agent | 統一調度 A~D（等 agent 多到需協調才做，保持輕量 in-process） | roadmap（收尾） |

**決賽排序決策（使用者拍板 2026-07-08）**：demo 主線聚焦 **A → B →（時間夠再）C**；**D/E 列 roadmap**（D 卡在 A1 跨軸、E 是收尾優化）。既保住「多 Agent 智慧大腦」完整敘事，又有可交付實體。

**三鐵律（延續研究裁決，全程遵守）**：
1. 不引 Hermes / LangGraph / AutoGen —— 編排用 in-process 輕量控制器（延伸既有 pattern）。
2. 一切走 `network_mode == "cloud"` 分支，斷網/逾時降級回本地路徑（保住決賽現場斷網保命線）。
3. 唯一交界仍是 `pipeline._process_text`；A 軸（雙網/全雙工音訊）完全不碰。

---

## 1. 目標與非目標

**目標**：線上場景（`network_mode == "cloud"`）時，讓**陪聊 Agent 與導師 Agent 的雲端推理都經 Amazon Bedrock 基礎模型（Converse API）**，滿足決賽「指定使用 Bedrock FM」合規硬條件（research/16 §6「大腦 100% 在 Bedrock」），並讓現有雙 Agent 閉環從 mock+規則式升級為真 FM 智慧。與 A1/A2/B1/B2/B3 正交，是最靠近合規 demo 的一步。

**非目標（YAGNI，本步不做）**：
- 陪聊串流首音優化（本步走單一 Converse 請求→完整文字，比照現有非串流契約；串流屬 A2 範疇）。
- Nova Sonic 英文端到端子場景（另立案，research/16 §7 列 stretch）。
- B4 完整版：家長 consent 流程、資料留存政策（本步只做「輸出過濾＋上雲去識別化」安全地板，其餘 roadmap）。
- 子專案 B/C/D/E 的任何 agent。
- 雲端 STT/TTS（維持本地 SenseVoice / sherpa，research/16 §7 決策 1、2）。

---

## 2. 使用者決策（2026-07-08，已確認）

- **Bedrock 現實**＝有/即將有 → `cloud_llm` 直接用 boto3 Bedrock Converse；**但仍保留薄 provider 抽象層＋config flag**，讓測試/開發期不必真打 Bedrock。
- **陪聊模型**＝兩者都支援、flag 切換（Nova Lite / Claude Haiku 皆 config 常數可切）。
- **範圍**＝陪聊＋導師都上 Bedrock（合規完整）。
- **B4 安全地板**＝納入（輸出過濾為兒童產品底線、去識別化模組已存在，接線成本低）；consent/留存留 roadmap。

---

## 3. 讀 code 後的現況事實（設計依據，file:line 已驗證 2026-07-08）

- `pipeline._process_text`（`pipeline.py:188`）在 line 216 呼 `self.llm.generate(asr_text, sc, self._directive)`，**完全不看 `network_mode`**（edge/cloud 都用本地 `EdgeLLM`）→ **陪聊雲端路徑從缺，本步最大的洞**。
- `pipeline.network_mode`（`pipeline.py:120`）預設 `"edge"`；`_refresh_directive`（`pipeline.py:259`）每 `DIRECTIVE_REFRESH_EVERY=5` 個成功回合背景呼 `diagnose.generate_diagnosis`。
- `diagnose.generate_diagnosis`（`diagnose.py:602`）已有雲端路徑，但走 **Anthropic 直連**（`_call_anthropic_api` urllib → `claude-sonnet-5`，`diagnose.py:520`），**非 Bedrock**；失敗 fallback 規則式 mock。
- `server/guardrails.py` **已完整存在**：`CHILD_SAFETY_CLAUSE`（L2）、`passes_guardrail`（L3，`llm.py:152` 已用）、`deidentify`（上雲去識別化）。B4 地板是「接線」非「造輪」。
- `app.py` `/api/network_mode` cloud 分支（`app.py:160`）：`generate_diagnosis` → `add_diagnosis` → 推 `pipeline._directive` → `build_profile`/`save_profile`。

---

## 4. 架構

### 4.1 陪聊即時路徑（`pipeline._process_text` line 216 附近）

```
if network_mode == "cloud" and cloud_llm.available():
    reply = cloud_llm.generate(asr_text, sc, self._directive)   # 先試 Bedrock 陪聊
    if reply is None:                                            # 逾時/例外/命中護欄
        reply = self.llm.generate(asr_text, sc, self._directive) # 降級回本地 EdgeLLM
else:
    reply = self.llm.generate(asr_text, sc, self._directive)     # edge：一行不變
# reply 仍為 None → 既有 scaffold.reply_text 兜底（現況不變）
```

- 現有本地 `EdgeLLM`（`self.llm`）**完全不動**。
- `VoicePipeline.__init__` 多收 `cloud_llm=None`（預設 None → 現有測試/呼叫端向後相容）。
- `directive` 注入介面不變；`cloud_llm.generate` 與 `EdgeLLM.generate` **同契約同簽名**（drop-in）。

### 4.2 導師異步路徑（`diagnose.generate_diagnosis`）

依 `LLM_CLOUD_PROVIDER` 路由，新增 Bedrock 分支，現有 Anthropic 降為 provider 選項，mock 永遠是最終 fallback：

```
provider = config.LLM_CLOUD_PROVIDER          # bedrock | anthropic | off
if provider == "bedrock" and cloud_llm.available():
    d = cloud_llm.diagnose_via_bedrock(interactions, prev)   # Converse + toolChoice 強制 JSON
elif provider == "anthropic" and ANTHROPIC_API_KEY:
    d = _call_anthropic_api(...)                             # 既有路徑
else:
    d = _rule_based_diagnosis(...)                           # mock 主線/兜底
# 任一失敗 → 靜默 fallback 到 _rule_based_diagnosis
```

`companion_directive` / `level_state` 契約**完全不變**（B1/B3 接點不受影響）。

---

## 5. 元件

### 5.1 新模組 `server/cloud_llm.py`

薄 Bedrock Converse provider（boto3），lazy import（無 boto3 時 import 不炸、`available()` 回 False）：

- `available() -> bool`：`LLM_CLOUD_PROVIDER != "off"` 且 boto3 可 import 且憑證鏈可用（bedrock）或 `ANTHROPIC_API_KEY` 存在（anthropic）。
- `generate(student_text, scaffold, directive=None) -> str | None`：**與 `EdgeLLM.generate` 同契約**。組 `_SYSTEM_PROMPT`（抽自 `llm.py` 共用）+ user prompt，呼 `bedrock-runtime.converse(modelId=COMPANION_MODEL_ID, ...)`；逾時（沿用 8s 上限）/例外/空輸出 → None；輸出過 `guardrails.passes_guardrail`（不安全→None）；帶讀護欄（target 未出現則補句）比照 `llm.py:155`。
- `diagnose_via_bedrock(interactions, prev) -> dict | None`：Converse + `toolConfig`/`toolChoice` 強制診斷 JSON schema（弱點四維 + `companion_directive` + `level_state`），解析 `toolUse.input`；經 `diagnose._validate_diagnosis` 收斂；失敗回 None（呼叫端 fallback mock）。
- **上雲前**：對送入雲端的學生文字套 `guardrails.deidentify`（COPPA 安全地板）。

### 5.2 `server/config.py` 新增常數

```python
LLM_CLOUD_PROVIDER = os.environ.get("LLM_CLOUD_PROVIDER", "bedrock")  # bedrock | anthropic | off
BEDROCK_REGION     = os.environ.get("BEDROCK_REGION", "us-east-1")
COMPANION_MODEL_ID = os.environ.get("COMPANION_MODEL_ID", "<nova-lite-id>")     # 可切 haiku id
TUTOR_MODEL_ID     = os.environ.get("TUTOR_MODEL_ID", "<claude-opus-or-sonnet-id>")
```
（實際 model id 於實作時填 Bedrock 官方 id；憑證走 boto3 credential chain，不寫進 repo。）

### 5.3 `server/llm.py`（可選小重構）

把 `_SYSTEM_PROMPT` 與「帶讀護欄補句」抽成模組級可共用（`cloud_llm` 重用），避免兩份 prompt 漂移。若重構風險過高，退而 `cloud_llm` 自行 import 常數即可，行為不變。

### 5.4 接線點

- `pipeline.py`：`__init__` 加 `cloud_llm` 參數；`_process_text` line 216 依 4.1 分支。
- `diagnose.py`：`generate_diagnosis` 依 4.2 路由。
- `app.py`：建 pipeline 時注入 `cloud_llm` 實例（比照 `cloud_tts` 注入 pattern）。

---

## 6. 韌性契約（回退保證）

- 所有新行為僅在 `LLM_CLOUD_PROVIDER != "off"` 且 `available()` 為真時啟動。
- 任何 None/例外/逾時/斷網 → 與**今天完全相同**的降級鏈：cloud → 本地 EdgeLLM → scaffold（陪聊）；bedrock → anthropic → mock（導師）。
- `network_mode == "edge"`、離線 `run_turn_audio`、A 軸音訊、B2/B3 資料流 **零改動**。

---

## 7. 測試（TDD，boto3 全 mock，不打真 Bedrock）

1. `cloud_llm.generate`：mock Converse 回應 → 過濾後回文字；逾時/例外/命中護欄 → None。
2. `LLM_CLOUD_PROVIDER == "off"` → `available()` False → pipeline 走 edge 路徑（既有測試全綠、向後相容）。
3. pipeline cloud 分支：`cloud_llm.generate` 回 None → 降級 `EdgeLLM` → scaffold。
4. `diagnose_via_bedrock`：解析 `toolUse.input` JSON 成合法診斷；失敗 → mock（`generate_diagnosis` 契約不變）。
5. `deidentify` 有套用到上雲學生文字（可觀察 mock 收到的 body 已遮罩）。
6. 向後相容：現有全套（119 passed 基準）維持綠。

---

## 8. 改動檔案清單

- **新**：`server/cloud_llm.py`、`tests/test_cloud_llm.py`、`tests/test_pipeline_cloud.py`
- **改**：`server/config.py`（常數）、`server/pipeline.py`（line 216 分支＋`__init__`）、`server/diagnose.py`（Bedrock 路由）、`server/app.py`（注入 cloud_llm）、`server/llm.py`（可選抽共用）
- **不碰**：A 軸音訊/串流、離線 `run_turn_audio`、B/C/D/E agent、`guardrails.py`（只呼用、不改）、教師端前端

---

## 9. 成功標準 / Demo 驗收

- `network_mode=cloud` 且 provider=bedrock（或開發期 anthropic）時，陪聊回覆由真 FM 生成、且仍遵守「先中文稱讚→帶讀目標句→60 字內→護欄」規則。
- 導師診斷由 Bedrock FM 產出結構化 JSON，`companion_directive` 正常回寫、B1 難度升降維持可展示。
- 拔網/provider=off → 無縫降級回本地，demo 不中斷（合規故事＋斷網保命線並存）。
- 合規答辯（research/16 §6）：可指著 code 說「陪聊＋導師的雲端推理 100% 經 Bedrock Converse」。
