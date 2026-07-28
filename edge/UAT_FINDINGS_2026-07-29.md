# 真機 UAT 發現（2026-07-29，使用者實測）

> 情境：斷網彩排前置完成後，使用者以筆電瀏覽器（`http://localhost:8787/`，
> SSH 埠轉發取得安全來源）連上 Genio 520 實測對話。
> 記錄者依使用者口述整理，**未經我方獨立重現**，重現步驟見各項。

---

## 發現 1：push-to-talk 體驗，沒有 live 感 —— **使用者判定「這樣不行」**

**現象**：必須「按一下、回一下」，不是連續對話。

**使用者原話**：「可以講話 但是要按一下回一下 沒有 live 感覺 這樣不行」

### 這不是 bug，是目前走的路徑本來就如此

專案有兩條並存路徑（`.planning/STATE.md` Decisions）：

| 路徑 | 入口 | 形態 | 現況 |
|---|---|---|---|
| **Path 1 自架串流（全雙工）** | `server/streaming/run_realwire.py`（本機 pipecat + 裸麥/喇叭） | 全雙工、可 barge-in | 有實作，但見下方已知缺口 |
| **Path 1 回合式** | `/ws/talk` | 按鈕觸發、逐回合 | **目前學生頁走這條**，Phase 8/9 全部驗證都在此 |
| **Path 2 Nova Sonic S2S** | `/ws/live` | 雲端語音進語音出 | `live_s2s: false`，本質上不可能離線 |

> 📝 **2026-07-29 更正**：本表原本把 Path 1 標成 `/ws/live`、Path 2 標成 `/ws/talk`，
> 兩者對調了。依 `.planning/PROJECT.md:72`，`/ws/talk` 是 Path 1 回合式、
> `/ws/live` 是 Path 2（Nova Sonic）。**Path 1 的全雙工入口根本不是 WebSocket 端點**，
> 而是 `run_realwire.py` 這支跑本機 pipecat pipeline 的 CLI（已於 `server/app.py:659`
> 與 `server/streaming/run_realwire.py` 讀碼確認）。

Phase 8 的 2.96–2.99s 穩態、Phase 9 的斷網演練，量的都是 Path 1 回合式。

### 已知缺口（Known-Gaps Backlog G2）

> `run_realwire.py` build_processors **漏接 `BargeInGate`** → 真實麥克風/喇叭上
> barge-in 不觸發；**無實機執行證據**
> —— `.planning/STATE.md`，嚴重度 🟠 中

**所以「live 感」這件事在 backlog 上是已登錄的缺口，不是今天才發現。**
但使用者實際體驗後判定「這樣不行」，**這是對決賽演示形態的重要輸入**。

### 對決賽的意義

- 演示若以 push-to-talk 進行，觀眾看到的是「按鈕→等待→回覆」，
  與提案書「自然、可 barge-in 的口說對話」的描述有落差
- 切到 Path 1 需要處理 G2 且**無實機驗證證據**，決賽前 2 天風險高
- **需要使用者決策**：接受 push-to-talk 演示並調整話術，或投入時間打通 Path 1

---

## 發現 2：edge 回覆牛頭不對馬嘴 —— **且 ASR 是正確的**

**現象**：

| | 內容 |
|---|---|
| 使用者說 | 「可以教我一些**動物**用法嗎？」 |
| 字幕（ASR） | ✅ **正確** |
| 系統回覆 | 「聽到了，跟我一起說看看 **how are you today**」 |

**與動物完全無關。**

### 根因：edge scaffold 是規則引擎，沒命中詞庫就回固定兜底句

這不是 LLM 理解失敗，是**根本沒走到 LLM**（或 LLM 輸出被護欄丟棄後降級）。
`server/scaffold.py` 的規則引擎在輸入沒命中詞庫時，回一句與情境無關的通用引導句。

### ⚠️ 這正是 PR #7 要修的問題

`origin/master` 的 PR #7（`658383d`）描述原文：

> 之前的鷹架回應太「機械」：鼓勵語用輸入文字 hash 挑，同一句話問幾次永遠拿到
> 一模一樣的罐頭回覆；**完全沒命中詞庫時固定回一句通用引導句，也不會跟今天在練的
> 課程內容有任何關係**。

PR #7 的修法：

- `scaffold.respond()` 新增 `lesson_topic` / `lesson_target_sentence`，
  純中文無命中詞庫時**優先引導今日目標句**
- `pipeline.VoicePipeline._ensure_lesson()`：第一輪對話前預先讀今日課程
- 鼓勵語改用 `turn_index` 輪替，不再依輸入文字 hash

**使用者今天獨立重現了 PR #7 描述的確切症狀。**

### 這推翻了先前「不合併 PR #7」的評估

先前（本日稍早）的判斷是「scaffold 那 167 行改變回覆行為、決賽前 2 天不宜引入」。
**但該判斷建立在『現況回覆品質可接受』的假設上，而使用者實測證明不可接受。**

修正後的權衡：

