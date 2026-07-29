# Network-Cut Demo Rehearsal — 斷網彩排腳本（NETCUT-03）

**建立日期：2026-07-25**
**適用需求：** NETCUT-03（`.planning/REQUIREMENTS.md`）—— ROADMAP Phase 9 success criterion #4「≥3 次實體斷網重複演練（含講話中途斷網），每次恢復時間 <1–2 秒」
**狀態：** 腳本與工具已交付；**真機實測回填為 pending human-verify 項目**（見 §5、`human_verify_mode: end-of-phase`，由 `/gsd-verify-work` 於 phase 結束時收取）

本文件語氣與結構比照 `edge/EDGE_TURN_LOOP_VALIDATION.md`（Phase 8 真機驗證紀錄）：實測表格、GO/NO-GO 判定、如實記錄未達標項目。

---

## §0 前置條件

- **裝置**：Genio 520（Hti hub G520，Yocto `Rity Demo Layer 25.1.1-release (scarthgap)`），SSH `root@192.168.31.78`，經 Tailscale subnet router 路由（見 `edge/BOARD_BRINGUP_DECISION.md` §2）。
- 裝置上 `run_edge.sh` 已啟動，且已通過本機健康檢查：
  ```
  curl -sf http://127.0.0.1:8787/api/status
  ```
  回傳 200 才繼續。
- 學生頁（`web/index.html`）已在瀏覽器登入（需先有 JWT）——自 09-01 起 `POST /api/network_mode` 已加上 JWT 閘門（`identity_from_header`），登入遮罩保證前端 `airplaneSwitch` 呼叫時已帶 token；未登入直接點擊會收到 401，無法完成演練。
- **依 08-05 殘留缺口，正式演練前先跑一輪暖場對話**：`edge/EDGE_TURN_LOOP_VALIDATION.md` 已證實暖身後冷啟動首句仍 5.85s NO-GO（根因是固定回覆規則文字位於使用者訊息尾巴、暖身無法命中）。本彩排的 M1/M2 皆針對「穩態」而非「冷啟動」，因此開場後、正式開始 §3/§4 演練前，主持人須先講一輪暖場對話（任意內容即可），把 KV cache 焐熱到穩態區間，冷啟動情境不得與本文件的穩態數字混算（詳見 §0 最後一段）。
- **當前雲端逾時預設值**（09-02 已縮短，可用同名環境變數覆寫，不必改程式碼）：
  - `CLOUD_LLM_TIMEOUT_S=1.5`（預設，`server/cloud_llm.py::_TIMEOUT_S`）
  - `CLOUD_TTS_TIMEOUT_S=1.5`（預設，`server/config.py::CLOUD_TTS_TIMEOUT_S`）
  - 兩者皆透過 `float(os.environ.get(...))` 讀取，啟動 `run_edge.sh` 前設定環境變數即可覆寫，無需改程式碼、無需重新部署。
- **冷啟動情境如需涵蓋**：若演練時想額外記錄「裝置剛開機、尚未暖場」的情境，必須另開一列並在證據欄明確標註「冷啟動」，不得與穩態列混算平均或放入同一列。

---

## §1 恢復時間的操作定義（本節是整份文件的核心）

ROADMAP 的「恢復時間 <1–2 秒」在 09-RESEARCH.md 被列為本 phase 唯一的 Open Question：它可能指「pipeline 決定放棄雲端、改走 edge」，也可能指「孩子真的聽到回覆」。這兩個讀法對應完全不同的可達成範圍，若不先拆開定義就上場演練，只會得到「量到 3 秒，算過還是沒過？」的爭論。本文件依 09-RESEARCH.md Open Questions #1 的建議，把它拆成 M1 與 M2 兩個獨立欄位。

### M1 — 降級決策延遲（適用 ROADMAP `<1–2 秒` 門檻）

**定義：** 從主持人按下 `airplaneSwitch` 到 pipeline 放棄雲端引擎、改由 edge/scaffold 產出該回合回覆為止的時間。

