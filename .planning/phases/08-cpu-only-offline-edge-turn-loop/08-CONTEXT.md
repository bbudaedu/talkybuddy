# Phase 8: CPU-Only Offline Edge Turn Loop - Context

**Gathered:** 2026-07-25 (auto mode — `--auto`, single pass per `[auto]` policy)
**Status:** Ready for planning

<domain>
## Phase Boundary

在 Genio 520 真機（Yocto，07-03 已確認 GO）上，全 CPU 引擎跑出一次完整**離線**「聽 ASR → 想 LLM → 說 TTS」中英雙語鷹架帶讀迴圈：

1. **LLM 引擎替換（ELOOP-02，已鎖定）**：`EdgeLLM`（`server/llm.py`）目前用 in-process `llama-cpp-python`（`from llama_cpp import Llama`），改成呼叫**交叉編譯的 llama.cpp native binary（`llama-server`）over localhost HTTP**，build flag 鎖定 `-march=armv8.2-a+dotprod+i8mm`（Cortex-A78；不可用 `armv8.7a`，會 SIGILL）。
2. **裝置端音訊 I/O**：新增 ALSA 擷取/播放層，讓真機不靠瀏覽器也能收音、放音，跑通完整迴圈。
3. **on-device 延遲實測 + go/no-go 門檻（ELOOP-03）**：真機 `llama-bench` + 首字/每回合延遲實測，訂出舞台可接受門檻。
4. **4GB 記憶體驗證閘（ELOOP-04）**：三引擎（ASR + LLM + TTS）同時載入峰值 < 4GB。

**Requirements:** ELOOP-01, ELOOP-02, ELOOP-03, ELOOP-04（`.planning/REQUIREMENTS.md` M2）

**不在本 phase**：NPU 加速（Phase 10）、斷網橋段話劇化（Phase 9）、前端瀏覽器 loopback 整合（可選 stretch，非驗收必要）。

</domain>

<decisions>
## Implementation Decisions

### LLM 服務化架構（ELOOP-02，已由 REQUIREMENTS.md 鎖定，非本輪討論）
- **D-01（鎖定）：** llama.cpp 改 native binary（`llama-server`，llama.cpp 內建的 OpenAI-compatible HTTP server）over `localhost`，取代 in-process `llama-cpp-python`。`server/llm.py::EdgeLLM` 改為 HTTP client（`requests`/`httpx` 呼叫 `http://127.0.0.1:<port>/completion` 或 `/v1/chat/completions`），保留現有 `available()`/`generate()` 契約與逾時、safety_check、降級語意不變。
- **D-02（鎖定）：** build flag 固定 `-march=armv8.2-a+dotprod+i8mm`（`.planning/research/STACK.md`、`PITFALLS.md` 已詳列理由：`armv8.7a` 隱含 A78 不支援的 ISA 特徵，會 runtime SIGILL）。
- **D-02 修正（2026-07-25，真機驗證後）：** 上述 `+i8mm` 假設本身也錯了——真機 `/proc/cpuinfo` 對全部 8 核心（6x Cortex-A55 `CPU part 0xd05`、2x Cortex-A78 `CPU part 0xd41`）皆只列出 `asimddp`（= dotprod），**沒有 `i8mm`**。含 `+i8mm` 編出的 binary 一進入推論就 SIGILL（kernel audit `sig=4`），不是先前假設的 glibc ABI 問題（D-03 fallback 對此無效）。修正為 `-march=armv8.2-a+dotprod`（移除 `+i8mm`），重編後真機 `/v1/chat/completions` 推論成功（見 `08-04-SUMMARY.md` 補記）。`edge/deploy/build.sh` 已同步更新並附上此發現的註解。

### 交叉編譯工具鏈取得方式（新缺口 — 07-03 實測發現裝置無 gcc/cmake）
- **D-03（auto 選定，可回頭調整）：**
  ```
  [auto] 交叉編譯工具鏈 — Q: "裝置（Yocto）確認無 gcc/cmake，llama.cpp native binary 要在哪裡編？"
  → 選定："開發機先試 apt 泛用 aarch64-linux-gnu cross-toolchain（如 gcc-aarch64-linux-gnu），
     交叉編譯後用既有 SSH/rsync 部署迴圈推上裝置；若跑起來 glibc ABI 不相容（版本落差造成
     動態連結失敗），才退而使用 ~/hackathon/ 的 Genio Yocto BSP SDK 官方 cross-toolchain。"
     （recommended default — 理由：apt 工具鏈免向使用者要額外 SDK 路徑，可立即動手；
     Yocto SDK 更保證 ABI 一致但需要使用者提供/確認路徑，時間成本較高，留作 fallback。）
  ```
  **執行時風險**：若 apt 工具鏈編出的 binary 在裝置上 `ldd`/執行失敗（glibc 版本不符），
  planner/executor 應立即改用 Yocto SDK 路徑，不要在 apt 路徑上反覆嘗試超過一次修正。

