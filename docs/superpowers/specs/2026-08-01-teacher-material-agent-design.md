# 老師教材提煉 agent（子專案 F）— 設計

> 2026-08-01 ／ 決賽當日設計，距上台約 6+ 小時
>
> 一句話：新增第四個 agent `server/agents/material.py`，把老師貼上的教材文字
> 提煉成跟現有 `scaffold.VOCAB` 同 schema 的詞條，**原地合併**進同一個全域字典，
> 讓現有三個遊戲、派作業、SRS、學生 profile **零改動**自動吃到新教材。
> 長期記憶與教師回饋不重造——沿用既有 `store.py`／`profile.py`／`srs.py`／
> `report.py`／`/teacher` 儀表板。

## 1. 問題

需求是「雙向互動：學生學習成果回饋老師、老師也能上傳教材」，並要求教材經
agent 提煉後轉化成跟孩子互動的聊天任務或遊戲主題，且要有長期記憶記錄學習狀況。

現況：`server/scaffold.py` 的 `VOCAB` 是**寫死**的 136 個課綱詞條，是全專案唯一
教材來源；沒有任何「老師上傳教材」的輸入路徑。而「長期記憶」與「回饋老師」
其實已經有一整套成熟機制（`profile.py` 學生畫像、`srs.py` 間隔重複複習、
`report.py`／`homework.py`／`orchestrator.py` 三個既有 agent 產出教師儀表板
內容）。真正缺的只有「教材輸入 → 提煉 → 進入現有詞庫」這一段。

## 2. 時程與範圍的現實檢查

決賽是**今天**（2026-08-01），且 `AgentCore` 目前從未 provision、憑證仍失效
（見 `project_agentcore_state_2026-08-01` 記憶）。因此本設計的雲端路徑比照
既有三個 agent，走 `agent_backends.resolve()` 的既定降級鏈
**AgentCore Harness → Bedrock Converse → 規則式**——架構上誠實地把 AgentCore
留在鏈首，但**今天的 demo 預期會落在 Bedrock 或規則式**，不賭 AgentCore 現場
突然能用。

明確排除（超出今天範圍，決賽現場不要講成已完成）：

- 檔案／PDF／圖片上傳與 OCR（只做純文字貼上）
- 多老師／多班級教材隔離（教材是全域共用詞庫的擴充，不分學生）
- 教材編輯或刪除
- 真正的雲端語意提煉品質評測
- `/teacher` 儀表板新增教材列表或單一教材統計 UI（沿用既有週報/作業卡片）

## 3. 為什麼「原地合併進 VOCAB」是對的取捨

`scaffold.VOCAB` 是一個模組層級的 dict 物件，`homework.py`／`games.py`／
`profile.py` 都用 `from server.scaffold import VOCAB`（或 `scaffold.VOCAB`）
拿到**同一個物件參照**。Python 的 `from x import y` 綁定的是物件參照，不是
複製值——只要合併時用 `VOCAB[zh] = {...}` **原地寫入**而不是重新賦值整個
`VOCAB`，所有既有 import 端都會立刻看到新詞，不必改一行既有程式碼：

| 既有系統 | 為什麼自動吃到新教材 |
|---|---|
| `games.py` 三個小遊戲 | 開局時即時讀 `scaffold.VOCAB`，`cat` 只要落在既有 6 類就自動可玩 |
| `homework._pick_vocab_entries` | 每次呼叫都重新 `VOCAB.items()`，非快取 |
| `srs.due_words` | 以詞字串為鍵，跟詞庫來源無關 |
| `profile.py` 興趣/錯點分類 | 依賴 `_EN_INFO` 反查表——**這是唯一例外**，見 §5 |

代價：全域可變狀態。教材驗證邏輯若有 bug，影響面是全部學生／全部遊戲，
不是單一教材。§6 的驗證器把這個風險收斂成「寧可少收詞，不可收壞詞」。

## 4. 公開契約 — `server/agents/material.py`（新）

比照現有三個 agent（`homework.py`／`report.py`／`orchestrator.py`）的形狀：

