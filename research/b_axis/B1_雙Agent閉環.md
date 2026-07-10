# B1 子題：雙 Agent 閉環設計（導師評估 → 回寫陪聊 directive）

> 日期：2026-07-07
> 範圍：只做 B1（最高優先、離 demo 最近）。把 `diagnose.py` 的導師評估結果，
> 回寫成陪聊 Agent 下一輪的「策略指令 directive」，注入 prompt，達成
> 「玩偶自動調難度／換主題」的自動迭代最小可行展示。
> 基準 code：本檔所有 file:line 皆已於 2026-07-07 實際 Read 驗證。

---

## 0. 一句話結論

現有 code 已具備閉環的兩端：**導師 = `diagnose.generate_diagnosis`（diagnose.py:427）**、
**陪聊 = `scaffold.respond` + `EdgeLLM.generate`（llm.py:102）**。缺的只有中間那條線
——把導師輸出的「策略」變成一個 `companion_directive` 欄位，經 pipeline 快取後，
在即時路徑注入陪聊 prompt。**全部改動集中在 4 個檔、約 5 個函式，皆為「加參數／加欄位」，
不刪任何現有欄位、不破壞降級韌性。**

---

## 1. directive 資料結構與內容（新增，規則式先給）

### 1.1 結構定義

`companion_directive` 是一個 **可選（optional）dict**，掛在 `generate_diagnosis` 回傳的
診斷 dict 底下（與 `instructions` 平行）。欄位如下：

```python
companion_directive = {
    "level": "L2",                 # 由四維平均映射的微階梯字串（展示用，可選）
    "difficulty": "hold",          # "up" | "hold" | "down"，難度方向
    "next_goal": "引導用顏色形容詞描述動物",   # 下一步教學目標（給陪聊看的中文）
    "topic": "農場動物與顏色",              # 本輪要帶的話題
    "example_questions": [                 # 陪聊可自然帶入的英文引導問句（1-3 句）
        "What color is the pig?",
        "Do you see a cat?"
    ],
    "fallback_hint": "若學生沉默或困惑，退回二選一：Is it red or blue?"  # 困惑時降級指引
}
```

設計理由：
- **level / difficulty**：對應「自動調難度」的展示點。difficulty 由分數趨勢推出（見 1.2）。
- **next_goal / topic**：對應「自動換主題」的展示點。
- **example_questions**：給陪聊「怎麼問」的具體彈藥，避免 LLM 空泛發散。
- **fallback_hint**：對應設計交接 §3 的「困惑則退回單字引導／二選一」，是韌性關鍵。

### 1.2 規則式產生邏輯（demo 主線，零依賴）

沿用 `diagnose.py` 內既有的 `flags`（`_detect_patterns` 產出，diagnose.py:144）
與 `scores`、`prev`，不需新資料來源：

- **difficulty** 由趨勢決定（重用 `_build_emotional_status` 的 delta 算法，diagnose.py:214）：
  - `avg - prev_avg >= 1.5` 且 `avg >= 60` → `"up"`（連續流暢 → 升句型）
  - `avg - prev_avg <= -1.5` → `"down"`（遇挫 → 降成二選一）
  - 其餘 → `"hold"`
- **level**：`avg` 分數映射微階梯，例如 `avg<50→L1、50-64→L2、65-79→L3、>=80→L4`。
- **next_goal / topic / example_questions** 由最高優先的 flag 決定（與
  `_build_instructions` 的分流一致，diagnose.py:233）：
  - `article_correction` → goal「冠詞 a/an 穩定使用」、topic「食物與水果」、
    questions 取母音開頭詞（Do you want an apple? / Is it an egg?）
  - `high_chinese_ratio` → goal「整句英文輸出、減少中文替代」、topic「今天的一天」
  - `short_sentences` → goal「把句子講長、加上形容詞」、topic「顏色與數量」
  - 都沒命中 → 依 difficulty：`up` 升級到延伸句型（because/and 複合句），
    否則維持最低分維度的鞏固主題
