# 對話回覆接自架中轉（雲端腦）設計

- 日期：2026-07-13
- 範圍：讓即時對話（聊天）回覆能走「雲端腦」（Anthropic 相容 Messages API，可指向自架中轉 relay），並保留家長同意閘、去識別化、護欄與降級回本地。
- 前置狀態：`diagnose.py` 已把診斷的雲端呼叫改為環境變數驅動（`_resolve_api_config` / `_messages_url`，未提交）。本設計把該解析邏輯抽成共用模組，讓對話與診斷共用。

## 1. 目標與非目標

**目標**
- 對話回覆在 `network_mode=="cloud"` 且家長同意時，優先由雲端腦生成；失敗/逾時降級本地 EdgeLLM，再降級 scaffold。
- relay 端點/認證/model 全由環境變數決定（`ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_API_KEY` / `ANTHROPIC_DEFAULT_OPUS_MODEL` / `ANTHROPIC_MODEL`），與 `diagnose.py` **共用同一組**。
- 上雲前對 `student_text` 去識別化（`guardrails.deidentify`），雲端輸出過 `guardrails.passes_guardrail`。

**非目標**
- 不改前端、不改 WS/HTTP 契約、不改對話狀態機的事件流。
- 不引入新的重依賴（純標準函式庫 urllib，與 diagnose 一致）。
- 不動 `llm.py`（EdgeLLM 保持不變；system prompt 由 cloud_llm 自帶一份）。
- 不做串流輸出（維持整段回覆，沿用現有 8s 逾時模型）。

## 2. 決策（已與使用者確認）

1. **降級順序**：雲端 → 邊緣 EdgeLLM → scaffold（最韌性，完全比照 `CloudTTS` 的 cloud→edge→none）。
2. **設定共用**：對話與診斷共用同一組 `ANTHROPIC_*` 環境變數（同端點/認證/model；relay 後端接什麼模型由中轉決定）。
3. **relay 解析放哪**：抽到新模組 `server/anthropic_relay.py`（方案 A，單一真相），`diagnose.py` 與 `cloud_llm.py` 共用。
4. **system prompt**：`cloud_llm` 自帶一份台灣英語鷹架家教 prompt，安全條款重用 `guardrails.CHILD_SAFETY_CLAUSE`；`llm.py` 不動、模組獨立、變更最小可回退。（代價：家教格式規則與 EdgeLLM 有少量重複，可接受。）

## 3. 元件與檔案異動

| 檔案 | 動作 | 內容 |
|---|---|---|
| `server/anthropic_relay.py` | 新增 | 從 `diagnose.py` 抽出：`resolve_config() -> dict | None`（原 `_resolve_api_config`，改公開）、`messages_url(base) -> str`（原 `_messages_url`）。回傳 `{"url","model","headers"}` 或 `None`（無憑證）。純函式、不觸網、只讀 env。 |
| `server/diagnose.py` | 改 | 刪除內部 `_resolve_api_config` / `_messages_url`，改 `from server import anthropic_relay`；`generate_diagnosis` 內 `cfg = anthropic_relay.resolve_config()`；`_call_anthropic_api(…, cfg)` 簽章不變。 |
| `server/cloud_llm.py` | 新增 | `CloudLLM` 類別，契約同 `EdgeLLM`。 |
| `server/pipeline.py` | 改 | `__init__` 加 `cloud_llm=None`；`_process_text` 加值段改 cloud→edge→scaffold。 |
| `server/app.py` | 改 | 建 `cloud_llm_engine=CloudLLM()`，注入全域與每連線 pipeline；`/api/status` 加 `"cloud_llm": bool(cloud_llm_engine.available())`。 |
| `server/CONTRACTS.md` | 改 | 補 `cloud_llm.py` 契約段與 pipeline 降級順序描述。 |

## 4. `CloudLLM` 契約

```python
class CloudLLM:
    def available(self) -> bool
    def generate(self, student_text: str, scaffold: "ScaffoldResult",
                 directive: str | None = None) -> str | None
```

- `available()`：`anthropic_relay.resolve_config() is not None`（憑證可解析即 True）。**不檢查 consent 與 network_mode**——那是 pipeline 的職責（`available()` 只答「技術上能不能跑」，與 EdgeLLM/CloudTTS 一致）。
- `generate(...)`：
  1. `cfg = anthropic_relay.resolve_config()`；`None` → 回 `None`。
  2. `safe_text = guardrails.deidentify(student_text)`（**上雲前去識別化**）。
  3. 組 Messages 請求：`system` = 台灣英語鷹架家教規則 + `guardrails.CHILD_SAFETY_CLAUSE`；`user` = 學生（去識別化）文字 + 目標英文句 + directive 區塊；`model=cfg["model"]`，`max_tokens≈160`，`timeout=_TIMEOUT_S`（預設 8s，與 pipeline 外層 `LLM_TIMEOUT_S` 對齊）。
  4. urllib POST `cfg["url"]`、headers=`cfg["headers"]`；解析 `content[0].text`。
  5. 空 → `None`；過 `guardrails.passes_guardrail`（命中兒少安全 → `None`）；`target_sentence` 若不在回覆內則補「跟我說一遍：<英文句>」（與 EdgeLLM 帶讀護欄一致）。
  6. 任何例外 / HTTP 錯誤 / 逾時 / JSON 解析失敗 → `None`（**絕不拋進 pipeline**）。

