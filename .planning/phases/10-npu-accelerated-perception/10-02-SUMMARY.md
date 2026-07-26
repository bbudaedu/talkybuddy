---
phase: 10-npu-accelerated-perception
plan: 02
subsystem: npu-evidence-layer
tags: [onnxruntime, npu, ort-verbose-log, fd-capture, tdd]
dependency-graph:
  requires: []
  provides:
    - server/npu_placement.py (parse_ep_placement_log, summarize_placement, format_placement_line, capture_fd_output, PLACEMENT_MARKER, MAX_CAPTURE_BYTES)
  affects:
    - "10-03（NPU spike 腳本，將直接呼叫本模組驗證 NPU 佔比）"
    - "10-05（正式 NPU ASR 引擎，會用 capture_fd_output 包住 session 建立）"
    - "10-06（/api/status 的 npu 欄位，會用 summarize_placement/format_placement_line）"
tech-stack:
  added: []
  patterns:
    - "純函式 + 缺值安全預設（比照 edge/runtime/dump_recent_turns.py 慣例），try/except 回部分結果而非拋例外"
    - "fd 級輸出攔截（os.dup2）取代 Python 層 contextlib.redirect_stderr，用於攔截 C++ 層直寫 fd 的輸出"
key-files:
  created:
    - server/npu_placement.py
    - tests/test_npu_placement.py
    - .planning/phases/10-npu-accelerated-perception/deferred-items.md
  modified: []
decisions:
  - "accelerated 欄位僅由 accel_provider（預設 NeuronExecutionProvider）節點數 > 0 決定，與 session 是否建立成功完全解耦，防止 Pitfall 1 靜默偽成功"
  - "parse_ep_placement_log 對格式漂移採容錯（略過殘缺行，不拋例外），因 VerifyEachNodeIsAssignedToAnEp 日誌格式非官方 API 保證（10-RESEARCH.md A4）"
  - "MAX_CAPTURE_BYTES=2_000_000 作為 T-10-04 DoS 緩解：擷取緩衝寫暫存檔而非常駐記憶體字串，超限截斷並設 truncated 旗標"
metrics:
  duration: "15min"
  completed: "2026-07-26"
status: complete
---

# Phase 10 Plan 02: NPU 加速證據層（EP placement 解析 + fd 級日誌擷取）Summary

建立本 phase 唯一的證據層：把 ONNX Runtime 的 `VerifyEachNodeIsAssignedToAnEp` verbose 節點放置日誌，解析成 `{provider: [node_names]}` 結構化資料，再摘要成「X/Y ops accelerated」的固定格式結論；並用 fd 級 `os.dup2` 攔截 ORT C++ 層直寫 fd 2 的輸出，因為 Python 的 `contextlib.redirect_stderr` 攔不到這類寫入。全程不 import onnxruntime，可在無硬體、無 onnxruntime 的機器上以 pytest 完整驗證。

## What Was Built

- **`server/npu_placement.py`**（新檔）
  - `PLACEMENT_MARKER = "VerifyEachNodeIsAssignedToAnEp"` — ORT verbose 日誌的識別字串。
  - `parse_ep_placement_log(log_text) -> dict[str, list[str]]` — 逐行掃描 `Provider: [<name>]: [...]` 樣式的行，支援跨行折斷（累積後續行直到方括號閉合或遇到 marker/空白行/無逗號括號的行為止）；完全不含 marker 回空 dict；結構殘缺行直接略過；外層 try/except 回部分結果而非拋例外。
  - `summarize_placement(placement, accel_provider="NeuronExecutionProvider") -> dict` — 回 `{ops_accelerated, ops_total, providers, accelerated}`；`accelerated` 僅由 `accel_provider` 節點數 > 0 決定，不看 session 是否建立成功；空輸入不除以零。
  - `format_placement_line(summary) -> str` — 固定樣板 `"NPU: ON, X/Y ops accelerated"` / `"NPU: OFF, X/Y ops accelerated"`。
  - `MAX_CAPTURE_BYTES = 2_000_000` — fd 擷取緩衝上限（T-10-04 DoS 緩解）。
  - `CapturedOutput`（dataclass：`text`、`truncated`）與 `capture_fd_output(fd=2)`（`@contextlib.contextmanager`）— 用 `os.dup(fd)` 保存原 fd、`os.dup2` 導向 `tempfile.TemporaryFile()`、`finally` 內先還原 fd 再讀出暫存檔內容（超限截斷、`errors="replace"` 解碼）。