- **型態 A（回合間切換）理論值 ≈ 0**：開關對「下一回合」立即生效，由 09-01 的每回合再同步保證（`conn_pipe.network_mode = pipeline.network_mode` 在每次 `run_turn_audio`/`run_turn_text` 呼叫前重新讀取全域狀態）。若下一回合開始時開關已切到 edge，該回合從一開始就不會嘗試雲端引擎，M1 理論上趨近於 0。
- **型態 B（回合中切換）理論上界 = 3.0 秒**：若主持人在雲端 LLM/TTS 請求進行到一半才按下開關，本回合已經在跑的雲端呼叫**不會被取消**（D-03 鎖定決策：不做 asyncio 取消/重跑機制），只會等到雲端呼叫自己的內層逾時（`CLOUD_LLM_TIMEOUT_S=1.5` + `CLOUD_TTS_TIMEOUT_S=1.5`）到期才降級。**最壞情況兩階段合計上界為 3.0 秒**（LLM 1.5s + TTS 1.5s）——此上界必須在本文件明寫，不得只寫 1.5s，因為若切換發生在 LLM 呼叫剛開始的瞬間，該回合仍可能需要先吃完 LLM 的 1.5s 逾時、才輪到 TTS 階段再吃一次 TTS 逾時（視切換時機落在哪個階段而定）。

### M2 — 可聽見回覆恢復時間

**定義：** 從按下開關到孩子聽見下一句回覆開始播放。

**明確不套用 1–2 秒門檻**，改為繼承 Phase 8 已接受的 edge 回合預算（出處 `edge/EDGE_TURN_LOOP_VALIDATION.md`）：
- 穩態：2.96–2.99s（GO）
- 暖身後冷啟動：5.85s（NO-GO，現場以「主持人先暖場一輪」規避）

只如實記錄與對照，**不判定通過/不通過**——M2 是繼承而來的既有預算，不是本 phase 的新門檻。

### 為何 M1 與 M2 必須分開，不可混算

若把 M2（可聽見回覆）硬套 1–2 秒門檻，等同要求 Phase 8 重新開放範圍（edge 回合本身已知需要 2.96–5.85s）——門檻會同時變成「不可能達成」且「無意義」（09-RESEARCH.md Open Question 1 的原話）。M1 才是 ROADMAP `<1–2 秒`真正可達成、也真正對應「pipeline 有沒有快速放棄雲端」這件事的量測對象。

### 誤讀防範：型態 B 中途切換時，DB 的 `network_mode` 欄位仍為切換前的值

型態 B 演練時容易誤讀的一點：該回合寫入 DB 的 interaction row（`server/pipeline.py:300-313`）其 `network_mode` 欄位，記的是**該回合起始時**承接的模式（切換**前**的 `"cloud"`），因為 `self.network_mode` 是在回合一開始被讀入、寫入 row 時取的還是同一個屬性值（除非切換發生在寫入 row 之前的極短窗口）。**判斷該回合是否已降級要看回覆內容與 `latency_ms`（尤其 `llm`/`tts_first` 是否明顯短於雲端正常值），不能只看該筆 row 的 `network_mode` 欄位**——下一回合的 row 才會確定顯示 `"edge"`。這是 D-03「不做取消機制」的預期行為，不是 bug。

---

## §2 量測方法

**主要證據來源**：裝置端執行

```bash
ssh root@192.168.31.78 "cd /root/talkybuddy && .venv/bin/python -m edge.runtime.dump_recent_turns --limit 5"
```

（`edge/runtime/dump_recent_turns.py`，Task 2 產出）把輸出表（含 `ts` / `network_mode` / `llm_ms` / `tts_first_ms` / `round_total_ms` / `synced`）**直接貼回 §5 結果表的證據欄**，作為客觀紀錄，不是憑碼錶口述。

