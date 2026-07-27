# 邊緣對話 → 教師洞察 決賽彩排 runbook（TCLOUD-01/02）

**目的**：把「邊緣對話 → 教師洞察」這條敘事閉環在 Genio 520 真機上跑過
一次，並留下可稽核證據——教師儀表板顯示的診斷是**真實（非 mock）**、
不是照跑一場「畫面有東西但雲端層根本沒被走到」的空戲。

金鑰只讀環境變數，絕不寫進 repo、絕不 commit。

---

## 這次彩排驗得到什麼、驗不到什麼

| ✅ 驗得到 | ❌ 驗不到 |
|---|---|
| consent 閘門在真機上生效（未同意時零出境） | 跨機同步壓測（單機拓樸，見下段誠實說明） |
| 上傳白名單在真機上生效（只有衍生文字／分數出裝置） | `deidentify()` 對中文人名的遮罩（已知不遮，見下） |
| 去識別化在**真資料**上生效（電話數字被遮） | `ai_response_text` 的去識別化（已知未做，見下） |
| edge→cloud 轉換瞬間觸發、不必等孩子再說話 | 多學生名冊（demo 為單一學生） |
| Bedrock 真實產出四維診斷（`source` 從 rule 變 cloud） | AgentCore Harness / Memory 的持久記憶驗證 |
| 教師儀表板顯示真姓名（非硬編字串） | Nova Sonic S2S 路徑（與本 phase 無關） |

**誠實限制是本文件的核心價值**：以上「驗不到」不是被忽略，是明確排除
在本 phase 範圍外（見 `.planning/phases/11-cloud-teacher-closed-loop/`
`11-CONTEXT.md` 的 `<deferred>` 段），不得在彩排結論裡含糊帶過。

---

## 單機拓樸的誠實說明

決賽現場只有**一台** Genio 520，同一個 `server.app` process 同時扮演
「孩子講話的裝置」與「教師查看的雲端伺服器」兩個角色——沒有第二台裝置
真的把資料透過 HTTP 上傳到別的機器。

因此，決賽現場「上傳」這個詞的**實際語意**是：

1. 本機 pending 佇列升級（`store.mark_synced()`／`opportunistic_sync()`
   的 local path，直接操作同一個 SQLite，不經 HTTP）；
2. 診斷 prompt 真的離開這台機器、送到 AWS Bedrock（這才是真正跨出裝置
   的那條邊界，見 `server/diagnose.py::_build_diagnosis_prompt`）。

`sync_client.push_pending()` 的完整 HTTP 上傳路徑（含白名單投影＋
consent 閘門）由單元測試（`tests/test_sync_client.py`）涵蓋，但**現場
彩排不會走到這條路**——沒有第二台裝置可以打這支 HTTP。不得以「已完成
上傳」一語帶過這個差異；「上傳」在決賽現場真正被驗證到的是「pending
歸零 + Bedrock 出境」，不是「裝置 A 把資料傳到裝置 B」。

---

## 環境變數

只寫變數**名稱**與用途，絕不寫入任何實際金鑰值。

```bash
# 雲端大腦後端選擇——切到 bedrock 才會走原生 Converse（TCLOUD-02 的重點）
export TALKYBUDDY_CLOUD_PROVIDER=bedrock

# 家長同意閘門（demo 預設 True；彩排要驗閘門時手動改 false）
export TALKYBUDDY_CONSENT_GRANTED=true

# AWS 憑證：boto3 標準鏈（env / ~/.aws / IAM role 皆可），本 runbook 只走 env 這條
export AWS_ACCESS_KEY_ID=<由憑證管理者提供，不進 repo>
export AWS_SECRET_ACCESS_KEY=<由憑證管理者提供，不進 repo>
# 若使用臨時憑證（STS）才需要，長期 IAM 使用者金鑰不需要這個
export AWS_SESSION_TOKEN=<視憑證類型而定，不進 repo>

# region 覆蓋（可選，預設已固定台北 ap-east-2，見 bedrock_converse.DEFAULT_REGION）
export BEDROCK_REGION=ap-east-2

# 診斷路徑用的 model 覆蓋（可選，有既有預設值）
export BEDROCK_MODEL_ID_DIAG=<可選，覆蓋 bedrock_converse.DEFAULT_MODEL_ID>
```

---

## 逐步驟彩排腳本

對應 `11-04-PLAN.md` Task 4 checkpoint 的五個步驟，每步附可貼上的指令
與預期輸出。

### 步驟 1：起始狀態

```bash
# 真機上：edge/runtime/run_edge.sh 已內建啟動 uvicorn（0.0.0.0:8787）
./edge/runtime/run_edge.sh
```

