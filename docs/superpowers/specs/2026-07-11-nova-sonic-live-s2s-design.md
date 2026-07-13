# Nova Sonic 即時 S2S 陪聊（Phase 1 垂直切片）設計

- 日期：2026-07-11
- 分支：`feat/cloud-llm-bedrock`（後續實作另切分支）
- 相關：`research/16_Bedrock端到端S2S_可行性評估.md`、`scripts/verify_nova_sonic_live.py`（已證協定）、`server/cloud_llm.py`（組裝式雲端大腦）、A2 `server/streaming/`

## 目標與場景

新增一條**中文全雙工即時陪聊**路徑當 demo 主亮點：學生說中文，Amazon Nova 2 Sonic 端到端直接即時回中文語音（<500ms、原生 barge-in、情感），並以**鷹架學習法引導孩子開口說英文**。每輪 transcript 回餵 B 軸存紀錄。可靠度自負（中文屬 Nova Sonic 非官方支援但實測可用）；正式教學仍走現有組裝式雲端。

與現有半雙工體驗**並存、互不干擾**：現有 `/ws/talk`（MediaRecorder→webm→`run_turn_audio`）一行不動；新增獨立 `/ws/live` + 前端模式鈕，隨時可切回。

## 決策摘要（brainstorming 收斂）

| 面向 | 決定 |
|------|------|
| 場景 | 中文全雙工陪聊 + 鷹架引導開口說英文 |
| 鷹架注入 | 混合：靜態教學框架寫死 prompt + 動態「今日目標句/難度」由 scaffold/B 軸帶入 |
| 進入點 | 新 `/ws/live` 端點 + index.html 模式鈕，與半雙工並存 |
| B 軸 | 每輪存 transcript（asr+reply）；scores 事後背景評分排 Phase 2 |
| 降級 | Nova Sonic 失效走組裝式 `run_turn_audio`——排 Phase 3；Phase 1 僅優雅提示+可切回 |
| 整合結構 | 方案 B：抽 `server/nova_sonic.py` 的 `NovaSonicSession`，`/ws/live` 當薄橋 |
| 本次範圍 | Phase 1 垂直切片：端到端能跑 + 鷹架注入 + transcript 落地 |

## 架構

```
瀏覽器 index.html
 ├─[現有] 半雙工：MediaRecorder→webm→/ws/talk→run_turn_audio      ← 不動
 └─[新增] 即時 S2S：AudioWorklet→PCM16k→/ws/live ⇄ NovaSonicSession ⇄ Nova Sonic bidi
                                     ↑ PCM24k 播放 ← audioOutput
```

## 元件（單一職責、介面清楚、可獨立測）

### 1. `server/nova_sonic.py` — `NovaSonicSession`（新）
封裝 Nova Sonic `InvokeModelWithBidirectionalStream` 協定一場對話，把 `verify_nova_sonic_live.py` 已證的協定收斂成類別。

介面：
- `__init__(model_id, voice, region)`
- `async start(system_prompt: str)`：開串流、送 `sessionStart` / `promptStart`（含 audioOutput 24kHz 設定）/ system TEXT `contentStart`+`textInput`+`contentEnd`
- `async send_audio(pcm16: bytes)`：送 `audioInput`；首塊前自動送 AUDIO `contentStart`（16kHz）
- `async end_user_turn()`：送音訊 `contentEnd`；**不立刻送 `promptEnd`**（協定踩雷已封裝）
- `events() -> AsyncIterator[NovaEvent]`：yield `transcript(role, text)` / `audio(pcm24_bytes)` / `turn_end`；對多 completion 收斂（先 USER-ASR 段、後 ASSISTANT 回覆段）
- `async close()`：`promptEnd` + `sessionEnd` + 關流

module 級 `available() -> bool`：SigV4 env（`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`）存在 + `aws_sdk_bedrock_runtime` 可 import。

封裝所有已知踩雷：`Config(aws_credentials_identity_resolver=EnvironmentCredentialsResolver())`、`contentEnd` 後不立刻 `promptEnd`、多 completion 別在第一個 `completionEnd` 就收、尾端補靜音幫 VAD。

### 2. `server/app.py` — `/ws/live` WebSocket 端點（新增，薄橋）
- 連線先過 `guardrails.consent_granted()`：未同意→送 `live_error(consent_required)`、關閉（音訊上雲，資料不出境須一致）。
- `available()` false / `LIVE_S2S_ENABLED` 關 → 拒連並提示。
- 取 scaffold 目標句 + B 軸 directive → `build_live_system_prompt(...)` → `NovaSonicSession.start`。
- 兩個 asyncio task 對接：上行 browser binary PCM→`send_audio`；下行 `events()`→browser（audio binary + transcript JSON `{"type":"live_transcript", role, text}`）。
- turn 邊界：前端送 `{"type":"user_end"}` → `end_user_turn`（Phase 1 明確訊號；半途 barge-in 靠 Nova Sonic 原生，不做前端視覺 UI）。
- `turn_end` → 組 transcript dict → `store.add_interaction`（asr_text=USER、reply_text=ASSISTANT；scores 留空待 Phase 2）。
- 逐輪例外不斷整條 WS；斷線 finally 收尾 `close()`。