### 裝置端音訊 I/O（ELOOP-01 完整迴圈的擷取/播放介面）
- **D-04（auto 選定）：**
  ```
  [auto] 裝置端音訊介面 — Q: "ELOOP-01 的『真機完整聽→想→說迴圈』，收音/放音走哪條路徑？"
  → 選定："裝置端直接走 ALSA（Python 層 sounddevice 或呼叫 arecord/aplay 子行程），
     擷取 16k mono WAV 直接命中 07-01 已做的 RIFF-sniff fast path；不透過瀏覽器 WebSocket。
     對應既有 `edge/runtime` launcher 之外，新增一個獨立進程（如
     `edge/runtime/local_client.py`）串 ALSA 擷取 → `VoicePipeline.run_turn_*` → ALSA 播放。"
     （recommended default — 呼應 `.planning/research/ARCHITECTURE.md` 既有規劃的
     `edge/runtime/audio_io.py` + `local_client.py` 設計，且與『邊緣不裝 ffmpeg、只吃
     16k mono WAV』的既有決策一致，不需要新增依賴。）
  ```
  **Claude's Discretion**：ALSA 擷取用 `sounddevice`（pip 有 aarch64 wheel）或子行程呼叫
  `arecord`/`aplay`（Yocto image 通常內建 alsa-utils）由 executor 依裝置實測結果選擇——
  若 `sounddevice` 需要編譯或缺 PortAudio，優先退到 `arecord`/`aplay` 子行程呼叫，避免
  重蹈 07-03 遇到的「裝置無編譯器」問題。

### On-device 延遲 go/no-go 門檻（ELOOP-03）
- **D-05（auto 選定）：**
  ```
  [auto] 延遲驗收門檻 — Q: "『舞台可接受』的首字延遲/每回合延遲門檻，具體數字訂多少？"
  → 選定："首字延遲 < 800ms、單回合總延遲（收音結束→開始播放回覆）< 3–4 秒 為 go；
     超過則記為 no-go 並列出 fallback（縮短 prompt/scaffold、降 n_ctx、或該回合改用
     scaffold-only 回覆，不強行等待 LLM）。"
     （recommended default — 直接沿用 `.planning/research/PITFALLS.md`
     「Pitfall 5」與「Cross-Cutting Risk Register」已给出的具體數字建議，非臆造。）
  ```
  這個門檻是**實測後才能真正判定 go/no-go**（真機 `llama-bench` + 端到端計時），本 phase
  的 auto 選定只鎖定「用哪個數字當門檻」，不是預先宣稱通過。

### Claude's Discretion
- llama-server 啟動方式（`run_edge.sh` 內同步拉起、或獨立 systemd/nohup 常駐行程）之取捨，
  由 executor 依 07-03 已驗證的 SSH/rsync 部署慣例決定，避免破壞既有 health-check 路徑。
- 執行緒數（`-t`）具體值：`PITFALLS.md` 已指出 Genio 520 為 2×A78 + 6×A55，8 執行緒不必然
  最快；executor 需依 `llama-bench` 實測 1/2/4 執行緒數值挑選，不可預設 `nproc`。
- `sounddevice` vs `arecord`/`aplay` 子行程之取捨（見 D-04 附註）。

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Architecture / Stack 研究（本輪唯一來源，已於前次 session 確認足夠、不需重做）
- `.planning/research/ARCHITECTURE.md` — 邊緣 runtime 整體架構、`edge/runtime/audio_io.py` + `local_client.py` 設計、記憶體預算表（§Memory Budget）、Day-by-day 執行順序建議
- `.planning/research/STACK.md` — llama.cpp native 交叉編譯完整指令範例、`-march` flag 理由、native `llama-server` vs `llama-cpp-python` 取捨
- `.planning/research/PITFALLS.md` — Pitfall 3（4GB OOM）、Pitfall 5（`-march`/thread tuning 陷阱）、Cross-Cutting Risk Register（延遲門檻建議數字）
- `.planning/research/SUMMARY.md`、`.planning/research/FEATURES.md` — 補充脈絡

