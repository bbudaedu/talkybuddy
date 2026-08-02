# 說說學伴 TalkyBuddy

一隻會聽、會想、會說的英語學伴玩偶，設計給師資與網路都不穩定的偏鄉國小教室——**孩子開口對話走邊緣，離線也不中斷；老師端的差異化教學判斷走雲端，多一份 AI 協助但不是必需品**。同一份教材，系統依每個孩子的練習狀況分派不同的路：超前的給延伸、落後的降階並請老師介入。

## 為什麼做這個

台灣偏鄉國中英語「待加強」比例已超過五成，是一般地區的近兩倍；偏遠地區代理教師比例逼近四分之一，且逐年上升。政府投入資源發放平板，但缺的從來不是硬體，是能陪一個孩子反覆開口練習、又不會累的那個人。完整的數據查證過程與出處見 [`docs/NEEDS_EVIDENCE.md`](docs/NEEDS_EVIDENCE.md)。

## 核心功能

- **語音陪聊**：ASR（SenseVoice）＋規則式鷹架引擎（`server/scaffold.py`，零外部依賴、永遠可用）＋LLM（邊緣 Qwen2.5-1.5B / 雲端 Bedrock）＋TTS（邊緣 VITS / 雲端 Polly 童聲）。鷹架帶讀不糾錯、不打斷開口意願。
- **四維診斷**：發音、流暢度、詞彙、文法，規則式為主幹、雲端 LLM 可加值，14 天趨勢追蹤。
- **教師端全班分層儀表板**：老師貼上課文，AI 提煉重點單字推給全班；系統依每個孩子的練習狀況分派路徑（延伸下一課／鞏固練習／跟讀模式／需老師介入），同一份教材、每個孩子走不同的路。
- **背景 agent**：派作業、家長週報、教材提煉三支 agent（`server/agents/`），走 AgentCore Harness → Bedrock Converse → 規則式的三層降級鏈，決策與執行分離，依學生 ID 分群記憶。
- **間隔重複複習（SRS）**：依答對/答錯排程下次複習時機。
- **四款互動小遊戲**：純規則判定，離線與連線行為逐字一致。
- **隱私與合規閘門**：語音音檔不落地、不上雲；`aws_only` 合規開關；上雲前去識別化；家長同意為資料出境的 chokepoint。詳見 [`docs/PRIVACY.md`](docs/PRIVACY.md)。

## 系統架構

三張圖分別回答三個問題——今天真的在跑什麼、裝置端全雙工串流長什麼樣、雲端要往哪裡去——都標了驗證狀態，見 [`docs/ARCHITECTURE_DIAGRAMS.md`](docs/ARCHITECTURE_DIAGRAMS.md)。設計核心是一條**kill-switch**：裝置端全部本地推論、斷網照跑；雲端端（AgentCore/Bedrock）斷線時整塊消失，但不影響孩子繼續對話——降級不是 `try/except` 的 `except` 分支，是架構分界線。完整的取捨敘事見 [`docs/ARCHITECTURE_NARRATIVE.md`](docs/ARCHITECTURE_NARRATIVE.md)。

## 技術棧

| 層 | 技術 |
|---|---|
| 邊緣 ASR | sherpa-onnx + SenseVoice-Small（int8） |
| 邊緣 LLM | llama.cpp + Qwen2.5-1.5B-Instruct（GGUF） |
| 邊緣 TTS | sherpa-onnx（VITS，中／英） |
| 雲端 LLM | Amazon Bedrock Converse（Claude Sonnet 5 / Haiku 4.5，依用途分流） |
| 雲端 Agent | Amazon Bedrock AgentCore（Harness × 4：編排／派作業／週報／教材，＋ Memory） |
| 雲端 TTS | Amazon Polly（英文段童聲、中文段 neural） |
| 雲端部署 | ECS Fargate ← ALB ← CloudFront（HTTPS/WSS） |
| 資料庫 | SQLite（單機） |
| 前端 | 原生 HTML／CSS／JS，無框架 |
| 邊緣硬體（roadmap） | MediaTek Genio 520（6× Cortex-A55 + 2× Cortex-A78），NPU 尚未參與推論 |

## 快速開始（本機開發）

