---
phase: 10-npu-accelerated-perception
plan: 03
subsystem: edge-npu-spike
tags: [onnxruntime, onnx, NeuronExecutionProvider, npu-placement, tdd, pytest]

# Dependency graph
requires:
  - phase: 10-npu-accelerated-perception (10-01)
    provides: "edge/npu_spike/inspect_model.py describe_graph_io spec dict shape ({name, shape, dtype, dynamic_dims}), fix_shape.py model.int8.fixed.onnx naming convention"
  - phase: 10-npu-accelerated-perception (10-02)
    provides: "server/npu_placement.py capture_fd_output/parse_ep_placement_log/summarize_placement/format_placement_line — the sole evidence layer for EP node placement"
provides:
  - "edge/npu_spike/raw_neuron_session.py: raw onnxruntime.InferenceSession + NeuronExecutionProvider Day-1 smoke test, bypassing sherpa-onnx's provider whitelist (Pitfall N1); two-phase provider_options retry (A2 hedge); DAY1_NPU_PROBE: PASS/FAIL <X>/<Y> line as the script's last line; exit codes 0/1/2 (PASS/FAIL/env-unavailable)"
  - "tests/test_raw_neuron_session.py: 10 unit tests (8 required behaviors + 2 supplementary) for build_neuron_providers/build_zero_feeds/format_probe_verdict, no onnxruntime/onnx import required"