### Phase 7 交付物（本 phase 直接沿用的部署與硬體現況）
- `edge/BOARD_BRINGUP_DECISION.md` — Yocto 板卡真實環境（無 gcc/cmake、有 curl/wget/rsync、Python 3.12.11 原生）、SSH/rsync 部署迴圈已驗證可用
- `docs/DEPLOY_EDGE.md` — 現行 SSH/rsync 部署流程、環境變數、health-check 範圍
- `edge/runtime/run_edge.sh`、`edge/runtime/provision_device.sh` — 現有 launcher 與 venv provisioning，本 phase 需在此基礎上擴充（不是重寫）

### 既有技術債 / 契約
- `server/llm.py` — `EdgeLLM` 現況（in-process `llama_cpp.Llama`），本 phase 要改的目標檔案
- `server/config.py:132-140` — `PIPELINE_PROFILE`、`LLM_N_CTX`、`LLM_GGUF` 既有機制
- `server/asr_base.py`、`server/pipeline.py`（RIFF-sniff fast path，07-01 已做）— ASR/音訊既有契約，D-04 的 ALSA 路徑要對接於此
- `CONTRACTS.md` — 各引擎 `available()`/`generate()`/`transcribe()` 契約，改動 `EdgeLLM` 內部實作時不可破壞外部契約

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `server/pipeline.py` 的 RIFF-sniff fast path（07-01 交付）：ALSA 擷取的 16k mono WAV 可直接命中，不需要新的音訊轉檔邏輯。
- `server/config.py` 的 `PIPELINE_PROFILE`/`LLM_N_CTX`/env-覆寫慣例：新增 llama-server 相關設定（如 port、host）應沿用同款式。
- `edge/deploy/*.sh` + `edge/runtime/run_edge.sh`（07-03 已驗證的 SSH/rsync 部署與啟動）：交叉編譯出的 native binary 用同一條 rsync 管線推送，`run_edge.sh` 可擴充成同時拉起 `llama-server` + `uvicorn`。

### Established Patterns
- lazy-import + try/except 保護（`server/llm.py:_get_gguf_path`）：新的 llama-server HTTP client 呼叫應維持同款降級語意——連不上 server 就回 `None`，不拋例外炸掉 pipeline。
- 降級鏈設計（CloudLLM→EdgeLLM→scaffold）：`EdgeLLM` 改成 HTTP client 後，這條鏈的外部行為（`available()`/`generate()` 回傳型別與逾時語意）必須不變，否則會波及 `server/pipeline.py` 既有呼叫端與既有測試。

### Integration Points
- `server/llm.py::EdgeLLM` ↔ 交叉編譯的 `llama-server` binary（新增，localhost HTTP）。
- 新的 `edge/runtime/local_client.py`（或同等命名）↔ ALSA 音訊層 ↔ 既有 `VoicePipeline`（`server/pipeline.py`）。
- `edge/deploy/push.sh` 需擴充推送交叉編譯出的 native binary（目前只推 `server/`、`edge/runtime`、`web/`）。

</code_context>

<specifics>
## Specific Ideas

- 決賽現場的核心價值判準（`.planning/PROJECT.md`）：「若淪為音箱則全案失敗」——延遲門檻與 fallback 策略（D-05）必須確保即使 LLM 生成慢，現場也不會出現多秒靜默 hang，這比單純追求最低延遲數字更重要。
- 07-03 board bring-up 已證實「不靜默偽成功」原則要延續到本 phase：延遲/記憶體實測必須是真機數字，不可用 PC 開發機數字替代（`llama-bench` 需在 Genio 520 上實際跑）。

</specifics>

<deferred>
## Deferred Ideas

- **NPU 加速路徑（ASR/TTS 經 Neuron Delegate）**：Phase 10，本 phase 全程 CPU-only，不預先摻雜 NPU 程式碼路徑（YAGNI）。
- **前端瀏覽器 loopback 對話整合**（裝置本機瀏覽器 → localhost:8787）：非本 phase 驗收必要（ELOOP-01 只要求「完整聽→想→說迴圈」跑通，未強制經瀏覽器 UI），可留給 Phase 9 或 stretch goal。
- **斷網橋段話劇化彩排**：Phase 9，本 phase 只需確認「全程零雲端網路呼叫」，不做主持人手動 kill-switch UI。

</deferred>

---

*Phase: 8-cpu-only-offline-edge-turn-loop*
*Context gathered: 2026-07-25*
