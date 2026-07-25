---
phase: 09-network-cut-demo-hardening
plan: 01
subsystem: api
tags: [fastapi, websocket, jwt, auth, asyncio, trust-boundary]

# Dependency graph
requires:
  - phase: 08-cpu-only-offline-edge-turn-loop
    provides: edge LLM/TTS runtime (llama-server + ALSA loop) that the kill-switch now correctly routes to mid-session
provides:
  - "conn_pipe.network_mode re-synced from the global pipeline.network_mode before every /ws/talk turn (both process_audio_buffer and text_input dispatch sites)"
  - "POST /api/network_mode now requires a valid JWT (identity_from_header gate, checked before the 400 mode-validation)"
  - "Live-WS regression test guarding against Pitfall 1 (stale per-connection network_mode) regressing"
affects: [09-02, 09-03, 09-04]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Re-sync per-connection state from a global authority immediately before each turn, inside the existing try/except-swallow block, rather than only at connect time"
    - "identity_from_header(authorization) as a reusable auth-gate call at the top of a handler, before business-logic validation"

key-files:
  created: []
  modified:
    - server/app.py
    - tests/test_e2e.py
    - tests/test_app_profile.py
    - tests/test_app_directive.py
    - tests/test_consent_gate.py

key-decisions:
  - "Left server/app.py:369's connect-time conn_pipe.network_mode copy unchanged (correct as an initial value); added re-sync as new reads immediately before both turn-dispatch calls, never a second writer of pipeline.network_mode"
  - "identity_from_header() call placed before the mode-validation 400 check, so an unauthorized request never reaches any business logic or mutates state"
  - "No role restriction on /api/network_mode per D-04 (student/tutor/device all pass) — only 'is authenticated' matters, not who"

patterns-established:
  - "Re-sync-before-turn pattern for any long-lived WS state that must reflect a value mutated by a separate short-lived HTTP request"

requirements-completed: [NETCUT-01]

coverage:
  - id: D1
    description: "Flipping /api/network_mode mid-session (without closing/reopening the WS) changes the very next turn's engine routing on an already-open /ws/talk connection"
    requirement: NETCUT-01
    verification:
      - kind: integration
        ref: "tests/test_e2e.py#test_network_mode_switch_affects_live_ws_session"
        status: pass
    human_judgment: false
  - id: D2
    description: "The second (post-switch) turn's persisted interaction row has network_mode == 'edge'"
    requirement: NETCUT-01
    verification:
      - kind: integration
        ref: "tests/test_e2e.py#test_network_mode_switch_affects_live_ws_session"
        status: pass
    human_judgment: false
  - id: D3
    description: "POST /api/network_mode rejects requests without a valid JWT (401) and does not mutate pipeline.network_mode when rejected"
    verification:
      - kind: unit
        ref: "tests/test_e2e.py#test_post_network_mode_requires_token"
        status: pass
      - kind: unit
        ref: "tests/test_e2e.py#test_post_network_mode_invalid_token_returns_401"
        status: pass
    human_judgment: false

# Metrics
duration: 15min
completed: 2026-07-25
status: complete
---

# Phase 9 Plan 1: Live-session kill-switch fix + JWT trust boundary Summary

**Fixed the NETCUT-01 blocking bug where flipping the airplane-mode switch mid-conversation had zero effect on an already-open `/ws/talk` session (only a page reload picked it up), and closed the kill-switch's unauthenticated trust boundary by requiring a valid JWT on `POST /api/network_mode`.**

## Performance

- **Duration:** ~15 min
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- `server/app.py`: `conn_pipe.network_mode = pipeline.network_mode` now re-runs as the first statement inside the existing `try:` block at both turn-dispatch call sites (`process_audio_buffer()` and the `text_input` branch), so a mid-session mode switch actually changes the next turn — not just new connections. The connect-time initial-value copy at line 369 is untouched.
- `server/app.py`: `POST /api/network_mode` now calls the existing `identity_from_header(authorization)` helper (same convention as `api_interactions`/`api_diagnoses`) before any mode validation — missing/invalid/expired token → 401, and the request never touches `pipeline.network_mode`. No role restriction (D-04).
- New live-WS integration test `tests/test_e2e.py::test_network_mode_switch_affects_live_ws_session`: opens one `/ws/talk` connection, runs a cloud turn, switches to edge mid-session without closing the WS, runs a second turn on the same connection, and asserts the cloud engine was not called again while the edge engine was, and the persisted interaction row shows `network_mode == "edge"`.
- Two new auth-gate tests (`test_post_network_mode_requires_token`, `test_post_network_mode_invalid_token_returns_401`) plus 10 existing `/api/network_mode` call sites across `tests/test_e2e.py`, `tests/test_app_profile.py`, `tests/test_app_directive.py`, `tests/test_consent_gate.py` updated to send a valid bearer token.