- **example_questions** 可直接借用 `scaffold._EXTENSION_QUESTIONS`（scaffold.py:152）
  的題庫，避免重造。
- **fallback_hint** 固定模板：`"若學生沉默或困惑，退回二選一或單字引導：{二選一範例}"`。

### 1.3 有 API key 走 Sonnet 生成、失敗 fallback（沿用現有雙軌 pattern）

完全沿用 `diagnose.py` 既有的雙軌：`generate_diagnosis`（diagnose.py:427）已是
「有 `ANTHROPIC_API_KEY` 先試 `_call_anthropic_api`，任何失敗靜默 fallback 規則式」。
B1 只需：
- 在 `_call_anthropic_api`（diagnose.py:355）的 `schema_example`（diagnose.py:371）
  與 prompt（diagnose.py:379）**加上 `companion_directive` 的範例與說明**，讓 Sonnet 一起產出。
- 在 `_validate_diagnosis`（diagnose.py:315）**軟性正規化** `companion_directive`：
  存在且合法就採用；缺失或格式不符 **不丟例外**，改由呼叫端補規則式版本（見 §2.3）。

---

## 2. diagnose.py 增產 companion_directive（不破壞 schema 與 _validate_diagnosis）

### 2.1 新增規則式產生函式

```python
# 微階梯與難度方向常數
_LEVEL_BANDS = ((50, "L1"), (65, "L2"), (80, "L3"), (999, "L4"))

def _avg(scores: dict) -> float:
    return sum(scores[d] for d in _DIM_KEYS) / len(_DIM_KEYS)

def _level_for(scores: dict) -> str:
    a = _avg(scores)
    for hi, name in _LEVEL_BANDS:
        if a < hi:
            return name
    return "L4"

def _build_companion_directive(flags: dict, scores: dict, prev: dict | None) -> dict:
    """依既有 flags/scores/prev 組出陪聊下一輪策略指令（規則式，零依賴）。"""
    # difficulty：重用趨勢 delta 邏輯
    prev_scores = (prev or {}).get("scores") or {}
    if prev_scores:
        old_avg = sum(float(prev_scores.get(d, scores[d])) for d in _DIM_KEYS) / len(_DIM_KEYS)
        delta = _avg(scores) - old_avg
    else:
        delta = 0.0
    if delta >= 1.5 and _avg(scores) >= 60:
        difficulty = "up"
    elif delta <= -1.5:
        difficulty = "down"
    else:
        difficulty = "hold"

    # goal / topic / questions 由最高優先 flag 決定（與 _build_instructions 分流一致）
    if flags["article_correction"]:
        goal = "冠詞 a/an 穩定使用（母音開頭名詞前用 an）"
        topic = "食物與水果"
        questions = ["Do you want an apple?", "Is it an egg or a banana?"]
        fb = "若學生困惑，退回二選一：Is it a or an?"
    elif flags["high_chinese_ratio"]:
        goal = "整句英文輸出、減少中文替代"
        topic = "今天的一天"
        questions = ["What did you eat today?", "How are you today?"]
        fb = "若學生說中文，退回單字引導：先說一個英文單字就好。"
    elif flags["short_sentences"]:
        goal = "把句子講長、加上形容詞或地點"
        topic = "顏色與數量"
        questions = ["What color is it?", "How many do you have?"]
        fb = "若學生只說單字，退回二選一：Is it big or small?"
    elif difficulty == "up":
        goal = "升級句型：用 and / because 造複合句"
        topic = "喜歡的事物與原因"
        questions = ["What do you like and why?"]
        fb = "若學生卡住，退回單句：What do you like?"
    else:
        low_dim = min(_DIM_KEYS, key=lambda d: scores[d])
        goal = f"鞏固{_DIM_ZH[low_dim]}：多做成功經驗"
        topic = "日常問候與自我介紹"
        questions = ["What is your name?", "How old are you?"]
        fb = "若學生困惑，退回二選一。"

    return {
        "level": _level_for(scores),
        "difficulty": difficulty,
        "next_goal": goal,
        "topic": topic,
        "example_questions": questions,
        "fallback_hint": fb,
    }
```