## 5. Pipeline 資料流（即時對話輪）

`_process_text` 的「LLM 加值」段（現行 llm.py 單一路徑）改為：

```
sc = scaffold.respond(text)          # 基底 reply（護欄後盾）
reply = sc.reply_text
enhanced = None
if network_mode == "cloud" and cloud_llm and cloud_llm.available()
       and guardrails.consent_granted():
    enhanced = wait_for(to_thread(cloud_llm.generate, text, sc, directive), 8s)  # 雲端
if not enhanced and edge_llm and edge_llm.available():
    enhanced = wait_for(to_thread(edge_llm.generate, text, sc, directive), 8s)   # 邊緣
if enhanced: reply = enhanced        # 否則維持 scaffold
```

- `latency_ms["llm"]` 量測整段加值（含實際跑的路徑）。
- 其餘（低信心兜底、TTS、寫 DB、每 N 輪 directive 背景刷新）**完全不變**。
- consent 未取得 → 雲端分支根本不進入（與 diagnose 同一 chokepoint）。

## 6. 錯誤處理與安全

- **同意閘**：`guardrails.consent_granted()` + `network_mode=="cloud"` 雙重把關才會資料出境。
- **去識別化**：出境前 `guardrails.deidentify(student_text)`（人名/電話/住址遮罩，保留詞庫學習詞）。
- **護欄縱深**：雲端輸出過 `passes_guardrail`（L3），命中即丟棄降級——edge/cloud 共用同一後置過濾。
- **降級不中斷**：relay 任何失敗 → `generate` 回 `None` → pipeline 靜默降級，對話不斷。
- **金鑰**：只讀 env，絕不寫入 repo（沿用現有慣例）。

## 7. 測試（TDD，先寫失敗測試）

- `tests/test_anthropic_relay.py`：搬移現有 `test_diagnose_relay.py` 的 resolver / messages_url 單元測試到共用模組（無憑證→None、x-api-key vs Bearer、AUTH_TOKEN 優先、base_url 正規化四型、model override）。
- `tests/test_cloud_llm.py`：
  - `available()` 有/無憑證。
  - `generate` 成功（monkeypatch `urllib.request.urlopen` 回假回覆）→ 回文字、命中 relay url/Bearer/model。
  - 送出前 `student_text` 已去識別化（假 urlopen 擷取 body，斷言含 `[名字]` 之類遮罩）。
  - 護欄命中 → `None`。
  - `target_sentence` 缺漏 → 自動補帶讀句。
  - urlopen 拋例外 → `None`。
- `tests/test_pipeline.py`（新增，用 stub CloudLLM）：
  - cloud 模式 + consent + cloud stub 有回覆 → `reply_text` 用雲端。
  - cloud stub 回 `None` → 降級 edge stub → edge 值。
  - **無 consent → 完全不呼叫雲端 stub**（只走 edge/scaffold）。
  - edge 模式 → 不呼叫雲端 stub。
- `tests/test_diagnose_relay.py`：保留 `_call_anthropic_api` 整合測試，cfg 改由 `anthropic_relay.resolve_config()` 取得（驗證抽出後診斷仍接得上）。

## 8. 手動驗證

1. 不設任何 `ANTHROPIC_*` → 啟動 server，cloud 模式對話仍走 edge/scaffold（`/api/status` `cloud_llm:false`）。
2. 設 `ANTHROPIC_AUTH_TOKEN` + `ANTHROPIC_BASE_URL` 指向本機假 relay → `/api/status` `cloud_llm:true`；cloud 模式對話回覆由 relay 產生；relay 關閉 → 自動降級 edge/scaffold、對話不中斷。
3. `TALKYBUDDY_CONSENT_GRANTED=0` → 即使有憑證也不呼叫雲端（觀察 relay 無請求）。

## 9. 回退

- 全部新增為獨立檔案 + 小幅注入；移除 `cloud_llm=` 注入即回退到純 edge/scaffold。
- `anthropic_relay` 抽出為機械式搬移，`diagnose` 行為不變（測試保證）。
