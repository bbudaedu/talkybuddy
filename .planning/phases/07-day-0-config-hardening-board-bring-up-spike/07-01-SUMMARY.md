---
phase: 07-day-0-config-hardening-board-bring-up-spike
plan: 01
subsystem: infra
tags: [llama-cpp, soundfile, ffmpeg, config, edge-profile, riff-sniff]

# Dependency graph
requires: []
provides:
  - "config.LLM_N_CTX：profile-driven llama.cpp context 視窗（edge=512 / cloud=1024），可 TALKYBUDDY_LLM_N_CTX 覆寫"
  - "server/pipeline.py RIFF-sniff fast path：原生 16kHz mono WAV bytes 走 soundfile 直讀，零 ffmpeg subprocess"
  - "WavSpecMismatchError：edge 端 WAV 規格不符（非 16k mono）明確 raise，不靜默偽成功"
affects: [08-cpu-only-offline-loop, edge-board-bring-up]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "env 覆寫數值常數慣例延伸：LLM_N_CTX 採雙層預設（profile 決定 base default，env 再覆寫 base default）"
    - "RIFF/WAVE magic sniff（前 12 bytes）決定 fast path vs. ffmpeg fallback，維持既有函式簽名與呼叫端不變"

key-files:
  created:
    - tests/test_llm_n_ctx_profile.py
    - tests/test_pipeline_wav_fastpath.py
    - .planning/phases/07-day-0-config-hardening-board-bring-up-spike/deferred-items.md
  modified:
    - server/config.py
    - server/llm.py
    - server/pipeline.py

key-decisions:
  - "沿用 _webm_to_wav() 函式名不改名，僅擴充 docstring 說明亦接受原生 WAV bytes（避免不必要改名波及 pipeline.py:159 呼叫點與其他測試對函式名的依賴）"
  - "新增 WavSpecMismatchError(ValueError) 作為 edge 規格不符明確例外類別，例外訊息不嵌入暫存檔路徑（呼應 threat_model T-07-03）"
  - "規格不符但非 edge 且有 ffmpeg 時，落回既有 ffmpeg subprocess 分支（不新增第二套轉檔邏輯，維持 D-09 語意）"

patterns-established:
  - "RIFF-sniff fast path：只讀前 12 bytes 判斷 magic，命中才交 soundfile 解析，避免對未受信 bytes 做不必要的完整解碼嘗試"

requirements-completed: [EDGE-01]

coverage:
  - id: D1
    description: "config.LLM_N_CTX 依 PIPELINE_PROFILE 決定預設值（edge=512、cloud/PC=1024），且 TALKYBUDDY_LLM_N_CTX 可強制覆寫（優先於 profile 預設）"
    requirement: "EDGE-01"
    verification:
      - kind: unit
        ref: "tests/test_llm_n_ctx_profile.py#test_default_profile_llm_n_ctx_is_512"
        status: pass
      - kind: unit
        ref: "tests/test_llm_n_ctx_profile.py#test_cloud_profile_llm_n_ctx_is_1024"
        status: pass
      - kind: unit
        ref: "tests/test_llm_n_ctx_profile.py#test_env_override_wins_over_edge_default"
        status: pass
      - kind: unit
        ref: "tests/test_llm_n_ctx_profile.py#test_env_override_wins_over_cloud_default"
        status: pass
    human_judgment: false
  - id: D2
    description: "server/llm.py::EdgeLLM._get_model() 以 config.LLM_N_CTX 建構 Llama 的 n_ctx 參數，程式碼不再硬編 1024"
    requirement: "EDGE-01"
    verification:
      - kind: unit
        ref: "tests/test_llm_n_ctx_profile.py#test_get_model_uses_config_llm_n_ctx"
        status: pass
    human_judgment: false
  - id: D3
    description: "pipeline 音訊入口對原生 16kHz mono WAV bytes 走 RIFF-sniff fast path，以 soundfile 直讀、完全不呼叫 ffmpeg 子行程"
    requirement: "EDGE-01"
    verification:
      - kind: unit
        ref: "tests/test_pipeline_wav_fastpath.py#test_wav_16k_mono_fast_path_skips_subprocess"
        status: pass
    human_judgment: false
  - id: D4
    description: "非 WAV bytes（瀏覽器 WebM/Opus）仍走既有 ffmpeg fallback 路徑，PC 原型行為不破壞"
    requirement: "EDGE-01"
    verification:
      - kind: unit
        ref: "tests/test_pipeline_wav_fastpath.py#test_non_wav_bytes_falls_back_to_ffmpeg_subprocess"
        status: pass
      - kind: unit
        ref: "tests/test_pipeline.py (23 tests, unchanged behavior)"
        status: pass
    human_judgment: false
  - id: D5
    description: "WAV bytes 但取樣率/聲道不符（非 16k mono）時：edge profile 下明確 raise 可辨識例外，不靜默偽成功、不自作 resample；例外訊息不含暫存檔路徑"
    requirement: "EDGE-01"
    verification:
      - kind: unit
        ref: "tests/test_pipeline_wav_fastpath.py#test_wav_spec_mismatch_raises_on_edge_profile"
        status: pass
      - kind: unit
        ref: "tests/test_pipeline_wav_fastpath.py#test_wav_spec_mismatch_message_has_no_tempfile_path"
        status: pass
    human_judgment: false

