# Nova Sonic hands-free 全雙工 設計

- 日期：2026-07-14
- 分支：`feat/cloud-llm-bedrock`（worktree `cloud-llm`）
- 母脈絡：`2026-07-11-nova-sonic-live-s2s-design.md`（hold-to-talk 版已上線）
- 前置事實：Nova 2 Sonic 中文全雙工 S2S 已實機證實全通（ASR＋繁中文字＋中文語音）；hold-to-talk 版尾靜音 fix commit `5440ad9`。

## 1. 目標與範圍

把現有 **hold-to-talk**（按住企鵝說話、放開送 `user_end`）升級為 **hands-free 全雙工**：麥克風常開、連續串流、turn 邊界交給 Nova Sonic 原生 server 端 VAD，並支援**真 barge-in**（AI 講話中使用者插話立刻打斷）。

三項已拍板決策（brainstorming 2026-07-14）：
1. **納入真 barge-in**：麥克風全程不 mute，成敗押在回音消除（AEC）。
2. **spike 先驗 AEC**：Phase 0 當硬閘門，過關才動 Phase 1 大工。
3. **點企鵝開/關整場**：拿掉「一輪一按」，點一下開始、再點結束。

**非目標**：本地離線全雙工、Phase 2 評分、多人對話。

## 2. 現況地圖（hold-to-talk）

| 層 | 現在的 turn 邊界機制 | 檔案 |
|---|---|---|
| client `LiveSession` | `capturing` 旗標＋`pushToTalkStart/End`；放開送 `user_end`。`_play()` 用 `_nextStart` 累加排程，**無法中途停播** | `web/live-client.js` |
| server `ws_live` | 單 `receive()` 迴圈：bytes→`send_audio`；`user_end`→`end_user_turn()`＋**阻塞式 `drain_events()`**（只在 user_end 後才收下行） | `server/app.py:373` |
| `NovaSonicSession` | `end_user_turn()` 補 0.8s 尾靜音＋contentEnd；`events()` 一次 turn_end 就 return | `server/nova_sonic.py` |
| index 接線 | penguin `pointerdown/up/cancel/leave` 綁 hold-to-talk | `web/index.html` |

核心結構轉變：**「user_end 觸發的一次性收下行」→「上下行雙 pump 常駐、turn 邊界交給 Nova VAD」**。

## 3. 核心風險：AEC 自激發回圈

真 barge-in 要求麥克風全程不 mute。若 AI 的喇叭聲從麥克風漏回，Nova server VAD 會把「AI 的回音」當成「使用者插話」→ 自己打斷自己 → 無限自問自答。

已知未知數：`web/index.html` 播放走**獨立 `playCtx` AudioContext**，瀏覽器/OS 的 AEC 未必參照得到此渲染路徑 → 可能壓不住回音。這是整套設計的存亡點，故 Phase 0 先實測。

## 4. Phase 切分

### Phase 0 — AEC spike（硬閘門，先做）

**做在 `/ws/live` 加 `?mode=continuous` 開關**（不另開頁；spike 與 Phase 1 共用同一條伺服器路徑，過關後前端接上即可）。

- 最小 hands-free：麥常開＋`echoCancellation:true`，AUDIO 內容一直開著（不送 contentEnd）。
- getUserMedia 約束：`{ echoCancellation:true, noiseSuppression:true, autoGainControl:true, channelCount:1 }`。

**真機量測（手機 → HTTPS server）三題**：
1. **靜默漏音測**：點開始 → 讓 AI 講一段長回覆 → 使用者**完全不出聲**。判定：整段 AI 講完、無假 USER 氣泡、無中斷 = AEC 過關。
2. **正常分段測**：一句話 → 停 → Nova 是否自動判定 turn 結束並回覆（不靠尾靜音）。
3. **barge-in 測**：AI 講話中使用者出聲 → 記錄 Nova 送出的中斷事件形態（欄位、`stopReason`），供 Phase 1 接 `interrupt` 訊號。

**產出**：spike findings（三題各一 PASS/FAIL＋觀測到的 Nova 事件樣態），寫入 spec 附錄與交接記憶。

**閘門判定**：
- 三題皆 PASS → 進 Phase 1。
- 題1 FAIL（漏音自激發）→ 先試把 `index.html` 播放改走**共用擷取 context 或 WebRTC audio element**（讓 AEC 一定參照得到）再重測；仍不行 → 退回「AI 說話時軟閘麥」（犧牲 barge-in）並回報用戶決策。此降級**不在 Phase 1 程式碼內**，是閘門分支。