### 2.2 掛進規則式主流程

`_rule_based_diagnosis`（diagnose.py:296）回傳 dict 尾端加一鍵（其餘欄位不動）：

```python
    return {
        "date": _today(),
        "scores": scores,
        "strengths": _build_strengths(scores, interactions),
        "weaknesses": _build_weaknesses(flags, scores),
        "emotional_status": _build_emotional_status(scores, prev, len(interactions)),
        "instructions": _build_instructions(flags, scores),
        "companion_directive": _build_companion_directive(flags, scores, prev),  # 新增
    }
```

### 2.3 API 路徑：軟性正規化，缺失就補規則式

**不改 `_validate_diagnosis` 現有六欄的嚴格驗證**（保持向後相容，現有 `__main__`
自我驗證 diagnose.py:493-527 全數仍過）。只在其結尾加「可選欄位」處理：

```python
def _normalize_directive(cd) -> dict | None:
    """正規化 companion_directive；格式不符回 None（呼叫端補規則式）。"""
    if not isinstance(cd, dict):
        return None
    try:
        qs = cd.get("example_questions") or []
        qs = [str(q) for q in qs if isinstance(q, str) and q.strip()][:3]
        out = {
            "level": str(cd.get("level", "")) or "L1",
            "difficulty": cd.get("difficulty") if cd.get("difficulty") in ("up", "hold", "down") else "hold",
            "next_goal": str(cd.get("next_goal", "")).strip(),
            "topic": str(cd.get("topic", "")).strip(),
            "example_questions": qs,
            "fallback_hint": str(cd.get("fallback_hint", "")).strip(),
        }
        if not (out["next_goal"] and out["topic"]):
            return None
        return out
    except Exception:
        return None
```

在 `_validate_diagnosis` 的 return dict（diagnose.py:345）加一鍵：

```python
        "companion_directive": _normalize_directive(d.get("companion_directive")),
```

（值可能是 None；由呼叫端保底。）

最後在 `generate_diagnosis`（diagnose.py:427）**收斂保底**：不論走 API 或規則式，
最終一定有合法 directive：

```python
def generate_diagnosis(interactions, prev):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    result = None
    if api_key:
        try:
            result = _call_anthropic_api(interactions, prev, api_key)
        except Exception:
            result = None
    if result is None:
        result = _rule_based_diagnosis(interactions, prev)
    # 保底：directive 缺失或 None → 補規則式
    if not result.get("companion_directive"):
        flags = _detect_patterns(interactions or [])
        result["companion_directive"] = _build_companion_directive(flags, result["scores"], prev)
    return result
```

**相容性檢查**：`store.add_diagnosis`（store.py:187）直接 `json.dumps` 整包 dict，
新增鍵自動落地；`list_diagnoses`（store.py:199）原樣讀回。teacher 頁 / `/api/diagnoses`
只是多一個欄位，舊前端忽略即可。**零 migration。**

---

## 3. EdgeLLM.generate 增收 directive 參數並拼進 prompt

### 3.1 簽名與 prompt 注入（llm.py）

`generate` 新增第三參數 `directive: str | None = None`（預設 None ＝ 現況）：

```python
def generate(self, student_text, scaffold, directive: str | None = None):
    ...
    directive_block = f"\n{directive.strip()}\n" if directive and directive.strip() else ""
    user_prompt = (
        f"學生剛剛說：「{student_text}」\n"
        f"目標英文句：{target or ''}\n"
        f"{directive_block}"
        "請照規則回覆：先一句繁體中文稱讚鼓勵，"
        "再用「跟我說一遍：<英文句>」帶讀目標英文句。"
    )
```

即 **在 llm.py:114 的 user_prompt** 中，目標英文句與「請照規則回覆」之間插入 directive 區塊。

### 3.2 directive 文字化（誰負責把 dict 變成文字）

由 **pipeline 端**把 dict 格式化成短字串再傳入（llm.py 對結構保持無知，好測試）。
在 `diagnose.py` 提供純函式供共用（規則式、零依賴）：

