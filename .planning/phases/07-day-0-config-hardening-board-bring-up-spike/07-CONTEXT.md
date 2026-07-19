# Phase 7: Day-0 Config Hardening & Board Bring-Up Spike - Context

**Gathered:** 2026-07-19
**Status:** Ready for planning

<domain>
## Phase Boundary

結清 Day-0 零硬體風險的技術債，並立起邊緣工作的地基：

1. **Config 退債**：`LLM_N_CTX` 改 profile-driven（edge=512），移除 `server/llm.py:94` 硬編 1024。
2. **音訊退債**：`server/pipeline.py` 加 RIFF-sniff fast path，原生 WAV（ALSA 擷取）輸入不再呼叫 ffmpeg 子行程。
3. **邊緣骨架**：頂層 `edge/`（`edge/deploy`、`edge/models`、`edge/runtime`）+ 對稱 `docs/DEPLOY_EDGE.md`。
4. **部署管線**：adb build → push → run 迴圈在板卡上完整跑過一次。
5. **Board bring-up spike**：對 Hti G520 的 OS 路徑（官方 Yocto BSP vs fallback Android 14）做出**有日期的 go/no-go 決策**。

**Requirements:** EDGE-01, EDGE-02, EDGE-03, EDGE-04（見 `.planning/REQUIREMENTS.md` M2）

**不在本 phase**：完整離線聲音迴路（Phase 8）、斷網橋段（Phase 9）、NPU 加速（Phase 10）、前端 loopback 對話整合。

</domain>

<decisions>
## Implementation Decisions

### 裝置端 Python Runtime（EDGE-03）
- **D-01:** Android 14 上以 **proot-distro (Debian)** 跑 FastAPI + 引擎。理由：完整 glibc + apt，llama.cpp / sherpa-onnx / SenseVoice native build 可行；且最接近最終 Yocto Linux 環境，降低第二次移植成本。**不用 Termux 原生**（bionic libc 破壞 manylinux wheel、與 Yocto glibc 不一致），**不用 chroot**（需 root，12 天衝刺風險高）。
- **D-02:** `edge/runtime` 啟動腳本**先只針對 Android 14（proot）**，不預先抽象成 dual-host（Android/Yocto）。board spike 若 Yocto 過了，再補 native 啟動路徑（YAGNI）。
- **D-03:** 本 phase 的「adb 跑一次」驗證範圍 = **只驗 server 在裝置上起來 + 回 health check**。完整聲音迴路、前端 loopback 對話留 Phase 8。

### edge/ 骨架範圍（EDGE-04）
- **D-04:** `edge/models` 與既有頂層 `models/` **分離**：`models/` 保持不動（PC 原型 onnx 等）；`edge/models` 只放邊緣專屬量化產物（INT8 tflite / GGUF）。本 phase **只立空 dir + README** 說明未來放什麼；實際模型 Phase 8/10 才產出。
- **D-05:** `edge/runtime` **引用既有 `server/`、不複製 code**：放 launcher（如 `run_edge.sh`：proot 進 Debian → 起 uvicorn，`TALKYBUDDY_PIPELINE_PROFILE=edge`）+ README，指向既有 `server/`。避免雙份 server 維護發散。
- **D-06:** 各子目錄 scaffold 完成度 = **`edge/deploy` 與 `edge/runtime` 放最小可跑腳本；`edge/models` 只放 README placeholder**。理由：success criteria #4 要 adb 真跑一次，deploy（adb push+run）與 runtime（launcher）腳本必須可執行，不能只是空殼。同時建 `docs/DEPLOY_EDGE.md` 對稱 `docs/DEPLOY_CLOUD.md`。

### ffmpeg 移除策略與 WAV 直讀（EDGE-01）
- **D-07:** **保留 ffmpeg 當 fallback（雙路徑）**：RIFF-sniff 偵測到 `RIFF`/`WAVE` magic → soundfile 直讀（省 subprocess）；非 WAV（瀏覽器 WebM/Opus）→ 走既有 ffmpeg 路徑。PC 原型不壞；**edge 上根本不裝 ffmpeg**（ALSA 只產 WAV，永不觸發 fallback）。符合 `.planning/codebase/CONCERNS.md` interim 策略。
- **D-08:** WAV 直讀用 **soundfile**（`server/asr_sensevoice.py` 已在用，libsndfile 支援 WAV，無新依賴）。RIFF-sniff 只需讀前 12 bytes 判 magic，確認 WAV 就交給 soundfile 讀 float32。
- **D-09:** fast path 偵測到 WAV 但**取樣率/聲道不符（非 16k mono）**時：**PC 有 ffmpeg → 退 fallback；edge 無 ffmpeg → 拋明確錯誤（不靜默偽成功）**。理由：邊緣 ALSA 由我們控制擷取為 16k mono，fast path 只接已符合的；不自作 resample 以保持邏輯乾淨。

### Config 退債（EDGE-01，success criteria 已鎖定）
- **D-10:** `LLM_N_CTX` 改 profile-driven：edge 預設 512、cloud/PC 維持 1024，可經 env 覆寫。接線細節（沿用既有 `TALKYBUDDY_PIPELINE_PROFILE` at `server/config.py:134`）由 planner/executor 決定。

