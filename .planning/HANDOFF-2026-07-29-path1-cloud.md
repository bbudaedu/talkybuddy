# 交接：2026-07-29 晚（重開機前）— Path 1 G2 已關 + 全雲端規則變更待執行

> 這份是 **worktree `/home/budaedu/talkybuddy-path1`（分支 `gsd/path1-realwire`）** 的交接。
> 主工作區 `/home/budaedu/talkybuddy`（`gsd/2-genio-520-edge-mvp`）由**另一個 session** 做決賽演示準備，**不要動**。

## 一句話狀態

Path 1 的 G2 接線缺口**已關掉並有自動化證明**（5 個 commit，全 suite 1012 passed）；
真麥驗收 **BLOCKED**（開發機無錄音硬體）。
然後使用者下了**規則變更：全雲端**，方向已定但**一步都還沒做** —— 卡在沒有 AWS 憑證。

---

## 一、已完成：Path 1 G2（可直接接受）

分支 `gsd/path1-realwire`，5 個 commit，working tree 乾淨：

```
246e087 docs(path1): 證據檔補上「Genio 520 有麥克風也不解這個卡點」與前置檢查現況
a2395f3 feat(path1): check_prerequisites 檢查有沒有錄音裝置，不只檢查 pyaudio 裝了沒
63b934b docs(path1): 記下 G2 實際狀態 — 接線已解，真麥驗收卡在沒有錄音裝置
95c204c docs(edge): Path 1 不是 /ws/live — 修正 UAT findings 的路徑對照表
ef68bf7 fix(path1): wire BargeInGate into run_realwire — barge-in now reaches the manager
```

- 鏈從 `input → STT → manager → TTS → output` 改成 **`input → BargeInGate → STT → manager → TTS → output`**
- gate 排在 STT **之前**：實測 FunASR 目前把 `InputAudioRawFrame` 原封轉發（28 frames / 178944 bytes 進出全等），
  所以放後面今天也能動 —— 但那是實作細節不是契約，排前面零成本移除這個相依。`test_barge_in_gate_sits_upstream_of_stt` 釘住此決策。
- `server/streaming/tests/` 從 26 條 → **33 條**；`./run_tests.sh` → **1012 passed, 0 failed**（3m23s）
- 完整證據：**`edge/PATH1_REALWIRE_EVIDENCE.md`**

**剩下的唯一缺口**：真麥克風的兩條二元判定（①講一句聽到回覆 ②插話句界乾淨停）。
開發機 `/dev/snd` 沒有任何 capture 節點，root 列舉到的 4 個裝置全是 NVidia HDMI 輸出（`in=0`）。
使用者選擇「接 USB 麥克風／耳麥後再驗」，**尚未接**。接上後第一道確認：

```bash
cd /home/budaedu/talkybuddy-path1
.venv/bin/python -c "from server.streaming.run_realwire import _has_audio_input_device as f; print(f())"
# 現在是 False；插上 USB 麥後應為 True
```

---

## 二、規則變更（使用者 2026-07-29 下達）：全雲端

原文：「**更改規定 全雲端 邊緣裝置 接通 模型在雲端**」（編號 1.，後續項目未講完）

三個已確認的裁定（用 AskUserQuestion 問過）：

| 項目 | 裁定 |
|---|---|
| 裝置存取 | **仍不碰裝置**。不 ssh/rsync 到 192.168.31.78。只做伺服器端 + 設定 + 文件，裝置端實跑由使用者或另一 session 執行 |
| 雲端形態 | **`/ws/live`（Nova Sonic S2S）全雙工可插話** —— 不是 `/ws/talk` push-to-talk |
| 雲端伺服器 | **已經有跟得上的伺服器，使用者會給位址** —— 位址**尚未提供** |

使用者補充：「之前測 S2S 全雙工 是用 AWS 給你 IAM 的 key」。

---

## 三、下一步：三個已查證的卡點