affects: [10-04, 10-05, 10-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pure-functions-on-top, single main()-at-bottom spike structure (copied from edge/npu_spike/inspect_model.py and server/npu_placement.py) — main() is the only real-I/O entrypoint, lazy-imports onnxruntime/onnx inside itself"
    - "Two-phase provider_options retry: first attempt uses DEFAULT_NEURON_OPTIONS (A2 unverified assumption), automatic single retry with empty {} options if zero acceleration detected on the first pass — prevents misreporting an A2 key-name error as 'NPU unavailable'"
    - "Machine-readable last-line verdict convention (DAY1_NPU_PROBE: PREFIX, mirrors 10-01's NEURON_EP: and 10-02's NPU: HUD line conventions) plus differentiated exit codes (0/1/2) so 10-04's ADR can consume the result programmatically"

key-files:
  created:
    - edge/npu_spike/raw_neuron_session.py
    - tests/test_raw_neuron_session.py
  modified:
    - .planning/phases/10-npu-accelerated-perception/deferred-items.md

key-decisions:
  - "build_neuron_providers(options=None) applies DEFAULT_NEURON_OPTIONS only on None; options={} (explicit empty dict) is preserved as a first-class retry shape, not overridden by the default — this is the exact mechanism main()'s A2-hedge retry relies on"
  - "build_zero_feeds's _DTYPE_TO_NUMPY map accepts BOTH ORT-style dtype strings (\"tensor(float)\", used by the plan's test fixtures) AND raw ONNX TensorProto.elem_type integers (1/6/7, what describe_graph_io's dtype field actually contains) so main() can pass describe_graph_io's spec dicts straight through without a manual conversion step"
  - "onnxruntime's Python API exposes no public getter for the current logger severity (only set_default_logger_severity). main() therefore restores to ORT's documented default (2 = WARNING) in finally rather than attempting to read-back an unsupported value — documented inline to avoid ambiguity for 10-05's asr_npu.py, which inherits this same obligation per 10-02's module docstring"
  - "Docstring bullet (2) in main() intentionally avoids repeating the literal string 'set_default_logger_severity' outside of actual call sites, so the acceptance criterion's grep -c count (must equal exactly 2: one open, one restore) is not inflated by prose mentions"

patterns-established: []

requirements-completed: [NPU-01, NPU-02]

coverage:
  - id: D1
    description: "build_neuron_providers: assembles [(\"NeuronExecutionProvider\", options_or_default), \"CPUExecutionProvider\"]; options=None applies DEFAULT_NEURON_OPTIONS, options={} is preserved verbatim as the A2-retry shape"
    requirement: "NPU-01"
    verification:
      - kind: unit
        ref: "tests/test_raw_neuron_session.py#test_build_neuron_providers_default_options"
        status: pass
      - kind: unit
        ref: "tests/test_raw_neuron_session.py#test_build_neuron_providers_empty_options_is_first_class_retry_shape"
        status: pass
    human_judgment: false
  - id: D2
    description: "build_zero_feeds: produces zero-value ndarrays from describe_graph_io-shaped specs; dynamic/non-positive axes default to 1 without raising; float/int64/int32 dtype mapping (language/textnorm scalar inputs need int, not float)"
    requirement: "NPU-01"
    verification:
      - kind: unit
        ref: "tests/test_raw_neuron_session.py#test_build_zero_feeds_basic_float_spec"
        status: pass
      - kind: unit
        ref: "tests/test_raw_neuron_session.py#test_build_zero_feeds_dynamic_axis_defaults_to_one"
        status: pass
      - kind: unit
        ref: "tests/test_raw_neuron_session.py#test_build_zero_feeds_non_positive_dim_without_dynamic_flag_defaults_to_one"
        status: pass
      - kind: unit
        ref: "tests/test_raw_neuron_session.py#test_build_zero_feeds_int64_dtype"
        status: pass
    human_judgment: false
  - id: D3
    description: "format_probe_verdict: DAY1_NPU_PROBE: PASS/FAIL <X>/<Y> ops on NeuronExecutionProvider, byte-exact for well-formed summaries; missing/empty summary data always FAILs, never silently passes (T-10-07 Repudiation mitigation)"
    requirement: "NPU-02"
    verification:
      - kind: unit
        ref: "tests/test_raw_neuron_session.py#test_format_probe_verdict_pass"
        status: pass
      - kind: unit
        ref: "tests/test_raw_neuron_session.py#test_format_probe_verdict_fail"
        status: pass
      - kind: unit
        ref: "tests/test_raw_neuron_session.py#test_format_probe_verdict_empty_summary_fails_without_raising"
        status: pass
      - kind: unit
        ref: "tests/test_raw_neuron_session.py#test_probe_verdict_prefix_constant_matches_marker"
        status: pass
    human_judgment: false
  - id: D4
    description: "main(): argparse CLI (--model/--no-provider-options/--run-inference), opens ORT verbose logger severity(0) before session creation and restores it in finally, wraps InferenceSession creation in server.npu_placement.capture_fd_output(2), retries once with empty provider_options on zero-acceleration first pass, prints per-provider counts + HUD line + DAY1_NPU_PROBE: verdict as the final line, exits 0/1/2 for PASS/FAIL/env-unavailable"
    requirement: "NPU-02"
    verification:
      - kind: unit
        ref: "python -c \"import edge.npu_spike.raw_neuron_session\" (no onnxruntime/onnx installed) — succeeds, confirms lazy import with no import-time side effects"
        status: pass
      - kind: other
        ref: "grep -v '^\\s*#' edge/npu_spike/raw_neuron_session.py | grep -c 'set_default_logger_severity' == 2 (one open, one restore)"
        status: pass
    human_judgment: true
    rationale: "main()'s actual real-onnxruntime execution path (session creation, verbose-log capture, retry trigger, exit code) can only be exercised end-to-end on a real Genio 520 device with a real NeuronExecutionProvider build of onnxruntime. This sandbox has neither the hardware nor onnxruntime installed. The pure-function logic it calls (D1-D3) is fully unit-tested; the I/O orchestration itself is verified by static/structural checks (import safety, severity-restore count) only."
  - id: D5
    description: "Task 1 package-legitimacy gate (onnx/onnxruntime dev-machine tooling): human-verified and approved prior to this execution session (orchestrator-provided evidence, recorded verbatim below)"
    requirement: "NPU-01"
    verification: []
    human_judgment: true
    rationale: "This is a blocking-human checkpoint by design (T-10-09, supply-chain Tampering mitigation) — package legitimacy on PyPI must always be a human judgment call, not an automated pass. Approval was already granted by the orchestrating session before this executor was spawned; see 'Task 1 Evidence' section below for the verbatim pip show output."
  - id: D6
    description: "edge/npu_spike/DAY1-EVIDENCE.md: real Genio 520 device output (Task 3 of the plan) capturing the actual DAY1_NPU_PROBE: verdict, per-provider node counts, and the Day-1 stop-loss checkpoint determination"
    verification: []
    human_judgment: true
    rationale: "Requires SSH access to the physical Genio 520 board to run `python -m edge.npu_spike.raw_neuron_session` and paste its verbatim stdout + exit code. This parallel worktree executor has no device access (same constraint documented in 10-01-SUMMARY.md's D4 item). The plan explicitly prohibits fabricating or simulating this output ('本檔的每一段輸出都必須是真機貼上的原文...不得用 dev 機模擬輸出或以合理推測填空'), so this deliverable is deferred, not faked. See 'Known Gaps' below for the exact command to run."

duration: 25min
completed: 2026-07-26
status: complete
---

# Phase 10 Plan 03: Raw NeuronExecutionProvider Day-1 Probe Summary

**Standalone raw `onnxruntime.InferenceSession` + `NeuronExecutionProvider` smoke-test script (bypassing sherpa-onnx's hardcoded provider whitelist) with a byte-exact `DAY1_NPU_PROBE: PASS/FAIL X/Y` verdict line, fully unit-tested pure-function core plus an on-device-only `main()` I/O entrypoint**

## Performance

- **Duration:** ~25 min (TDD RED/GREEN cycle + full-suite regression check)
- **Completed:** 2026-07-26
- **Tasks:** 2 of 3 completed in this session (Task 1 pre-approved by orchestrator, Task 2 fully implemented; Task 3's real-device evidence file is deferred — see Known Gaps)
- **Files modified:** 3 (2 created, 1 deferred-items log appended)

## Accomplishments

- `edge/npu_spike/raw_neuron_session.py`: three pure functions (`build_neuron_providers`, `build_zero_feeds`, `format_probe_verdict`) plus a real-I/O `main()` that is the literal executable form of the D-02 Day-1 stop-loss checkpoint — "does at least one op get placed on NeuronExecutionProvider, per raw ORT verbose logs, independent of sherpa-onnx's convenience wrapper."
- Two-phase provider_options retry implemented exactly as specified: first pass uses `DEFAULT_NEURON_OPTIONS` (an unverified single-forum-post assumption per RESEARCH.md A2); if that pass shows zero acceleration, `main()` automatically retries once with empty `{}` options before concluding FAIL — this prevents an A2 key-name error from being misreported as "NPU unavailable."
- `format_probe_verdict` locks the machine-readable last-line contract: `DAY1_NPU_PROBE: PASS 3/152 ops on NeuronExecutionProvider` / `DAY1_NPU_PROBE: FAIL 0/152 ops on NeuronExecutionProvider`, byte-exact per Test 6/7; missing/empty summary data always FAILs (Test 8), never silently passes — this is the machine-readable form of T-10-07 (Repudiation mitigation).
- `tests/test_raw_neuron_session.py`: 10 tests (8 required behaviors + 2 supplementary constant/edge-case checks), all green, none importing `onnxruntime`/`onnx`.
- Full TDD RED -> GREEN gate sequence followed (test commit before implementation commit).
- Full `pytest` suite re-run after implementation: identical pre-existing failure set to 10-01/10-02 (no new regressions), logged in `deferred-items.md`.

## Task Commits

1. **Task 1: 套件正當性閘 — onnx / onnxruntime（dev 機工具鏈）** — pre-approved by the orchestrating session before this executor was spawned; no new commit in this session (see Task 1 Evidence below).
2. **Task 2: edge/npu_spike/raw_neuron_session.py — raw NeuronEP session 與放置量測**
   - RED `1bd4986` (test: add failing tests for raw NeuronEP probe helpers)
   - GREEN `0de12f9` (feat: add raw NeuronExecutionProvider Day-1 probe)
   - Follow-up `d08466b` (docs: confirm full-suite pre-existing failures unchanged)

_Note: Task 2 is a `tdd="true"` task, hence RED -> GREEN commit pair; no REFACTOR commit was needed (implementation was clean at GREEN)._

## Task 1 Evidence (Package Legitimacy Gate — Pre-Approved)

This blocking-human checkpoint (`gate="blocking-human"`) was verified and approved by the orchestrating session prior to this executor being spawned. Recording the evidence verbatim per the plan's acceptance criteria, as instructed:

```
$ pip show onnx
Home-page: https://onnx.ai/
Author-email: ONNX Contributors <onnx-technical-discuss@lists.lfaidata.foundation>
Version: 1.22.0

$ pip show onnxruntime
Home-page: https://onnxruntime.ai
Author: Microsoft Corporation
Version: 1.24.4
```

- Both packages were already present in the dev venv (`.venv`), installed as a project dependency — neither was freshly installed for this gate.
- **No `pip install onnxruntime` was or will be run on the Genio 520 device itself.** Device-side inference must use the Yocto/MediaTek-provided onnxruntime build with `NeuronExecutionProvider`; the generic PyPI wheel does not ship `NeuronExecutionProvider` and would permanently destroy the only usable NPU path if it overwrote the Yocto build.
- Homepage/maintainer fields match the plan's acceptance criteria (`onnx.ai` / `onnxruntime.ai`, ONNX Foundation / Microsoft).

Task 1 is treated as satisfied/complete; this session proceeded directly to Task 2.

## Files Created/Modified

- `edge/npu_spike/raw_neuron_session.py` - Day-1 raw NeuronEP smoke test; pure functions on top, `main()`/`_probe_once()` at bottom, lazy-imports `onnxruntime`/`onnx` inside `main()` only
- `tests/test_raw_neuron_session.py` - 10 pure-function unit tests, no real onnx/onnxruntime dependency
- `.planning/phases/10-npu-accelerated-perception/deferred-items.md` - appended a 10-03 confirmation entry (same pre-existing failure set as 10-01/10-02, no new regressions)

## Decisions Made

- `build_neuron_providers(options=None)` applies `DEFAULT_NEURON_OPTIONS` only when `options` is `None`; `options={}` is preserved as a first-class value (not coerced to the default) — this is the exact mechanism `main()`'s A2-hedge retry depends on.
- `_DTYPE_TO_NUMPY` accepts both ORT-style dtype strings (`"tensor(float)"`, matching the plan's test fixtures) and raw ONNX `TensorProto.elem_type` integers (1/6/7, what `describe_graph_io` actually returns) so `main()` can pass `describe_graph_io`'s output straight into `build_zero_feeds` without a manual conversion step.
- Since `onnxruntime`'s Python API has no public getter for current logger severity, `main()` restores to ORT's documented default (2 = WARNING) in `finally`, rather than attempting to read back an unsupported value. Documented inline for 10-05's `asr_npu.py`, which inherits the same severity-restore obligation per 10-02's module docstring.
- Deliberately rephrased one docstring bullet in `main()` to avoid repeating the literal string `set_default_logger_severity` outside actual call sites, so the acceptance criterion's `grep -c` count (must equal exactly 2) isn't inflated by prose.

## Deviations from Plan

None (Rules 1-4) - Task 2's implementation follows the plan's `<behavior>`/`<action>` sections exactly (function signatures, exports, two-phase retry logic, exit codes, docstring's four required points). The one adjustment made was a **documentation wording fix** during self-verification (not a deviation from behavior): the initial `main()` docstring accidentally repeated the literal string `set_default_logger_severity` in prose, which would have made the acceptance criterion's `grep -c ... == 2` check fail with 3 instead of 2. Rephrased to describe the action without repeating the identifier — no functional/behavioral change, verified via `grep -v '^\s*#' edge/npu_spike/raw_neuron_session.py | grep -c 'set_default_logger_severity'` returning exactly `2` after the fix.

## Issues Encountered

- **Task 3 (`edge/npu_spike/DAY1-EVIDENCE.md`) not produced in this session** — this executor runs in an isolated parallel worktree with no SSH access to the physical Genio 520, identical to the constraint 10-01-SUMMARY.md documented for its own device-dependent item (D4 there, D6 here). The plan explicitly forbids fabricating or simulating this output. See "Known Gaps" below for the exact command and required paste-in structure.
- Full-suite `pytest` re-confirms the same 9 pre-existing failures + 3 collection errors already logged in `deferred-items.md` by 10-01/10-02 (missing `soundfile`/`opencc`/`pytest-asyncio` packages, pipecat spike dependency gaps) — unrelated to this plan's files, not fixed per scope-boundary rules, appended a confirming entry rather than duplicating the list.

## User Setup Required

None for Task 2 (dev-machine work, no external service configuration). Task 3 (device evidence capture) requires SSH access to the Genio 520 board — see Known Gaps.

## Known Gaps — Pending Real-Device Verification (Task 3)

Per the plan's `<output>` requirement, the SUMMARY must eventually include the real device's `DAY1_NPU_PROBE:` line and exit code verbatim. This cannot be produced from this sandboxed parallel-worktree execution context (no SSH access to the Genio 520). All automatable acceptance criteria for Task 2 are green; only Task 3's physical-hardware step and its `DAY1-EVIDENCE.md` artifact remain. To close this gap, run on the device:

```bash
# Uses 10-01's fix_shape.py output (model.int8.fixed.onnx) by default via server.config.SENSEVOICE_DIR
python -m edge.npu_spike.raw_neuron_session --model <path-to-model.int8.fixed.onnx>
echo "exit code: $?"
```

Paste the full stdout (per-provider node counts, the `NPU:` HUD line, and the final `DAY1_NPU_PROBE:` line) and the exit code into a new `edge/npu_spike/DAY1-EVIDENCE.md`, structured per the plan's Task 3 `<action>` (5 sections: header w/ date+DEVICE_ID, environment probe from 10-01, shape-fixing from 10-01, raw NeuronEP probe output from this plan, Day-1 checkpoint determination, open questions) — before 10-04's ADR can rely on the answer.

## Next Phase Readiness

- `edge/npu_spike/raw_neuron_session.py` is ready to run on-device immediately; no further code changes needed before real-device verification.
- 10-04 (ADR) needs the real `DAY1_NPU_PROBE:` verdict from `DAY1-EVIDENCE.md` (Task 3) before it can make a GO/NO-GO recommendation — this remains the single blocking gap for the phase's Day-1 stop-loss question.
- No risk introduced to the Phase 8 CPU-only baseline: this plan touched no files under `server/` (only reads `server/npu_placement.py` and `server/config.py`), only added new isolated `edge/npu_spike/` script and its tests.

## Self-Check: PASSED

Files verified present on disk:
- FOUND: edge/npu_spike/raw_neuron_session.py
- FOUND: tests/test_raw_neuron_session.py
- FOUND: .planning/phases/10-npu-accelerated-perception/deferred-items.md (modified)

Commit hashes verified present in `git log`:
- FOUND: 1bd4986 (test: RED, Task 2)
- FOUND: 0de12f9 (feat: GREEN, Task 2)
- FOUND: d08466b (docs: deferred-items confirmation)

Test/verification commands re-confirmed:
- `pytest tests/test_raw_neuron_session.py -x` -> 10 passed
- `python -c "import edge.npu_spike.raw_neuron_session"` -> succeeds without onnxruntime/onnx installed
- `grep -v '^\s*#' edge/npu_spike/raw_neuron_session.py | grep -c 'set_default_logger_severity'` -> 2
- `pytest --ignore=tests/test_audio_io.py --ignore=tests/test_pipeline_wav_fastpath.py` -> 366 passed, 2 skipped, 9 pre-existing failures (unchanged from 10-01/10-02), 3 pre-existing collection errors (unchanged)

---
*Phase: 10-npu-accelerated-perception*
*Completed: 2026-07-26*
