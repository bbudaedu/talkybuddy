# 斷網彩排執行清單（2026-07-29 備妥）

> 配合 `edge/NETWORK_CUT_REHEARSAL.md` 使用。該文件定義**為什麼這樣量**，
> 本清單是**現場照著做的步驟**。
>
> 目的：回填 `NETWORK_CUT_REHEARSAL.md` §5 結果表——**至少 3 列實測、其中至少 1 列型態 B**。
> 這是 ROADMAP Phase 9 成功條件 #4，也是決賽的核心記憶點。
>
> **紅線（原文）**：「任何一項未實測就不得填數字；如實記 `blocked` 比填一個看起來
> 合理的數字有價值得多。」

---

## ⛔ 目前的阻擋：裝置上沒有雲端憑證

2026-07-29 檢查結果：裝置 `/api/status` 回傳

```json
{"asr":true,"llm":true,"tts":true,"cloud_tts":false,"cloud_llm":false,"network_mode":"edge",...}
```

**`cloud_llm` 與 `cloud_tts` 皆為 false**，裝置上無 `.env`、shell 亦無相關環境變數。

**為什麼這擋住演練**：斷網演練量的是「pipeline 多快放棄雲端、改走 edge」。
若雲端引擎本來就不可用，pipeline 根本不會嘗試雲端，M1 會是一個**沒有意義的 0**——
量到的不是「快速降級」，而是「從頭到尾都沒連過雲」。

### 需要設定的環境變數

**雲端 LLM**（`server/anthropic_relay.py::resolve_config`，兩條路擇一）：

| 路徑 | 必要變數 |
|---|---|
| Anthropic relay | `ANTHROPIC_AUTH_TOKEN` **或** `ANTHROPIC_API_KEY`（擇一即可）<br>選用：`ANTHROPIC_BASE_URL`、`ANTHROPIC_MODEL` |
| Bedrock | `TALKYBUDDY_CLOUD_PROVIDER=bedrock` + AWS 憑證 |

**雲端 TTS**：`ELEVENLABS_API_KEY`

> 參考：`/home/budaedu/hackathon/.env.aws.workshop` 可能含 AWS 憑證。

**最低可行**：只設雲端 LLM 也能演練，但 M1 的理論上界會從 3.0s（LLM 1.5 + TTS 1.5）
降為 1.5s，**須在 §5 結果表證據欄註明「本次僅雲端 LLM 可用」**，否則數字無法與
文件的 3.0s 上界對照。

### 設定方式（不必改程式碼）

```bash
ssh root@192.168.31.78
cd /root/talkybuddy
cat > .env <<'EOF'
ANTHROPIC_API_KEY=...
ELEVENLABS_API_KEY=...
EOF
# 或直接在啟動前 export，run_edge.sh 會繼承
```

設定後**重啟 stack**並確認 `/api/status` 的 `cloud_llm` / `cloud_tts` 變為 `true`。

---

## ✅ 已備妥的前置（2026-07-29 實測確認）

| 項目 | 狀態 |
|---|---|
| SSH `root@192.168.31.78` | ✅ 可連（今日曾兩次失聯，見下方風險） |
| `run_edge.sh` 已啟動 | ✅ uvicorn + llama-server 皆在 |
| `curl http://127.0.0.1:8787/api/status` | ✅ 200 |
| llama-server `/health` | ✅ 200 |
| 記憶體 | ✅ 2038MB 已用／1755MB 可用 |
| USB 麥克風 `plughw:1,0` | ✅ 收音正常（**須先按實體靜音鍵**） |
| 3.5mm 喇叭 `plughw:0,0` | ✅ 已聽測確認，`Lineout` 音量 7（39%） |
| 證據工具 `dump_recent_turns.py` | ✅ 可執行（**注意叫用方式，見下**） |

### ⚠️ `dump_recent_turns.py` 的叫用陷阱

直接 `python edge/runtime/dump_recent_turns.py` 會 **`ModuleNotFoundError: No module named 'server'`**
——Python 把 script 所在目錄（`edge/runtime`）加進 path，不是 cwd。**正確用法**：

```bash
cd /root/talkybuddy
TALKYBUDDY_PIPELINE_PROFILE=edge ./.venv/bin/python -m edge.runtime.dump_recent_turns
```

---

## 演練步驟

### 步驟 0：現場檢查（每次演練前都做）

