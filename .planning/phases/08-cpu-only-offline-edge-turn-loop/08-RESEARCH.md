# Phase 8: CPU-Only Offline Edge Turn Loop - Research

**Researched:** 2026-07-25
**Domain:** llama.cpp native cross-compile + HTTP client refactor, ALSA audio I/O in Python, on-device benchmarking/memory validation for MediaTek Genio 520 (Yocto, no on-device compiler)
**Confidence:** MEDIUM — project-level research (STACK/ARCHITECTURE/PITFALLS) is HIGH; the 5 phase-specific gaps closed in this document rely on WebSearch-only verification (no board access during this research pass) and are tagged accordingly.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**D-01（鎖定）：** llama.cpp 改 native binary（`llama-server`，llama.cpp 內建的 OpenAI-compatible HTTP server）over `localhost`，取代 in-process `llama-cpp-python`。`server/llm.py::EdgeLLM` 改為 HTTP client（`requests`/`httpx` 呼叫 `http://127.0.0.1:<port>/completion` 或 `/v1/chat/completions`），保留現有 `available()`/`generate()` 契約與逾時、safety_check、降級語意不變。

**D-02（鎖定）：** build flag 固定 `-march=armv8.2-a+dotprod+i8mm`（`armv8.7a` 隱含 A78 不支援的 ISA 特徵，會 runtime SIGILL）。

**D-03（auto 選定，可回頭調整）：** 交叉編譯工具鏈 — 開發機先試 apt 泛用 aarch64-linux-gnu cross-toolchain（如 `gcc-aarch64-linux-gnu`），交叉編譯後用既有 SSH/rsync 部署迴圈推上裝置；若跑起來 glibc ABI 不相容（版本落差造成動態連結失敗），才退而使用 `~/hackathon/` 的 Genio Yocto BSP SDK 官方 cross-toolchain。**執行時風險**：若 apt 工具鏈編出的 binary 在裝置上 `ldd`/執行失敗，planner/executor 應立即改用 Yocto SDK 路徑，不要在 apt 路徑上反覆嘗試超過一次修正。

**D-04（auto 選定）：** 裝置端直接走 ALSA（Python 層 `sounddevice` 或呼叫 `arecord`/`aplay` 子行程），擷取 16k mono WAV 直接命中 07-01 已做的 RIFF-sniff fast path；不透過瀏覽器 WebSocket。新增一個獨立進程（如 `edge/runtime/local_client.py`）串 ALSA 擷取 → `VoicePipeline.run_turn_*` → ALSA 播放。**Claude's Discretion**：`sounddevice`（pip 有 aarch64 wheel）或子行程呼叫 `arecord`/`aplay`（Yocto image 通常內建 alsa-utils）由 executor 依裝置實測結果選擇——若 `sounddevice` 需要編譯或缺 PortAudio，優先退到 `arecord`/`aplay` 子行程呼叫。

**D-05（auto 選定）：** 首字延遲 < 800ms、單回合總延遲（收音結束→開始播放回覆）< 3–4 秒 為 go；超過則記為 no-go 並列出 fallback（縮短 prompt/scaffold、降 `n_ctx`、或該回合改用 scaffold-only 回覆，不強行等待 LLM）。這個門檻是**實測後才能真正判定 go/no-go**（真機 `llama-bench` + 端到端計時），本 phase 的 auto 選定只鎖定「用哪個數字當門檻」，不是預先宣稱通過。

### Claude's Discretion

- llama-server 啟動方式（`run_edge.sh` 內同步拉起、或獨立 systemd/nohup 常駐行程）之取捨，由 executor 依 07-03 已驗證的 SSH/rsync 部署慣例決定，避免破壞既有 health-check 路徑。
- 執行緒數（`-t`）具體值：Genio 520 為 2×A78 + 6×A55，8 執行緒不必然最快；executor 需依 `llama-bench` 實測 1/2/4 執行緒數值挑選，不可預設 `nproc`。
- `sounddevice` vs `arecord`/`aplay` 子行程之取捨（見 D-04 附註）。

### Deferred Ideas (OUT OF SCOPE)

- **NPU 加速路徑（ASR/TTS 經 Neuron Delegate）**：Phase 10，本 phase 全程 CPU-only，不預先摻雜 NPU 程式碼路徑（YAGNI）。
- **前端瀏覽器 loopback 對話整合**（裝置本機瀏覽器 → localhost:8787）：非本 phase 驗收必要，可留給 Phase 9 或 stretch goal。
- **斷網橋段話劇化彩排**：Phase 9，本 phase 只需確認「全程零雲端網路呼叫」。
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| ELOOP-01 | 裝置端 FastAPI 以 `TALKYBUDDY_PIPELINE_PROFILE=edge` 跑完整離線 聽ASR→想LLM→說TTS 迴路，全 CPU 引擎，不依賴雲端 | §Architecture Patterns Pattern 1（WS loopback client 沿用既有設計）、§Code Examples（`local_client.py` 骨架）、§Runtime State Inventory（現有 CPU 引擎皆已可用，缺口只在音訊 I/O 與 LLM 服務化） |
| ELOOP-02 | llama.cpp 以 native binary over localhost 生成，build flag `-march=armv8.2-a+dotprod+i8mm`；離線真生成中英雙語鷹架帶讀簡短回覆 | §Standard Stack（交叉編譯完整指令）、§Architecture Patterns Pattern 2（EdgeLLM → HTTP client 契約保留）、§Common Pitfalls Pitfall 1/2（glibc ABI、n_ctx 搬遷位置）、§Package Legitimacy Audit |
| ELOOP-03 | on-device 首字延遲 / 每回合延遲實測，訂出舞台可接受延遲 go/no-go 門檻 | §Common Pitfalls Pitfall 3（thread tuning 方法論）、§Code Examples（`llama-bench` 掃描指令）、§Validation Architecture |
| ELOOP-04 | 4GB 記憶體驗證閘——三引擎鏈於真機同時載入的峰值 < 4GB 並留 headroom | §Common Pitfalls Pitfall 4（VmHWM 量測方法、多行程加總）、§Code Examples（RSS 監測 script） |
</phase_requirements>

## Summary

這個 phase 的核心工作是把 `server/llm.py::EdgeLLM` 從「in-process `llama_cpp.Llama`」改成「HTTP client 打交叉編譯出的 `llama-server` native binary」，並補上裝置端 ALSA 音訊 I/O 讓真機不靠瀏覽器也能跑完整聽→想→說迴路，最後用真機 `llama-bench` + peak RSS 量測驗證延遲與記憶體門檻。專案層級研究（STACK.md/ARCHITECTURE.md/PITFALLS.md）已把「為什麼」講清楚（`-march` 理由、NPU 延後、4GB 預算），本文件補上「怎麼做」的具體缺口：交叉編譯工具鏈實際指令與 glibc ABI 風險偵測、`EdgeLLM` HTTP client 該打哪個 endpoint、Python 音訊擷取的具體套件選擇、on-device 效能與記憶體量測方法論。

**最重要的架構發現（非 CONTEXT.md 已鎖定範圍，但直接影響任務拆解）**：native `llama-server` 的 `n_ctx`（context window）是**啟動時 CLI flag**（`--ctx-size`），不是像 `llama_cpp.Llama(n_ctx=...)` 那樣的 Python 建構參數。這代表 `config.LLM_N_CTX` 這個既有 profile-driven 設定值，消費點要從 `server/llm.py::EdgeLLM._get_model()` 搬到「啟動 llama-server 的 shell 指令/腳本」——這會讓 `tests/test_llm_n_ctx_profile.py` 目前對 `Llama(n_ctx=...)` 建構參數的斷言整組失效，需要改測「llama-server 啟動指令組出的 `--ctx-size` 引數值」而不是 Python 物件的 kwarg。同理 `tests/test_llm.py` 目前 monkeypatch `llama_cpp.Llama`/`create_chat_completion` 的方式也要整組改成 monkeypatch `EdgeLLM` 內部的 HTTP 呼叫函式。這兩個既有測試檔案的重寫，是本 phase 隱含但必須做的工作，不是選配。