- **M1 推算**：由該回合的 `llm_ms`（對照純 edge 回合的 `llm_ms` 差額）推得——差額 ≈ 雲端放棄前多等待的時間。型態 B 若該回合 `llm_ms` 明顯高於純 edge 值（純 edge LLM 穩態約 1.7–1.8s，見 `edge/EDGE_TURN_LOOP_VALIDATION.md` A 節熱#2/熱#3）且低於雲端逾時上界，可推知中途切換確實觸發了雲端內層逾時降級。
- **M2 輔助**：以碼錶或錄影計時，記錄「按下開關」到「聽到下一句開始播放」的牆鐘秒數。
- **時間對齊**：演練時同步記錄按下開關的牆鐘時間（例如手機碼錶起始時刻），方便與 dump 輸出的 `ts` 欄位對齊比對。

---

## §3 演練型態 A — 回合間切換（回合之間按下開關）

1. 確認 badge 顯示為 `cloud`（學生頁 modeBadge）。
2. 完成一輪正常對話（雲端模式，確認雲端引擎確實被呼叫、回覆有雲端特徵）。
3. 在孩子**沒有說話的空檔**按下 `airplaneSwitch`。
4. 觀察 badge 變為 `edge` + toast 顯示「✈️ 飛航模式開啟，雲端已斷線 — 邊緣運算持續對話」。
5. **立刻**再說一句，記錄：
   - M1（本次應趨近 0，因為切換發生在回合之間，下一回合從一開始就走 edge）
   - M2（可聽見回覆恢復時間）
   - 執行 `dump_recent_turns.py`，把輸出貼進 §5 該列證據欄

---

## §4 演練型態 B — 講話中途切換（NETCUT-03 明文要求的情境）

1. 確認 badge 顯示為 `cloud`。
2. 孩子開始說話並送出，讓回合進入雲端 LLM 階段（可由主持人肉眼判斷「已經開始等回覆」的時間點，或以裝置端 log 觀察雲端請求是否已發出）。
3. **在該回合仍在處理中**（雲端 LLM 或 TTS 階段尚未完成前）按下 `airplaneSwitch`。
4. 記錄：
   - 該回合**是否仍完整完成**（D-03 的承諾：該輪對話仍會完成，只是改走 edge 回覆，不會卡住或無聲失敗）
   - M1（本次應落在 0 到 3.0s 理論上界之間，取決於切換發生在雲端呼叫的哪個階段）
   - M2（可聽見回覆恢復時間）
   - 是否出現任何**多秒靜默**（若有，記錄靜默秒數與發生時機——這是判定 NO-GO 的關銵訊號之一）
   - 執行 `dump_recent_turns.py`，把輸出貼進 §5 該列證據欄
5. **再說一句**，確認下一回合完全走 edge（badge 已是 edge，`network_mode` 欄位這次應正確反映 edge）。

---

## §5 結果表（演練時回填）

| # | 日期 | 型態 | M1 降級決策(s) | M2 可聽見(s) | GO/NO-GO | 證據（dump 輸出摘要 / 備註） |
|---|------|------|----------------|--------------|----------|-------------------------------|
<!-- 範例 -->
| 0 | 2026-07-25（範例，非實測） | A | 0.1 | 2.98 | GO | `# ts network_mode llm_ms tts_first_ms round_total_ms synced` → `1 2026-07-25T20:10:00+08:00 edge 1780 950 2960 True`（範例格式，不是真數字） |
| 1 | 2026-07-29 | A | **≈ 0** | `blocked` | GO（僅 M1） | 切換後回合 `llm_ms=4310`，與本次純 edge 基準同區間 → 該回合從頭走 edge。**採用 `CLOUD_LLM_TIMEOUT_S=4`**。⚠️ 走 API 路徑（`text_input` + `/api/network_mode`），**未經麥克風/瀏覽器**；M2 需碼錶，未量 |
| 2 | 2026-07-29 | A | **≈ 0** | `blocked` | GO（僅 M1） | 切換後回合 `llm_ms=5315`，同上。同一連線、暖場後穩態 |
| 3 | 2026-07-29 | B（軟體切換） | **未測到降級** | `blocked` | 見下方註記 | 回合開始後 709ms 按切換，該回合 `llm_ms=2463` —— **比純 edge 更快**，代表雲端仍跑完、該回合完全沒降級。非失敗，是 D-03「不取消已發出請求」的預期行為 |
| 4 | 2026-07-29 | B（真實斷網） | `blocked` | `blocked` | `blocked` | 正要以「回合中切斷反向隧道」製造雲端無回應時**裝置失聯**（當日第 4 次），未取得數據 |