```bash
cd /home/budaedu/talkybuddy   # 注意：不是 /home/budaedu/hackathon/talkybuddy，那是已經分岔的舊副本
bash scripts/setup_env.sh     # 建立 .venv，安裝依賴，下載邊緣模型到 models/
bash scripts/run.sh           # 啟動 FastAPI 伺服器
```

- 學生端：`http://localhost:8787`
- 教師端：`http://localhost:8787/teacher`
- 手機同網段：`http://<本機區網 IP>:8787`（`hostname -I` 查本機 IP）

伺服器啟動時會初始化資料庫並灌入示範資料（首次啟動）、把三顆邊緣引擎的預熱丟到背景執行緒，不擋住伺服器啟動。雲端功能（Bedrock／AgentCore／Polly）需另外設定 AWS 憑證與環境變數，正式雲端部署流程見 [`docs/DEPLOY_CLOUD.md`](docs/DEPLOY_CLOUD.md)；邊緣裝置部署見 [`docs/DEPLOY_EDGE.md`](docs/DEPLOY_EDGE.md)。

## 測試

```bash
./run_tests.sh
# 或
.venv/bin/python -m pytest tests/ -q
```

測試涵蓋 ASR/LLM/TTS 降級鏈、agents 決策與 schema 驗證、隱私閘門、遊戲判定的離線/連線一致性、教師儀表板等面向。**請執行上述指令查看當下的實際通過情況**——這份 README 不寫死通過筆數，因為那個數字比程式碼變動得更快，寫死了很快就是另一個過期的謊言。

## 已知限制

- **半雙工**：單一對話 session 同一時間只能跑一輪（`asyncio.Lock`），重入會收到 `busy`。
- **webm → wav 轉檔**依賴系統 `ffmpeg` subprocess（逾時 10 秒）；邊緣裝置刻意不安裝 ffmpeg，改用 ALSA 直接擷取 16k mono，規格不符時會明確拋錯而非靜默降級。
- **喚醒詞不可用**：真人測試約 11 次僅 1 次命中，已判定 NO-GO；現場觸發改用裝置 power 鍵短按。
- **NPU 尚未參與任何推論**：實測 NeuronExecutionProvider 為 0/0 個算子相容，三顆邊緣引擎目前全在 CPU 上運行。
- **AgentCore 降級鏈是靜默的**：回應的 `source` 欄位只有 `cloud`/`rule` 兩值，無法從 API 回應本身區分這一輪走的是 AgentCore 還是純 Bedrock，需要比對日誌佐證。
- **單機 SQLite**：`student_id`/`device_id` 目前為固定值場景設計，未實作多裝置、多學生的真實帳號系統與衝突解決。
- **教師端可視性與無障礙**（教室後排字級對比度、鍵盤導覽、`aria-label` 完整度）尚未完整驗證。

## 文件索引

| 文件 | 內容 |
|---|---|
| [`docs/ARCHITECTURE_DIAGRAMS.md`](docs/ARCHITECTURE_DIAGRAMS.md) | 系統架構三視圖（現況／裝置端管線／雲端目標），含驗證狀態 |
| [`docs/ARCHITECTURE_NARRATIVE.md`](docs/ARCHITECTURE_NARRATIVE.md) | 生成式 AI 使用架構的設計取捨敘事 |
| [`docs/AGENTCORE_ARCHITECTURE.md`](docs/AGENTCORE_ARCHITECTURE.md) | Bedrock AgentCore 架構設計的權威版本與遷移路徑 |
| [`docs/OPERATIONS_MODEL.md`](docs/OPERATIONS_MODEL.md) | 落地維運模型：部署、成本、資料治理 |
| [`docs/PRIVACY.md`](docs/PRIVACY.md) | 隱私與資料最小化政策，逐條對應原始碼行號 |
| [`docs/GAMES.md`](docs/GAMES.md) | 四款互動遊戲設計與課綱依據 |
| [`docs/DEPLOY_CLOUD.md`](docs/DEPLOY_CLOUD.md) | 雲端部署指南 |
| [`docs/DEPLOY_EDGE.md`](docs/DEPLOY_EDGE.md) | 邊緣裝置（Genio 520）部署指南 |
| [`docs/NEEDS_EVIDENCE.md`](docs/NEEDS_EVIDENCE.md) | 痛點論述的數據查證、更新與出處 |
| `edge/EDGE_TURN_LOOP_VALIDATION.md` | 邊緣回合迴圈真機實測數據 |
