# 交接 2026-07-30 晚間 — S2S 從「完全沉默」修到能對話，雲端 TTS 通了

> 這份接續 `HANDOFF-2026-07-30.md`。**那份有數處已過期**，見文末「已推翻的舊資訊」。

## 現在的狀態（一句話）

**回合式與 S2S 兩條路都能 demo；雲端 TTS 真的可用了；但雲端「大腦」是死的，
而那正是主軸。**

裝置 `root@192.168.31.78` 目前跑在 **S2S 模式**。要切回可 demo 的預設：

```bash
ssh root@192.168.31.78 '/root/talkybuddy/edge/deploy/switch_mode.sh turn'
```

---

## ⚠️ 下一棒的第一優先：雲端大腦沒有在運作

`/api/status` 回 `cloud_provider="relay"`，而 `ANTHROPIC_BASE_URL=http://127.0.0.1:8317`
指向**裝置本機**一個沒有任何行程在聽的埠。每一輪都 `ConnectionRefusedError`
後靜默降級回本機 Qwen 1.5B。

**完整調查在 `.planning/INVESTIGATION-cloud-brain-2026-07-30.md`**，含發現途徑
（靠 edge/cloud 對照組讓 llm 延遲重合而現形）與三個選項的比較。

這同時是延遲的主要槓桿：`round_total` 4685ms 裡 llm 佔 3860ms，
**D-05 的 3–4 秒門檻在邊緣 LLM 速度下不可能達到**。

> 另一個 session 已 commit `15f3408`（`/api/status` 不再說謊）與
> `server/agents/` 相關改動。接手前先 `git log` 看它做到哪。

---

## 今天的 commit（本 session 9 個）

```
2294758  aplay 在等待空檔 underrun 導致破音 — 空檔餵靜音
486327c  S2S 從「完全不回話」修到能對話 — 三個坑都是同一個形狀
56d6b38  麥克風削波偵測 — 但實測證明它現在沒在削波
a874141  量測 cloud vs edge 的 round_total — 順便揪出雲端 LLM 根本沒在跑
0374d18  調查報告 — 雲端大腦是死的，而 /api/status 說它好好的
136667c  部署到裝置，順手修兩個會說謊的訊息
9536a51  雲端 TTS 換 eleven_turbo_v2_5 — 2.97s → 0.33s
6fc96d7  /api/status 的 cloud_tts 綠燈是假的 — 設定齊全 ≠ 跑得動
38aa261  兩個 client 搶麥克風 — 症狀長得跟按鍵故障一模一樣
```

測試 1239 → **1329 passed**。

---

## 一條貫穿今天的通則：不要在串流中挖洞

Nova Sonic 是持續串流、由 server VAD 判 turn 邊界。**任何「這塊不送」的過濾
都會留下斷口，VAD 把斷口讀成「使用者開始說話」**，於是它不斷打斷自己。

同一個形狀今天出現四次，每次症狀都不同、都花了時間才連起來：

| # | 哪裡挖洞 | 症狀 |
|---|---|---|
| 1 | 近場門檻丟棄 84% 上行 | 下行音訊 **0 bytes**，玩偶全程沉默 |
| 2 | 播放閘門關閉時完全不送 | 玩偶自問自答、自己稱讚、繞回開頭重講 |
| 3 | 近場門檻擋掉的塊 | 同 1 |
| 4 | aplay 在等待空檔沒資料 | underrun → 下一句開頭破音 |

**修法一律是「送靜音」而不是「不送」**：內容誠實、串流連續、不含迴音。

---

## S2S 現況：能跟讀對話，剩兩個已知問題

**已證實可用**：按鍵 → 講話 → 它回話 → 跟讀 → 它給回饋。逐字稿與音訊都正常。

| 問題 | 狀態 |
|---|---|
| 完全不回話 | ✅ 已修（`NEAR_FIELD_PEAK=0`） |
| 自我迴音（收到自己的聲音） | ✅ 已修（閘門算進 aplay 緩衝延遲） |
| 自問自答、繞回開頭 | ✅ 已修（閘門關閉時送靜音）＋ prompt |
| 忽略題外話（問「鯨魚」沒反應） | ✅ 已修（prompt 改為先回應再導回） |
| **句中破音** | ⚠️ 剛修完（keepalive 餵靜音），**尚未實機驗證** |
| **閘門死區 2.6 秒** | ❌ 未解，見下 |