**至少 3 列實測，且其中至少 1 列型態為 B**。未實測一律留空或記 `blocked`，**禁止填入未實際量到的數字**。

### ⚠️ 本次演練的三個限制（讀結果表前必看）

1. **型態 B 尚未真正測到降級**，NETCUT-03 的要求**未滿足**。原因是方法論問題：
   `airplaneSwitch` 切的是 `network_mode`，只決定**下一回合要不要嘗試雲端**，
   不會取消已發出的請求。當雲端健康（本次 1.7–2.3s 就回應）時，回合中途切換
   的那一回合仍會跑完雲端。**要測到型態 B 的降級，雲端必須真的無回應**——
   對應現場是拔網路線，不是按開關。§4 的步驟描述應補上這一點。
2. **M1 的基準值與文件既有記載不符。** §2 用「純 edge LLM 穩態約 1.7–1.8s」
   當基準，但本次實測純 edge `llm_ms` 是 **4442–5056ms**。若用 1.7s 當基準，
   型態 A 的 M1 會算成 2.5s 而被判 NO-GO；用當下實測基準則 ≈ 0。
   **基準值的選擇直接決定 GO/NO-GO，必須用同一場演練的實測值。**
   這個落差與 PR #7 無關（合併前後都一樣慢，見 `PR7_MERGE_VALIDATION_2026-07-29.md`），
   一個未驗證的線索是 `llama-server` 目前跑 `--threads 4`。
3. **M2 全部 `blocked`**：需要碼錶/錄影記錄牆鐘時間，且要走真實的麥克風與
   瀏覽器路徑，本次 API 路徑量不到。

---

## §6 判定與後續動作（決策樹）

1. **全部 ≥3 次的 M1 皆 <2.0s 且無多秒靜默** → NETCUT-03 **GO**。

2. **若某次 M1 超過型態 B 的理論上界（3.0s）**：
   - 先確認是否為冷啟動情境或忘記做暖場——若是，重跑一次（先做暖場、再重新演練）。
   - 仍超過理論上界則調降 `CLOUD_LLM_TIMEOUT_S` / `CLOUD_TTS_TIMEOUT_S`（環境變數即可，不必改程式碼，`run_edge.sh` 啟動前 export 即生效）。
   - 若決定改動**預設值**（即改 `server/cloud_llm.py::_TIMEOUT_S` 或 `server/config.py::CLOUD_TTS_TIMEOUT_S` 的 fallback 字串本身，而非只設環境變數），必須同步更新：
     - `tests/test_cloud_tts_config.py` 的預設值斷言
     - 確認 `tests/test_pipeline_timeout_isolation.py` 的常數契約仍成立（雲端逾時 `<= 2.0`、`server/pipeline.py::LLM_TIMEOUT_S >= 6.0`）——這條測試會在契約被打破時明確失敗並指出來源測量依據，不會靜默通過。

3. **若在真正連線良好時發現雲端 LLM 幾乎每次都來不及回覆、整場退化成 edge 品質**：
   - 這是 `1.5s` 偏緊的預期代價（09-RESEARCH.md Assumption A1：1.5s 是保守估計，未在真實會場網路路徑上實測）。
   - 若現場希望雲端橋段有較好的回覆品質，設 `CLOUD_LLM_TIMEOUT_S=4` 即可（`server/cloud_llm.py:28` 已預留此註記），但需重測 M1 並在 §5 結果表註明本次演練實際採用的逾時值。

4. **紅線**：任何一項未實測就不得填數字；如實記 `blocked` 比填一個看起來合理的數字有價值得多。NETCUT-03 的全部驗收價值在於「這些數字真的量過」——一旦允許推估或補填，整條需求即失去驗收意義。