- **`tests/test_npu_placement.py`**（新檔，14 個測試）
  - Task 1（8 個）：正常路徑、跨行折斷、無 marker、格式漂移、`summarize_placement` 正常/空輸入/純 CPU、`format_placement_line` ON/OFF。
  - Task 2（6 個）：fd 攔截真實寫入、離開後還原且不再擷取、例外時 `finally` 仍還原、超限截斷、未超限不截斷、與 `parse_ep_placement_log` 串接的整合測試。

## Task Sequence (TDD)

| 順序 | Commit | 內容 |
|------|--------|------|
| 1 | `45ce007` test | Task 1 八個測試（RED：`ModuleNotFoundError`） |
| 2 | `7efeda0` feat | 實作三個純函式（GREEN：8/8） |
| 3 | `261407c` test | Task 2 六個測試（RED：`ImportError: cannot import name 'MAX_CAPTURE_BYTES'`） |
| 4 | `44279bb` feat | 實作 `capture_fd_output`（GREEN：14/14） |
| 5 | `12e7e5e` docs | 記錄與本 plan 無關的既有測試失敗（scope boundary） |

## Verification

- `pytest tests/test_npu_placement.py -x` → **14 passed**（超過驗收要求的 ≥12）。
- `python3 -c "import server.npu_placement"` 在未安裝 `onnxruntime` 的環境成功（已確認環境中 `import onnxruntime` 本身會 `ModuleNotFoundError`，而本模組 import 正常）。
- `git log` 顯示 test-first（紅）→ 實作（綠）序列，Task 1、Task 2 各一組，符合 TDD 閘門。
- 全套 `pytest`（排除既有環境缺口）：341 passed, 2 skipped；本 plan 新增的 14 個測試全數在其中通過，未對既有測試造成回歸或 stderr 污染。

## Deviations from Plan

None — plan 按原計畫執行，兩個 task 皆走 RED→GREEN 兩階段 commit，未觸發 REFACTOR commit（程式碼在 GREEN 階段即已符合預期整潔度，無需額外整理）。

### Out-of-Scope Discoveries (documented, not fixed)

執行全套 `pytest` 時發現以下既有失敗，與本 plan 改動檔案（`server/npu_placement.py`、`tests/test_npu_placement.py`）無關，記錄於 `.planning/phases/10-npu-accelerated-perception/deferred-items.md`，依 Scope Boundary 規則不修正：

- `tests/test_audio_io.py`、`tests/test_pipeline_wav_fastpath.py` — collection 階段 `ModuleNotFoundError: No module named 'soundfile'`（環境缺套件）。
- `tests/test_asr_backend.py` 兩個 OpenCC s2twp 相關測試失敗（疑環境缺轉換資料）。
- `tests/test_nova_sonic.py` 多個測試（疑 `pytest-asyncio` 外掛未安裝，`@pytest.mark.asyncio` 未生效）。
- `spike/a2_pipecat/tests/test_interruptible_synth.py` collection error（spike 目錄依賴缺失）。

## Known Stubs

None — 本 plan 交付的四個函式與一個 context manager 皆為可直接呼叫、無占位邏輯的完整純函式實作；無 UI/資料源串接（本 plan 明確不建立任何 ONNX session、不接觸 `/api/status`，那是 10-05/10-06 的範圍）。

## Threat Flags

無新增威脅面。本 plan 產生的解析邏輯與 fd 擷取皆已在 plan frontmatter 的 `<threat_model>` 中列為 T-10-04（DoS，已緩解：`MAX_CAPTURE_BYTES` + 暫存檔 + `finally` 還原）與 T-10-05（Repudiation，已緩解：`accelerated` 與 session 建立解耦 + 八個單元測試鎖住行為）；T-10-06（Tampering，日誌內容不含個資，accept）維持原判。三者皆在本次實作中依原計畫落實，無新發現的未列管介面。

## Self-Check: PASSED

- `server/npu_placement.py` — FOUND
- `tests/test_npu_placement.py` — FOUND
- `.planning/phases/10-npu-accelerated-perception/deferred-items.md` — FOUND
- commit `45ce007` — FOUND（`git log --oneline --all | grep 45ce007`）
- commit `7efeda0` — FOUND
- commit `261407c` — FOUND
- commit `44279bb` — FOUND
- commit `12e7e5e` — FOUND