duration: 20min
completed: 2026-07-19
status: complete
---

# Phase 7 Plan 01: Config Hardening — Profile-Driven LLM_N_CTX + Pipeline RIFF-Sniff Fast Path Summary

**LLM context 視窗改 profile-driven（edge=512/cloud=1024，env 可覆寫）並移除 llm.py 硬編 1024；pipeline 音訊入口加 RIFF-sniff fast path，讓原生 16kHz mono WAV 直接以 soundfile 讀取、零 ffmpeg 子行程，非 WAV 與規格不符時行為分流明確。**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-07-19T20:40:00+08:00（估計，含 context 讀取）
- **Completed:** 2026-07-19T20:56:00+08:00
- **Tasks:** 2 (both TDD, RED→GREEN per task)
- **Files modified:** 3 (server/config.py, server/llm.py, server/pipeline.py)
- **Test files created:** 2 (tests/test_llm_n_ctx_profile.py, tests/test_pipeline_wav_fastpath.py)

## Accomplishments
- `config.LLM_N_CTX` 依 `PIPELINE_PROFILE` 決定預設值（edge=512、cloud/PC=1024），`TALKYBUDDY_LLM_N_CTX` 環境變數可強制覆寫（優先於 profile 預設）
- `server/llm.py::EdgeLLM._get_model()` 改用 `config.LLM_N_CTX` 建構 Llama context 視窗，移除硬編 `n_ctx=1024` 字面值
- `server/pipeline.py` 新增 `_is_wav_riff()` helper 與 RIFF-sniff fast path：原生 16kHz mono WAV bytes（未來 Genio 520 ALSA 擷取）直接 soundfile 讀取寫暫存 wav，全程零 `subprocess.run` 呼叫
- 非 WAV bytes（瀏覽器 WebM/Opus）不受影響，仍走既有 ffmpeg subprocess 分支
- WAV 但取樣率/聲道不符 16k mono 時：edge profile（或無 ffmpeg）明確 raise `WavSpecMismatchError`，訊息不含暫存檔完整路徑（呼應「不靜默偽成功」prohibition 與 T-07-03 mitigation）；非 edge 且有 ffmpeg 則落回既有 ffmpeg fallback

## Task Commits

Each task followed TDD RED → GREEN:

1. **Task 1: LLM_N_CTX profile-driven**
   - `b8c9ae0` test(07-01): add failing test for profile-driven LLM_N_CTX (RED)
   - `1f9d37d` feat(07-01): make LLM_N_CTX profile-driven, consumed by EdgeLLM (GREEN)
2. **Task 2: pipeline.py RIFF-sniff fast path**
   - `1034f97` test(07-01): add failing test for pipeline WAV RIFF-sniff fast path (RED)
   - `4de60ea` feat(07-01): add RIFF-sniff fast path for native 16k mono WAV audio (GREEN)

**Plan metadata:** (this commit, docs: complete 07-01 plan)