1. **按下 USB 麥克風的實體靜音鍵**——重開機後會回到靜音，且**軟體偵測不到也控制不了**。
   驗證：`arecord -D plughw:1,0 -f S16_LE -r 16000 -c 1 -d 3 /tmp/t.wav`，
   確認 peak > 0.05 且**人聲頻段（0.5–3kHz）占比 > 25%**。
   > 只看音量會誤判：曾有一段 `peak=0.109` 但 98% 能量在 500Hz 以下，其實是噪音不是人聲。
2. 確認 `/api/status` 的 `cloud_llm` / `cloud_tts` 為 `true`。
3. 學生頁登入取得 JWT——**未登入點 `airplaneSwitch` 會收到 401**，演練無法進行。
4. **把 `network_mode` 切到 `cloud` 當起點**（否則沒有東西可降級）。

### 步驟 1：暖場（必做，不可略）

冷啟動首句 5.85s 是已知 NO-GO（`edge/EDGE_TURN_LOOP_VALIDATION.md`），
根因是固定回覆格式文字在使用者訊息尾巴、暖身焐不到。

**先講一輪暖場對話**（任意內容），把 KV cache 焐熱到穩態（純 edge LLM 穩態約 1.7–1.8s）。
**冷啟動數字不得與穩態列混算**；若要記錄冷啟動情境，另開一列並在證據欄標註「冷啟動」。

### 步驟 2：型態 A —— 回合間切換（做 ≥2 次）

1. 在雲端模式完成一個回合
2. **回合之間**按下 `airplaneSwitch`
3. 進行下一回合
4. 記錄按下開關的牆鐘時間

**預期**：M1 ≈ 0（09-01 的每回合再同步保證下一回合從一開始就不試雲端）。

### 步驟 3：型態 B —— 講話中途切換（**至少 1 次，NETCUT-03 明文要求**）

1. 在雲端模式開始一個回合，**在雲端 LLM/TTS 請求進行到一半時**按下 `airplaneSwitch`
2. 記錄按下開關的牆鐘時間

**預期**：M1 落在 0–3.0s（D-03 鎖定不做 asyncio 取消，須等雲端內層逾時到期）。

> **判讀陷阱（文件明文警告）**：型態 B 該回合寫入 DB 的 `network_mode` 欄位
> **仍是切換前的 `"cloud"`**，因為該屬性在回合開始時就讀入了。
> **要看 `llm_ms` / `tts_first_ms` 是否明顯短於雲端正常值來判斷是否降級，
> 不能只看 `network_mode` 欄位**。下一回合的 row 才會顯示 `"edge"`。這是預期行為不是 bug。

### 步驟 4：產出證據並回填

```bash
cd /root/talkybuddy
TALKYBUDDY_PIPELINE_PROFILE=edge ./.venv/bin/python -m edge.runtime.dump_recent_turns
```

把輸出**直接貼進** §5 結果表的證據欄（不是憑碼錶口述）。

- **M1 推算**：由該回合 `llm_ms` 對照純 edge 回合 `llm_ms` 的差額推得
- **M2**：以碼錶或錄影記錄「按下開關」到「聽到下一句開始播放」的牆鐘秒數
- **M2 不判定通過與否**，只如實記錄並對照 Phase 8 預算（穩態 2.96–2.99s）

---

## 今日新增的現場風險（必讀）

1. **USB 麥克風實體靜音鍵**——重開機後回到靜音，軟體無法偵測或控制。
   今日有兩次錄音全空就是這個原因，且當下看 `peak` 值看不出來。
2. **裝置會自己重開機**——今日發生過一次（uptime 掉到 43 分）。
   板上有 `mtk-wdt` 硬體看門狗（31 秒逾時），系統卡住即強制重開。
   **ALSA 設定已驗證撐得過重開機**，但麥克風實體按鍵撐不過。
3. **SSH 會無預警失聯**——今日兩次，每次數小時。
   **決賽現場不可依賴遠端連線，應規劃為完全離線操作。**

---

## 演練後

結果回填 `NETWORK_CUT_REHEARSAL.md` §5 後，依該文件 §6 決策樹判定：

- M1 全部 < 2.0s（型態 B 上界 3.0s）→ Phase 9 成功條件 #4 達成
- 若型態 B 超過 3.0s → 依 §6 檢查是否誤用了較長的 `CLOUD_LLM_TIMEOUT_S`
- 現場若想要雲端橋段品質較好，可設 `CLOUD_LLM_TIMEOUT_S=4`
  （`server/cloud_llm.py:28` 已預留），**但需重測 M1 並在結果表註明實際採用值**
