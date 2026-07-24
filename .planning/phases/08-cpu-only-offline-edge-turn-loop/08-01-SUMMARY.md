---
phase: 08-cpu-only-offline-edge-turn-loop
plan: 01
subsystem: infra
tags: [llama-server, llama.cpp, config, argv-builder, edge-runtime, unit-testing]

# Dependency graph
requires: []
provides:
  - "server/config.py 新增 LLM_SERVER_HOST / LLM_SERVER_PORT / LLM_THREADS（TALKYBUDDY_* env 可覆寫）"
  - "edge/runtime/run_llama_server.py::build_llama_server_argv() 純函式（--ctx-size/--host/--port/--threads argv builder）"
  - "edge/runtime/run_llama_server.py::main() launcher 骨架（lazy import config、相對定位、os.execv）"
  - "tests/test_run_llama_server.py 單元測試（6 tests，涵蓋 argv 內容、host 預設值、純函式無副作用）"
affects: [08-02, 08-03, 08-04, 08-05]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "純函式 argv builder：CLI 啟動參數組裝抽成無 I/O、無副作用的可測函式，而非藏進 shell 字串"
    - "llama-server 為獨立 OS 行程：n_ctx 從 Llama(n_ctx=) 建構參數搬遷為 --ctx-size CLI flag"
    - "host 預設 loopback 127.0.0.1：LLM 端點不對外暴露的結構性第一道防線（T-08-01）"

key-files:
  created:
    - edge/runtime/run_llama_server.py
    - tests/test_run_llama_server.py
  modified:
    - server/config.py

key-decisions:
  - "host 參數預設寫死 127.0.0.1（loopback），不可對外可路由；由單元測試斷言守護（T-08-01）"
  - "未新增 edge/__init__.py 或 edge/runtime/__init__.py — 命名空間套件匯入（edge.runtime.run_llama_server）直接可行，pytest 與 python -c 兩者皆驗證成功，故不需額外套件標記檔"
  - "main() 之 binary 路徑經 TALKYBUDDY_LLAMA_SERVER_BIN env 覆寫，預設指向 push.sh 部署的 edge/deploy/bin/llama-server；config 一律 lazy import 以避免 import 本模組即需 server 套件齊全"

patterns-established:
  - "argv builder 純函式模式：可被未來 tests/test_llm_n_ctx_profile.py（08-02）等測試直接匯入斷言，取代對子行程/CLI 字串的間接驗證"

requirements-completed: [ELOOP-02]

coverage:
  - id: D1
    description: "server/config.py 新增 LLM_SERVER_HOST/PORT/THREADS，預設值正確且可經 TALKYBUDDY_* env 覆寫"
    requirement: "ELOOP-02"
    verification:
      - kind: unit
        ref: ".venv/bin/python -c \"import config; assert LLM_SERVER_HOST=='127.0.0.1' ...\" (Task 1 verify command)"
        status: pass
    human_judgment: false
  - id: D2
    description: "build_llama_server_argv() 純函式：--ctx-size 承接傳入 ctx_size、--host 預設 127.0.0.1、--model/--port/--threads 引數正確、無副作用"
    requirement: "ELOOP-02"
    verification:
      - kind: unit
        ref: "tests/test_run_llama_server.py (6 tests, all pass)"
        status: pass
    human_judgment: false
  - id: D3
    description: "既有 tests/test_llm_n_ctx_profile.py（LLM_N_CTX profile/env 行為）不回歸"
    verification:
      - kind: unit
        ref: "tests/test_llm_n_ctx_profile.py (6 tests, all pass)"
        status: pass
    human_judgment: false

# Metrics
duration: 12min
completed: 2026-07-25
status: complete
---

# Phase 8 Plan 01: llama-server argv builder + config Summary

**新增 config.py 三個 llama-server 連線設定（host/port/threads）與可單元測試的純函式 build_llama_server_argv()，把 --ctx-size/--host 等啟動參數組裝從潛在 shell 字串拼接改為受測 Python 函式，host 預設 127.0.0.1 作為 T-08-01 防線起點**

## Performance

- **Duration:** ~12 min
- **Completed:** 2026-07-24T22:51:03Z
- **Tasks:** 2
- **Files modified:** 3 (1 modified, 2 created)

