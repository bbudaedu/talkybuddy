# 交接：決賽現場改乙太直連筆電，開新的 Claude Code session

**時間**：2026-08-01，決賽當天，會場。
**觸發**：開發機（`Ubuntu-AI-Server`）改用乙太網路線直連筆電，使用者要在筆電開
Claude Code、SSH 控制，並把專案 repo 抓到筆電。

## 讀這份文件前最重要的一件事：**auto-memory 不會跟著過去**

Claude 的 auto-memory（`MEMORY.md` + 各則 `.md` 記憶檔）存在
`/home/budaedu/.claude/projects/-home-budaedu-talkybuddy/memory/`，
**這個目錄只存在於這台開發機上**，是依專案目錄路徑產生的 key，不會因為
git clone 或改路徑就出現在筆電上。筆電開的 Claude Code 是**完全空白的新
session**，不知道這份文件之前的任何一次對話、任何一次真機實測結果。

已經查過：`-home-budaedu-talkybuddy-pipecat`（另一個 worktree 對應的
project 目錄）底下只有原始對話 log（`.jsonl`），**沒有**整理過的
auto-memory。也就是說 pipecat 那條分支目前完全沒有精煉過的記憶檔可用。

**How to apply**：
1. 筆電上的新 Claude session 開始工作前，先請它讀這份檔案
   （`.planning/HANDOFF-2026-08-01-laptop-transfer.md`）建立上下文。
2. 如果要把這台機器已經整理好的 auto-memory 也帶過去，另外
   `scp -r budaedu@192.168.100.200:/home/budaedu/.claude/projects/-home-budaedu-talkybuddy/memory /path/on/laptop/`
   （這是選配，不做也能工作，只是筆電的 Claude 要重新累積這些教訓）。

## 這次 session 做的事（`talkybuddy` / master worktree）

任務：實測 Amazon Polly 能不能取代/輔助邊緣 TTS。**只做實測，沒有改任何
既有專案程式碼**（只新增檔案）。

**已查證的結論**（完整版在 auto-memory `project_tts_polly_findings.md`）：
- Polly 中文只有 `Zhiyu`(cmn-CN)/`Hiujin`(yue-CN)，**都是成人女聲，沒有中文
  童聲**。英文 `Ivy`/`Justin`/`Kevin` 才有官方認證的童聲。
- **英文 voice 唸中文是完全不出聲**（實測 pcm 時長 0.01s，不是外國腔），
  使用者親耳試聽 `ivy_zh.mp3` 確認過。
- 中英混合句丟給單一英文 voice 只會唸出英文段，中文被跳過——使用者試聽
  `ivy_mix.mp3` 確認過。
- **分段合成＋拼接的解法可行**：英文段用 Ivy、中文段用 Zhiyu，各自呼叫
  Polly、段間插 150ms 靜音（對齊 `server/tts.py` 既有慣例）再拼接，做出
  `demo_mix.mp3`，使用者試聽確認可以接受。
- Polly 呼叫本身在這台開發機到 `us-west-2` 量到的延遲：穩態每句
  **230–290ms**（第一次連線多花約 550ms 握手）。**這不是 Genio 520 真機
  的網路路徑**（真機走乙太→手機熱點→行動網路），數字僅供參考。

**新增檔案**（都在 `talkybuddy` master worktree，未 commit）：
- `edge/probes/probe_tts_latency.py` — 雙邊基準測試腳本：edge 量
  `TTSEngine.synth()` 的 RTF（合成秒數/音訊秒數），cloud 量 Polly
  `synthesize_speech` 的網路來回秒數。**要在 Genio 520 實機上跑**才有意義
  （這台開發機沒裝 sherpa-onnx 模型，edge 那段會自動跳過）。
- `scratch_polly_listen/*.mp3` — 給使用者試聽用的音檔，聽完可以整個資料夾
  刪掉，不影響任何東西。

**⚠️ 重要落差**：`talkybuddy-pipecat`（`feat/pipecat-edge`）那邊早就有更成熟
的端對端基準測試 `edge/probes/probe_latency_cloud_vs_edge.py`——直接連真機
`ws://192.168.31.78:8787/ws/talk`、切 `edge`/`cloud` 模式跑五輪對話、量
`round_total`/`llm`/`tts_first`。**但它量的雲端是 ElevenLabs，不是
Polly**，而且 ElevenLabs 在決賽期間被 `server/aws_only.py` 合規閘門擋掉
（見 `docs/ARCHITECTURE_DIAGRAMS.md` 視圖01：X2 ElevenLabs 是被擋的路徑之
一）。如果要用真機量 Polly 的完整端對端延遲（含 LLM），應該照這支腳本的
形狀改，而不是只用我寫的 `probe_tts_latency.py`（那支只量 TTS 本身，不含
LLM/網路層真實條件）。這件事還沒做。

**待辦（下一步）**：
1. 在 Genio 520 上跑 `edge/probes/probe_tts_latency.py`，拿到真實 edge RTF
   （用來確認「CPU 跑卡」量化起來是多少）。
2. 決定要不要照 `probe_latency_cloud_vs_edge.py` 的形狀，另寫一支端對端版
   本量 Polly（含 LLM 全鏈路），而不是只有 TTS 單獨的秒數。