### 3. 鷹架 system prompt 組裝（重用現有）
`build_live_system_prompt(target_sentence, directive)`（放 `server/scaffold.py` 或 nova_sonic prompt helper）：
- **靜態框架（寫死）**：企鵝學伴「說說學伴」角色；主要用繁體中文（台灣用語）溫暖簡短回覆；每輪自然帶出一個簡單英語詞/短句、鼓勵孩子跟著開口（帶讀）；鷹架法（i+1、先示範再邀請、說對給具體正向回饋、說錯溫和重述不指責、循序漸進）；安全（不談暴力/成人/個資、分心溫柔拉回）；語音節奏（回覆 <2 句）。
- **動態帶入**：`scaffold` 今日目標英語句 + `diagnose.format_directive_for_prompt(companion_directive, level_state)`（沿用現有 B 軸注入格式）。

### 4. 前端 `web/live-client.js` + index.html 模式鈕（新）
- AudioWorklet 擷取麥克風 → 16kHz mono PCM16 → WS binary 上行。
- 播放 Nova Sonic 24kHz PCM（AudioContext 排隊播放）。
- 模式鈕切「即時對話」：連 `/ws/live`、斷 `/ws/talk`；可切回。
- 能力檢查：`/api/status` 加 `live_s2s` 欄，false 則鈕置灰。

## 資料流（一輪）
1. 按「即時對話」→ 前端連 `/ws/live`
2. server 過 consent → 取 scaffold 目標句 + directive → build prompt → `session.start`
3. 學生說話 → PCM16k 上行 → `send_audio`
4. 學生停 → 前端 `user_end` → `end_user_turn`
5. Nova Sonic 回 USER transcript(ASR) / ASSISTANT transcript / audioOutput(PCM24k)
6. server 下行 audio binary（前端播）+ transcript JSON（前端顯示）
7. `turn_end` → `store.add_interaction`（asr_text/reply_text；scores 留空）

## 錯誤處理與韌性
- 隱私 gate：未同意直接拒連。
- 開場不可用：`live_s2s=false` → 鈕置灰、進不來。
- 開串流失敗/憑證錯/配額 throttle/串流中斷 → `{"type":"live_error", reason}`、優雅關閉、已得 transcript 落地、前端可切回半雙工。Phase 1 **不**自動接組裝式（Phase 3）。
- 逐輪例外不斷整條 WS。

## 測試策略（TDD）
- `NovaSonicSession`：mock SDK bidi client → 驗 start 事件序、send_audio chunk、`end_user_turn` 不送 promptEnd、`events()` 多 completion 收斂、close 收尾序。
- `build_live_system_prompt`：靜態關鍵規則在 + 動態 target/directive 有折入。
- `/ws/live` handler：注入 fake `NovaSonicSession` + FastAPI TestClient websocket → 上行轉 send_audio、下行 audio/transcript 轉發、`turn_end` 觸發 `add_interaction`、consent 未同意拒連、`available=false` 行為。
- 前端純函式（PCM 轉換、播放 queue）走 node 測；擷取/播放整合走手動 e2e checklist（真麥克風）。
- 實機驗證：沿用/擴充 `verify_nova_sonic_live.py`；補「瀏覽器→/ws/live→Nova Sonic→播回」手動 e2e checklist。

## Config 新增
- `NOVA_SONIC_MODEL_ID`（預設 `amazon.nova-2-sonic-v1:0`）
- `NOVA_SONIC_VOICE`（預設 `tiffany`）
- `LIVE_S2S_ENABLED` 旗標（可強制關）；實際可用性 = 旗標 AND `available()`
- region 沿用 `BEDROCK_REGION`；SigV4 憑證走 env（`EnvironmentCredentialsResolver`），不進 repo

## 非目標（Phase 1 不做，YAGNI）
- 事後背景評分（scores）→ Phase 2
- Nova Sonic 失效自動接組裝式 fallback → Phase 3
- 前端半途 barge-in 視覺 UI（倚賴 Nova Sonic 原生打斷）
- 英文/中英切換模式
- 導師（diagnose）流程改動（沿用現有每 5 輪節奏）

## 安全
- Nova Sonic bidi 只吃 SigV4；憑證走 env、不進 repo；測試 IAM user 用短期 key 且測後撤銷。
- 音訊上雲受 consent gate 管，與現有 cloud 路徑一致。

---

## 前端錄放整合層設計（2026-07-13 修訂）

> 本章細化原 §4「前端 live-client.js + 模式鈕」，並**修訂定位**。後端 Task 1–7（`NovaSonicSession`、`/ws/live`、`/api/status`、`build_live_system_prompt`）已實作完成；本章聚焦前端真正的錄放接線與模式定位。**本章與原文衝突處以本章為準**：原 §11、§4（第 68 行）、§65–68、§72「模式鈕手動切『即時對話』／可切回」的描述，被下方「定位翻轉」取代。

### 定位翻轉：live 為主、半雙工為自動 fallback ⚠️

