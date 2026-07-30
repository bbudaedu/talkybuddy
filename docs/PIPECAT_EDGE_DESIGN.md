# pipecat 在 Genio 520 上全接管語音管線 — 設計

**狀態**：元件已實作並單元測試通過（41 passed），**端到端未驗證**。
**日期**：2026-07-31（決賽 8/1 前夜，全程未動決賽路徑）
**分支**：`feat/pipecat-edge`（worktree），未合併、未 push

---

## 先講這個方案買到什麼、買不到什麼

| 你想要的 | 這個方案給不給 | 真正的解藥 |
|---|---|---|
| **可控**（教材／SRS／發音評測插得進每一輪） | ✅ **給**，這是主要價值 | 就是這個 |
| **好換模型**（Nova Sonic ↔ Gemini Live ↔ 本地） | ✅ 給 | 就是這個 |
| **自然**（會插話、turn 判斷準） | 🟡 部分，靠 VAD 與模型 | 換模型 |
| **流暢**（不要等 4 秒） | ❌ **不給** | **把 LLM 換掉** |

**最後一列是這份設計最重要的一句話。** 實測 `a874141`：round_total 5025ms
之中 LLM 佔 3859ms（**78%**）。本方案量到的每一個元件成本都是零頭：

```
VAD    1.90ms/窗（處理 32ms 音訊，即時率 6%）
STT   147ms（2 秒音訊）
TTS   150-750ms（即時率 0.25x，合成比播放快 4 倍）
LLM  3859ms  ←───────────────────────── 瓶頸在這裡，pipecat 動不到它
```

**2026-07-31 全部量完之後，這個結論只有更強**：除了 LLM 以外的所有元件
加起來不到 1 秒，而 LLM 一個人 3.9 秒。

pipecat 換的是**編排層**，不是推論速度。把整套接上去之後，孩子還是要等約 4 秒。
如果目標是「對話變流暢」，**應該先換 LLM，那件事不需要 pipecat**。

---

## 為什麼仍然值得做：現行架構的兩個結構性問題

### 1. S2S 是黑箱，教學內容只能用祈使句拜託它

現行 `/ws/live` 走 Nova Sonic S2S，turn 邊界與回應內容都由模型自己決定。
教材、SRS、發音評測只能塞進 `system_prompt` 求它照做——而 `project-edge-s2s-tuning`
記錄了這條路的實際下場：寫「每次回覆不超過兩句話」**沒被遵守**，實測每次 4 句以上、
單場下行 102 秒；要把後果寫進 prompt（「你多講一句，他就多一句話的時間不能開口」）
才收斂。

拆成 STT → LLM → TTS 之後，這些變成程式碼層級的控制，不是對模型的請求。

### 2. 「兩個 client 互斥」的設計會讓玩偶變啞

現行用 systemd `Conflicts=` 讓 `live-client`／`local-client` 互斥。已知兩個事故：

- **`Conflicts=` 只停不啟**：`systemctl stop talkybuddy-live-client` 之後兩個
  client 都 inactive、**玩偶直接變啞**，症狀跟按鍵故障一模一樣
- **兩個 client 搶同一支麥克風**（`38aa261`），而「麥克風被佔用」的症狀
  又跟「麥克風壞掉」一模一樣

pipecat 的形狀讓這兩個同時消失：**麥克風由單一 transport 持有、從頭到尾不轉移**，
降級發生在 pipeline 內部的 service 層。最壞結果從「玩偶不會講話」降級成
「這一輪回答比較笨」。

---

## 架構

```
Genio 520（單一行程，麥克風所有權不轉移）
┌────────────────────────────────────────────────────────┐
│  AlsaInputTransport   ← arecord 子行程 16k S16_LE raw  │
│         ↓                                              │
│  SileroVADAnalyzer（官方，1.90ms/窗）                   │
│         ↓                                              │
│  SenseVoiceSTTService（自寫，包既有 sherpa 引擎）        │
│         ↓                                              │
│  LLM  ── FailoverPolicy 決定 ──┬─→ 遠端（雲端／GPU）    │
│                                └─→ 本機 llama-server    │
│         ↓                                              │
│  EdgeVitsTTSService（自寫，包既有 sherpa VITS）          │
│         ↓                                              │
│  AlsaOutputTransport  → aplay 子行程 24k raw           │
└────────────────────────────────────────────────────────┘
```

### 元件盤點：只有兩個半需要自寫