## Task Commits

Each task followed the RED → GREEN TDD cycle:

1. **Task 1: live-WS network_mode re-sync fix + regression test**
   - `bd8c62d` (test, RED) — added `test_network_mode_switch_affects_live_ws_session`; confirmed failing on unfixed code (cloud stub called twice)
   - `c78943c` (fix, GREEN) — added the two one-line re-syncs in `server/app.py`; test passes, full `test_e2e.py` suite green (11 tests)
2. **Task 2: JWT gate on `/api/network_mode` + existing call-site tokens**
   - `ab65fc7` (test, RED) — added two new 401 tests + tokens on 10 existing call sites; confirmed `test_post_network_mode_requires_token` failing (200 instead of 401) on unfixed code
   - `ad61b5b` (feat, GREEN) — added `identity_from_header(authorization)` gate to `api_network_mode`; all five affected test files green (25 tests)

**Plan metadata:** (this commit, docs: complete plan)

## Files Created/Modified
- `server/app.py` — two-site `conn_pipe.network_mode` re-sync (NETCUT-01 core fix) + `Header`-based JWT gate on `api_network_mode`
- `tests/test_e2e.py` — new live-WS regression test, two new 401 tests, tokens added to 4 existing `/api/network_mode` calls
- `tests/test_app_profile.py` — module-level `_AUTH` constant, tokens added to 2 calls
- `tests/test_app_directive.py` — module-level `_AUTH` constant, tokens added to 2 calls
- `tests/test_consent_gate.py` — module-level `_AUTH` constant, tokens added to 2 calls

## Decisions Made
- Re-sync inserted *inside* the existing `try:` blocks (not before) so a broken re-sync degrades through the same `except Exception: emit idle` path as any other turn failure — the "single turn failure never kills the WS loop" contract is preserved.
- `identity_from_header()` call placed before the `mode not in ("edge","cloud")` 400 check, so unauthorized requests are rejected before any business logic runs (per acceptance criteria).
- No new writer of `conn_pipe.network_mode` was introduced; the global `pipeline.network_mode` (written only at `server/app.py:229`) remains the single source of truth, with two additional *readers*.

## Deviations from Plan

None — plan executed exactly as written. Both tasks' `<action>` steps, `<verify>` commands, and `<acceptance_criteria>` were followed literally; the plan's own fallback instruction ("if starlette TestClient can't POST while a WS session is open, fall back to setting `app_module.pipeline.network_mode` directly") was not needed — the portal-based `TestClient` handled the concurrent POST-during-open-WS call without issue, confirmed by running the test against unfixed code first (RED) before applying the fix (GREEN).

## Issues Encountered

None.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- NETCUT-01's core causal chain (`POST /api/network_mode` → global `pipeline.network_mode` → per-turn `conn_pipe.network_mode` re-sync → `VoicePipeline._process_text` engine gate) is now unbroken end-to-end and guarded by a live-WS regression test.
- The kill-switch is now a real trust boundary (JWT-gated), ready to serve as the operator-observable guarantee for 09-02's timeout hardening and 09-03/09-04's rehearsal work.
- Full test suite (`tests/`) green: 333 passed, no regressions introduced by this plan's changes.
- No blockers for 09-02 (timeout shortening) — this plan deliberately left `LLM_TIMEOUT_S`, `cloud_llm.py::_TIMEOUT_S`, and `config.py::CLOUD_TTS_TIMEOUT_S` untouched, per RESEARCH.md's scope split.

---
*Phase: 09-network-cut-demo-hardening*
*Completed: 2026-07-25*

## Self-Check: PASSED

All created/modified files and all 4 task commits (bd8c62d, c78943c, ab65fc7, ad61b5b) verified present.
