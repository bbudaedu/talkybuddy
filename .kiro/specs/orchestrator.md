# 子專案 E：決策判斷（中央編排 agent）— 介面契約

由雲端線（`pipeline.py` 的擁有者）制定。Kiro 依此實作，不要自行更動介面。

## 為什麼是這個形狀

編排 agent 掛在 `server/pipeline.py::_refresh_directive`（line 326）。
那裡是既有的**回合後背景鉤子**：每 `DIRECTIVE_REFRESH_EVERY` 回合觸發一次、
跑在 `asyncio.to_thread` 不進即時路徑、已經有 `allow_cloud` 閘門、
且手上正好有 `recent`（最近互動）、`diagnoses`（歷史診斷）、`diag`（剛產出的診斷）。

不另開觸發點的理由：即時語音迴圈只有 1.5 秒預算，任何新的同步決策都會吃掉它。

## 最重要的架構決定：E 只做決策，不執行

`decide_next_actions()` **不可以**呼叫 `homework.generate_homework` 或
`report.generate_report`。它只回傳「該做什麼」，由 `pipeline.py` 決定要不要真的做。

三個理由：

1. **可測性** — E 的測試不需要 mock B 和 C，只驗決策邏輯本身。
2. **pipeline 保有控制權** — kill-switch、逾時預算、失敗降級都在 pipeline 手上。
   若 E 自己執行，斷網時的行為就散在三個模組裡，現場出事很難查。
3. **鐵律 1** — in-process 輕量控制器。決策與執行分離才輕得起來。

## 公開契約

```python
def decide_next_actions(
    profile: dict,
    diagnosis: dict,              # 剛產出的那份四維診斷
    history: list[dict],          # 歷史診斷，最舊在前；可能是空的
    turn_count: int,              # 累計成功回合數
    *,
    allow_cloud: bool = True,
) -> dict
```

回傳固定 schema（雲端與離線格式完全一致）：

```python
{
    "actions": [str],     # 子集合 of ["homework", "report"]；可以是空 list
    "reason": str,        # 為什麼這樣決定，一到兩句完整中文，會顯示在教師儀表板
    "priority": str,      # "low" | "normal" | "high"，給儀表板排序用
    "source": "cloud" | "rule",
}
```

`actions` 只允許這兩個值，因為目前只有 B 和 C 兩個可執行的 agent。
出現其他字串一律視為錯誤（要有測試擋）。

## 決策該考慮什麼

這是產品判斷，不是純技術。至少要涵蓋：

- **弱項嚴重度** — 診斷有維度明顯偏低時，派作業的價值高
- **趨勢** — 連續退步比單次低分更值得通知家長
- **頻率控制** — 不可以每次都回傳 `["homework", "report"]`。
  孩子每隔幾回合就收到一份新作業、家長每天收到五封週報，是騷擾不是服務。
  用 `turn_count` 與 `history` 長度做節流。
- **資料不足** — `history` 為空或只有一筆時，趨勢算不出來，
  這種情況傾向回傳空 `actions`，`reason` 說明「觀察中，資料還不夠」。

## 行為要求（每條都要有測試）

1. 雲端走 `bedrock_converse.converse_text`，`cfg=resolve_config(role="diag")`
2. `allow_cloud=False` 時完全不碰雲端，連 `resolve_config` 都不呼叫
3. 上雲前經 `guardrails.deidentify`
4. 雲端回覆經 `guardrails.passes_guardrail`，不通過降級
5. 任何例外不外拋，一律降級回規則式
6. 規則式路徑永遠能產出合法結果，**包含 `history` 為空、`diagnosis` 為空 dict、
   `turn_count=0` 這些邊界**
7. `actions` 內容必須是 `["homework", "report"]` 的子集，且不得重複

## 品質底線

前兩個 agent 在這裡被退過，先看清楚：

- `reason` 必須是通順完整的中文句子，不是欄位拼接。
  「發音 42 分，退步」不合格；「最近三次練習的發音分數持續下滑，建議加強針對性練習」合格。
- **不同輸入要產生不同決策**。分數優異且穩定的學生，和連續退步的學生，
  `actions`、`reason`、`priority` 三者都應該不同。
- 節流要能被測出來：連續多次呼叫，不可以每次都回傳相同的滿版 actions。

## pipeline 接線方式（我負責，你不要動 pipeline.py）

供你理解上下文用：

```python
# server/pipeline.py::_refresh_directive 的 _work() 內，
# diag 產出並存檔之後
decision = orchestrator.decide_next_actions(
    profile=store.get_profile(),
    diagnosis=diag,
    history=diagnoses,
    turn_count=self._turn_count,
    allow_cloud=allow_cloud,
)
if "homework" in decision["actions"]:
    hw = homework.generate_homework(profile, diag, allow_cloud=allow_cloud)
    store.add_homework(hw)          # 這個 store 函式由我新增
if "report" in decision["actions"]:
    rp = report.generate_report(profile, diagnoses, allow_cloud=allow_cloud)
    store.add_report(rp)
```
