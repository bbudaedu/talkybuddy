---
phase: 09-network-cut-demo-hardening
plan: 02
subsystem: api
tags: [asyncio, urllib, timeouts, trust-boundary, privacy]

# Dependency graph
requires:
  - phase: 08-cpu-only-offline-edge-turn-loop
    provides: edge LLM/TTS runtime (llama-server + ALSA loop) whose real-hardware worst-case latency (4170ms) bounds how far LLM_TIMEOUT_S can safely shrink
  - phase: 09-network-cut-demo-hardening
    plan: 01
    provides: live-session network_mode re-sync fix (this plan's timeout/gate work assumes the kill-switch actually takes effect mid-session)
provides:
  - "server/cloud_llm.py::_TIMEOUT_S shortened 8.0 -> 1.5 (env CLOUD_LLM_TIMEOUT_S), decoupled from pipeline's shared outer timeout"
  - "server/config.py::CLOUD_TTS_TIMEOUT_S default shortened 6.0 -> 1.5 (env name unchanged)"
  - "server/pipeline.py::LLM_TIMEOUT_S left at 8.0 with expanded comment documenting it as the edge engine's real safety margin"
  - "tests/test_pipeline_timeout_isolation.py: structural regression guard against LLM_TIMEOUT_S ever being uniformly slashed"
  - "server/diagnose.py::generate_diagnosis(allow_cloud=True) third egress gate, closing the one background side-channel that bypassed the kill-switch"
affects: [09-03, 09-04]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Cloud engines fail fast on their own inner urlopen timeout; the shared outer asyncio.wait_for wrapper stays generous because it also bounds the edge engine"
    - "Env-var-configurable timeout constants (float(os.environ.get(NAME, default))) reused for the new CLOUD_LLM_TIMEOUT_S, matching the existing CLOUD_TTS_TIMEOUT_S/PRON_SCORE_TIMEOUT_S convention"
    - "Capture a mutable instance attribute (self.network_mode) into a local variable before entering an asyncio.to_thread closure, so the closure sees the value at trigger time rather than whatever the attribute holds when the thread actually runs"
    - "New boolean gate kwarg placed last with a default that preserves every existing positional call site, added to the front of an existing `if` condition for short-circuit"

key-files:
  created:
    - tests/test_pipeline_timeout_isolation.py
    - tests/test_diagnose_network_gate.py
  modified:
    - server/cloud_llm.py
    - server/config.py
    - server/pipeline.py
    - server/diagnose.py
    - tests/test_cloud_tts_config.py
    - tests/test_pipeline_directive.py

key-decisions:
  - "LLM_TIMEOUT_S stays 8.0, not shortened despite D-03's literal three-constant list — RESEARCH.md Pitfall 2 proved it's the shared cloud/edge outer wrapper and Phase 8 real hardware measured edge LLM alone at 4170ms; executing D-03's intent (fast cloud degrade) rather than its literal text"
  - "Only cloud-specific inner urlopen timeouts (cloud_llm._TIMEOUT_S, config.CLOUD_TTS_TIMEOUT_S) were shortened to 1.5s, both env-overridable so rehearsal (09-04) can retune without a code change"
  - "generate_diagnosis's new allow_cloud parameter defaults to True so every existing call site (server/app.py's two generate_diagnosis calls) is unaffected; only VoicePipeline._refresh_directive passes allow_cloud=False, and only when network_mode == \"edge\""
  - "_refresh_directive still runs its local rule-based diagnosis refresh in edge mode — only the cloud branch is skipped; the offline B1 tutor-update feature is preserved, not disabled"
  - "/api/status zero-outbound-call test distinguishes loopback urlopen calls (EdgeLLM.available()'s /health probe to 127.0.0.1:8080) from genuine cloud egress by inspecting the request's hostname, rather than asserting zero urlopen calls of any kind"

patterns-established:
  - "Structural constant-contract tests (assert X >= N / assert Y <= N with an explanatory failure message citing the source measurement) as a regression guard against a comment-only prohibition being silently violated later"

requirements-completed: [NETCUT-02]

coverage:
  - id: D-03a
    description: "雲端 LLM/TTS 各自的內層 urlopen 逾時皆 <= 2.0 秒（預設 1.5），斷網/切換時單一雲端階段最多多等 1.5 秒即降級"
    requirement: NETCUT-02
    verification:
      - kind: unit
        ref: "tests/test_pipeline_timeout_isolation.py#test_cloud_timeouts_are_short"
        status: pass
    human_judgment: false
  - id: D-03b
    description: "server/pipeline.py::LLM_TIMEOUT_S 維持 >= 6.0，不隨 D-03 一併縮短，edge 引擎不被外層共用逾時餓死"
    requirement: NETCUT-02
    verification:
      - kind: unit
        ref: "tests/test_pipeline_timeout_isolation.py#test_llm_timeout_stays_generous_for_edge_engine"
        status: pass
      - kind: unit
        ref: "tests/test_pipeline_timeout_isolation.py#test_cloud_slow_then_none_does_not_starve_edge_when_llm_timeout_generous"
        status: pass
    human_judgment: false
  - id: D-03c
    description: "兩個雲端逾時皆可經環境變數覆寫（CLOUD_LLM_TIMEOUT_S / CLOUD_TTS_TIMEOUT_S）"
    requirement: NETCUT-02
    verification:
      - kind: manual
        ref: "CLOUD_LLM_TIMEOUT_S=4 .venv/bin/python -c \"from server import cloud_llm; assert cloud_llm._TIMEOUT_S == 4.0\" — CLOUD_TTS_TIMEOUT_S already had this pattern pre-existing"
        status: pass
    human_judgment: false
  - id: T-09-04
    description: "network_mode 為 edge 時，背景 _refresh_directive 的雲端診斷分支不被執行，本地規則式刷新仍運作"
    requirement: NETCUT-02
    verification:
      - kind: unit
        ref: "tests/test_diagnose_network_gate.py#test_refresh_directive_edge_mode_passes_allow_cloud_false"
        status: pass
      - kind: unit
        ref: "tests/test_diagnose_network_gate.py#test_refresh_directive_cloud_mode_passes_allow_cloud_true"
        status: pass
    human_judgment: false
  - id: T-09-11
    description: "/api/status 5 秒輪詢對此端點不需暫停邏輯，證據性證明零真正出境呼叫"
    requirement: NETCUT-02
    verification:
      - kind: unit
        ref: "tests/test_pipeline_timeout_isolation.py#test_api_status_makes_no_outbound_call"
        status: pass
    human_judgment: false

# Metrics
duration: 20min
completed: 2026-07-25
status: complete
---

# Phase 9 Plan 2: Cloud-only timeout hardening + background diagnosis egress gate Summary

**Shortened the cloud-specific inner urlopen timeouts (LLM 8.0s->1.5s, TTS 6.0s->1.5s, both env-overridable) so a mid-turn switch-flip degrades to edge within ~1.5s, while deliberately leaving the shared `pipeline.py::LLM_TIMEOUT_S` outer wrapper at 8.0s so it doesn't starve the edge engine's real-hardware worst-case (4170ms) — and closed the one background side-channel (`_refresh_directive`'s diagnosis call) that previously bypassed the kill-switch entirely.**

## Performance

- **Duration:** ~20 min
- **Tasks:** 3
- **Files modified:** 8 (2 new test files, 6 modified)

## Accomplishments

- `server/cloud_llm.py::_TIMEOUT_S`: `8.0` -> `float(os.environ.get("CLOUD_LLM_TIMEOUT_S", "1.5"))`. Rewrote the stale "aligned with pipeline's outer LLM_TIMEOUT_S, double insurance" comment (which would have been actively misleading after this plan) into a decoupling explanation.
- `server/config.py::CLOUD_TTS_TIMEOUT_S`: default string `"6.0"` -> `"1.5"`, env var name (`CLOUD_TTS_TIMEOUT_S`) unchanged, `PRON_SCORE_TIMEOUT_S` untouched.
- `server/pipeline.py::LLM_TIMEOUT_S`: value **unchanged at 8.0**. Comment expanded to explain it's the shared cloud/edge outer `asyncio.wait_for` wrapper and therefore the edge engine's actual safety margin (Phase 8 real Genio 520 measurement: LLM stage alone up to 4170ms). This is the plan's deliberate reconciliation of D-03's literal three-constant list against RESEARCH.md Pitfall 2's real-hardware evidence — executing D-03's *intent* (fast cloud degrade) rather than its literal text, as flagged in this phase's delivery.
- New `tests/test_pipeline_timeout_isolation.py` (5 tests): two constant-contract tests (`LLM_TIMEOUT_S >= 6.0`, cloud timeouts `<= 2.0`) that fail with an explanatory message citing the source measurement if anyone violates them later; a behavior-isolation positive test proving a slow cloud stub doesn't starve a normal-speed edge stub's own timeout budget; a documented negative/regression-record test showing what breaks if `LLM_TIMEOUT_S` *is* slashed; and a `/api/status` zero-outbound-call test.
- New `server/diagnose.py::generate_diagnosis(..., allow_cloud: bool = True)` parameter — third egress gate placed at the front of the existing `if cfg and guardrails.consent_granted()` condition for short-circuit, so `network_mode == "edge"` skips even the credential/consent check.
- `server/pipeline.py::_refresh_directive`: captures `self.network_mode == "cloud"` into a local variable before the `asyncio.to_thread` closure runs (so the closure sees the mode at trigger time, not whatever it is when the thread actually executes), and passes it as `allow_cloud` to `generate_diagnosis`. The local rule-based diagnosis refresh still runs unconditionally in edge mode — only the cloud branch is skipped.
- New `tests/test_diagnose_network_gate.py` (4 tests) covering the gate itself and the pipeline wiring in both modes, including an assertion that edge mode's local refresh still updates `vp._directive` and `store.list_diagnoses()`.

## Task Commits

1. **Task 1: shorten cloud-only inner timeouts, keep LLM_TIMEOUT_S generous**
   - `169a421` (feat) — `server/cloud_llm.py`, `server/config.py`, `server/pipeline.py` (comment only), `tests/test_cloud_tts_config.py`
2. **Task 2: timeout-isolation regression suite**
   - `09ae22c` (test) — new `tests/test_pipeline_timeout_isolation.py`
3. **Task 3: background diagnosis egress gate**
   - `a75fdf7` (feat) — `server/diagnose.py`, `server/pipeline.py`, new `tests/test_diagnose_network_gate.py`, `tests/test_pipeline_directive.py` (stub signature fix)

**Plan metadata:** (this commit, docs: complete plan)

## Files Created/Modified

- `server/cloud_llm.py` — `_TIMEOUT_S` env-configurable, default 1.5; decoupling comment
- `server/config.py` — `CLOUD_TTS_TIMEOUT_S` default 1.5
- `server/pipeline.py` — `LLM_TIMEOUT_S` comment expanded (value unchanged); `_refresh_directive` allow_cloud wiring
- `server/diagnose.py` — `generate_diagnosis(allow_cloud=True)` third egress gate
- `tests/test_cloud_tts_config.py` — updated default-value assertion (6.0 -> 1.5)
- `tests/test_pipeline_timeout_isolation.py` (new) — 5 tests: constant contracts + behavior isolation + `/api/status` egress evidence
- `tests/test_diagnose_network_gate.py` (new) — 4 tests: `allow_cloud` gate + `_refresh_directive` wiring
- `tests/test_pipeline_directive.py` — two `generate_diagnosis` stub lambdas widened to accept `**kwargs` (regression from the new keyword-only `allow_cloud` parameter)

## Decisions Made

- Reconciled D-03's literal "縮短 pipeline.py::LLM_TIMEOUT_S" instruction against RESEARCH.md's real-hardware evidence by leaving its *value* untouched and instead making the reconciliation explicit and structurally enforced (comment + regression test), per the plan's own stated intent-over-literal-text approach. Documented here for user visibility as the plan's objective required.
- `allow_cloud` parameter placed last with a default (`True`) rather than inserted before `profile` — every existing positional call site (`server/app.py`'s two `generate_diagnosis(recent, prev)` / `generate_diagnosis(recent, prev, store.get_profile())` calls) is unaffected and continues to allow cloud calls exactly as before.
- `_refresh_directive`'s `allow_cloud` local variable is captured *before* entering the `try:` block's `_work()` closure definition, not read live inside the thread — this avoids a subtle race where `network_mode` could theoretically change between task creation and thread execution, making the gate's decision deterministic and tied to the moment the background refresh was triggered.
- `/api/status` egress test distinguishes loopback (`127.0.0.1`/`localhost`) `urlopen` calls from genuine outbound calls by hostname, rather than asserting zero `urlopen` calls of any kind — `EdgeLLM.available()`'s `/health` probe to the local llama-server (`server/llm.py::_HEALTH_TIMEOUT_S`) also goes through `urllib.request.urlopen` and is legitimately invoked on every `/api/status` call; this is not a cloud egress and treating it as one would have made the test permanently fail against correct, unrelated existing behavior.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `/api/status` zero-outbound-call test needed to exclude the local llama-server health-check loopback call**
- **Found during:** Task 2, writing `test_api_status_makes_no_outbound_call`
- **Issue:** The plan's `<action>` described the spy as recording all `urlopen` calls and asserting the list is empty. Running the naive version against real code showed `GET /api/status` legitimately calls `llm_engine.available()` (`server/llm.py::EdgeLLM.available()`), which itself calls `urllib.request.urlopen(..., timeout=0.5)` against `127.0.0.1:8080` (the local llama-server health endpoint) — not a cloud call, but it uses the same stdlib function the spy intercepts.
- **Fix:** Spy now inspects each intercepted request's hostname via `urllib.parse.urlsplit` and only records/asserts on calls whose host is *not* `127.0.0.1`/`localhost`, matching RESEARCH.md's own framing ("both hit local-only endpoints on the same device").
- **Files modified:** `tests/test_pipeline_timeout_isolation.py`
- **Commit:** `09ae22c`

**2. [Rule 1 - Bug] Two `generate_diagnosis` stub lambdas in `tests/test_pipeline_directive.py` needed widening for the new kwarg**
- **Found during:** Task 3 verification (`test_refresh_directive_updates_cache` regression)
- **Issue:** `_refresh_directive` now calls `diagnose.generate_diagnosis(recent, prev, allow_cloud=allow_cloud)`. Two existing tests monkeypatched `generate_diagnosis` with `lambda recent, prev: fake_diag` (2-positional-only), which raised `TypeError: unexpected keyword argument 'allow_cloud'` inside the `_work()` closure — silently swallowed by `_refresh_directive`'s outer `except Exception: pass`, leaving `vp._directive` as `None` and failing the test's `assert vp._directive is not None`.
- **Fix:** Widened both lambdas to `lambda recent, prev, **kwargs: fake_diag`.
- **Files modified:** `tests/test_pipeline_directive.py`
- **Commit:** `a75fdf7`

## Issues Encountered

None beyond the two auto-fixed items above (both anticipated categories of "existing test needs updating for a widened production signature").

## User Setup Required

None — no external service configuration required. `CLOUD_LLM_TIMEOUT_S` and `CLOUD_TTS_TIMEOUT_S` env vars are optional overrides with working defaults.

## Next Phase Readiness

- Both cloud-only timeouts are now short (1.5s default) and env-overridable, ready for 09-04's rehearsal to confirm/retune the exact number against real venue network conditions (per RESEARCH.md Open Question 2) without any code change.
- `LLM_TIMEOUT_S` structural guard (`tests/test_pipeline_timeout_isolation.py`) means any future accidental or well-intentioned attempt to "align" it with the shortened cloud timeouts will fail CI immediately with a message pointing at the Phase 8 hardware evidence, rather than silently degrading every offline reply to scaffold.
- The background diagnosis egress gate closes the last identified cloud side-channel independent of the primary conversational path; combined with 09-01's live-session `network_mode` re-sync fix, the kill-switch narrative ("開關=這台裝置現在零出境呼叫") now holds end-to-end for both the visible conversation and this background task.
- Full test suite green: 342 passed (up from 333 after 09-01; +9 new tests from this plan), no regressions.
- No blockers for 09-03/09-04 — this plan did not touch UI copy, badge visuals, or the rehearsal script; those remain fully open for the next plans.

---
*Phase: 09-network-cut-demo-hardening*
*Completed: 2026-07-25*

## Self-Check: PASSED

All created/modified files and all 3 task commits (169a421, 09ae22c, a75fdf7) verified present.