### Phase 1 — 完整 hands-free（spike 過關後）

伺服器雙 Task ＋ `NovaSonicSession` 連續模式 ＋ client 常開擷取 ＋ barge-in 播放 flush ＋ 點企鵝開/關。詳見 §5–§8。

## 5. 伺服器設計（`server/app.py` + `server/nova_sonic.py`）

**開關**：連線帶 `?mode=continuous`（`websocket.query_params`）。
- 無 → 現有 hold-to-talk 行為，**一行不動**（安全網、降級路徑續用）。
- `continuous` → 雙 Task 連續模型。

**`ws_live` 連續分支（方案 A：雙 asyncio.Task）**
```
uplink  : while receive(): bytes→send_audio；user_end 忽略（連續模式無此概念）；bye/disconnect→收
downlink: async for ev in session.events_continuous():
             audio→emit_bytes
             transcript→emit + 累積 turn_user/turn_asst
             turn_end→_store_live_turn + emit {type:"turn_end"}
             interrupt→emit {type:"interrupt"}
asyncio.gather(uplink, downlink, return_exceptions=True)
任一結束 → cancel 另一；finally 一定 session.close()（零資源洩漏）
```

**`NovaSonicSession` 新增（不動既有 hold-to-talk 方法）**
- `send_audio` 不變（首塊自動開 AUDIO 內容）。連續模式**永不呼叫 `end_user_turn`** → AUDIO 內容一直開著，交給 Nova VAD 分段。尾靜音 `_TAIL_SILENCE_PCM` 只屬 `end_user_turn`，連續模式碰不到 → 保留作降級安全網。
- `_receive_loop`：加解析 **Nova 中斷事件**（barge-in 時的 `stopReason`/INTERRUPTED，實際欄位由 Phase 0 spike 探明）→ `put NovaEvent("interrupt")` 並 reset `got_assistant`；多輪不再因單次 turn_end 而終止。
- `events_continuous()`：跨多輪連續 yield（audio/transcript/turn_end/interrupt），只在串流結束（None 哨兵）才 return。既有 `events()` 保留給 hold-to-talk。

## 6. 前端設計（`web/live-client.js`）

**新增方法（不動既有 `pushToTalkStart/End`，降級路徑續用）**
- `startConversation()`：`capturing=true`（整場常開）＋ resume `micCtx`/`playCtx`（點企鵝＝使用者手勢，滿足瀏覽器開麥限制）＋ WS 用 `?mode=continuous` ＋ 狀態 `listen`。
- `stopConversation()`：`capturing=false` ＋ `_flushPlayback()` ＋ 送 `bye` ＋ 狀態 `idle`。

**getUserMedia 約束改**：`{ echoCancellation:true, noiseSuppression:true, autoGainControl:true, channelCount:1 }`（原本只有 `channelCount:1`）。

**barge-in 播放 flush（關鍵新增）**
- `_play()` 把每個 `BufferSourceNode` 記進 `this._sources`；`node.onended` 播完自動移除。
- 新增 `_flushPlayback()`：`this._sources.forEach(n => n.stop())` ＋ 清陣列 ＋ `_nextStart = playCtx.currentTime`。
- `_onMessage` 收 `{type:"interrupt"}` → 呼 `_flushPlayback()` ＋ 狀態切 `listen`（AI 閉嘴、換使用者）。

**視覺狀態（事件驅動近似）**：收到 AI audio→`talk`；`turn_end`→`listen`；`interrupt`→`listen`。

## 7. index.html 接線（`web/index.html`）

- `enterLiveMode()`：改成**只建立** `LiveSession`（WS + getUserMedia + worklet 就緒）但**不開始擷取**；連線狀態顯示「就緒，點企鵝開始」。麥克風權限在首次點擊（使用者手勢）時才真正拉動。
- **移除** hold-to-talk 的 `pointerdown/up/cancel/leave` 四個 handler。
- penguin `click`（liveMode 分支）改為 **toggle**：未開始→`startConversation()`＋UI「對話中，再點結束」；對話中→`stopConversation()`＋UI 回 idle。
- `holdHint` 文案改「👆 點企鵝開始對話，再點結束」。
- callbacks：`onTurnEnd`→`listen`（整場還在，不再 idle）；barge-in 由 `LiveSession` 內部處理，index callback 不用改。