## Files Created/Modified
- `server/config.py` - 新增 `_LLM_N_CTX_DEFAULT`（模組私有）與 `LLM_N_CTX: int`，緊接 `PIPELINE_PROFILE` 定義之後
- `server/llm.py` - `EdgeLLM._get_model()` 的 `Llama(...)` 建構改用 `n_ctx=config.LLM_N_CTX`，更新註解移除「PLAN.md 要求」舊字樣
- `server/pipeline.py` - 新增 `_is_wav_riff()` helper、`WavSpecMismatchError` 例外類別、`_webm_to_wav()` 前置 RIFF-sniff fast path 分支；`import io`, `import shutil` 新增於頂部
- `tests/test_llm_n_ctx_profile.py` - 新增，5 個測試涵蓋 edge/cloud/env 覆寫/覆寫優先/`_get_model()` 消費 `config.LLM_N_CTX`
- `tests/test_pipeline_wav_fastpath.py` - 新增，4 個測試涵蓋 fast path 零 subprocess、非 WAV fallback、edge 規格不符 raise、例外訊息不洩漏路徑
- `.planning/phases/07-day-0-config-hardening-board-bring-up-spike/deferred-items.md` - 新增，記錄範圍外的既有測試失敗（見下方 Issues Encountered）

## Decisions Made
- `_webm_to_wav()` 保留原函式名不改名，只擴充 docstring 說明「亦接受原生 WAV bytes」；`pipeline.py:159` 呼叫點與 `tests/test_pipeline.py` 既有對函式名的 monkeypatch 依賴皆不受影響（YAGNI，避免不必要改名波及面）
- 新增 `WavSpecMismatchError(ValueError)` 作為 edge 規格不符時的明確例外類別，而非沿用泛用 `ValueError`/`RuntimeError`，方便呼叫端未來可辨識分流
- 規格不符但非 edge profile 且系統有 ffmpeg 時，直接落回既有 ffmpeg subprocess 分支處理（不新增第二套轉檔邏輯），維持與既有 fallback 行為一致
- 測試執行使用 `/home/budaedu/hackathon/talkybuddy/.venv`（此專案目錄 `/home/budaedu/talkybuddy` 本身無 `.venv`，但該既有 venv 的 `sys.executable` 對 cwd 無硬依賴，從本專案根目錄執行 `python -m pytest` 可正確解析到本地 `server/`、`tests/`，已驗證 293→317 tests 皆命中本地檔案而非 hackathon repo 副本）

## Deviations from Plan

None - plan executed exactly as written（含 TDD RED/GREEN 兩階段、acceptance_criteria 全數以 grep/pytest 驗證通過）。

## Issues Encountered

**Full test suite (`.venv/bin/python -m pytest -q`) 執行後有 11 failed + 6 errors，皆位於 `server/streaming/` 與 `spike/a2_pipecat/` 目錄（barge-in gate、turn manager、VAD、realwire synth、sherpa voice locate、interruptible synth）。**
- 已確認與本 plan 無關：此 plan 只改動 `server/config.py`、`server/llm.py`、`server/pipeline.py`；`server/streaming/tests/test_isolation.py`（唯一有 import `server.pipeline` 的 streaming 測試）通過。
- 對照 pipeline.py 於 plan 執行前的版本（commit `07d0e17`），本次改動為純新增（新 helper + 新分支），未觸及既有 ffmpeg fallback 邏輯本體。
- 依 SCOPE BOUNDARY 規則，未修復，已記錄於 [deferred-items.md](./deferred-items.md)，建議由擁有 `server/streaming/`（Path 1 自架串流 barge-in）的未來 phase/plan 處理。
- 本 plan 相關範圍（`tests/test_llm_n_ctx_profile.py`、`tests/test_pipeline_wav_fastpath.py`、`tests/test_pipeline.py`、`tests/test_llm.py`、`tests/test_pipeline_profile.py`）合計 35 個測試全數通過。

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- `config.LLM_N_CTX` 與 pipeline RIFF-sniff fast path 已就緒，為 Phase 8（CPU-only 離線迴路）與未來 Genio 520 board bring-up 提供穩定 config 地基；board bring-up 時只需設 `TALKYBUDDY_PIPELINE_PROFILE=edge`（已是預設值）即可自動取得 n_ctx=512 與零 ffmpeg 音訊路徑
- `server/streaming/` 既有測試失敗（見 Issues Encountered）建議在觸及該子系統的 phase 開始前先行 triage，避免與 Phase 7/8 工作混淆
- Phase 07 剩餘 plans（02、03，依 07-PLAN.md wave 規劃）尚未執行

---
*Phase: 07-day-0-config-hardening-board-bring-up-spike*
*Completed: 2026-07-19*

## Self-Check: PASSED

All created/modified files verified present on disk; all 4 task commit hashes (`b8c9ae0`, `1f9d37d`, `1034f97`, `4de60ea`) verified present in git log.