### 未解：孩子話音剛落跟讀會被吃掉開頭

玩偶講完後閘門仍聾 **2.6 秒**（2.0 aplay 緩衝 + 0.6 tail）。

**下一步候選**：把 `PLAYBACK_BUFFER_US` 從 2 秒降下來。keepalive 已接手
「撐過空檔」的職責，緩衝不再需要那麼深；閘門的 `buffer_delay` 會自動跟上，
死區可降到約 1.1 秒。**但要先確認 keepalive 真的消除了破音再動**——
今天已經有兩次因為同時改多個變因而分不清因果。

注意 `tests/test_live_client.py::test_aplay_has_a_large_enough_buffer_to_absorb_jitter`
釘著 `buffer_us >= 1_900_000`，那條測試編碼的是舊理由，要一起更新。

---

## 雲端 TTS：已可用

`eleven_turbo_v2_5`，裝置實測 **0.67s**（開發機 0.33s）。
`/api/status` 的 `cloud_tts` 現在是**有證據的**綠燈（`CloudTTS.verified()`）。

換模型的連帶影響：**放慢語速改成依模型分流**。v2_5 系列原生支援 `speed`
（實測 0.7→5.94s vs 1.0→4.74s），`eleven_v3` 忽略它（4.16 vs 4.32s，無差別）。
兩條路只能擇一，都做會放慢兩次且不報錯。見 `_model_honours_speed()`。

`CLOUD_TTS_TIMEOUT_S` 維持 **1.5s**——一度想放寬到 3.0，被
`test_pipeline_timeout_isolation.py` 擋下（ROADMAP 要求斷網降級 < 1–2 秒）。
放寬的理由本來就只是遷就 v3 的 3 秒延遲，換 turbo 後理由消失。

---

## 部署（今天新增的工具，請務必使用）

```bash
export TALKYBUDDY_EDGE_SSH_HOST=192.168.31.78
./edge/deploy/push.sh
ssh root@192.168.31.78 'cd /root/talkybuddy && ./edge/deploy/install_services.sh'
ssh root@192.168.31.78 '/root/talkybuddy/edge/deploy/switch_mode.sh status'
```

**兩個會咬人的陷阱**：

1. **推檔 ≠ 生效**。unit 檔 rsync 過去只是放著，`/etc/systemd/system/` 下還是舊的，
   而且沒有任何錯誤訊息。改 `Environment=`／`Conflicts=` 後**必須重跑
   `install_services.sh`**。（`push.sh` 先前根本不推 unit 檔，已修。）
2. **`Conflicts=` 只停不啟**。`systemctl stop` 之後兩個 client 都 inactive、
   玩偶變啞，症狀又跟按鍵故障一樣。**一律用 `switch_mode.sh`**。

---

## 現場量測工具（`edge/probes/`）

| 工具 | 用途 |
|---|---|
| `probe_latency_cloud_vs_edge.py` | 量 round_total，含 edge 對照組 |
| `probe_mic_gain.py` | 掃 USB 麥克風擷取增益，含物理合理性檢查 |
| `probe_near_field_threshold.py` | 量逐塊音量分布訂近場門檻，**用嗶聲提示** |
| `scripts/verify_cloud_tts_live.py` | 把 CloudTTS 吞掉的 HTTP 錯誤攤開 |

---

## 已推翻的舊資訊（別再照舊文件走）

1. **`HANDOFF-2026-07-30.md` 說「`server/agents/` 只有骨架、今天完全沒碰」→ 過期。**
   它已接線：`server/pipeline.py:744` 的 `_run_agents()` 已呼叫
   orchestrator/homework/report、`web/teacher.html:233` 有畫面、8 支測試。

2. **「麥克風削波拉低 ASR」→ 條件式成立，不能一概而論。**
   增益掃描（40 秒連續說話，較遠）：五個增益值**全部零削波**，滿檔 peak 中位數
   僅 0.284。近場門檻量測（對著玩偶正常講話）：**peak 最大值 1.0000，確實削波**。
   兩組都是真的——**削波取決於距離與音量**。建議增益維持 147。