瀏覽器開 `http://<真機IP>:8787/teacher`（demo 帳號 `tutor@demo` /
`demo1234`）。

**確認**：學生卡「學生檔案」區塊的姓名／學生編號／配對裝置皆為真實值
（不是佔位符 `–`）——這三個值來自 `GET /api/student_profile`，見
11-03 的 D-05 交付。

### 步驟 2：拔網路

用學生端畫面的按鈕切到 edge 模式（或真的拔乙太網路線，真機彩排建議
用後者，理由見 `docs/RELAY_VERIFY.md` 第 4 步）。與孩子完成 2–3 輪
離線對話。

**確認**：回覆正常、無多秒靜默（Phase 9 網路中斷不回歸的既有保證）。

### 步驟 3：觀察佇列

```bash
curl -s http://<真機IP>:8787/api/status | python3 -m json.tool
```

**確認**：`pending` 欄位隨離線回合遞增；教師儀表板「待同步」數字同步
增加（5 秒輪詢會自動反映）。

### 步驟 4：插回網路

切回 cloud 模式（學生端按鈕或真的插回網路線）。**不要再讓孩子說話。**

**確認三件事**：
1. 教師儀表板「待同步」數字在 5 秒輪詢內歸零
2. 「最新 AI 診斷」卡出現新的一筆（`date` 更新）
3. 診斷卡的來源徽章顯示什麼——**這是本次彩排最關鍵的一格**

### 步驟 5：佐證

見下一節「現場佐證指令」。把兩個值都記下來，回報給 checkpoint。

---

## 現場佐證指令

```bash
curl -s http://<真機IP>:8787/api/status | python3 -m json.tool
```

看 `cloud_provider` 欄位（`"bedrock"` / `"relay"` / `"none"`）。

```bash
# 先登入拿 token（tutor 角色）
TOKEN=$(curl -s -X POST http://<真機IP>:8787/api/login \
  -H 'content-type: application/json' \
  -d '{"email":"tutor@demo","password":"demo1234"}' | python3 -c \
  "import sys,json; print(json.load(sys.stdin)['token'])")

curl -s "http://<真機IP>:8787/api/diagnoses?student=STUDENT-AMING-004" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | tail -20
```

看最新一筆診斷的 `source` 欄位（`"cloud"` / `"rule"`）。

**兩者合起來才構成「大腦真的在 Bedrock」的證據**：單看 `cloud_provider`
不足——有憑證不代表該次呼叫成功，降級鏈是**刻意靜默**的，`cloud_provider`
只回報「後端有沒有設定」，不回報「這次呼叫有沒有真的走到」。`source`
才是那個唯一能當場證明的欄位（見 `11-04-PLAN.md` 的旗標假設表）。

**判定標準：**
- `source` 為 `cloud` → SC4「真實（非 mock）診斷」成立，TCLOUD-02 驗證通過
- `source` 為 `rule` → 不算通過，代表雲端層被靜默降級了

---

## 失敗時的降級劇本

決賽只有一次機會，這段不能臨場才想。

| 症狀 | 可能原因 | 現場要怎麼講 | 要切到哪個畫面 |
|---|---|---|---|
| `cloud_provider` 為 `none` | `TALKYBUDDY_CLOUD_PROVIDER` 未吃到 env | 「雲端大腦目前走離線規則式，我們示範離線韌性」 | 停留教師儀表板，講解 rule 徽章與 14 天趨勢圖，改談 Phase 9 的斷網保證 |
| `cloud_provider` 為 `bedrock` 但 `source` 為 `rule` | 憑證/區域/模型 ID/逾時其中之一失敗 | 不要說「雲端在跑」——誠實講「本次呼叫降級，正在排查」 | 切到互動紀錄表，證明對話本身完全正常，把焦點從「雲端」轉到「離線韌性」 |
| 「待同步」未歸零 | consent 未授權（回應帶 `consent_required`）或觸發未生效 | 若帶 `consent_required`：這是預期的隱私閘門，講解 D-02 的設計 | 打開瀏覽器開發者工具 Network 分頁看 `/api/network_mode` 回應，現場解釋 |
| 教師儀表板完全連不上 | server 未啟動或埠號/網段問題 | 切備援：用預錄影片或本機截圖走一遍敘事 | 備援簡報（若有準備） |

**核心原則**：任何時候都不能因為「畫面上有診斷卡」就宣稱雲端在跑——
`source` 欄位就是為了防止這種自我欺瞞而存在的。降級是本專案刻意的
韌性設計，誠實講清楚降級發生了，比硬凹雲端在跑更有說服力。