```python
def extract_vocab(text: str, *, allow_cloud: bool = True) -> dict
```

回傳固定 schema（雲端與規則式格式一致）：

```python
{
    "topic": str,              # 這份教材的主題，人話描述，例如「動物園一日遊」
    "entries": [               # 通過驗證、已合併進 VOCAB 的詞條
        {"en": str, "zh": str, "cat": str, "np": str, "sent": str}
    ],
    "accepted_count": int,     # entries 的長度
    "rejected_count": int,     # 雲端提議但驗證未過的詞條數
    "source": "cloud" | "rule",
}
```

流程與既有三個 agent 完全一致：

1. `allow_cloud=False` 或未取得家長同意 → 直接走規則式，不碰 `resolve_config`
2. 雲端路徑：`agent_backends.resolve("material")` → 依序試 AgentCore → Bedrock
   Converse（`cfg=resolve_config(role="diag")`，沿用既有 12s 逾時）
3. 雲端回覆整體字串過 `guardrails.passes_guardrail`；不通過 → 降級
4. 解析 JSON，對每個提議詞條呼叫 `scaffold.register_material_vocab`（§6）
   逐條驗證；不合法的詞條被丟棄但不影響其他詞條
5. 任何例外不外拋，一律降級回規則式；規則式路徑永遠能產出合法結果（可以是
   空 `entries`）

**雲端 system prompt 的核心要求**：「從這段教材文字裡挑出最多 8 個適合國小
生的詞彙，每個詞附中文、分類（只能是 food/school/animal/family/action/color
之一）、名詞片語（含正確冠詞）、一句目標英文例句。只輸出 JSON。」——刻意限制
分類只能是既有 6 類，不開放模型自創新分類（見 §6）。

## 5. `profile.py` 的唯一改動

`profile._EN_INFO` 目前是 import 時算好的**靜態快照**：

```python
_EN_INFO: dict[str, dict] = {v["en"].lower(): {...} for zh, v in scaffold.VOCAB.items()}
```

教材合併發生在 import 之後，這份快照不會自動更新，學生 profile 的興趣/
錯點分類就看不到新詞。改成一個小函式，每次呼叫時用當下的 `scaffold.VOCAB`
重算（136→約 150 個詞條的 dict 推導成本可忽略，且 `build_profile` 本來就
是非同步背景路徑，不在 1.5 秒即時迴圈裡）：

```python
def _en_info() -> dict[str, dict]:
    return {v["en"].lower(): {...} for zh, v in scaffold.VOCAB.items()}
```

呼叫端把 `_EN_INFO` 改成 `_en_info()`。這是本次唯一一處必須修改的既有檔案。

## 6. 驗證與合併 — `scaffold.register_material_vocab`（新）

```python
def register_material_vocab(entries: list[dict]) -> tuple[int, int]:
    """驗證並原地合併詞條進 VOCAB。回傳 (accepted, rejected)。"""
```

單一驗證入口，逐條檢查，任何一條不合法只丟該條，不中斷整批：

1. `cat` 必須是既有 6 類之一（food/school/animal/family/action/color）——
   不開放新分類，否則 `games.I_SPY_TOPICS` 等假設「這個 cat 至少 5 個詞
   可以排一局」會被打破
2. `en`（不分大小寫）不得與現有 `VOCAB` 任何詞重複——`profile._EN_INFO`
   反查表假設 en → 詞條是一對一
3. `sent` 全域唯一——`homework._pick_vocab_entries` 用 `sent` 去重出題，
   重複的目標句會被靜默丟棄，等於這個詞永遠出不了作業
4. `np` 冠詞檢查沿用既有規則（可數用 a/an、不可數用 some，其餘 my/the 或不加）
5. 通過以上檢查 → `VOCAB[zh] = {"en": ..., "cat": ..., "np": ..., "sent": ...}`
   （原地寫入，見 §3）

不合法的詞條記 warning log 但不拋例外，維持專案「規則式/驗證路徑永遠不
炸」的規矩。

## 7. 規則式 fallback：不發明新詞