| | 不合併 | 合併 |
|---|---|---|
| 風險 | **演示時被評審問到就穿幫**（回覆與提問無關） | 需解 2 個衝突 + 重跑中文稱讚合規率驗證 |
| 依據 | 已被使用者實測推翻 | PR 有 11 條新測試、作者稱全套 300 passed |

**建議重新評估合併，但必須連同 `edge/PROMPT_ORDERING_FINDING.md` 的教訓
——prompt/scaffold 改動必須逐條檢查產品規則合規率——一起做。**

### 待釐清（未確認，不得當結論）

1. **當時 `network_mode` 是 edge 還是 cloud？** 若是 cloud 而雲端逾時降級，
   則問題另有一層（雲端逾時率）；若是 edge，則純粹是 scaffold 規則問題。
2. **雲端模式下同一句話的回覆是什麼？** 雲端腦（`cloud_llm.py`）有完整 system prompt，
   應能理解「動物」的請求。**若雲端回覆正常，則此問題只影響斷網後的 edge 橋段**——
   而那正是演示的重點段落。
3. 是否有 `directive`／課程內容被載入（`_ensure_lesson()` 在此分支上不存在，
   那是 PR #7 才加的）。

**這三項須實測確認後才能定案修法。**

---

## 發現 3：三個小遊戲**開得起來，但完全不會動**（已修）

> 起因：使用者推測「今天剛部署的三個小遊戲可能已改善發現 2」，要求先測遊戲觸發。
> **測出來的結論相反**——遊戲根本沒有接上對話迴圈。

### 裝置實測（2026-07-29，`192.168.31.78:8787`）

| 檢查點 | 結果 |
|---|---|
| `POST /api/game` 開 `i_spy`/animal | ✅ 正常，回開場白與提示詞 |
| `GET /api/game` | ✅ `game=i_spy`，全域 pipeline 確實有這局 |
| 在 `/ws/talk` 說 "I see a dog." | ❌ 回「很棒，你說出完整的句子了！**跟我說一遍：What animal do you like?**」——自由對話，不是遊戲判定 |
| 再說 "I see a cat." | ❌ 同上 |
| **這局的進度** | ❌ **`turns=0`、`found=[]`**——講了兩句，一步都沒前進 |

### 根因：遊戲狀態掛在 pipeline 實例上，而每條連線都有自己的實例

- `/api/game` 動的是**全域 `pipeline` 單例**（`server/app.py:277`）
- `/ws/talk` **每條連線新建自己的 `VoicePipeline`**（`server/app.py:534`），
  只承接 `network_mode`（`app.py:538`）、**沒有承接遊戲狀態**
- 所以 `conn_pipe.game` 永遠是 `None` → `play_turn()` 直接回 `None` → 走自由對話

### 為什麼 89 條遊戲測試沒抓到

測試分別守住了 pipeline 層（`vp.play_turn`）與 HTTP 層（`/api/game`），
**中間這一段——也就是現場唯一的真實路徑——沒有任何測試**。

### 修法：遊戲狀態改為裝置級單例

`server/pipeline.py` 的 `_active_game` 模組級變數 + `VoicePipeline.game` property。
理由是這個狀態的作用域本來就是「這台裝置前面坐著的那一個孩子」，
而不是「一條 WebSocket 連線」——`app.py:274` 的既有註解已經是這個設計意圖。

⚠️ **代價（誠實記錄）**：同一行程的所有連線共用一局。單裝置（玩偶）正確；
多個孩子連同一台伺服器時會互相干擾，與 ASR/TTS in-process 單例同級的既有限制。

新增 `tests/test_games_ws_talk.py` 4 條測試守住這條路徑（先寫成紅燈、確認
`turns` 0≠1 之後才修實作）。

### 真機驗證（修復後，同一台裝置、同一支 probe 腳本）

部署後重啟 stack（`network_mode` 回到預設 `edge`），三個遊戲全部觸發：

| 遊戲 | 說的話 | 回覆 | 進度 |
|---|---|---|---|
| i_spy | "I see a dog." / "I see a cat." | 「對！找到「狗」了。還差 4 個」 | `turns=2` `found=['狗','貓']` |
| guess_who | "Is it an animal?" / "Does it start with D?" / "Is it a dog?" | 「對！還可以問 7 次。Yes, it is.」→ 兩題正確答否 | `turns=3` |
| restaurant | "I want an apple." / "I want some bread." | 「好的，一份「蘋果」！還要別的嗎？」 | `turns=2`（記在 `order`，不是 `found`） |

**三個遊戲的每一輪 `latency_ms.llm` 都是 0**——「遊戲進行中一次都不碰雲端」
這條設計在真機上得到可觀測的證實，不只是測試裡的 monkeypatch。

修復前的同一支腳本測出的是：回覆「跟我說一遍：What animal do you like?」、
`turns=0`、`found=[]`。

### 對發現 2 的影響：**假設被推翻，PR #7 的必要性維持原判**

遊戲既然不觸發，就**沒有**改善固定目標句的問題——自由對話仍是原本的格式尾巴。
本次實測順帶印證了發現 2 的根因分析正確：topic 命中詞庫時尾巴是
「What animal do you like?」，沒命中才退回「How are you today?」。
