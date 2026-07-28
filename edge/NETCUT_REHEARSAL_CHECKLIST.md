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

## ⚠️ 逾時值實測結論（2026-07-29，回答 `cloud_llm.py:28` 的「待彩排確認」）

`server/cloud_llm.py:26-29` 的註解寫明預設 `CLOUD_LLM_TIMEOUT_S=1.5` 是為滿足
ROADMAP「恢復 <1–2 秒」而選的偏緊值，「代價是真正連線良好時雲端 LLM 也可能來不及
回覆而降級到 edge 品質⋯⋯**最終值待 09-04 彩排實測確認**」。

**實測答案（經 SSH 反向隧道連使用者自建中轉，`claude-sonnet-5`，`max_tokens=24`，6 次）**：

| 次數 | 延遲 |
|---|---|
| 1–4 | 1698 / 1948 / 1725 / 1817 ms |
| 5–6 | 4020 / 2563 ms |

**中位數 1883ms、最小 1698ms、最大 4020ms —— 6/6 全部超過 1.5s 門檻。**

### 這代表什麼

**在預設 `CLOUD_LLM_TIMEOUT_S=1.5` 下，雲端 LLM 每次都會逾時、自動降級到 edge，
即使完全沒有按 kill-switch。** 後果有二：

1. **演練會量到假結果**——型態 A 看不出任何差別，因為雲端本來就一直在失敗
2. **現場演示沒有對比**——「雲端模式」實際上跑的是 edge，觀眾看不到降級前後的差異

### 這是設計上的真實張力，不是 bug

- M1 < 2s 門檻 → 需要**短**逾時
- 雲端真的能用 → 需要逾時 **> 實際延遲（中位數 1.9s）**

兩者直接衝突。

### 建議的解法（利用型態 A 的特性）

**設 `CLOUD_LLM_TIMEOUT_S=4` 並以型態 A（回合間切換）作為演示橋段。**

理由：型態 A 的 M1 與逾時值**無關**——開關對下一回合立即生效，該回合從一開始
就不會嘗試雲端（`NETWORK_CUT_REHEARSAL.md` §1：「M1 理論上趨近於 0」）。
所以可以同時取得：

| | |
|---|---|
| 雲端真的會回應（逾時 4s > 延遲 1.9s） | ✅ 演示有對比 |
| 型態 A 的 M1 ≈ 0 | ✅ 遠低於 2s 門檻 |

**型態 B 仍須做**（NETCUT-03 明文要求），但要誠實記錄：逾時設 4s 時，
型態 B 的 M1 上界也會變成約 4s，**超過文件原本寫的 3.0s 上界**，
結果表證據欄必須註明本次採用的逾時值。

> ⚠️ **延遲來源說明**：本次量測經 SSH 反向隧道（裝置與中轉不同網段），
> 隧道本身在區網內僅增加毫秒級延遲，1.9s 主要是中轉到上游模型的真實往返。
> **但決賽當天若改用不同的雲端路徑，須重新量一次**——這個數字是路徑相依的。

---

## ✅ 憑證阻擋已解除（2026-07-29）

目前狀態：

```json
{"asr":true,"llm":true,"tts":true,"cloud_tts":false,"cloud_llm":true,"network_mode":"edge",...}
```

`cloud_llm` **已為 true**（使用者於 2026-07-29 提供自建中轉憑證）。
`cloud_tts` 仍為 false（無 `ELEVENLABS_API_KEY`）——可演練，但 M1 理論上界少掉 TTS 那 1.5s。

**兩項本次為此打通的設定**（決賽當天須重新確認）：

1. `run_edge.sh` 現會自動載入 `<TARGET_ROOT>/.env`（先前不會，即使檔案存在
   `cloud_llm` 仍是 false——這個坑在此踩過一次）。
2. **SSH 反向隧道**：裝置在 `192.168.31.x`、中轉在 `192.168.100.200`，
   不同網段且裝置無 tailscale。以 `ssh -N -R 127.0.0.1:8317:192.168.100.200:8317`
   讓裝置經開發機連到中轉，`.env` 的 `ANTHROPIC_BASE_URL` 指向 `http://127.0.0.1:8317`。
   包裝腳本在 scratchpad 的 `tunnel.sh`。
   **此隧道僅為賽前演練用；決賽當天若裝置能直連網際網路則不需要，
   但 `ANTHROPIC_BASE_URL` 必須改回真實端點。**

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

## 🔴 演練前必查：裝置上的程式碼是否為最新版

**2026-07-29 踩到**：準備演練時發現裝置上的 `server/` 是**部分過期**的——
`server/app.py` 已含 Phase 9 的 NETCUT-01 與 JWT 閘門，但：

| 檔案 | 裝置上（過期） | repo（正確） |
|---|---|---|
| `server/cloud_llm.py` | `_TIMEOUT_S = 8.0`（**寫死，不吃環境變數**） | `float(os.environ.get("CLOUD_LLM_TIMEOUT_S", "1.5"))` |
| `server/diagnose.py` | 無 `allow_cloud` 閘門 | 有（Phase 9 NETCUT-02） |