離線或無雲端時，`extract_vocab` 的規則式路徑**不生成任何新詞條**——只在
貼上的文字裡掃描既有 136 個詞的中文鍵或英文詞是否出現，命中的詞就是這份
「教材」的重點詞（回傳時 `source="rule"`）：

```python
def _rule_based_extract(text: str) -> dict:
    hits = [entry for zh, entry in scaffold.VOCAB.items()
            if zh in text or entry["en"].lower() in text.lower()]
    ...
```

因為這些詞本來就已經在 `VOCAB` 裡，`register_material_vocab` 對它們是
no-op（已存在、不重複寫入）。這保證規則式路徑**不可能**弄壞全域字典——
跟現有三個 agent「規則式永遠合法」的原則一致，同時是誠實的降級：離線時
老師上傳教材的效果變成「幫你標出教材裡對應課綱的詞」，不是假裝離線也能
做語意提煉。

## 8. 資料流與新端點

```
老師在 /teacher 貼上教材文字
        │  POST /api/material {title, text}
        │  （tutor 角色 JWT 閘門，比照 /api/network_mode 的 identity_from_header）
        ▼
agents.material.extract_vocab(text, allow_cloud=(pipeline.network_mode=="cloud"))
        │  沿用既有降級鏈 + guardrails.consent_granted() 閘門
        ▼
scaffold.register_material_vocab(entries) → (accepted, rejected)
        │  合法詞條原地寫入 scaffold.VOCAB
        ▼
store.add_material({title, text, topic, entries_json,
                     accepted_count, rejected_count, ts, source})
        ▼
回應 {"topic", "accepted_count", "rejected_count", "source"} 給老師端
```

**App 啟動時（`lifespan`）補一步 replay**：`store.list_materials()` →
逐筆重跑 `register_material_vocab`，讓裝置重開機後老師先前上傳的教材
詞不會消失。這點刻意處理，因為現場已經吃過裝置重啟的虧（見專案交接紀錄）。

`store.py` 新增一張表，比照既有 `agent_outputs` 的 pattern：

```sql
CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    text TEXT,
    topic TEXT,
    entries_json TEXT,
    accepted_count INTEGER,
    rejected_count INTEGER,
    source TEXT,
    ts TEXT
)
```

## 9. 錯誤處理

- `allow_cloud=False` 或未同意 → 完全不碰雲端，直接走 §7 規則式比對
- 雲端回覆 schema 不合法／未過護欄 → 降級規則式，不拋例外
- `register_material_vocab` 對每條獨立驗證，不合格的詞條被丟棄但不影響
  同批其他合格詞條；`accepted_count`／`rejected_count` 誠實回報
- DB 寫入失敗 → 記 log，但當下 session 的記憶體合併已經生效（demo 韌性
  優先於持久化，跟專案既有「DB 壞掉不能讓現場停擺」原則一致）
- `/api/material` 本身的例外 → 500，但因為合併順序是「先驗證、後合併、
  再持久化」，DB 寫入失敗不會讓已合併的詞從 `VOCAB` 消失

## 10. 教師回饋與長期記憶：刻意不重造

- **長期記憶**：`profile.py`（學生畫像）／`srs.py`（SM-2 間隔重複）不需要
  新增任何機制——教材詞合併進 `VOCAB` 後，這兩層本來就是「詞字串為鍵」
  的通用系統，自動涵蓋新詞的複習排程與興趣分類
- **回饋老師**：`report.py`／`homework.py`／`orchestrator.py` 產出的教師
  儀表板卡片不變——孩子練到教材詞之後，既有週報敘事會自然提到（因為
  週報是依 `diagnosis.scores` 與弱項描述生成，不分詞彙來源）
- `/teacher` 頁面本次**不新增** UI 區塊（已與使用者確認），只加一個
  `/api/material` 端點供貼上教材用

## 11. 檔案落點

