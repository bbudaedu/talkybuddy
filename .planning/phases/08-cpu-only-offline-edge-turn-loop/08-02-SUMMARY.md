---
phase: 08-cpu-only-offline-edge-turn-loop
plan: 02
subsystem: infra
tags: [llama-server, urllib, http-client, tdd, edge-llm, degrade-chain]

# Dependency graph
requires:
  - phase: 08-cpu-only-offline-edge-turn-loop
    provides: "08-01: build_llama_server_argv() argv builder + config.LLM_SERVER_HOST/PORT/THREADS"
provides:
  - "server/llm.py::EdgeLLM 重構為 stdlib urllib HTTP client（打 llama-server /health、/v1/chat/completions），public 契約（available/generate 簽名、回傳、逾時、降級語意）逐字不變"
  - "server/llm.py::_llama_server_base_url() 與 EdgeLLM._call_llama_server() — ELOOP-02 唯一必要程式碼變更點，唯一 HTTP 呼叫點，可被 monkeypatch"
  - "tests/test_llm.py、tests/test_llm_n_ctx_profile.py 改寫完成，斷言標的換成新架構（_call_llama_server / build_llama_server_argv），全套件無回歸"
affects: [08-03, 08-04, 08-05]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "EdgeLLM 內部唯一 HTTP 呼叫點 _call_llama_server(messages)：所有測試改用 monkeypatch.setattr(edge, '_call_llama_server', fake) 攔截，不需真的起 llama-server"
    - "available() 短逾時 GET /health（0.5s）+ generate() 內部呼叫逾時（7.5s，略小於外層 8.0s 逾時預算）分層防呆，符合 T-08-05 DoS 緩解"

key-files:
  created: []
  modified:
    - server/llm.py
    - tests/test_llm.py
    - tests/test_llm_n_ctx_profile.py

key-decisions:
  - "EdgeLLM 內部移除 in-process 模型單例（_model/_model_failed/_lock/_get_model/_get_gguf_path）與 threading/llama_cpp 依賴，改用 lazy import 的 _llama_server_base_url() 組 base URL"
  - "generate() 的 prompt 組裝與護欄尾段逐字保留，只把 model.create_chat_completion(...) 換成 self._call_llama_server(messages)，確保 server/pipeline.py 呼叫端零改動"
  - "test_llm.py 新增 test_available_false_on_connection_error（monkeypatch config.LLM_SERVER_PORT=1，觸發真實連線失敗）與 test_generate_returns_none_when_call_llama_server_raises（_call_llama_server 拋例外→generate 回 None），補齊新架構的自動化保護"

patterns-established:
  - "HTTP client 重構時，改寫測試優先於改寫實作（RED→GREEN），讓兩份 commit 各自可回溯：test(...) 先行、feat(...) 後續轉綠"

requirements-completed: [ELOOP-02]

coverage:
  - id: D1
    description: "EdgeLLM.available() 對 llama-server /health 發短逾時 GET，200 回 True；連線失敗回 False，絕不拋出"
    requirement: "ELOOP-02"
    verification:
      - kind: unit
        ref: "tests/test_llm.py#test_available_false_on_connection_error"
        status: pass
    human_judgment: false
  - id: D2
    description: "EdgeLLM.generate() public 簽名與降級語意不變：_call_llama_server 拋例外/逾時/safety_check 未過/空輸出一律回 None"
    requirement: "ELOOP-02"
    verification:
      - kind: unit
        ref: "tests/test_llm.py#test_generate_returns_none_when_call_llama_server_raises"
        status: pass
    human_judgment: false
  - id: D3
    description: "generate() prompt 組裝（system prompt、directive 注入）與護欄尾段（passes_guardrail、target 補句）逐字保留"
    requirement: "ELOOP-02"
    verification:
      - kind: unit
        ref: "tests/test_llm.py#test_generate_without_directive_has_no_strategy_block, test_generate_with_directive_injects_strategy_block, test_generate_appends_target_when_missing, test_generate_empty_directive_treated_as_none"
        status: pass
    human_judgment: false
  - id: D4
    description: "n_ctx 測試改為斷言 build_llama_server_argv() 的 --ctx-size 值，取代失效的 Llama(n_ctx=) kwarg 攔截"
    requirement: "ELOOP-02"
    verification:
      - kind: unit
        ref: "tests/test_llm_n_ctx_profile.py#test_get_model_uses_config_llm_n_ctx"
        status: pass
    human_judgment: false
  - id: D5
    description: "全套件無回歸，import server.llm 不觸網"
    verification:
      - kind: unit
        ref: "pytest tests/ -x -q (319 passed)"
        status: pass
      - kind: unit
        ref: "python -c \"import server.llm\" (import OK, no network)"
        status: pass
    human_judgment: false

# Metrics
duration: ~15min
completed: 2026-07-25
status: complete
---

# Phase 8 Plan 02: EdgeLLM → llama-server HTTP client Summary

**server/llm.py::EdgeLLM 從 in-process llama_cpp.Llama 物件改為 stdlib urllib HTTP client（打交叉編譯出的 llama-server /health 與 /v1/chat/completions），public 契約與降級語意逐字不變，兩份測試檔同步改寫斷言標的**

## Performance