**Primary recommendation：** `EdgeLLM` 保留完全相同的 `available()`/`generate()` public 介面，內部把 `_get_model()`/`create_chat_completion` 換成一個小的 private HTTP helper（`_call_llama_server(messages) -> str | None`，用 stdlib `urllib.request`——**不要引入 `requests`/`httpx` 新依賴**，理由見下方 Package Legitimacy Audit），打 `POST http://127.0.0.1:<port>/v1/chat/completions`；`available()` 改成對 `/health` 發一個短逾時 GET。`llama-server` 本身由部署腳本（`run_edge.sh` 或新腳本）以 `--ctx-size ${TALKYBUDDY_LLM_N_CTX}` 啟動，Python 端完全不再持有 n_ctx 這個概念。

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| LLM 推論（token 生成） | 裝置端 native process（llama-server，非 Python） | — | llama.cpp 原生二進位跑在自己的作業系統行程，不在 FastAPI/uvicorn 行程內；這是本 phase 的核心邊界搬遷 |
| LLM 服務化契約（available/generate、逾時、safety_check） | Backend（`server/llm.py::EdgeLLM`，FastAPI 行程內） | — | 既有 `VoicePipeline` 降級鏈只認得這個 Python 物件介面，llama-server 只是它背後打的一個 HTTP endpoint |
| 音訊擷取/播放（ALSA） | 裝置端獨立 client 進程（`edge/runtime/local_client.py`） | — | 不是瀏覽器（Browser tier 不存在於本 phase 驗收路徑）；是一個新的、與 FastAPI server 平行的 OS process，透過 loopback WS 與既有 server 溝通 |
| ASR/TTS 推論 | Backend（既有 `server/asr_base.py`/`server/tts.py`，CPU sherpa-onnx） | — | 本 phase 不改動，已存在且驗證過；不屬本次研究缺口 |
| 對話狀態機/降級鏈 | Backend（`server/pipeline.py::VoicePipeline`） | — | 既有邏輯完全重用，本 phase 不修改其介面 |
| 延遲/記憶體量測 | 裝置端 shell 工具（`llama-bench`、`/proc/<pid>/status`） | Backend（無） | 這是 phase 驗收證據的產生方式，不是應用程式碼的一部分 |

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| llama.cpp (native `llama-server`/`llama-bench`/`llama-cli`) | latest `master`（無穩定 release tag，專案已鎖定 build-from-source） | 邊緣 LLM 推論服務（HTTP）+ 效能量測工具 | 專案既有選型（STACK.md 已鎖定），本 phase 只是把「怎麼交叉編譯出這個 binary」的缺口補上 |
| `gcc-aarch64-linux-gnu`（Ubuntu/Debian apt 套件） | Ubuntu 24.04 apt 版本（`aarch64-linux-gnu-gcc` 13.x 系列，隨 host distro 版本浮動） | 開發機交叉編譯 llama.cpp 的第一選擇工具鏈（D-03） | apt 免額外下載、免向使用者要 SDK 路徑，符合 D-03「recommended default」理由；風險（glibc ABI）已在 D-03 明訂偵測與 fallback 流程 |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `arecord`/`aplay`（alsa-utils，Yocto image 通常內建） | Yocto scarthgap 內建版本（待 07-03 之後首次 SSH 進裝置以 `arecord --version` 確認） | 裝置端 16k mono WAV 擷取/播放，經 subprocess 呼叫 | D-04 的**優先選項**——零 Python C-extension 編譯風險，裝置無 gcc/cmake 下最穩妥 |
| `sounddevice`（PyPI） | 最新版（PyPI，見下方 Package Legitimacy Audit） | 裝置端 ALSA 擷取/播放的 Python 原生 API（PortAudio binding） | 僅在 `arecord`/`aplay` subprocess 呼叫證實不敷需求（如需要低延遲串流、非簡單「錄一段→存檔」模式）時才升級到此方案；見下方風險說明 |
| `urllib.request`（Python stdlib） | Python 3.12 內建 | `EdgeLLM` → `llama-server` 的 HTTP client | 專案既有 `server/cloud_llm.py` 已用 stdlib `urllib.request`/`urllib.error`（非 `requests`/`httpx`）打 Anthropic API；沿用同款式，**零新增依賴**，也避開下方 Package Legitimacy Audit 對 `requests`/`httpx` 的 SUS 標記 |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| stdlib `urllib.request` | `requests` 或 `httpx`（CONTEXT.md D-01 原文提及） | D-01 原文寫「`requests`/`httpx` 呼叫」，但兩者皆需新增 pip 依賴（provision_device.sh 目前刻意不裝任何未釘版新套件），且 package-legitimacy 查核對兩者皆回 `SUS`（見下方，僅因 registry download-count 資料源限制，非真的可疑）。專案自己的 `cloud_llm.py` 已示範 stdlib 做法完全夠用（單一 POST + JSON + timeout），**建議 executor 用 stdlib 取代 D-01 原文提及的 requests/httpx**，此為研究層級的具體化建議，不牴觸 D-01 鎖定的「HTTP client」精神 |
| apt `gcc-aarch64-linux-gnu` 泛用工具鏈（D-03 primary） | Genio Yocto BSP SDK 官方 cross-toolchain（`~/hackathon/`） | Yocto SDK 版本 glibc 與裝置系統映像**保證**一致（因為是同一個 BSP 建置出來的），ABI 風險趨近零；但需要使用者提供/確認 SDK 路徑，設置時間較長。D-03 已鎖定「apt 先試、失敗一次即切換」，不需要 planner 重新決策，只需要在任務中明確安排「試一次、失敗即切換」的檢查點 |
| `sounddevice`（PortAudio） | `pyalsaaudio`（直接綁 ALSA API，無 PortAudio 中介層） | `pyalsaaudio` 更貼近 ALSA 原生、依賴鏈更短，但同樣是 C-extension，一樣需要在裝置上編譯（裝置無 gcc）除非有 prebuilt wheel；兩者風險相近，`arecord`/`aplay` subprocess 才是真正避開編譯風險的選項 |

**Installation（開發機交叉編譯）：**
```bash
# 開發機（Ubuntu/Debian）安裝交叉工具鏈（D-03 primary path）
sudo apt-get install -y gcc-aarch64-linux-gnu g++-aarch64-linux-gnu cmake

# clone llama.cpp（若尚未在 repo 內）
git clone https://github.com/ggml-org/llama.cpp.git
cd llama.cpp

# 交叉編譯：明確關閉 x86 SIMD 探測、關閉 native march 探測、
# 顯式指定 Cortex-A78 相容 ISA（D-02 鎖定值）
cmake -B build-aarch64 \
  -DCMAKE_SYSTEM_NAME=Linux \
  -DCMAKE_SYSTEM_PROCESSOR=aarch64 \
  -DCMAKE_C_COMPILER=aarch64-linux-gnu-gcc \
  -DCMAKE_CXX_COMPILER=aarch64-linux-gnu-g++ \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_NATIVE=OFF \
  -DCMAKE_C_FLAGS="-march=armv8.2-a+dotprod+i8mm" \
  -DCMAKE_CXX_FLAGS="-march=armv8.2-a+dotprod+i8mm" \
  -DGGML_OPENMP=OFF

cmake --build build-aarch64 --config Release -j"$(nproc)" --target llama-server llama-bench llama-cli

# 產物：build-aarch64/bin/llama-server、llama-bench、llama-cli
```