## Accomplishments
- `server/config.py` 新增 `LLM_SERVER_HOST`（預設 "127.0.0.1"）、`LLM_SERVER_PORT`（預設 8080）、`LLM_THREADS`（預設 4），皆沿用既有 `os.environ.get(TALKYBUDDY_*, default)` idiom，可 env 覆寫
- 新增 `edge/runtime/run_llama_server.py`：純函式 `build_llama_server_argv(model_path, ctx_size, host="127.0.0.1", port=8080, threads=4, binary_path="llama-server") -> list[str]`，無 I/O、無副作用，`--host` 預設 loopback；`main()` launcher 骨架以 lazy import config、相對定位求根目錄、`os.execv` 啟動
- 新增 `tests/test_run_llama_server.py`：6 個單元測試，涵蓋 `--ctx-size` 值、`--host` 預設值/傳入值、`--model`/`--port`/`--threads` 引數、回傳型別、純函式無副作用（monkeypatch `subprocess.run`/`Popen`/`urllib.request.urlopen` 皆不得被呼叫）

## Task Commits

Each task was committed atomically:

1. **Task 1: config.py 新增 llama-server 連線設定（host/port/threads）** - `bfafcb7` (feat)
2. **Task 2: run_llama_server.py — build_llama_server_argv() 純函式 + main() launcher，附單元測試** - `4bec811` (feat)

**Plan metadata:** committed by orchestrator after wave completion (worktree convention — this executor does not write STATE.md/ROADMAP.md)

_Note: both tasks were `tdd="true"` in the plan; tests and implementation were authored together per task and verified green before commit (behavior/action/verify collapsed into a single commit per task per plan's TDD instructions — no separate RED-only commit was required since each task's `<action>` already specified writing the test alongside the implementation)._

## Files Created/Modified
- `server/config.py` - 新增 `LLM_SERVER_HOST`/`LLM_SERVER_PORT`/`LLM_THREADS` 三個模組層級設定
- `edge/runtime/run_llama_server.py` - 新檔：`build_llama_server_argv()` 純函式 + `main()` launcher
- `tests/test_run_llama_server.py` - 新檔：6 個單元測試

## Decisions Made
- host 參數預設寫死 `"127.0.0.1"`（不可為對外可路由位址），由測試 `test_host_defaults_to_loopback_when_omitted` 明確斷言守護，作為 T-08-01 結構性防線起點
- 未新增 `edge/__init__.py`／`edge/runtime/__init__.py`：`python -c "from edge.runtime.run_llama_server import build_llama_server_argv"` 與 `pytest tests/test_run_llama_server.py` 皆在命名空間套件（PEP 420）下直接匯入成功，故略過 plan 中「若無法解析則新增空 `__init__.py`」的條件式分支
- `main()` 的 llama-server binary 路徑經 `TALKYBUDDY_LLAMA_SERVER_BIN` env 覆寫，預設 `edge/deploy/bin/llama-server`（比照 push.sh 部署佈局）；config 一律 lazy import，維持「import 本模組不觸網、不啟動子行程」的純函式模組層級保證

## Deviations from Plan

None — plan executed exactly as written. Both tasks' `<action>`/`<behavior>`/`<verify>` were implemented as specified; the one conditional branch in the plan (adding `edge/__init__.py` / `edge/runtime/__init__.py` if namespace-package import failed) was evaluated and found unnecessary (import succeeded without it), which the plan itself anticipated as a possible outcome ("若 pytest 無法解析...則新增").

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required. (Real `llama-server` binary and model file are provisioned in later plans of this phase — 08-01 only builds the pure argv-assembly function and config values, not the actual binary invocation.)

## Next Phase Readiness
- `build_llama_server_argv()` is import-ready for 08-02's rewrite of `tests/test_llm_n_ctx_profile.py`'s `test_get_model_uses_config_llm_n_ctx` (per 08-PATTERNS.md), which will assert against this function instead of the old `Llama(n_ctx=...)` kwarg interception
- `config.LLM_SERVER_HOST/PORT/THREADS` are ready for 08-02's `EdgeLLM._call_llama_server` HTTP client and for `edge/runtime/run_edge.sh`'s health-check wait block (later plan in this phase)
- No blockers identified for downstream plans in this wave/phase

## Self-Check: PASSED

- FOUND: server/config.py
- FOUND: edge/runtime/run_llama_server.py
- FOUND: tests/test_run_llama_server.py
- FOUND commit: bfafcb7
- FOUND commit: 4bec811

---
*Phase: 08-cpu-only-offline-edge-turn-loop*
*Completed: 2026-07-25*