### Board Bring-Up Spike go/no-go（EDGE-02，未選討論 → 採 success criteria 預設）
- **D-11:** Yocto 燒錄 time-box **~2 天**（roadmap 暫定）；**成功定義 = 能開機 + adb 可連 + 跑得動我們的 stack**；未過則 **fallback Android 14** 並記錄新增成本（如 Java/NDK shim）。產出**一份有日期的 go/no-go 決策紀錄**。planner 若對此有更細的取捨可再細化。

### Claude's Discretion
- `LLM_N_CTX` / RIFF-sniff / launcher 的具體程式碼結構、命名、測試骨架。
- adb 部署腳本的具體 flag 與檔案佈局（在 D-04~D-06 的方向內）。
- board spike 的實際執行細節（誰按 adb / NB 分攤）由執行時視硬體現況決定。

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 技術債位置（EDGE-01）
- `.planning/codebase/CONCERNS.md` — ffmpeg `_webm_to_wav`、n_ctx=1024、espeak-ng-data GPL、全域單例等已定位技術債與 interim 策略。
- `server/llm.py:94` — 硬編 `n_ctx=1024`，本 phase 改 profile-driven。
- `server/pipeline.py`（`_webm_to_wav()`，約 57–108 行）— ffmpeg 子行程轉檔，本 phase 加 RIFF-sniff fast path。
- `server/asr_sensevoice.py:7` — 已用 soundfile 讀 16k mono float32（WAV 直讀沿用同函式庫）。
- `server/config.py:132-139` — 既有 `TALKYBUDDY_PIPELINE_PROFILE`（edge/cloud）切換機制，n_ctx profile 化掛在此。

### 部署骨架（EDGE-04）
- `docs/DEPLOY_CLOUD.md` — `docs/DEPLOY_EDGE.md` 的對稱範本。

### Milestone / 需求脈絡
- `.planning/REQUIREMENTS.md` — M2 v2 Requirements（EDGE-01~04 全文與 Out of Scope）。
- `.planning/ROADMAP.md` §Phase 7 — Goal 與 5 條 success criteria。

### 外部來源（repo 外，需使用者提供或執行者於現場取得）
- `~/hackathon/` — Hti G520 SDK、技術 SPEC v2、28 天 MVP 規劃書、決賽 demo 腳本。**board spike（Yocto 燒錄步驟、Genio Tools v1.7+）與 proot-distro provisioning 細節需參照 G520 SDK 文件**；若 planner/executor 需要具體燒錄指令，向使用者索取對應 SDK 路徑。

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `soundfile`（已於 `asr_sensevoice.py` 使用）：WAV 直讀 fast path 直接沿用，無新依賴。
- `TALKYBUDDY_PIPELINE_PROFILE`（`server/config.py:134`）：edge/cloud 切換已存在，n_ctx profile 化與 edge launcher 掛在此機制上。
- `docs/DEPLOY_CLOUD.md`：`DEPLOY_EDGE.md` 的結構範本。

### Established Patterns
- 降級鏈為設計上容錯層（CloudLLM→EdgeLLM→scaffold、ElevenLabs→Piper、SenseVoice→whisper）：ffmpeg fallback 雙路徑符合此「fast path + 保底」慣例。
- venv-based、無 Docker / pyproject：edge runtime 走 proot-distro Debian 內 pip/venv，與現有慣例一致。

### Integration Points
- `edge/runtime` launcher → 既有 `server/`（uvicorn，port 8787，edge profile）。
- `edge/deploy` adb 腳本 → push server + launcher 到裝置 proot rootfs → run。
- RIFF-sniff → `pipeline.py` 音訊入口，WAV 走 soundfile、其餘走 ffmpeg。

</code_context>

<specifics>
## Specific Ideas

- edge runtime 環境刻意選 **Debian glibc（proot-distro）而非 Termux bionic**，明確目的是「與最終 Yocto Linux 環境一致、降低二次移植成本」——planner 應把這個一致性當設計約束。
- edge 端**不安裝 ffmpeg**：ALSA 直接擷 16k mono WAV，fast path 永遠命中；ffmpeg 僅存在於 PC 原型的 WebM 路徑。
- 「不靜默偽成功」原則貫穿本 phase（呼應 NPU-02 精神）：WAV 規格不符時 edge 明確報錯而非硬轉。

</specifics>

<deferred>
## Deferred Ideas

- **前端 loopback 對話在裝置上跑通**（裝置本機瀏覽器 → localhost:8787）→ Phase 8/9。
- **Yocto native 啟動路徑抽象**（dual-host launcher）→ 待 board spike go 才做。
- **邊緣量化模型實際產出**（INT8 tflite / GGUF 放進 `edge/models`）→ Phase 8（CPU 引擎）/ Phase 10（NPU）。
- **espeak-ng-data GPL 殘留清除**、**app.py 全域單例重構**（CONCERNS.md 其他技術債）→ 非 Day-0 零風險項，未排入本 phase。

</deferred>

---

*Phase: 7-day-0-config-hardening-board-bring-up-spike*
*Context gathered: 2026-07-19*