### 🔴 卡點 1：沒有 AWS 憑證（阻擋一切 S2S 驗證）

實測：

```
LIVE_S2S_ENABLED       = True
nova_sonic.available() = False      ← 卡在這
BEDROCK_REGION         = us-east-1
NOVA_SONIC_MODEL_ID    = amazon.nova-2-sonic-v1:0
```

- `server/nova_sonic.py:30` `available()` 第一關查 `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`
- **主工作區與 worktree 都沒有 `.env` / `.env.local` / `.env.cloud`**；shell env 中 0 個 `AWS_*`
- 專案**沒有用 python-dotenv**，沒有任何自動載入機制 —— 憑證一直靠 shell export，session 結束就沒了
- 結果：`/ws/live` 連上會直接被關並回 `{"type":"live_error","reason":"unavailable"}`

**要使用者做的**（已告知，尚未執行）：憑證**不要貼進對話**（會留在 transcript，等於外洩）。建議：

```
! printf 'AWS_ACCESS_KEY_ID=%s\nAWS_SECRET_ACCESS_KEY=%s\n' 'AKIA...' '...' > /home/budaedu/talkybuddy-path1/.env.local && chmod 600 /home/budaedu/talkybuddy-path1/.env.local && echo written
```

臨時憑證要多加 `AWS_SESSION_TOKEN`。`.env.*` 已在 `.gitignore`。

**憑證到手後的驗證順序**（工具都是現成的，不必新寫）：

```
scripts/verify_nova_sonic_live.py   # 憑證 + model 存取權
scripts/verify_ws_live_e2e.py       # 對運行中 server 的 /ws/live 送合成音訊，繞過瀏覽器與麥克風
scripts/verify_bedrock_live.py
scripts/verify_cloud_llm_live.py
tests/test_nova_sonic.py
```

> `verify_ws_live_e2e.py` 很關鍵：它用 `websockets` 直連 `wss://HOST:8000/ws/live`，
> WAV → 16k/mono/PCM16 當 binary frame 上行。**不需要瀏覽器也不需要麥克風** ——
> 開發機沒有錄音硬體這件事，在 S2S 這條線上不成立。

### 🔴 卡點 2：`/ws/live` 沒有 token 驗證（安全，屬伺服器端＝可直接做）

已查證比對：

- `/ws/talk`（`server/app.py:520-529`）：`accept()` → 讀 `?token=` → `auth.verify_token()` → 壞/缺 token `close(1008)`
- `/ws/live`（`server/app.py:659-`）：`accept()` 之後**直接進 consent + available gate，全域找不到任何 `token` / `identity_from` / `auth`**

在 loopback 上可接受；**一旦依規則變更暴露到雲端，就是任何人都能驅動的「麥克風→Bedrock」開放端點**
（成本 + 隱私 + 資料出境）。Phase 9 已為同一類疑慮替 `POST /api/network_mode` 加上 JWT 閘門（09-01, T-09-02），
此處是同型缺口。**這項不需要憑證就能做，是憑證到手前唯一能推進的實作。**
使用者被問「要我先做嗎」時尚未回答（接著就講重開機）。

### 🟠 卡點 3：裝置端 `audio_io` 是半雙工的（跑不了 `/ws/live`）

`edge/runtime/audio_io.py` 公開介面只有：

```
capture_16k_mono_wav(seconds=4.0)   # 錄固定長度 → 回完整 WAV bytes
play_wav_bytes(wav)                 # 播完整段
wait_for_trigger()
```

底層是 `arecord` / `aplay` 子行程（裝置無 gcc/cmake，`sounddevice` 僅在已安裝時才升級採用）。

`/ws/live` 全雙工需要的是：**持續小塊串流上行**、**同時播放**、**收到 `{"type":"interrupt"}` 立即停播**。
這三件現在都沒有。`edge/runtime/local_client.py` 只講 `/ws/talk`（「等觸發 → 錄音 → 送 → 播放」），
且固定連 loopback（但 `TALKYBUDDY_EDGE_WS_HOST` / `_PORT` **本來就可 env 覆寫**）。