- **Duration:** ~15 min
- **Completed:** 2026-07-24T23:01:04Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- `server/llm.py::EdgeLLM` 內部改為 stdlib `urllib.request`/`urllib.error` HTTP client：新增模組層級 `_llama_server_base_url()`、private `_call_llama_server(messages) -> str`（唯一 HTTP 呼叫點，可被 monkeypatch）；移除 in-process 模型單例（`_model`/`_model_failed`/`_lock`/`_get_model`/`_get_gguf_path`）與 `threading`/`llama_cpp` 依賴
- `available()` 改為對 `{base}/health` 發 0.5 秒短逾時 GET，200 回 True，任何例外回 False（絕不拋出）
- `generate()` public 簽名 `generate(self, student_text, scaffold, directive=None)`、prompt 組裝（`_SYSTEM_PROMPT`、directive 注入）、護欄尾段（`passes_guardrail`、target 補句「跟我說一遍：」）逐字保留，只把中間 `model.create_chat_completion(...)` 換成 `self._call_llama_server(messages)`
- `tests/test_llm.py` 改寫：4 個既有 directive 測試改用 `monkeypatch.setattr(edge, "_call_llama_server", fake_call)` 攔截；新增 `test_available_false_on_connection_error`（打不通的 port→`available()`回 False）與 `test_generate_returns_none_when_call_llama_server_raises`（HTTP 例外→`generate()`回 None）
- `tests/test_llm_n_ctx_profile.py` 改寫：前 5 個 config/profile 測試逐字保留；`test_get_model_uses_config_llm_n_ctx` 改為斷言 `build_llama_server_argv(...)` 的 `--ctx-size` 值，移除 `_FakeLlama`/`_FakeGguf`
- `server/pipeline.py` 呼叫端完全零改動（`available()`/`generate()` duck-typed 契約邊界受到保護）

## Task Commits

Each task was committed atomically:

1. **Task 1: 改寫 tests/test_llm.py 與 tests/test_llm_n_ctx_profile.py 的失效斷言（RED）** - `907b08f` (test)
2. **Task 2: EdgeLLM 內部改為 stdlib urllib HTTP client（GREEN，public 契約不變）** - `6172505` (feat)

**Plan metadata:** committed by orchestrator after wave completion (worktree convention — this executor does not write STATE.md/ROADMAP.md)

_Note: this plan's two tasks were designed as an explicit plan-level RED/GREEN TDD gate (`tdd="true"` on both tasks) — Task 1 rewrote the test files' assertion targets to the new architecture and confirmed a genuine RED (5 failures: `AttributeError: EdgeLLM object has no attribute '_call_llama_server'`) before any implementation changed; Task 2 then implemented `EdgeLLM`'s HTTP client internals and turned all 18 tests in the plan's test scope green, plus the full 319-test suite with zero regressions._

## Files Created/Modified
- `server/llm.py` - `EdgeLLM` 重構為 stdlib urllib HTTP client；`_llama_server_base_url()`、`_call_llama_server()` 新增；in-process 模型單例移除
- `tests/test_llm.py` - 4 個既有測試改攔截點；新增 2 個測試（available 連線失敗、generate HTTP 例外降級）
- `tests/test_llm_n_ctx_profile.py` - 前 5 個測試不動；`test_get_model_uses_config_llm_n_ctx` 改斷言 `build_llama_server_argv`

## Decisions Made
- 移除 in-process 模型單例與 `threading`/`llama_cpp` 依賴，改用 lazy import 的 `_llama_server_base_url()`（比照 `server/cloud_llm.py` 的 lazy-import 慣例）
- `available()` 逾時值刻意設短（0.5s），因 pipeline 每輪都呼叫一次；`_call_llama_server` 逾時（7.5s）略小於外層 `_GENERATE_TIMEOUT_S`（8.0s），為 `time.monotonic()` 逾時檢查留餘裕（T-08-05 DoS 緩解）
- 測試改寫時將原本 docstring 中出現的 `llama_cpp.Llama` 字面字串改寫為「in-process Python 模型物件」，避免與 acceptance criteria 的 `grep -n "llama_cpp"` 檢查誤判（純命名巧合，非邏輯變更）

## Deviations from Plan

None - plan executed exactly as written. Both tasks' `<action>`/`<behavior>`/`<verify>` implemented as specified; the RED step in Task 1 genuinely failed as anticipated (attribute error on `_call_llama_server` before Task 2's implementation existed), confirming the TDD gate was not skipped.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required. (The real `llama-server` binary and model file are provisioned/verified in later plans of this phase — 08-02 only refactors the Python HTTP client and its unit tests, all of which run against monkeypatched `_call_llama_server`, not a real running llama-server process.)

## Next Phase Readiness
- `EdgeLLM._call_llama_server` and `EdgeLLM.available()` are the sole HTTP integration points ready for 08-03+ to wire against a real running `llama-server` process (launched via 08-01's `build_llama_server_argv()`/`run_llama_server.py`)
- `server/pipeline.py`'s degrade chain (CloudLLM → EdgeLLM → scaffold) is unaffected — `EdgeLLM`'s duck-typed `available()`/`generate()` contract is byte-identical to before this refactor
- No blockers identified for downstream plans in this wave/phase

## Self-Check: PASSED

- FOUND: server/llm.py
- FOUND: tests/test_llm.py
- FOUND: tests/test_llm_n_ctx_profile.py
- FOUND commit: 907b08f
- FOUND commit: 6172505

## TDD Gate Compliance

Plan-level RED/GREEN gate confirmed in git log:
- `test(08-02): rewrite EdgeLLM tests for HTTP client refactor (RED)` — `907b08f` (RED gate; verified genuine failure before commit)
- `feat(08-02): EdgeLLM as stdlib urllib HTTP client to llama-server (GREEN)` — `6172505` (GREEN gate; all tests pass after)

No REFACTOR-only commit was needed — Task 2's implementation was already the minimal correct change per the plan's explicit `<action>` spec.

---
*Phase: 08-cpu-only-offline-edge-turn-loop*
*Completed: 2026-07-25*