**Version verification：** llama.cpp 無穩定版本號（rolling `master`），無法用 `npm view`/`pip index` 類指令查版本；改以 `git rev-parse HEAD` 記錄實際編譯的 commit hash 到部署文件，確保 Day-N 之間可重現、可追溯（見 Pitfall 1 的「先小步驗證再整包編」建議）。

## Package Legitimacy Audit

| Package | Registry | Age | Downloads | Source Repo | Verdict | Disposition |
|---------|----------|-----|-----------|-------------|---------|-------------|
| `sounddevice` | PyPI | established（多年歷史，spatialaudio/python-sounddevice 專案） | unknown（查核工具無法讀取 download count） | 查核工具回傳 `null`（實際上有：`github.com/spatialaudio/python-sounddevice`） | SUS（僅因 `unknown-downloads`＋`no-repository` 兩項訊號，皆為查核工具資料源限制，非真實可疑） | Approved with note — 只有在真的選用 `sounddevice`（非預設路徑）時才安裝；executor 安裝前建議先確認裝置能否免編譯拿到 wheel（`pip install sounddevice` 觀察是否觸發原始碼編譯），若觸發編譯則直接改用 `arecord`/`aplay`，不強行排除障礙 |
| `requests` | PyPI | established（`psf/requests`，Python 生態系最廣泛使用套件之一） | unknown（同上限制） | `github.com/psf/requests`（確認存在） | SUS（同上，僅 `unknown-downloads` 訊號） | **不建議採用** — 本研究建議改用 stdlib `urllib.request`（見上方 Alternatives Considered），故不需要安裝此套件，SUS 標記不適用 |
| `httpx` | PyPI | established（`encode/httpx`） | unknown（同上限制） | `github.com/encode/httpx`（確認存在） | SUS（同上） | **不建議採用** — 同上，改用 stdlib，不需要安裝 |