| 檔案 | 動作 | 規模 |
|---|---|---|
| `server/agents/material.py` | 新增 | ~150 行，比照 `homework.py` 形狀 |
| `server/scaffold.py` | 新增 `register_material_vocab` | ~50 行 |
| `server/profile.py` | `_EN_INFO` 常量 → `_en_info()` 函式 | ~5 行改動 |
| `server/store.py` | 新增 `materials` 表 + `add_material`/`list_materials` | ~40 行 |
| `server/app.py` | 新增 `POST /api/material`；`lifespan` 補 replay | ~30 行 |
| `server/agent_backends.py` | `resolve("material")` 沿用既有函式，免改 | 0 行 |
| `tests/test_material_agent.py` | 新增 | 純規則路徑 + schema 驗證 |
| `tests/test_scaffold_register_material.py` | 新增 | 驗證器邊界測試 |

**不動**：`games.py`、`homework.py`、`srs.py`、`report.py`、`orchestrator.py`、
`pipeline.py`、`game_intent.py`、WS 協定。

## 12. 測試

**必須有的自動測試**（風險最高、最容易默默壞掉的點）：

- `register_material_vocab`：合法詞條原地寫入 `VOCAB`（用 `is` 驗證同一個
  dict 物件而非重新賦值）；重複 `en`/`sent`、不合法 `cat`、冠詞錯誤的
  詞條各自被拒絕且不影響同批其他詞條
- `extract_vocab`：`allow_cloud=False` 時完全不呼叫 `resolve_config`；雲端
  schema 不合法時降級規則式且不拋例外；規則式路徑回傳的詞條必須是既有
  `VOCAB` 的子集（永不虛構新詞——用一個含新造英文字的假文字輸入去測，
  斷言不會出現在結果裡）
- 整合測試：合併教材詞後，斷言該詞的中文鍵出現在
  `homework._pick_vocab_entries(dim, rotation=0)` 或某個 cat 的候選池裡

**現場彩排手動驗證腳本**：

1. 開啟 `/teacher`，貼一段含 3–5 個課綱內詞彙的短文，送出，確認回應顯示
   `accepted_count`
2. 開一局既有小遊戲（如 I Spy 選對應主題），確認新詞有機會被抽到
3. 觸發一次作業產生，確認新詞出現在作業詞條裡
4. 重啟 server，重新打開 `/teacher`，確認先前上傳的教材詞仍在（驗證
   replay 有效）
5. 把 `network_mode` 切成 edge，重複步驟 1，確認離線時走規則式比對
   （只回既有詞，`source: "rule"`），不報錯

## 13. 已知邊界（不要在台上講過頭）

- **只吃純文字貼上，沒有檔案/PDF/OCR。** 老師必須自己把教材轉成文字貼上。
- **教材是全域共用擴充，不分班級/學生。** 一份教材上傳後，所有孩子的遊戲
  與作業詞庫都會擴充，沒有教材級別的權限隔離。
- **`/teacher` 沒有新增教材列表或統計 UI。** 教材上傳後的效果體現在既有
  週報/作業卡片，老師看不到「這份教材本身」的獨立畫面。
- **規則式離線路徑不做真正的語意提煉**，只是既有詞庫的關鍵字比對——這是
  刻意的誠實降級，不是完整功能的縮水版。
- **雲端提煉品質未經評測。** 8 個詞條的分類/例句品質依賴 Bedrock 一次呼叫，
  沒有像 `spelling.py` 那樣先做過離線 spike 驗證。

## 14. 風險與對策

| 風險 | 對策 |
|---|---|
| 全域 `VOCAB` mutate 影響全部學生/遊戲 | §6 驗證器「寧可少收詞，不可收壞詞」，逐條獨立驗證 |
| `_EN_INFO` 快照沒跟上新詞，profile 分類漏掉教材詞 | §5 改成惰性函式，每次重算 |
| 裝置重啟後教材詞消失 | §8 `lifespan` 補 replay，從 `materials` 表重新合併 |
| 雲端提議的詞條 `cat` 是模型自創的新分類 | §6 白名單擋掉，不接受既有 6 類以外的值 |
| 現場時間不夠做完整測試 | §12 明確排優先序：驗證器與規則式路徑優先於雲端 prompt 調優 |