| 元件 | 來源 | 理由 |
|---|---|---|
| VAD | **官方 `SileroVADAnalyzer`** | onnxruntime 有 aarch64 wheel，模型內建 |
| Transport | **自寫 `alsa_transport`** | `pipecat-ai[local]` 要 pyaudio，板子無 gcc 裝不了 |
| STT | **自寫 `sensevoice_stt`** | 包既有引擎，不重載模型 |
| TTS | **自寫 `edge_tts`** | 包既有 sherpa VITS |
| LLM | **官方 `OpenAILLMService`** | llama-server 本來就是 OpenAI 相容 |
| 降級 | **自寫 `failover`** | 純狀態機，無 I/O |

---

## 降級設計

`FailoverPolicy` 只決定「用哪個 service」，**永遠不碰 transport**。這不是實作細節，
是這個設計存在的理由（見上面的問題 2）。

### 與 pipecat 內建 `ServiceSwitcher` 的分工

pipecat 有 `pipeline/service_switcher.py`，提供 `ServiceSwitcher`
（用 `ParallelPipeline` + filters 做 frame 路由）與 `ServiceSwitcherStrategyFailover`。
**兩者互補，不重複**：

| | 提供什麼 |
|---|---|
| 官方 `ServiceSwitcher` | **機制**——frame 怎麼路由到當下作用中的 service |
| 官方 `StrategyFailover` | 收到 `ErrorFrame` **就立刻切**，無遲滯 |
| 自寫 `FailoverPolicy` | **策略**——連續失敗計數、遲滯、冷卻、防抖動 |

官方文件自己寫明「Recovery and fallback policies are **left to application code**
via the `on_service_switched` event」。而「一錯就切」正是本設計論證過會在
116ms／會整條斷的鏈路上抖動的做法。

**接線方式**：用官方 `ServiceSwitcher` 做路由，把 `FailoverPolicy` 掛在
`on_service_switched` 與錯誤處理上決定何時切、何時切回。**不要自己另做 frame 路由。**

| 參數 | 預設 | 為什麼 |
|---|---|---|
| `failure_threshold` | 2 | 單次逾時不算數；鏈路 RTT 116ms 本來就會抖 |
| `recovery_threshold` | 3 | **刻意比降級門檻高**——降級安全（本地一定在），升級有風險 |
| `cooldown_s` | 30 | 防 flapping；每次切換對孩子都是一次語音風格突變 |

降級**不看冷卻**（雲端已經壞了，等冷卻只是讓每輪白等一次逾時）；升回**要同時**
滿足連續成功次數與冷卻時間。

這不是理論上的謹慎：**2026-07-31 03:1x 這條鏈路整條斷過一次**（ping 100% 丟包、
TCP 22 不通），就發生在本文件寫作期間。

---

## 已驗證的數字（板子實測，2026-07-31）

| 項目 | 數字 | 備註 |
|---|---|---|
| `import pipecat` | 0.27s | |
| pipecat + sherpa 同 process import | 0.16s | **numpy 不衝突**（詳見下） |
| `SileroVADAnalyzer` 實例化 | 0.10s | 模型內建 |
| VAD `analyze_audio()` | **1.90ms/窗** | 每窗 32ms 音訊 → 即時率 6% |
| SenseVoice 模型載入 | 1.93s | |
| SenseVoice 辨識 2 秒音訊 | **147ms** | |
| **完整 pipeline RSS** | **747MB** | ✅ 可用 1759MB，**通過 1.2G 紅線** |
| TTS 合成（暖機後） | **150–750ms** | 即時率 **0.25x**，合成比播放快 4 倍 |
| TTS 首次合成（冷） | 2068ms | 含 voice 模型載入，**只發生一次** |
| 磁碟增量 | 約 600MB | 裝完剩 3.9G |
| edge↔server RTT / 頻寬 | 87–116ms / ~550kB/s | 且**會整條斷**（2026-07-31 半夜斷過數小時） |

### RSS 分階段拆解（2026-07-31 板子實測）

```
[0] baseline         8 MB
[1] +pipecat        34 MB   (+26)
[2] +VAD           136 MB   (+102)
[3] +SenseVoice    664 MB   (+528)  ← 最大宗
[4] +TTS           744 MB   (+80)
[5] 跑過一輪       747 MB
```

llama-server 另外吃 1413MB。兩者相加約 2.16G，板子總共 3.7G——**塞得下，
但沒有第二個大模型的空間了**。

### 一條被推翻的既有結論