→ 走 `/ws/live` 需要**新寫一支裝置端 S2S 串流 client**。裝置端 execution 不歸我做，但程式碼可以在 repo 內寫。

---

## 四、`/ws/live` wire protocol（已讀 `server/app.py` 確認，寫 client 直接照這個）

連線：`ws(s)://HOST:PORT/ws/live?mode=continuous`（`mode=continuous` = hands-free，turn 邊界交給 Nova server VAD）

| 方向 | 內容 |
|---|---|
| 上行 binary | PCM16 **16kHz** mono raw |
| 上行 text | `{"type":"bye"}` 結束（continuous 模式下 `user_end` 被忽略） |
| 下行 binary | Nova 合成音訊 PCM16 **24kHz** raw |
| 下行 text | `{"type":"live_transcript","role":"USER"\|"ASSISTANT","text":...}` |
| 下行 text | `{"type":"turn_end"}` |
| 下行 text | `{"type":"interrupt"}` ← **barge-in，client 必須立即停播** |
| 下行 text | `{"type":"live_error","reason":"consent_required"\|"unavailable"}` 後即 close |

連線前兩道 gate：`guardrails.consent_granted()`（優先，資料出境）→ `config.LIVE_S2S_ENABLED and nova_sonic.available()`。

前端既有實作可參考：**`web/live-client.js`**。

---

## 五、環境現況（重開機後仍有效，都在磁碟上）

- worktree `/home/budaedu/talkybuddy-path1`，分支 `gsd/path1-realwire`，基於 `104c41b`
  （主工作區已推進到 `40b65d8`，**尚未 rebase/merge**）
- `.venv` 是 **symlink** → `/home/budaedu/talkybuddy/.venv`（與主工作區共用，本 session 往裡面裝了 `pyaudio 0.2.14`）
- `models/` 內大型資產是 **symlink** → 主工作區（sherpa/SenseVoice/espeak-ng-data/gguf）
- 系統套件：本 session 裝了 **`portaudio19-dev`**（`/usr/include/portaudio.h` 已在）
- 開發機音訊：**無任何 capture 裝置**（`/dev/snd` 無 `pcmC*D*c`；card0 Intel 類比連 pcm 節點都沒有）
- 開發機有裝：`pipecat-ai 1.5.0`、`torch 2.13.0+cpu`、`funasr 1.3.14`、`sherpa_onnx 1.13.3`、`onnxruntime 1.26.0`
- Genio 520 **刻意未裝** torch/pipecat（`edge/runtime/provision_device.sh:11-12` 原文）—— 此為腳本記載，未連裝置實測

---

## 六、紅線（延續，不得違反）

1. **不碰 `/home/budaedu/talkybuddy` 主工作區**，不 ssh/rsync 到 **192.168.31.78** —— 另一 session 獨佔
2. **未實測不得寫數字或宣稱通過**。記 `blocked` 比填合理猜測有價值 ——
   本專案已因「用 TTS 合成音驗 KWS」拿過一次假信心
3. 不碰 `server/games.py`、`server/pipeline.py` 的遊戲區塊、`server/app.py` 的 `/api/game`
4. 小而可回退的 atomic commit；行為有變更就補測試（TDD：先紅再綠）
5. **憑證不得出現在對話、log 或 commit**

---

## 七、下個 session 的第一句話建議

先問使用者兩件事（兩者都在等他）：

1. **雲端伺服器位址**是什麼？（他說「已經有跟得上的伺服器，我給你位址」，尚未給）
2. **AWS 憑證**要不要現在放？（放法見卡點 1，強調不要貼進對話）

在等待期間**可以直接做且不需憑證**的：**卡點 2 —— 替 `/ws/live` 補 token 驗證**（TDD，比照 `/ws/talk` 的 `close(1008)` pattern）。