```python
def format_directive_for_prompt(cd: dict | None) -> str | None:
    """把 companion_directive dict 壓成注入 prompt 的短中文區塊。"""
    if not cd:
        return None
    qs = " / ".join(cd.get("example_questions") or [])
    diff_zh = {"up": "可升句型", "down": "請降到二選一/單字", "hold": "維持難度"}.get(
        cd.get("difficulty"), "維持難度")
    return (
        "【本輪教學策略】"
        f"目標：{cd.get('next_goal','')}；話題：{cd.get('topic','')}；"
        f"難度：{diff_zh}；可用引導問句：{qs}；"
        f"困惑時：{cd.get('fallback_hint','')}。"
        "請在稱讚後自然帶入此話題與問句；"
        "但「跟我說一遍：」後面仍必須逐字使用目標英文句，不可改寫。"
    )
```

**關鍵護欄**：directive 只影響「稱讚語＋延伸問句」，`target_sentence` 仍由
`scaffold.respond` 決定，且 llm.py:144-146 既有「target 未出現就補上」的保底不變。
避免 directive 把 LLM 帶偏、漏掉帶讀句。

### 3.3 雲端 provider 同理（誠實標註：目前尚未存在）

現況 `app.py:44` 只有 `llm_engine = EdgeLLM()`，**沒有獨立的雲端陪聊 provider**；
`network_mode="cloud"` 目前只觸發 diagnose 同步（app.py:123-155），不切換陪聊 LLM。
因此本子題的雲端側 = **未來新增 `CloudLLM.generate` 時，採同一組簽名
`generate(student_text, scaffold, directive=None)`**，pipeline 呼叫端不必再改。
此為「預留一致介面」，非本次必做。

---

## 4. pipeline._process_text 讀最新 directive（快取、異步更新）

### 4.1 pipeline 持有 directive 快取

`VoicePipeline.__init__`（pipeline.py:111）新增兩個欄位：

```python
        self._directive: str | None = None   # 已格式化的注入字串（即時路徑只讀這個）
        self._turn_count: int = 0            # 累計輪數，用於每 N 輪觸發導師更新
        self._directive_refreshing: bool = False  # 防重入
```

### 4.2 即時路徑：呼叫前讀快取傳入（pipeline.py:205）

`_process_text`（pipeline.py:179）在呼叫 `self.llm.generate` 處，多傳 `self._directive`：

```python
                llm_text = await asyncio.wait_for(
                    asyncio.to_thread(self.llm.generate, result.asr_text, sc, self._directive),
                    timeout=LLM_TIMEOUT_S,
                )
```

即時路徑 **只讀記憶體字串**，零 DB、零網路、零阻塞。

### 4.3 directive 何時更新（異步、不擋即時路徑）

三個更新時機，皆 **在回覆送出後、以背景 task 執行**，導師絕不進即時路徑：

1. **每 N 輪（建議 N=5）**：在 `_process_text` 寫完 DB、`emit idle` 之前／之後，
   `self._turn_count += 1`，達門檻就 `asyncio.create_task(self._refresh_directive())`
   （不 await）。
2. **切換 cloud 時**：`app.py` 的 `/api/network_mode`（app.py:146）本就會產新診斷，
   直接把結果的 directive 塞進 pipeline 快取（見 §5）。
3. **session 結束**：WebSocket 斷線 handler 可再觸發一次（可選）。

背景更新函式（讀 DB → 產診斷 → 存 DB → 更新快取，全在 thread，不擋事件迴圈）：

```python
    async def _refresh_directive(self) -> None:
        if self._directive_refreshing:
            return
        self._directive_refreshing = True
        try:
            from server import diagnose
            def _work():
                recent = store.list_interactions(limit=10)
                diagnoses = store.list_diagnoses()
                prev = diagnoses[-1] if diagnoses else None
                diag = diagnose.generate_diagnosis(recent, prev)
                store.add_diagnosis(diag)   # 持久化（含 companion_directive）
                return diagnose.format_directive_for_prompt(diag.get("companion_directive"))
            self._directive = await asyncio.to_thread(_work)
        except Exception:
            pass  # 更新失敗維持舊 directive（或 None），即時路徑不受影響
        finally:
            self._directive_refreshing = False
```