- 產品方向：**雲端以 live 全雙工為主要體驗，半雙工 `/ws/talk` 降為 fallback**（僅在 live 不可用/失敗時使用）。斷網本地全雙工僅開發版、留待後續，本次不做。
- 前端 `init()` 讀 `/api/status` 一次決定主模式（兩模式**互斥、不做執行期熱切換**）：
  - `live_s2s=true` 且無強制半雙工旗標 → **直接進 live 模式**（hold-to-talk）；停現有喚醒引擎常駐麥、**不建 `/ws/talk`**、連 `/ws/live`。
  - `live_s2s=false` → 走半雙工（**完全現狀，一行不動**）。
- 因兩模式由 init 一次決定，**不需要**原 spec 的手動切換模式鈕；前端無使用者主動熱切換。

### 互動模型：hold-to-talk（Phase 1）

- **按住** live 主鈕 → 開始擷取上行 PCM（視覺 `listen`）；**放開** → `ws.send({"type":"user_end"})` 並停上行（視覺 `talk`）。
- 下行 audio 播放與 transcript 持續進來；收到 `turn_end` → 回 `idle`，可連續多輪（一條 `/ws/live` 連線）。
- 明確 `user_end` 訊號對接後端既有 `end_user_turn()`（後端設計不變）。
- **非目標（緊接下一步）**：麥克風常開、可打斷的真 barge-in 全雙工。Phase 1 先跑通 hold-to-talk 端到端，再增量升級（風險隔離）。

### 錄放資料流

```
上行：getUserMedia → AudioContext → AudioWorklet(live-capture-processor.js)
      → float32 frames → downsampleTo16k → floatTo16BitPCM → ws.send(binary 16k PCM16)
下行：ws binary(24k PCM16) → PlaybackQueue → AudioBufferSourceNode 排程接續播放
      （nextStartTime 累加，低延遲串流；後端下行為 raw binary，前端免 base64 解碼）
文字：ws text {live_transcript|turn_end|live_error} → callbacks → 逐字稿/狀態/toast
```

- 播放採 **AudioBufferSourceNode 排程接續**（非播放 worklet），Phase 1 求穩定可跑、複雜度低。
- 上行必須 **AudioWorklet**（`/ws/talk` 的 MediaRecorder→webm/opus 給的是壓縮流，無法產出 Nova Sonic 需要的 raw PCM16）。

### 自動降級（旗標 + 重整，乾淨還原）

- live 連線失敗 / 收到 `live_error`（`consent_required`/`unavailable`/`stream_error`）/ AudioWorklet 載入失敗 →
  `sessionStorage['forceHalfDuplex']='1'` → toast 提示 → `location.reload()`。
- `init()` 見此旗標 → 強制走半雙工並**立即清旗標**（避免重整後又進 live 的無窮循環；下次重開頁面恢復 live 為主）。
- 以重整達成降級：**零資源洩漏**（麥克風/AudioContext/WS 隨頁面卸載自然釋放），不需手寫拆解/重建邏輯。

### 前端元件

- **純函式層**（`web/live-client.js`，node `--test` 可測）：
  - `floatTo16BitPCM(float32) → ArrayBuffer`
  - `base64ToPCM16(b64) → Int16Array`
  - `PlaybackQueue`（`enqueue`/`size`/`drain`）
  - **新增 `downsampleTo16k(float32, inRate) → Float32Array`**（麥克風常 44.1k/48k，需降到 16k）
- **整合層**（`web/live-client.js` 同檔，瀏覽器 API，走手動 e2e）：`LiveSession` class 封裝一場對話的錄放與 WS 接線（`start`/`pushToTalkStart`/`pushToTalkEnd`/`close` + callbacks）。
- **新增 `web/live-capture-processor.js`**：AudioWorklet processor 檔（audio thread 收 128-frame float32，`postMessage` 回主線程累積）。

### 麥克風／喚醒協調

- 進 live 模式（init 判定）→ 停 `window._wakeController` 的喚醒引擎常駐麥（`engine.stop`）、不建 `/ws/talk`，避免兩路搶麥／搶連線。
- 還原（降級或使用者離開）→ 靠頁面重整，`init` 重新以半雙工建立喚醒/`/ws/talk`。

### 測試策略（前端）

- 純函式 node `--test`：`floatTo16BitPCM`（16-bit LE、夾限）、`base64ToPCM16`、`PlaybackQueue`、`downsampleTo16k`（升/降取樣長度正確、邊界）。
- 整合層 + 錄放 + 自動降級：**手動 e2e**——本次含**真機接 Nova Sonic**（使用者提供短期 SigV4，本機 HTTPS 實跑瀏覽器 hold-to-talk 中文往返、聽到語音、transcript 落地）。
- 回歸：後端 `pytest` 全綠；`/ws/talk` 半雙工路徑一行不動。

### 前端非目標（本次不做）

- 麥克風常開、可打斷的真 barge-in 全雙工 → 緊接下一步。
- 斷網本地全雙工（僅開發版）→ 留待後續。
- 使用者主動在兩模式間熱切換的 UI（改由 init 一次決定 + 自動降級）。