## 未提交狀態警示（`talkybuddy` master worktree）

`git status --short` 目前顯示：
```
 M deploy/aws/provision_agentcore.py   # 25 insertions, 3 deletions — 不是這次 session 改的，來源未知
?? edge/probes/probe_tts_latency.py    # 這次 session 新增
?? scratch_polly_listen/               # 這次 session 新增，聽完可刪
```
`deploy/aws/provision_agentcore.py` 的修改**不是這次 session 做的**，來歷
不明——接手的人先 `git diff deploy/aws/provision_agentcore.py` 看內容，
判斷是要 commit、丟棄，還是有人正在改一半，不要直接假設是雜訊清掉。

`talkybuddy-pipecat` worktree 目前 `git status` 是**乾淨的**（已查證，無
未提交異動），所以 clone/checkout 該分支不會漏東西。

## Repo / worktree 地圖

同一個 repo 底下有三個 git worktree，分支彼此沒合併、內容差很多：

| 路徑 | 分支 | 最新 commit | 內容方向 | 大小 | 備註 |
|---|---|---|---|---|---|
| `/home/budaedu/talkybuddy` | `master` | `b9cc274` | AgentCore/agents 降級鏈、deploy 佈建、scaffold 詞彙 | 9.4G（含 models 5.3G + 這次 session 帶出的音檔） | 本次 session 的工作目錄 |
| `/home/budaedu/talkybuddy-pipecat` | `feat/pipecat-edge` | `f9bc559` | **裝置端 pipecat 全雙工串流管線本身**——barge-in、跟讀自動聽、緩衝調校 | 22M（乾淨、無 models/.venv） | **使用者選定：筆電要抓這個分支** |
| `/home/budaedu/talkybuddy-path1` | `gsd/path1-realwire` | `36737b3` | Path 1 全雲端 S2S 實驗（`/ws/live`） | — | 未涉及本次任務 |

`master` 領先 `feat/pipecat-edge` 48 commit、落後 16 commit——**master 的
`docs/ARCHITECTURE_DIAGRAMS.md` 雖然畫了 pipecat 管線的架構圖，但那 16 個
commit（含今天 06:54 才驗證的「跟讀自動開始聽」）都還沒合併進 master**，
文件描述的是目標／現況的敘事，不代表 master 分支的程式碼本身已經有這些
修正。

`server/tts.py` 在兩分支之間**完全相同**（已 diff 確認），所以本次 Polly
測試的結論可以直接套用到 pipecat 分支，不需要重跑。

`.venv` 在 master worktree 是 symlink 指到
`/home/budaedu/hackathon/talkybuddy/.venv`（不在 repo 裡）；pipecat
worktree 甚至沒有自己的 `.venv`，`models/` 目錄是空的——這條分支的程式碼
本身很輕量，真的要跑模型推論要另外處理。**使用者已決定筆電只要程式碼
（git clone 即可），不含 models/venv。**

## 開發機目前網路狀態（`Ubuntu-AI-Server`）

```
eth0   192.168.100.200/24   UP   ← 判斷為新接的乙太直連線
docker0 172.17.0.1/16       UP   （不相關，docker 內部網段）
tailscale0                  已登出，目前不可用
```
- `sshd` 服務 `active`，監聽 `0.0.0.0:22` 與 `[::]:22`。
- 已知 Genio 520 真機過去的位址是 `192.168.31.78:8787`（來自
  `edge/probes/*.py` 裡寫死的 `HOST` 常數，那是舊的手機熱點網段）。**改乙太
  直連後 Genio 520 本身走的是哪個網段沒有查證過，現場要自己確認**——這條
  線目前看起來只接了開發機到筆電，裝置端的連線方式應該不受影響（裝置本身
  的網路依然是它自己的乙太/熱點路徑），但沒有實測過不能保證。

## 筆電端操作步驟（只要程式碼）

```bash
# 1. 確認筆電到開發機的乙太連線通
ping 192.168.100.200

# 2. clone（走 SSH，不依賴會場網路/GitHub，只要這條乙太線通就行）
git clone budaedu@192.168.100.200:/home/budaedu/talkybuddy talkybuddy-pipecat
cd talkybuddy-pipecat
git checkout feat/pipecat-edge

# 3. 開 Claude Code，並先請它讀這份交接文件
claude
# 進去之後跟它說：「先讀 .planning/HANDOFF-2026-08-01-laptop-transfer.md」
```

如果筆電本身另外有獨立網路（會場 WiFi），也可以改用
`git clone https://github.com/bbudaedu/talkybuddy.git` 直接從 GitHub 拉，
兩者結果一樣（因為 `origin` 本來就指向這個 GitHub repo），差別只在要不要
依賴會場網路。

如果要用筆電直接 SSH 控制**這台開發機本身**（而不是筆電自己另開一份
複本）：`ssh budaedu@192.168.100.200`，進去後 `cd /home/budaedu/talkybuddy-pipecat && claude` ——這種情況下 auto-memory 目錄還是這台機器上那個空的
（`-home-budaedu-talkybuddy-pipecat` 只有原始 log，沒有整理過的記憶），一樣
建議先讀本文件。