### 4.4 directive 存哪

- **即時取用**：pipeline 記憶體 `self._directive`（已格式化字串）。
- **持久化**：`companion_directive` 隨診斷寫入 `diagnoses` 表 payload（store.py:187），
  重啟後可由「最新一筆診斷」回填快取（可在 pipeline 首次啟動或 `app.py` 啟動時
  讀 `list_diagnoses()[-1]` 預熱 `self._directive`）。

---

## 5. app.py 接線（cloud 切換時同步更新快取）

`/api/network_mode`（app.py:123）cloud 分支已產出 `new_diag`（app.py:146）。
在 `store.add_diagnosis(new_diag)` 後，加一行把 directive 推進 pipeline 快取：

```python
        store.add_diagnosis(new_diag)
        pipeline._directive = diagnose.format_directive_for_prompt(
            new_diag.get("companion_directive"))
```

（app.py 已 import `diagnose` 與 `pipeline`；此為單行接線。）

---

## 6. 韌性總表（directive=None → 全部退回現況）

| 情境 | 行為 |
|---|---|
| `directive=None`（冷啟動、尚未產生） | `EdgeLLM.generate` 的 `directive_block=""`，prompt ＝ 現況，行為不變 |
| 導師背景更新失敗 | 保留舊 directive（或 None），即時路徑照跑，不拋錯 |
| API（Sonnet）失敗 | `generate_diagnosis` fallback 規則式，`companion_directive` 仍由 §2.3 保底補上 |
| directive 把 LLM 帶偏 | `target_sentence` 仍由 scaffold 決定；llm.py:144-146 保底補帶讀句 |
| LLM 整體不可用 | pipeline 既有降級：用 `scaffold.reply_text`，directive 不生效但無害 |
| 導師誤入即時路徑 | 結構上不可能：即時路徑只讀字串，診斷一律在 `asyncio.to_thread` 背景 task |

---

## 7. 最小改動清單（4 檔）

| 檔案 | 函式 / 位置 | 改動 |
|---|---|---|
| `server/diagnose.py` | 新增 `_build_companion_directive`、`_level_for`、`_avg`、`_normalize_directive`、`format_directive_for_prompt` | 規則式產 directive ＋ 文字化工具 |
| `server/diagnose.py` | `_rule_based_diagnosis`（:296） | 回傳 dict 加 `companion_directive` 鍵 |
| `server/diagnose.py` | `_call_anthropic_api`（:355）`schema_example`/prompt | 讓 Sonnet 一併產出 directive |
| `server/diagnose.py` | `_validate_diagnosis`（:345）、`generate_diagnosis`（:427） | 軟性正規化 ＋ 缺失保底 |
| `server/llm.py` | `EdgeLLM.generate`（:102, prompt 在 :114） | 加 `directive=None` 參數並注入 user_prompt |
| `server/pipeline.py` | `__init__`（:111）、`_process_text`（:179, 呼叫在 :205） | 加 `_directive`/`_turn_count` 快取；傳入 generate；`_refresh_directive` 背景 task |
| `server/app.py` | `/api/network_mode` cloud 分支（:146 後） | 單行：把新診斷 directive 推進 `pipeline._directive` |

**新增測試建議**：
- `diagnose`：斷言輸出含 `companion_directive` 且六欄與趨勢決定 difficulty（up/down/hold）；
  API 缺 directive 時保底補上。
- `llm`：`generate(..., directive=None)` 行為與現況一致；帶 directive 時 prompt 含策略區塊
  （用 stub model 攔截 messages 檢查）。
- `pipeline`：`_directive` 為 None 走現況；設定後傳入 generate；`_refresh_directive`
  不阻塞即時路徑（可用 fake store/diagnose）。