`docs/DEPLOY_EDGE.md:136` 排除 `pipecat-ai`，理由之一是「會升級 numpy，
sherpa-onnx 依賴現版」。**實測不成立**：

| venv | numpy | sherpa-onnx |
|---|---|---|
| `/root/talkybuddy/.venv`（決賽路徑） | 2.5.1 | ✅ |
| `/root/pipecat-lab/.venv` | 2.4.6 | ✅ 事後裝入無衝突 |

方向甚至相反（決賽 venv 的 numpy 還比較新）。

**但過程本身是個教訓**：最初用
`pip download --platform manylinux2014_aarch64 --python-version 312 --only-binary=:all:`
在開發機模擬，結論是「onnxruntime 無 aarch64 wheel、`[silero]` 不可行」——
**那是假陰性**，platform tag 訂太舊。真的在板子上裝就裝起來了。

> **platform tag 模擬只能證明「可行」，不能證明「不可行」。**
> 下不可行的結論一律要在真板子上跑過。

---

## 已解除的風險

- ~~**完整 pipeline RSS**~~ — **已量測：747MB，可用 1759MB，通過**（2026-07-31）
- ~~**TTS 慢到無法接受**~~ — 暖機後即時率 0.25x，first-audio 150–750ms，
  不是瓶頸。（冷啟動 2068ms 只發生一次，可用預熱一句消化掉。）
- ~~**TTS 在真實 pipeline 中不出聲**~~ — **已在板子上用真實 sherpa VITS 引擎
  跑通**：23 個 `TTSAudioRawFrame`、99328 bytes（2.25s @22050Hz），
  與單獨量測 `synth()` 的結果一致。

  **根因不是 adapter，是驅動方式**：TTS 音訊先進 audio context，由背景 task
  在 context **關閉後**才 drain；而關閉 context 的 `on_turn_context_completed()`
  要等 **turn 邊界**（`LLMFullResponseEndFrame`）。只送 `TextFrame` 時，
  `synth()` 會被呼叫但音訊卡在 context 裡出不來——症狀跟「TTS 壞掉」一模一樣。
  已用 `tests/test_pipecat_tts_in_pipeline.py` 把兩個方向都釘住。

## 未驗證的風險（按嚴重度排序）

1. **端到端 round_total**——未跑過。依元件數字推算應為 LLM 3.9s + 其餘 <1s，
   若量出來明顯更差，代表 pipecat 的編排開銷不可忽略。
3. **CPU 爭用**——各元件單獨量測都很快，但 VAD+STT+TTS 同時跑、
   且與 llama-server 搶 8 核，未量測。
4. **AEC 仍然不存在**——喇叭與麥克風同在玩偶內、板子無 gcc 裝不了 AEC，
   換 pipecat **不會**改變這件事。真正的全雙工做不到，仍要靠 half-duplex 閘門。
   （`project-edge-s2s-tuning`：實體把麥克風移遠離喇叭是唯一治本方向，零開發成本。）
5. **TTS 不是串流的**——`TTSEngine.synth()` 一次合成整段才回傳，
   first-audio 延遲 = 整句合成時間。實測 150–750ms，可接受，但長句會線性增加。

---

## 執行步驟（決賽後）

1. 板子上 `/root/pipecat-lab/.venv` 已備妥（pipecat-ai 1.6.0 + sherpa-onnx 1.13.4）
2. 量完整 pipeline RSS —— **若超過約 1.2G 就停下來重新評估**，別硬上
3. 組 pipeline，用**檔案**餵音訊量 round_total（不要搶麥克風，
   live-client 正持有；搶麥的症狀跟麥克風壞掉一模一樣）
4. round_total 若如預期由 LLM 主宰，**先去換 LLM，不要先調 pipecat**
5. 真機接麥克風前，先用 `switch_mode.sh` 確認恰好一個 client 在跑

---

## 什麼情況應該放棄這個方案

誠實的設計要寫清楚退出條件：

- **完整 pipeline RSS 超過可用記憶體**，且無法用更小的模型解決 → 放棄，
  維持現行 S2S
- **目標只是「對話變快」** → 放棄，直接換 LLM，這個方案幫不上忙
- **決賽現場只能走主辦方 AWS** → 這個方案的本地 pipeline 用不上，
  價值只剩「可控」那一半
- **沒有人力維護四個自寫 adapter** → `server/streaming/` 已經有一套自製的
  turn/barge-in/VAD（26 個測試在守），繼續養它可能比引入 pipecat 便宜