## 8. 錯誤處理與降級

- 沿用既有 `degradeToHalfDuplex(reason)`：`live_error`/`stream_error`/`consent_required`/`unavailable` → 設 `forceHalfDuplex` + reload，**零改動**。
- 連續模式雙 Task：任一 Task 例外 → emit `live_error` → 前端降級。`asyncio.gather(..., return_exceptions=True)` 收斂、`finally` 一定 `session.close()`。
- spike 題1 FAIL 的閘麥降級不在 Phase 1 程式碼內（見 §4 Phase 0 閘門）。

## 9. 測試策略

- **純函式**（node，`web/live-client.test.mjs`）：`_flushPlayback()` 後 `_nextStart` 歸零、`_sources` 清空；`startConversation()` 設 `capturing=true`。
- **伺服器**（pytest，fake `NovaSonicSession` monkeypatch）：`?mode=continuous` 走雙 Task；`user_end` 被忽略；`interrupt` 事件轉發成 `{type:"interrupt"}`；`turn_end` 落地 transcript；`events_continuous()` 跨多輪 yield 不提早 return。
- **整合層**（LiveSession/worklet/真 AEC）：靠真機 e2e（設計上無自動測試），沿用 e2e checklist 補 hands-free 案例（開始/結束、連續多輪、barge-in 打斷、AEC 靜默測）。
- **Phase 0 spike**：本身即量測，產出 findings 當 Phase 1 前置。

## 10. 元件邊界摘要

| 單元 | 職責 | 介面 | 依賴 |
|---|---|---|---|
| `NovaSonicSession.events_continuous()` | 跨多輪 yield Nova 事件（含 interrupt） | `async for ev` | `_receive_loop` queue |
| `ws_live` 連續分支 | 上下行雙 Task 橋接 | `?mode=continuous` WS | NovaSonicSession、store |
| `LiveSession.startConversation/stopConversation` | 整場常開擷取 + 播放 | 點企鵝 toggle | getUserMedia、worklet、WS |
| `LiveSession._flushPlayback` | barge-in 停播 | `{type:"interrupt"}` | `_sources`、playCtx |

## 附錄 A：Phase 0 spike findings（2026-07-14 真機測，手機 → HTTPS server）

- **題1 靜默漏音：PASS**。點開始後讓 AI 講長回覆、使用者完全不出聲 → AI 整段講完、無假 USER 氣泡、無自問自答。**AEC 過關**：即使播放走獨立 `playCtx` AudioContext，瀏覽器/OS 的回音消除仍罩得住，**不需重構播放路徑**（§3 的存亡點解除）。
- **題2 正常分段：PASS**。說一句話 → 停頓 → Nova server 端 VAD **不靠尾靜音**即自動判定 turn 結束並回覆。連續模式永不呼叫 `end_user_turn`／`_TAIL_SILENCE_PCM` 成立。
- **題3 barge-in 中斷事件形態：`userSpeechStart`（非計畫假設的 `contentEnd.stopReason=="INTERRUPTED"`）**。使用者出聲的瞬間，Nova 送出獨立事件：

  ```json
  {"userSpeechStart": {"inputAudioOffsetMs": 11760,
    "promptName": "<uuid>", "sessionId": "<uuid>"}}
  ```

  對稱地在使用者語音結束送 `userSpeechEnd`（帶 `inputAudioDetectionOffsetMs`/`inputAudioOffsetMs`）。**實測 `contentEnd` 一律帶 `stopReason:"PARTIAL_TURN"`，barge-in 時並不出現 `INTERRUPTED`** → 計畫 Task 6 原假設作廢，改以偵測 `userSpeechStart` 鍵作為 barge-in（interrupt）信號。`userSpeechStart` 在**每次**使用者語音起點都會觸發（含正常輪首句），故映射成 `interrupt` 時，client `_flushPlayback()` 對「沒有播放中音訊」的正常輪自然是 no-op、對「AI 講話中插話」則即時停播，語義正確。
- **閘門結論：三題皆 PASS → 進 Phase 1（Task 6 依本附錄改以 `userSpeechStart` 解析）。** 播放路徑無需改走共用 context / WebRTC audio element。