**Packages removed due to [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** `sounddevice`、`requests`、`httpx` — 三者的 SUS 判定皆源自查核工具無法取得 PyPI 週下載量資料（`unknown-downloads`），**不是**基於任何實際可疑訊號（無 postinstall script、無新註冊、有已知官方 repo）；`requests`/`httpx` 本研究建議直接不採用（改走 stdlib），`sounddevice` 若 executor 選用，仍建議在 PLAN.md 中加入 `checkpoint:human-verify`（確認 `pip install sounddevice` 在裝置上是否觸發原始碼編譯）作為安裝前置檢查，而非因為套件本身可疑。

*本表所有套件名稱皆來自現有 codebase 慣例（`cloud_llm.py` 已用 `urllib`）或 CONTEXT.md D-04 原文（`sounddevice`），非 WebSearch/訓練資料臆測的新套件名 — 但 `sounddevice` 在 aarch64 上的 wheel 可用性本身是 `[CITED: python-sounddevice 官方安裝文件]`，非 `[VERIFIED]`，見下方 Common Pitfalls Pitfall 5。*

## Architecture Patterns

### System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                  Genio 520 裝置（Yocto，SSH 已驗證可達）              │
│                                                                       │
│  [麥克風] → arecord/aplay 或 sounddevice (擷取 16k mono WAV bytes)   │
│       │                                                              │
│       ▼                                                              │
│  edge/runtime/local_client.py  ← NEW（獨立 OS process）              │
│       │  WS client（沿用既有 wire protocol，binary WAV + audio_end） │
│       ▼                                                              │
│  ws://127.0.0.1:8787/ws/talk  ─────────────────────────────────────┐│
│       │                          server/app.py（uvicorn，UNCHANGED）││
│       ▼                                                              ││
│  server/pipeline.py::VoicePipeline.run_turn_audio()（UNCHANGED 邏輯）││
│       │  RIFF-sniff fast path 命中 → soundfile 直讀，零 ffmpeg       ││
│       ▼                                                              ││
│  server/asr_base.py → CPU SenseVoice（UNCHANGED）→ (text, conf)     ││
│       ▼                                                              ││
│  scaffold.respond() → ScaffoldResult                                 ││
│       ▼                                                              ││
│  server/llm.py::EdgeLLM.generate()  ← MODIFIED（本 phase 核心）      ││
│       │  HTTP POST /v1/chat/completions（urllib，逾時 8s）           ││
│       ▼                                                              ││
│  llama-server（native binary，獨立 OS process，NEW）                 ││
│       │  --ctx-size ${LLM_N_CTX}（啟動時 CLI flag，非 Python 參數） ││
│       │  監聽 127.0.0.1:<port>（非 8787，避免撞 uvicorn）            ││
│       ▼                                                              ││
│  回傳 completion text → EdgeLLM.generate() 回傳字串（契約不變）      ││
│       ▼                                                              ││
│  server/tts.py（CPU sherpa-onnx，UNCHANGED）→ WAV bytes              ││
│       ▼                                                              ││
│  server/store.py（SQLite，UNCHANGED）                                ││
│       ▼                                                              ││
│  WS response {"type":"tts_audio",...} → local_client.py ────────────┘│
│       │                                                              │
│       ▼                                                              │
│  arecord/aplay 或 sounddevice 播放 → [喇叭]                          │
│                                                                       │
│  量測工具（部署時執行，非應用程式碼一部分）：                        │
│  - llama-bench（cross-compiled binary）→ 首字延遲/tokens-per-sec    │
│  - /proc/<pid>/status VmHWM（uvicorn PID + llama-server PID 加總）  │
│    → 驗證三引擎峰值 RSS < 4GB                                        │
└─────────────────────────────────────────────────────────────────────┘
```

### Recommended Project Structure

```
edge/
├── runtime/
│   ├── audio_io.py          # NEW: ALSA capture/playback wrapper（arecord/aplay 或 sounddevice 二擇一，統一介面）
│   ├── local_client.py       # NEW: WS client 迴圈（沿用既有 web/*.js 的 wire protocol）
│   ├── run_edge.sh           # MODIFIED: 除了拉起 uvicorn，也負責拉起 llama-server（見 Pattern 3）
│   └── run_llama_server.sh   # NEW（建議獨立腳本）: 組出 llama-server 啟動指令，注入 --ctx-size ${LLM_N_CTX}
├── deploy/
│   ├── build.sh              # MODIFIED: 交叉編譯 llama.cpp（cmake 指令見 Standard Stack）+ push 前先跑 file/ldd 快篩
│   └── push.sh                # MODIFIED: 除既有 server/、edge/runtime、web，追加 rsync 交叉編譯出的 llama-server/llama-bench binary
└── models/                    # 既有目錄，追加 GGUF 模型檔（若尚未 push）
server/
├── llm.py                     # MODIFIED: EdgeLLM 內部改 HTTP client（見 Pattern 2）
└── config.py                  # MODIFIED: 新增 LLM_SERVER_HOST/LLM_SERVER_PORT/LLM_SERVER_URL（同款 os.environ.get 慣例）
tests/
├── test_llm.py                 # MUST REWRITE: 現有 monkeypatch llama_cpp.Llama 的方式全面失效
└── test_llm_n_ctx_profile.py   # MUST REWRITE: n_ctx 斷言標的從 Llama(n_ctx=) kwarg 改成 llama-server 啟動指令組出的 --ctx-size 引數
```

### Pattern 1: `local_client.py` 是 WS Client，不是取代 Server（沿用既有 ARCHITECTURE.md 設計）

**What：** `edge/runtime/local_client.py` 是一個獨立的 OS process，用 WebSocket **client** 連到已經在跑的 `ws://127.0.0.1:8787/ws/talk`，送出的 binary frame 與 `{"type":"audio_end"}` 完全比照 `web/*.js` 今天做的事；收到 `{"type":"tts_audio",...}` 後改用 ALSA 播放而非瀏覽器 `<audio>`。這不是新協定，是既有協定換一個「講話的人」。

**When to use：** 任何「裝置本身就是麥克風+喇叭，沒有螢幕/瀏覽器」的情境。

**Trade-offs：**
- ✅ `server/app.py` 的 routing、auth、WS 狀態機完全不用改，這是本 phase 風險最低的部分。
- ✅ 同一個 server process 理論上可同時服務 `local_client.py`（離線 demo）與遠端瀏覽器（教師儀表板），不需要額外設計。
- ⚠️ `local_client.py` 進程必須在 uvicorn 已經 ready（health check 通過）之後才啟動；`run_edge.sh` 若同時拉起兩者，需要一個簡單的等待/重試迴圈（`curl` health check），不能假設啟動順序天然正確。

**Example（骨架，沿用 ARCHITECTURE.md 既有規劃）：**
```python
# edge/runtime/local_client.py
import asyncio, base64, json
import websockets
from edge.runtime import audio_io

async def run_loop():
    async with websockets.connect("ws://127.0.0.1:8787/ws/talk") as ws:
        while True:
            await audio_io.wait_for_button_or_vad_trigger()
            wav_bytes = audio_io.capture_16k_mono_wav()   # 見 Code Examples
            await ws.send(wav_bytes)
            await ws.send(json.dumps({"type": "audio_end"}))
            async for msg in ws:
                event = json.loads(msg)
                if event["type"] == "tts_audio":
                    audio_io.play_wav_bytes(base64.b64decode(event["wav_b64"]))
                if event["type"] == "idle":
                    break
```

### Pattern 2: `EdgeLLM` 保留介面、內部從 Constructor-based 換成 HTTP-based（核心重構）

**What：** `EdgeLLM.available()`/`EdgeLLM.generate()` 的 public 簽名與回傳語意**完全不變**（`available() -> bool`；`generate(student_text, scaffold, directive=None) -> str | None`；逾時/例外/safety_check 未過一律回 `None`）。內部把「懶載入 `llama_cpp.Llama` 物件」換成「懶連線一個 HTTP endpoint」。

**When to use：** 這是本 phase ELOOP-02 的唯一必要程式碼變更點。

**Trade-offs：**
- ✅ `server/pipeline.py`（呼叫端）完全不用改一行——這正是既有降級鏈設計（`available()`/`generate()` duck-typed 契約）要保護的東西。
- ⚠️ `_GENERATE_TIMEOUT_S = 8.0` 現在要包住「HTTP 連線 + 等回應」而非「Python 函式呼叫」，`urllib.request.urlopen(req, timeout=...)` 的 `timeout` 參數要設定為略小於 8.0（如 7.5s），為外層 `time.monotonic()` 檢查留一點餘裕，避免逾時判定發生在網路層而非邏輯層。
- ⚠️ `available()` 語意徹底改變：原本是「模型檔存在 + `llama_cpp` 可 import」，現在必須是「對 `llama-server` 的 `/health` 發一個短逾時（如 0.3–0.5s）GET，200 才回 True」。這代表 `available()` 不再是純本地檢查，而是一次網路 I/O——必須包在 try/except 裡（連線被拒/逾時都回 False），且逾時值要夠短，不能拖慢 pipeline 每輪都要跑的 `available()` 呼叫。

**Example（概念骨架）：**
```python
# server/llm.py — 內部改動示意（public 介面不變）
import json
import urllib.request
import urllib.error

def _llama_server_base_url() -> str:
    from server import config
    return f"http://{config.LLM_SERVER_HOST}:{config.LLM_SERVER_PORT}"

class EdgeLLM:
    def available(self) -> bool:
        try:
            req = urllib.request.Request(f"{_llama_server_base_url()}/health")
            with urllib.request.urlopen(req, timeout=0.5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _call_llama_server(self, messages: list[dict]) -> str | None:
        """唯一的 HTTP 呼叫點；測試 monkeypatch 這個方法即可，不需真的起 llama-server。"""
        body = json.dumps({
            "messages": messages,
            "max_tokens": 120,
            "temperature": 0.7,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{_llama_server_base_url()}/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=7.5) as resp:
            payload = json.loads(resp.read())
        return payload["choices"][0]["message"]["content"]

    def generate(self, student_text, scaffold, directive=None):
        # ...既有 prompt 組裝邏輯完全不變...
        try:
            text = self._call_llama_server(messages)
        except Exception:
            return None
        # ...既有 safety_check / target 補句邏輯完全不變...
```

**這個 pattern 對既有測試的直接衝擊（必須在本 phase 任務拆解中處理）：**
- `tests/test_llm.py` 目前用 `_FakeModel`/`monkeypatch.setattr(edge, "_get_model", ...)` 攔截 `create_chat_completion`；改版後應改成 `monkeypatch.setattr(edge, "_call_llama_server", lambda messages: "...")`，驗證 `messages` 內容而非 `create_chat_completion` 的呼叫參數。
- `tests/test_llm_n_ctx_profile.py` 目前斷言 `Llama(...).kwargs["n_ctx"] == config.LLM_N_CTX`；改版後 `n_ctx` 已經不是 Python 端概念，該測試應改成斷言「組出 llama-server 啟動指令的函式，其 `--ctx-size` 引數值 == `config.LLM_N_CTX`」（若把啟動指令組裝邏輯放進一個可測試的 Python 函式，例如 `edge/runtime/run_llama_server.py::build_llama_server_argv()`，而非只留在 shell 腳本裡——這樣才有東西可以單元測試）。

### Pattern 3: llama-server 啟動時機與 `n_ctx` 的搬遷

**What：** `n_ctx`（context window）現在是 llama-server 的啟動參數 `--ctx-size`，必須在**啟動 llama-server 的那一刻**決定，不能像 Python 物件屬性一樣事後查詢/傳遞。部署腳本（`run_edge.sh` 或新增的 `run_llama_server.sh`）要讀 `config.py` 的等效邏輯（或直接讀同名環境變數 `TALKYBUDDY_LLM_N_CTX`），組出：

```bash
./llama-server \
  --model /path/to/qwen2.5-1.5b-instruct-q4_k_m.gguf \
  --ctx-size "${TALKYBUDDY_LLM_N_CTX:-512}" \
  --host 127.0.0.1 \
  --port "${TALKYBUDDY_LLM_SERVER_PORT:-8080}" \
  --threads "${TALKYBUDDY_LLM_THREADS:-4}"
```

**When to use：** 這是 ELOOP-02 的部署面配套，與 Pattern 2 的 Python 面配套是一體兩面。

**Trade-offs：**
- ✅ 沿用既有 `TALKYBUDDY_LLM_N_CTX` env var 名稱（不新增一個平行的設定管道），維持 `docs/DEPLOY_EDGE.md` 既有環境變數表的慣例。
- ⚠️ `--threads` 這個 CLI flag 現在也要走同樣的「啟動時決定」路徑，而非 Python 端的 `n_threads=4` kwarg（原 `server/llm.py:96` 硬編 `n_threads=4`）——這正好是 ELOOP-03「executor 需依 llama-bench 實測挑選」要覆寫的值，必須做成環境變數/部署參數，不能維持硬編。
- ⚠️ `run_edge.sh` 需要保證 llama-server 先於（或至少與）uvicorn 一起就緒；建議 llama-server 啟動後先 curl `/health` 迴圈確認就緒，再啟動 uvicorn（或反過來——uvicorn 啟動不阻塞，`EdgeLLM.available()` 的短逾時設計本來就能容忍 llama-server 稍晚就緒，`available()` 回 False 時 pipeline 走 scaffold-only，不會 crash，只是本輪沒有 LLM 加值）。

## Common Pitfalls

### Pitfall 1: apt 交叉工具鏈編出的 binary 在裝置上因 glibc 版本落差跑不起來，且不易快速判斷

**What goes wrong：** `gcc-aarch64-linux-gnu` 交叉編出的 `llama-server` binary 用 `file` 檢查是正確的 aarch64 ELF，push 到裝置後執行卻直接報 `version 'GLIBC_2.XX' not found` 或無聲的 exec format 錯誤/segfault，卡在「看起來對但跑不動」的除錯泥沼。

**Why it happens：** apt 泛用交叉工具鏈綁定的 glibc 版本由**開發機的 distro 版本**決定（例如 Ubuntu 24.04 的 `gcc-aarch64-linux-gnu` 可能鏈結較新的 glibc symbol version），而裝置（Yocto scarthgap）的 glibc 版本是由 BSP 建置時決定、與開發機無關。若交叉工具鏈的 glibc 版本**新於**裝置系統 glibc，binary 會要求裝置系統沒有的 symbol version，執行時失敗；若交叉工具鏈 glibc 版本**舊於或等於**裝置，通常可以正常執行（glibc 向前相容）。

**How to avoid：**
1. **編譯前**：SSH 進裝置跑 `ldd --version` 或 `/lib/aarch64-linux-gnu/libc.so.6`（若路徑存在）取得裝置 glibc 版本號，與開發機 `aarch64-linux-gnu-gcc --version` 隨附的 glibc 版本（可用 `aarch64-linux-gnu-gcc -print-file-name=libc.so` 或直接查 apt 套件 `libc6-dev-arm64-cross` 的版本字串）比對，若開發機工具鏈版本明顯較新，直接跳過 apt 路徑，用 D-03 訂的 fallback（Yocto SDK 工具鏈）。
2. **編譯後、大規模驗證前的快篩**（D-03 原文要求「不要在 apt 路徑上反覆嘗試超過一次修正」）：
   ```bash
   file build-aarch64/bin/llama-server   # 開發機確認 ELF 架構是 aarch64，不是 x86-64
   # push 到裝置後，SSH 執行最小可行性測試：
   ssh "${SSH_TARGET}" "${TARGET_ROOT}/edge/deploy/llama-server --version"
   # 若印出版本字串 → 動態連結成功，繼續量測；
   # 若印出 "GLIBC_2.XX not found" 或 "cannot execute binary file" →
   #   立即切換 Yocto SDK 交叉工具鏈重編，不要在 apt 路徑上除錯第二次
   ```
3. 若時間緊迫想避開整個 ABI 議題，可嘗試 `-DCMAKE_EXE_LINKER_FLAGS="-static"` 全靜態連結（llama-server 只綁 loopback、不需要 DNS/NSS，全靜態連結對這個用途風險較低）；但這不是 D-03 鎖定的官方 fallback，僅作為「apt 路徑失敗但還想省下 Yocto SDK 設置時間」時的實驗性選項，若失敗仍要照 D-03 走 Yocto SDK 路徑。

**Warning signs：** `file` 顯示正確架構但裝置端 `--version` 卡住/報錯；`ssh device "ldd ./llama-server"` 顯示任何 "not found"。

**Confidence:** `[CITED: CMake Discourse cross-compile aarch64 threads]`／`[ASSUMED]` 針對確切 glibc 版本號（未在此研究階段實測比對，需 executor 在裝置上實際跑 `ldd --version` 才能拿到真數字）。

### Pitfall 2: `n_ctx`/`n_threads` 從 Python kwarg 搬到 CLI flag 後，既有測試斷言全部失效卻沒人注意到

**What goes wrong：** `tests/test_llm_n_ctx_profile.py::test_get_model_uses_config_llm_n_ctx` 目前斷言 `_FakeLlama(...).kwargs["n_ctx"] == 999`，重構後 `EdgeLLM` 根本不再建構任何 `Llama` 物件、也不再有 `n_ctx` kwarg 可斷言——如果只刪掉這個測試而不補等價驗證，`config.LLM_N_CTX` 這個 profile-driven 值就會失去自動化保護，回歸成「executor 手動記得改部署腳本」的脆弱狀態，正是 PITFALLS.md 已警告過的「n_ctx=1024 遺留過久」同類風險換了個發生位置。

**Why it happens：** 重構聚焦在讓「原有測試綠燈」，卻沒意識到綠燈的測試斷言標的（Python 物件 kwarg）本身已經因架構搬遷而失去意義，會被誤判為「這段邏輯不需要測了」。

**How to avoid：** 把「組裝 llama-server 啟動指令」寫成一個獨立、可單元測試的 Python 函式（例如 `edge/runtime/run_llama_server.py::build_llama_server_argv(ctx_size: int, port: int, threads: int) -> list[str]`），而不是直接寫死在 shell 腳本字串拼接裡；`run_edge.sh`/`run_llama_server.sh` 呼叫這個函式取得 argv 再 `exec`。這樣 `test_llm_n_ctx_profile.py` 改寫後可以斷言 `build_llama_server_argv(config.LLM_N_CTX, ...) 包含 "--ctx-size" 後面接 "999"`，維持與原測試等價的保護力。

**Warning signs：** PLAN.md 若只寫「更新 EdgeLLM 為 HTTP client」而未提及「同步改寫 test_llm.py / test_llm_n_ctx_profile.py 的斷言標的」，就是這個坑的前兆。

**Confidence:** `[VERIFIED: 直接讀取 tests/test_llm_n_ctx_profile.py 原始碼]`（本研究已讀取該檔案確認斷言方式，見上方 code_context）。

### Pitfall 3: Thread 數沿用既有硬編 `n_threads=4`，未針對 big.LITTLE 實測就當作已解決

**What goes wrong：** `server/llm.py:96` 現有 `n_threads=4` 是舊架構（in-process）下的硬編值，重構後若原封不動搬進 llama-server 啟動指令的 `--threads 4`，看似「沿用既有已知安全值」，實際上從未在 Genio 520 真機上驗證過 4 是否真的優於 2 或 6——ELOOP-03 明文要求「executor 依 llama-bench 實測 1/2/4 執行緒數值挑選，不可預設」，若照抄舊值等於沒有真的做這個驗證步驟。

**Why it happens：** 重構時「保留原有數字」感覺風險最低，但這個數字的來源本來就不是 Genio 520 實測結果，只是 PC 開發機時代的預設猜測。

**How to avoid：**
```bash
# 在裝置上直接跑 llama-bench 掃描（llama-bench 支援 -t 逗號分隔多值）：
./llama-bench -m qwen2.5-1.5b-instruct-q4_k_m.gguf -t 1,2,4,6,8 -p 128 -n 128
# 讀 pp（prompt processing tokens/sec）與 tg（token generation tokens/sec）兩欄，
# 分別找出各自最佳執行緒數（兩者最佳值不一定相同，llama-server 的
# --threads 與 --threads-batch 可分別設定 prefill 與 decode 的執行緒數）。
```
社群在結構類似的 big.LITTLE SoC（如 RK3588，2×A76+4×A55-class）上的回報顯示：把 A55（LITTLE）核心也算進執行緒池，往往因為每層計算後的同步 barrier 被慢核拖累而導致 4 threads 優於 8 threads——這與 Genio 520 的 2×A78+6×A55 配置在架構上類似，但這是**其他晶片的社群回報，不是 Genio 520 本身的實測數字**，僅作方向性參考，執行緒數的實際最佳值仍必須在 Genio 520 上重新掃描。

**Warning signs：** PLAN.md 若把 thread 數直接寫死成某個具體數字而非「llama-bench 掃描後填入」的佔位符，就是把假設當結論。

**Confidence:** `[CITED: RK3588 社群回報 via WebSearch]`，Genio 520 專屬數字為 `[ASSUMED — 待 executor 實測]`。

### Pitfall 4: 只量測 uvicorn（Python）行程的 RSS，漏算 llama-server 這個獨立行程

**What goes wrong：** 重構後 llama-server 是一個**獨立的 OS process**（不像原本的 `llama_cpp.Llama` 是 uvicorn 行程內的一個物件），若延續舊有「量測 Python process RSS」的量測腳本/直覺，llama-server 自己的記憶體占用（GGUF 權重 + KV cache，原本估計約 1.1GB+150-400MB）會被完全漏算，讓 ELOOP-04 的「三引擎峰值 < 4GB」量測看起來遠低於真實值，形成一個危險的假陰性（看起來有大量 headroom，實際上快爆表）。

**Why it happens：** 架構搬遷把一部分記憶體占用從「Python 行程內」移到「另一個 OS 行程」，量測方法如果沒有跟著架構調整，會系統性低估。

**How to avoid：**
```bash
# 分別取得 uvicorn 與 llama-server 的 PID，各自讀 VmHWM（peak RSS，非目前 RSS）：
UVICORN_PID=$(pgrep -f "uvicorn server.app:app")
LLAMASERVER_PID=$(pgrep -f "llama-server")
for pid in "$UVICORN_PID" "$LLAMASERVER_PID"; do
  echo "PID $pid: $(grep VmHWM /proc/$pid/status)"
done
# 三引擎峰值 = uvicorn 行程 VmHWM（含 ASR sherpa-onnx + TTS sherpa-onnx，皆在同一 Python 行程內）
#            + llama-server 行程 VmHWM
# 這是本 phase「4GB 驗證閘」唯一正確的加總方式。
```
量測時機要涵蓋「一輪完整對話期間」（ASR 解碼 + LLM 生成 + TTS 合成前後重疊的瞬間），而非個別引擎閒置時的穩態值——沿用 PITFALLS.md Pitfall 4 已強調的「不能只在各引擎獨立測試時量測」原則，本 phase 額外要求「加總跨行程」。

**Warning signs：** 量測腳本/報告只出現一個 PID 或一個數字，沒有分別列出 uvicorn 與 llama-server 兩行程各自的 VmHWM。

**Confidence:** `[CITED: /proc/pid/status man page via WebSearch]`（VmHWM 語意）；「必須跨行程加總」的架構推論為 `[VERIFIED: 本研究對照 Pattern 2/3 的架構搬遷直接推導]`。

### Pitfall 5: 假設 `sounddevice` 在 aarch64 上「有現成 wheel」而未實測就寫進部署腳本

**What goes wrong：** CONTEXT.md D-04 附註提到「`sounddevice`（pip 有 aarch64 wheel）」，但官方安裝文件實際上**沒有**承諾 Linux/aarch64 有預編譯 wheel——文件明確指出 PortAudio 在 Linux 上不會隨 pip 自動安裝，且只有 Windows 平台的 wheel 是有把 PortAudio 打包進去的；`sounddevice` 本身的 C extension 部分即使有 wheel，仍需要系統已裝好 `libportaudio2`（動態連結庫），而裝置目前確認無 gcc/cmake，若剛好沒有對應的 aarch64 manylinux wheel，會觸發原始碼編譯並直接卡在「無編譯器」這個 07-03 已經踩過的坑。

**Why it happens：** CONTEXT.md 撰寫時的「pip 有 aarch64 wheel」判斷來自訓練資料的一般印象，未在本次研究或裝置上實際驗證。

**How to avoid：** 部署腳本安裝 `sounddevice` 時，**先**在裝置上執行 `pip install sounddevice` 並觀察是否觸發 `Building wheel for sounddevice`/編譯錯誤訊息（而非直接假設成功）；若觸發編譯或缺 `libportaudio2`，立即改用 `arecord`/`aplay` subprocess 路徑（D-04 已明訂此 fallback 順序）。不要把「pip 有 wheel」當作已驗證的前提寫進最終部署文件，直到裝置上實測過一次。

**Warning signs：** PLAN.md 若把 `sounddevice` 列為「唯一」音訊擷取方案（而非「先試、失敗即退」的雙選項），沒有安排實測檢查點。

**Confidence:** `[CITED: python-sounddevice 官方安裝文件 via WebSearch]`——文件明確表示 Linux 平台 PortAudio 不隨 pip 打包，與 CONTEXT.md「pip 有 aarch64 wheel」的樂觀假設有落差，此為本研究最重要的一項修正。

## Code Examples

### llama-bench 首字延遲/tokens-per-sec 量測（ELOOP-03）

```bash
# Source: https://github.com/ggml-org/llama.cpp/blob/master/tools/llama-bench/README.md
# -p：prompt-processing token 數（模擬 prefill/首字延遲）；-n：generation token 數
# -t：thread 數（逗號分隔多值一次掃描）
./llama-bench -m /path/to/qwen2.5-1.5b-instruct-q4_k_m.gguf \
  -p 128 -n 128 -t 1,2,4,6,8 -r 3
# 輸出欄位含 pp（prompt eval tokens/sec）、tg（generation tokens/sec）；
# 首字延遲估算 ≈ (prompt token 數 / pp tokens-per-sec) * 1000 ms，
# 用實際 system prompt + user prompt 的估計 token 數代入，比對 D-05 的 800ms 門檻。
```

### llama-server 啟動 + `/health` 就緒探測（Pattern 3 配套）

```bash
# Source: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
./llama-server --model /path/to/model.gguf --ctx-size 512 \
  --host 127.0.0.1 --port 8080 --threads 4 &
LLAMA_SERVER_PID=$!

# 就緒探測（run_edge.sh 內建議加入，避免 EdgeLLM 第一輪 available() 誤判 False）：
for i in $(seq 1 30); do
  curl -sf http://127.0.0.1:8080/health && break
  sleep 1
done
```

### 峰值 RSS 跨行程加總（ELOOP-04，Pitfall 4 配套）

```python
# Source: 綜合 man proc_pid_status(5) 語意，本研究撰寫
def read_peak_rss_kb(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1])  # kB
    except FileNotFoundError:
        return None
    return None

# 加總 uvicorn（ASR+TTS 常駐）+ llama-server（LLM 常駐）兩行程的 VmHWM
# 才是「三引擎同時載入峰值」的正確數字，不能只讀其中一個 PID。
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| `server/llm.py::EdgeLLM` in-process `llama_cpp.Llama(n_ctx=..., n_threads=4)` | `EdgeLLM` HTTP client → 交叉編譯 `llama-server` native binary（`--ctx-size`/`--threads` CLI flag） | 本 phase（ELOOP-02） | `n_ctx`/`n_threads` 的「所有權」從 Python 物件搬到部署腳本；既有 2 個測試檔案的斷言標的整組失效，需改寫 |
| `scripts/setup_env.sh` 的 `pip install llama-cpp-python`（PC 原型路徑） | 裝置端不再需要此 pip 套件；改為交叉編譯 native binary + rsync push | 本 phase（僅限 edge 部署路徑；PC 原型 `scripts/setup_env.sh` 本身**不在本 phase 修改範圍**，需與 planner 確認 PC 測試環境是否也要換成打真的 llama-server，或維持 mock/monkeypatch 純測試不需要真跑模型） | 影響部署腳本與（間接）單元測試撰寫方式，不影響 `VoicePipeline` 呼叫端 |

**Deprecated/outdated：**
- `EdgeLLM._get_model()` 回傳 `llama_cpp.Llama` 物件的模式：本 phase 之後應視為歷史模式，僅供理解既有測試為何長那樣。

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | 開發機 apt `gcc-aarch64-linux-gnu` 工具鏈的 glibc 版本與 Yocto scarthgap 裝置 glibc 版本之間的確切數字關係（誰新誰舊） | Common Pitfalls Pitfall 1 | 若判斷方向錯誤，會在「該不該切換 Yocto SDK 工具鏈」上多花一次來回；緩解：D-03 已明訂「失敗一次即切換」的止損規則，不需要研究階段先驗證出精確數字 |
| A2 | Genio 520（2×A78+6×A55）的最佳 llama.cpp 執行緒數會落在 2 或 4（依 RK3588 社群類比推論） | Common Pitfalls Pitfall 3 | 若實測結果差異很大（如最佳值是 6 或 8），只是需要多花一次 llama-bench 掃描週期，不影響安全性；ELOOP-03 本來就要求實測，此假設只是「預期落點」不是「跳過實測的理由」 |
| A3 | Yocto scarthgap image 內建 `alsa-utils`（`arecord`/`aplay` 可直接用），未在 07-03 board bring-up 的既有記錄中被明確驗證過 | Standard Stack Supporting、Common Pitfalls Pitfall 5 | 若裝置實際上沒有 `alsa-utils`，D-04 的「優先選項」會落空，需要退到 `sounddevice` 這個本身也有風險的路徑，或用 `opkg`/其他套件管理器現場安裝；緩解：這應是 phase 內第一個要做的實測動作（`ssh device "which arecord aplay"`），成本極低 |
| A4 | `sounddevice` 官方文件未明確排除 aarch64/Yocto 平台，但也未明確保證有 manylinux aarch64 wheel（本研究判讀為「不保證」而非「保證沒有」） | Common Pitfalls Pitfall 5 | 若 PyPI 實際上已有 aarch64 manylinux wheel（近年 sounddevice 版本確實逐步擴大 wheel 涵蓋範圍），此假設偏保守，只會讓 executor 多做一次不必要的「先確認再用」動作，不會導致錯誤決策 |

**若這張表為空：** 不適用——以上 4 項皆需 executor 在裝置實測後確認或推翻。

## Open Questions

1. **PC 原型測試環境（`scripts/setup_env.sh`）是否也要移除 `llama-cpp-python`？**
   - What we know：CONTEXT.md D-01 的措辭聚焦在「邊緣 LLM」，`server/llm.py::EdgeLLM` 這個類別在既有 codebase 中同時服務 PC 原型與（未來）邊緣部署——兩者共用同一份 `server/llm.py`。
   - What's unclear：若 `EdgeLLM` 徹底改成 HTTP client，PC 開發/CI 環境的單元測試該如何驗證「真的能打通 llama-server」（而非只測 mock），是否需要在 PC 上也裝一份 llama-server 供整合測試用，或是否维持「單元測試一律 monkeypatch `_call_llama_server`，不測真連線」。
   - Recommendation：建議 planner 明確排定「單元測試層級全部 monkeypatch（如 Pattern 2 所述），不依賴真的 llama-server 進程」，並把「PC 上也能起一份 llama-server 做手動整合驗證」列為選配的驗證步驟（而非自動化測試的必要條件），避免 CI 環境需要交叉編譯產物。

2. **llama-server 的埠號與 uvicorn 是否可能衝突/需要防火牆考量？**
   - What we know：uvicorn 固定用 8787（`run_edge.sh` 現況），llama-server 建議另開一個埠（如 8080，llama.cpp 預設）。
   - What's unclear：07-03 已記錄裝置 sshd 無驗證機制的已知風險——llama-server 若綁 `0.0.0.0` 而非 `127.0.0.1`，會在同網段/tailnet 上暴露一個無驗證的 LLM 推論端點，這是本 phase 若不小心會意外擴大的攻擊面。
   - Recommendation：llama-server 啟動指令**必須**明確指定 `--host 127.0.0.1`（本文件範例已如此），不可沿用 uvicorn 的 `--host 0.0.0.0` 慣例；PLAN.md 應把這一點寫成明確的驗證項目（如 `curl` 從裝置外部 IP 打 llama-server 埠應該連不上）。

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `gcc-aarch64-linux-gnu`（開發機） | 交叉編譯 llama.cpp（D-03 primary） | 未知，需執行 `apt list --installed \| grep aarch64-linux-gnu` 確認 | — | 若開發機非 Debian/Ubuntu 系，改用 Docker 內建 apt 環境或 Yocto SDK 工具鏈（D-03 fallback） |
| `alsa-utils`（`arecord`/`aplay`，裝置端） | D-04 音訊擷取/播放主選項 | 未知，07-03 記錄未明確驗證此項——需 SSH 進裝置執行 `which arecord aplay` 確認 | — | 若缺失，`opkg install alsa-utils`（若 Yocto image 支援 opkg 動態安裝）或改用 `sounddevice`（見 Pitfall 5 風險） |
| Yocto BSP SDK cross-toolchain（`~/hackathon/`） | D-03 fallback 路徑 | 未知，需使用者確認確切路徑（CONTEXT.md 提及但未給絕對路徑） | — | 若使用者無法即時提供路徑，且 apt 路徑也失敗，需回報阻塞，等待使用者提供 SDK 位置 |
| `llama-bench`（隨 llama.cpp 一起交叉編譯） | ELOOP-03 延遲量測 | 會隨 Standard Stack 的 cmake 指令一併建置（`--target` 已包含 `llama-bench`） | — | 無需獨立 fallback，與 `llama-server` 同一個編譯產物 |

**Missing dependencies with no fallback：**
- 無（以上四項皆有 fallback 或屬於「需先在裝置上確認一次」而非「確定缺失」的狀態）。

**Missing dependencies with fallback：**
- `alsa-utils` 若缺失 → `sounddevice`（風險已於 Pitfall 5 說明）
- 開發機 apt 工具鏈若不可用/glibc 不合 → Yocto BSP SDK 工具鏈（D-03 官方 fallback）

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest（既有 `tests/` 目錄，無 `pytest.ini`/`pyproject.toml` 專屬設定檔，走預設探索規則） |
| Config file | none — 沿用既有慣例（`tests/conftest.py` 提供 `tmp_db` autouse fixture + `anyio_backend` fixture） |
| Quick run command | `.venv/bin/python -m pytest tests/test_llm.py tests/test_llm_n_ctx_profile.py -x -q` |
| Full suite command | `.venv/bin/python -m pytest tests/ -x -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ELOOP-02 | `EdgeLLM.available()` 對 `/health` 逾時/連線失敗回 False，不拋例外 | unit | `pytest tests/test_llm.py::test_available_false_on_connection_error -x` | ❌ Wave 0（需新增，現有 test_llm.py 沒有這個案例，因為舊架構的 `available()` 語意完全不同） |
| ELOOP-02 | `EdgeLLM.generate()` 逾時/HTTP 錯誤/safety_check 未過一律回 None，pipeline 降級不變 | unit | `pytest tests/test_llm.py -x` | 🟡 需**改寫**（現有測試存在但斷言標的錯誤，見 Pitfall 2） |
| ELOOP-02 | `config.LLM_N_CTX` 正確反映到 llama-server 啟動指令的 `--ctx-size` | unit | `pytest tests/test_llm_n_ctx_profile.py -x` | 🟡 需**改寫**（同上） |
| ELOOP-01 | `edge/runtime/audio_io.py` 擷取的 WAV bytes 符合 16k mono，命中既有 RIFF-sniff fast path | unit（可用假 WAV bytes，不需真麥克風） | `pytest tests/test_audio_io.py -x` | ❌ Wave 0（新模組，無既有測試） |
| ELOOP-03 | llama-bench 掃描結果与 D-05 門檻（800ms/3-4s）比對，產出 go/no-go 判定 | manual-only（需真機） | 無自動化指令；人工記錄 `llama-bench` 輸出並比對門檻 | — （本質上是硬體量測，非程式碼行為，manual-only 合理） |
| ELOOP-04 | 三引擎峰值 RSS < 4GB | manual-only（需真機） | 無自動化指令；人工執行 §Code Examples 的 RSS 加總 script 並記錄結果 | — （同上，硬體量測） |

### Sampling Rate
- **Per task commit：** `pytest tests/test_llm.py tests/test_llm_n_ctx_profile.py tests/test_audio_io.py -x -q`
- **Per wave merge：** `pytest tests/ -x -q`
- **Phase gate：** Full suite green + 真機 `llama-bench`/RSS 量測記錄（manual-only 項目）皆完成並附上實際輸出，才能進 `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/test_audio_io.py` — 覆蓋 `edge/runtime/audio_io.py`（ELOOP-01），可用假 bytes/mock subprocess 測試，不需真硬體
- [ ] `tests/test_llm.py` 改寫 — 覆蓋 `EdgeLLM` HTTP client 化後的 `available()`/`generate()` 契約（ELOOP-02，見 Pitfall 2）
- [ ] `tests/test_llm_n_ctx_profile.py` 改寫 — 覆蓋 `--ctx-size` 引數組裝邏輯（ELOOP-02，見 Pattern 2/Pitfall 2）
- [ ] 若採用 Pattern 2 的 `build_llama_server_argv()` 設計，需新增對應單元測試檔案（如 `tests/test_run_llama_server.py`）

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | 本 phase 新增的 llama-server 端點僅綁 `127.0.0.1`，不對外開放，不涉及使用者認證 |
| V3 Session Management | no | 不涉及 |
| V4 Access Control | yes | llama-server **必須**綁 `--host 127.0.0.1`，不可綁 `0.0.0.0`——見 Open Questions #2；07-03 已記錄裝置 sshd 無驗證機制的既有風險，本 phase 新增的網路服務不應再擴大暴露面 |
| V5 Input Validation | yes | `EdgeLLM._call_llama_server()` 的 HTTP request body 由既有 prompt 組裝邏輯產生（非使用者直接可控的原始 JSON），沿用既有 `guardrails.passes_guardrail()` 對輸出做安全檢查，本 phase 不改變這條護欄 |
| V6 Cryptography | no | localhost HTTP，不涉及跨網路傳輸敏感資料；不需要 TLS（loopback） |

### Known Threat Patterns for {stack}

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| llama-server 意外綁 `0.0.0.0` 並暴露在裝置所在的區網/tailnet 上，任何連得到裝置 IP 的人都能直接打 LLM 推論端點（繞過既有 FastAPI 的降級/護欄邏輯，因為 llama-server 本身沒有 `guardrails.passes_guardrail()` 這層） | Elevation of Privilege / Information Disclosure | 部署腳本明確寫死 `--host 127.0.0.1`；PLAN.md 應包含一個驗證步驟：從開發機對裝置區網 IP + llama-server 埠號發 `curl`，預期連線被拒絕（而非從裝置本機 loopback 驗證，那樣測不出綁定範圍是否正確） |
| 交叉編譯出的 native binary 若來源不明或被竄改（supply-chain），在裝置上以無驗證 SSH 的 root 權限執行 | Tampering | 記錄實際編譯所用的 `git rev-parse HEAD`（llama.cpp commit hash）到部署文件；只從官方 `ggml-org/llama.cpp` repo clone，不使用來路不明的第三方 fork/預編譯 binary |

## Sources

### Primary (HIGH confidence)
- 直接讀取 `server/llm.py`、`server/config.py`、`server/pipeline.py`、`server/asr_base.py`、`CONTRACTS.md`、`tests/test_llm.py`、`tests/test_llm_n_ctx_profile.py`、`tests/conftest.py`（本次研究直接讀取，2026-07-25）
- `edge/BOARD_BRINGUP_DECISION.md`、`docs/DEPLOY_EDGE.md`、`edge/deploy/*.sh`、`edge/runtime/*.sh`（07-03 已驗證的真機部署現況，2026-07-25 直接讀取）
- `.planning/research/STACK.md`、`ARCHITECTURE.md`、`PITFALLS.md`、`SUMMARY.md`、`FEATURES.md`（專案層級 M2 研究，2026-07-18，已於本文件開頭聲明不重複研究）
- `.planning/phases/08-cpu-only-offline-edge-turn-loop/08-CONTEXT.md`（本 phase 使用者鎖定決策，2026-07-25）

### Secondary (MEDIUM confidence)
- [llama.cpp tools/server/README.md](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) — `/health`、`/v1/chat/completions`、`/completion` endpoint 語意
- [llama.cpp tools/llama-bench/README.md](https://github.com/ggml-org/llama.cpp/blob/master/tools/llama-bench/README.md) — `-t`/`-p`/`-n` 參數語意
- [python-sounddevice 官方安裝文件](https://python-sounddevice.readthedocs.io/en/0.5.1/installation.html) — Linux 平台 PortAudio 未隨 pip 打包的明確聲明
- [proc_pid_status(5) man page](https://www.man7.org/linux/man-pages/man5/proc_pid_status.5.html) — VmHWM/VmRSS 欄位語意
- [CMake Discourse — Cross compile for aarch64 on Ubuntu](https://discourse.cmake.org/t/cross-compile-for-aarch64-on-ubuntu/2161) — toolchain file 結構

### Tertiary (LOW confidence)
- RK3588（非 Genio 520）社群 llama.cpp 執行緒數回報（透過 WebSearch 綜合摘要取得，無單一可直接引用連結，僅方向性參考，Genio 520 專屬數字待實測）

## Metadata

**Confidence breakdown：**
- Standard stack（交叉編譯指令、HTTP client 改法）：MEDIUM — 指令結構有官方文件佐證，但未在真機/真交叉工具鏈上實際跑過一次完整編譯驗證
- Architecture（EdgeLLM 重構、n_ctx 搬遷）：HIGH — 直接對照既有 codebase 與既有測試檔案推導，架構影響是可驗證的邏輯推論，非臆測
- Pitfalls（glibc ABI、thread tuning、RSS 加總、sounddevice wheel）：MEDIUM — 官方文件與社群回報交叉參照，但 Genio 520 專屬數字全部有待真機驗證

**Research date：** 2026-07-25
**Valid until：** 2026-08-01（7 天——涉及真機實測結果與快速變動的 llama.cpp `master` 分支，數字類結論應視為短效）