3. **「`PLAYBACK_VOLUME=0.15` 實測有效」→ 太小聲。** 使用者實聽確認，已改 0.5。

4. **「近場門檻擋環境噪音」→ 擋不住。** 實測環境底噪尖峰會到 0.1027，
   本來就穿得過 0.06；卻把使用者的話剁掉 84%。

---

## Pipecat 串流管線：評估過，不建議接

`server/streaming/` 有 676 行、含 VAD/barge-in/可打斷 TTS，且 `reply_source.py`
明寫「大腦免疫接縫」。但：

- 裝置**刻意不裝** pipecat（`docs/DEPLOY_EDGE.md:136`）。dry-run 實測最小要
  **37 個**套件（**會升級 numpy**，sherpa-onnx 與 preflight 都依賴現版），
  含 STT 的 `[funasr]` 要 **83 個**。裝置剩 4.9G 磁碟、1.7G 記憶體。
- `pyaudio` 需編譯（無 gcc/cmake/apt），`FunASRSTTService` 需 funasr+torch。
  要跑必須自己寫 arecord/aplay transport 與包在既有 sherpa ASR 上的 STTService。
- **而且大腦不是串流的**：`batch_reply_source.py` 自述「過渡實作」，一次算完
  整段再切句。接通後仍要等 EdgeLLM 3.9 秒才開口，且是本機 Qwen。

**Nova Sonic 已經提供真串流＋可打斷＋雲端（Bedrock）**，這條路投報率明顯較低。

---

## 待辦（依建議優先序）

1. **雲端大腦接通** — 見上，主軸所在，也是延遲槓桿
2. **驗證 keepalive 有沒有消除破音** — 已部署未驗
3. **閘門死區 2.6 秒** — 降 `PLAYBACK_BUFFER_US`，但要等 2 完成
4. **`EdgeLLM generate 失敗，降級回 scaffold`** — llama.cpp 回 HTTP 500
   `The model produced output that does not match the expected peg-native format`，
   約 10% 機率，現場中獎玩偶會講無關的罐頭句。未查
5. **人聲頻段占比 19–27%（門檻 25%）** — `preflight --mic` 會隨機 OK/WARN，
   代表錄到的音有相當比例能量不在人聲頻段。成因未查
6. **實體把麥克風移遠離喇叭** — 零開發成本，是自我迴音的治本方向

---

## 除錯方法論（今天新增的教訓）

1. **要有對照組，而且它不只是為了證明改善——是為了讓異常現形。**
   雲端 LLM 是死的這件事，是靠 edge 模式對照組讓兩邊 llm 延遲重合
   （3859 vs 3860ms、第 3 輪都是 4178ms）才發現的。只量 cloud 的話，
   4685ms 看起來就只是「還是有點慢」。

2. **經 SSH 執行時 stdout 會被緩衝，`print()` 提示等於不存在。**
   近場門檻第一版靠 print 指示受測者何時說話，結果量到兩段一模一樣的靜默。
   要即時提示就用**聲音**（裝置有喇叭）。

3. **從壞數據算出來的建議值比不給建議更糟。** 增益掃描第一版量到
   「增益 80 的 peak 比滿檔 147 高 6 倍」（物理上不可能），卻照樣輸出
   「建議增益：60」。現在加了物理合理性檢查，違反就拒絕給建議。

4. **`pkill -f` 會殺掉自己所在的 shell；`pgrep -f` 也會匹配到自己。**
   今天在「殺」和「偵測」上各中一次。用 `ps -eo args | grep "[l]ive_client"`。

5. **修一個問題可能無聲弄壞另一個。** 為壓 underrun 把 aplay 緩衝調成 2 秒，
   同時讓迴音防線破了 1.4 秒的洞，而兩者的耦合當時沒有任何地方記錄。
   現在 `buffer_delay` 綁在一起了。

---

## 主軸提醒（未變）

使用者定調：**主軸是 AWS 雲端多 agent 生態，斷網降級點到為止。**
edge 這條線今天已達「兩種模式都能 demo」，**投報率已經遞減**，
建議把剩餘時間投到雲端大腦與多 agent 驗收。