**若沒發現就直接演練，量到的會是舊版行為，整份結果無效**——`CLOUD_LLM_TIMEOUT_S`
設什麼都沒用，實際跑的是寫死的 8.0s。

### 每次演練前的版本確認

```bash
ssh root@192.168.31.78 'cd /root/talkybuddy && \
  grep -n "_TIMEOUT_S" server/cloud_llm.py | head -2 && \
  grep -c allow_cloud server/diagnose.py'
```

期望：`_TIMEOUT_S: float = float(os.environ.get(...))` 且 `allow_cloud` 計數 > 0。

### 同步方式（不要用 push.sh）

`edge/deploy/push.sh` 會連 1GB GGUF 與交叉編譯產物一起推，
而大檔傳輸在這台裝置上經常斷線。只同步真正會變的三個目錄即可：

```bash
for d in server edge/runtime web; do
  rsync -az --exclude='__pycache__' --exclude='*.pyc' \
    -e "ssh -o ServerAliveInterval=15" "$d/" root@192.168.31.78:/root/talkybuddy/$d/
done
```

無 `--delete`，不會刪掉裝置上的 `.env`。同步後**必須重啟 stack** 才會生效。

---

## 逾時值最終建議（2026-07-29 實測）

同步程式碼後以 `CLOUD_LLM_TIMEOUT_S=4` 實測 `cloud_llm.generate()`：

| # | 延遲 | 結果 |
|---|---|---|
| 1–3 | 2332 / 2252 / 2361 ms | ✓ 雲端 |
| 4 | 4010 ms | ✗ 降級（撞到 4s 上限） |

**成功 3/4。** 典型延遲 2.3s，但尾端偶爾超過 4s。

- 若演示需要**更穩的雲端回覆**，可提高到 5–6s；型態 A 的 M1 不受影響（≈0），
  但型態 B 的 M1 上界會同步升高，須在結果表註明。
- 目前 `.env` 採用 **4**。

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

---

## 附錄：PR #7 評估（2026-07-29 決定不合併）

`origin/master` 有一個本分支未合併的 PR：`658383d 減少機械感 + 教學引導鷹架
+ 多人併發 segfault 修復 (#7)`。**決賽前決定不合併**，但結論須記錄以免重複評估。

### 併發 segfault 修復——對 edge 不適用，對 cloud 是真風險

| 部署 | LLM | ASR / TTS | segfault 風險 |
|---|---|---|---|
| **Edge**（玩偶） | `llama-server` **獨立行程**（HTTP） | in-process，但單一連線 | ❌ 不會發生 |
| **Cloud**（伺服器） | 雲端 API | **in-process 單例，所有連線共用** | ✅ **真實** |

PR 作者的重現條件是「3 個並發連線各講 4 輪話，容器直接被砍」——那是**雲端多學生**
的情境。`server/asr_sensevoice.py` 與 `server/tts.py` 兩個 profile 共用，
雲端模式下所有學生的 ASR/TTS 打同一個模型單例。

> ⚠️ **論述漏洞**：`docs/NEEDS_EVIDENCE.md` 與 `docs/OPERATIONS_MODEL.md` 都講
> 「一位老師照顧多個孩子」。若評審問「同時 30 個孩子用會怎樣」，
> **現況的誠實答案是「會 segfault」**。這是 v2 必修項。

### scaffold 改動——有價值但改變回覆行為

- 鼓勵語原本用**輸入文字 hash** 挑，同一句話問幾次永遠拿到一模一樣的罐頭回覆
  （舞台上很致命），PR 改為依 `turn_index` 輪替
- `diagnose.companion_directive` 的 `fallback_hint` 措辭改動**會流進雲端腦的
  system prompt**（`pipeline.py:285` → `cloud_llm.py:83-89`），
  原因是「LLM 會照著『退回』這個框架生成語氣，講出讓學生覺得被貼標籤的話」

### 不合併的理由

1. 合併有衝突（`server/llm.py`、`server/pipeline.py`）
2. 改雲端 LLM 的 system prompt **正是** `edge/PROMPT_ORDERING_FINDING.md` 記載
   害中文稱讚合規率 5/5→0/5 的那類改動，決賽前 2 天引入未驗證行為變更風險過高
3. 決賽演示為單一主持人 edge 情境，segfault 不會觸發

### v2 backlog

- **併發鎖應優先移植**：只動 4 個檔案的推論呼叫包裝
  （`asr_sensevoice.py`+11／`asr_whisper.py`+13／`tts.py`+4／`llm.py`+20），
  **不改變任何回覆內容**，不需重跑合規驗證，可與 scaffold 改動分開處理
- scaffold 167 行改動需連同中文稱讚合規率重新驗證後再上

### 決賽現場規避

演示話術**刻意讓每輪講不同句型**，即可繞開 hash 相同回覆的問題（非程式改動）。
