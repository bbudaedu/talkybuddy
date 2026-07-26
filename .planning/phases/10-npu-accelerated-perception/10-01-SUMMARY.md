---
phase: 10-npu-accelerated-perception
plan: 01
subsystem: edge-diagnostics
tags: [onnxruntime, onnx, NeuronExecutionProvider, make_dynamic_shape_fixed, sherpa-onnx, pytest]

# Dependency graph
requires:
  - phase: 08-cpu-only-offline-edge-turn-loop
    provides: SenseVoice CPU-only ASR baseline (server/asr_sensevoice.py) and existing model file at SENSEVOICE_DIR/model.int8.onnx that this spike inspects
provides:
  - "edge/npu_spike/inspect_model.py: on-device ORT provider probe (NEURON_EP: PRESENT/ABSENT) + ONNX graph IO / custom-metadata dump, pure functions unit-tested"
  - "edge/npu_spike/fix_shape.py: fixed-argv wrapper around onnxruntime.tools.make_dynamic_shape_fixed for the hard NPU static-shape prerequisite"
  - "tests/test_npu_spike_tools.py: 15 stub-based unit tests for both tools, no real onnx/onnxruntime import required"
affects: [10-02, 10-03, 10-05]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Diagnose-before-build spike pattern (edge/npu_spike/): pure functions on top, single main() I/O entrypoint at bottom, module docstring states why the split exists — copied verbatim from edge/runtime/measure_peak_rss.py"
    - "main() prints sys.executable/sys.version first to disambiguate venv vs system python3 on-device (provision_device.sh venv lacks --system-site-packages)"
    - "Each diagnostic step in main() wrapped in its own try/except so one failure doesn't abort the rest of the print-everything diagnostic run"

key-files:
  created:
    - edge/npu_spike/__init__.py
    - edge/npu_spike/inspect_model.py
    - edge/npu_spike/fix_shape.py
    - tests/test_npu_spike_tools.py
    - .planning/phases/10-npu-accelerated-perception/deferred-items.md
  modified: []

key-decisions:
  - "build_fix_shape_argv raises ValueError (not a safe-default) when both or neither of the two argument forms are given — this is a programmer assembly error, not a runtime external-call failure, so it should fail loudly per the plan's explicit behavior spec"
  - "fix_shape.py's --model-out default inserts '.fixed' before the extension (model.int8.onnx -> model.int8.fixed.onnx) to match the filename 10-05's NPU_ASR_MODEL_PATH default will reference"

patterns-established:
  - "Pattern: NPU spike scripts live under edge/npu_spike/, fully isolated from server/ and Phase 8's CPU baseline — zero risk to the shipped CPU-only edge turn loop"

requirements-completed: [NPU-01]

coverage:
  - id: D1
    description: "probe_runtime/format_provider_report: on-device ORT provider probe with fixed NEURON_EP: PRESENT/ABSENT last-line marker"
    requirement: "NPU-01"
    verification:
      - kind: unit
        ref: "tests/test_npu_spike_tools.py#test_probe_runtime_with_neuron_present"
        status: pass
      - kind: unit
        ref: "tests/test_npu_spike_tools.py#test_format_provider_report_last_line_present_when_neuron_available"
        status: pass
    human_judgment: false
  - id: D2
    description: "describe_graph_io/format_metadata_map: ONNX graph input/output signature and custom-metadata dump, pure functions"
    requirement: "NPU-01"
    verification:
      - kind: unit
        ref: "tests/test_npu_spike_tools.py#test_describe_graph_io_marks_dim_param_axis_as_dynamic"
        status: pass
      - kind: unit
        ref: "tests/test_npu_spike_tools.py#test_format_metadata_map_sorted_key_value_lines"
        status: pass
    human_judgment: false
  - id: D3
    description: "build_fix_shape_argv/run_fix_shape: fixed-argv wrapper for make_dynamic_shape_fixed, ValueError on malformed argument combination, subprocess OSError degrades to (-1, msg) instead of raising"
    requirement: "NPU-01"
    verification:
      - kind: unit
        ref: "tests/test_npu_spike_tools.py#test_build_fix_shape_argv_dim_param_form"
        status: pass
      - kind: unit
        ref: "tests/test_npu_spike_tools.py#test_run_fix_shape_oserror_returns_minus_one"
        status: pass
    human_judgment: false
  - id: D4
    description: "Real Genio 520 device output: NEURON_EP PRESENT/ABSENT verdict, SenseVoice graph input/shape/dtype, custom metadata map, and FIX_SHAPE: OK/FAILED line, pasted verbatim"
    verification: []
    human_judgment: true
    rationale: "Requires SSH access to physical Genio 520 hardware; this parallel worktree executor has no device access. Must be run manually and results pasted back into this SUMMARY (or a follow-up) before 10-03/10-05 can rely on the answer."

duration: 20min
completed: 2026-07-26
status: complete
---

# Phase 10 Plan 01: NPU-01 Diagnose-First Spike Foundation Summary

**On-device ORT/NeuronExecutionProvider probe + SenseVoice ONNX graph/metadata inspector, plus a fixed-argv `make_dynamic_shape_fixed` wrapper — all pure-function logic unit-tested without requiring onnx/onnxruntime to be installed**

## Performance

- **Duration:** ~20 min (analysis + TDD RED/GREEN cycles for both tasks)
- **Completed:** 2026-07-26
- **Tasks:** 2 completed (both `tdd="true"`)
- **Files modified:** 5 (4 created + 1 deferred-items log)

## Accomplishments

- `edge/npu_spike/inspect_model.py`: `probe_runtime()` + `format_provider_report()` answer RESEARCH.md's Open Question 1 (does the flashed Yocto image's onnxruntime actually ship `NeuronExecutionProvider`) with a `NEURON_EP: PRESENT`/`ABSENT` fixed marker line; `describe_graph_io()` + `format_metadata_map()` dump SenseVoice's actual input names/shapes/dynamic axes and ONNX custom metadata (LFR/CMVN/blank_id-style keys) without guessing.
- `edge/npu_spike/fix_shape.py`: `build_fix_shape_argv()` + `run_fix_shape()` produce a shape-fixed `model.int8.fixed.onnx` via a single fixed-argv subprocess call to the official `onnxruntime.tools.make_dynamic_shape_fixed` tool — no hand-edited protobuf, no shell string interpolation.
- `tests/test_npu_spike_tools.py`: 15 unit tests covering all four exported pure functions plus the argv builder and subprocess wrapper, all using stub objects — no real `onnx`/`onnxruntime` import needed (confirmed: dev sandbox lacks both packages).
- Full TDD RED->GREEN gate sequence followed for both tasks (test commit before implementation commit each time).

## Task Commits

Each task followed RED (failing test) -> GREEN (implementation) TDD gates:

1. **Task 1: inspect_model.py** — RED `e5ea06b` (test), GREEN `28968f6` (feat)
2. **Task 2: fix_shape.py** — RED `89a7dc4` (test), GREEN `85a161d` (feat)

**Deferred-items log:** `e757f78` (docs: unrelated pre-existing test-suite gaps found during full-suite verification run)

_Note: both tasks are TDD tasks, hence 2 commits each (test -> feat); no refactor commit was needed._

## Files Created/Modified

- `edge/npu_spike/__init__.py` - marks package, docstring notes this is a spike dir, not production runtime
- `edge/npu_spike/inspect_model.py` - ORT provider probe + ONNX graph/metadata diagnostic, `main()` is the only I/O entrypoint
- `edge/npu_spike/fix_shape.py` - `make_dynamic_shape_fixed` fixed-argv wrapper, `main()` prints executed argv + `FIX_SHAPE: OK`/`FAILED`
- `tests/test_npu_spike_tools.py` - 15 pure-function unit tests for both tools (stub-based, no real onnx/onnxruntime dependency)
- `.planning/phases/10-npu-accelerated-perception/deferred-items.md` - logs pre-existing, unrelated test-suite environment gaps found while verifying no regression

## Decisions Made

- `build_fix_shape_argv` raises `ValueError` (not a safe default) when both or neither of the two argument forms (`dim_param`+`dim_value` vs `input_name`+`input_shape`) are supplied — per plan spec, this is a caller assembly bug that should fail loudly, distinct from the "safe degrade on external failure" behavior required of `run_fix_shape`.
- `fix_shape.py --model-out` default inserts `.fixed` before the `.onnx` extension (`model.int8.onnx` -> `model.int8.fixed.onnx`) — this exact filename convention will be referenced by 10-05's `NPU_ASR_MODEL_PATH` default, so it was locked here rather than left ad hoc.
- `describe_graph_io` combines both `graph.input` and `graph.output` tensors into one flat list (plan text says "每個 input/output 回一個 dict") rather than only inputs, so `main()`'s printed table shows the full graph signature, not just inputs.

## Deviations from Plan

None - plan executed exactly as written. Both tasks' pure-function behavior, exports, and acceptance criteria match the PLAN.md spec verbatim.

## Issues Encountered

- **Full-suite `pytest` run surfaced 9 failed tests + 2 collection errors unrelated to this plan's changes** (missing `soundfile`/`opencc` packages and AWS credentials/mocks in this dev sandbox — see `.planning/phases/10-npu-accelerated-perception/deferred-items.md` for the full list). These are pre-existing environment gaps in this particular dev sandbox, not caused by `edge/npu_spike/` or `tests/test_npu_spike_tools.py`. Verified isolation: `pytest tests/test_npu_spike_tools.py -x` is 15/15 green; `pytest --ignore=tests/test_audio_io.py --ignore=tests/test_pipeline_wav_fastpath.py` shows 342 passed / 2 skipped / the same 9 pre-existing failures, none touching this plan's files. Logged to deferred-items.md per scope-boundary rules rather than fixed (out of scope for this plan).
- **Real Genio 520 device execution not performed** — this executor runs in an isolated parallel worktree without SSH access to the physical board. Per the plan's `<verify><human-check>` requirements (Task 1 and Task 2), the actual `NEURON_EP:` verdict, SenseVoice graph signature/metadata, and `FIX_SHAPE:` output must be captured on the real device and pasted into this SUMMARY (or a follow-up note) before 10-03/10-05 can rely on the answer. See "Known Gaps" below for exact commands to run.

## User Setup Required

None - no external service configuration required. Device verification (see Known Gaps) requires SSH access to the Genio 520 board, not new service setup.

## Known Gaps — Pending Real-Device Verification

The plan's `<output>` requirement states the SUMMARY "務必包含真機兩支直譯器的原始輸出（不要摘要成「有／沒有」，貼原文）" — this cannot be produced from this sandboxed parallel-worktree execution context (no SSH access to the Genio 520). All automatable acceptance criteria (pytest, import-without-onnxruntime) are verified and green; only the physical-hardware steps remain. To close this gap, run on the device:

```bash
# 1. System python3 (checks whether Yocto's system-level onnxruntime has NeuronExecutionProvider)
python3 -m edge.npu_spike.inspect_model

# 2. venv python (provision_device.sh's .venv, which does NOT inherit system site-packages)
.venv/bin/python -m edge.npu_spike.inspect_model

# 3. Once the actual dynamic-axis name/position is known from step 1/2's graph dump:
python -m edge.npu_spike.fix_shape \
  --model-in models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/model.int8.onnx \
  --dim-param <actual_axis_name> --dim-value 200
```

Paste the `NEURON_EP:` line, the full graph IO table, the custom metadata table, and the `FIX_SHAPE:` line (verbatim, not summarized) into this file or a follow-up note before 10-03 begins — 10-03's raw NeuronEP session work depends on this evidence, per the plan's stated purpose.

## Next Phase Readiness

- Both diagnostic tools are ready to run on-device immediately; no further code changes needed before real-device verification.
- 10-03 (raw NeuronEP session) and 10-05 (`server/asr_npu.py`) can proceed once the device verification above is captured — until then, Open Question 1 (does this specific flashed image actually have `NeuronExecutionProvider`) remains formally unanswered, per RESEARCH.md Assumption A1.
- No risk introduced to the Phase 8 CPU-only baseline: this plan touched no files under `server/`, only added new isolated `edge/npu_spike/` scripts and their tests.

## Self-Check: PASSED

All claimed files verified present on disk:
- FOUND: edge/npu_spike/__init__.py
- FOUND: edge/npu_spike/inspect_model.py
- FOUND: edge/npu_spike/fix_shape.py
- FOUND: tests/test_npu_spike_tools.py
- FOUND: .planning/phases/10-npu-accelerated-perception/deferred-items.md

All claimed commit hashes verified present in `git log`:
- FOUND: e5ea06b (test: RED, Task 1)
- FOUND: 28968f6 (feat: GREEN, Task 1)
- FOUND: 89a7dc4 (test: RED, Task 2)
- FOUND: 85a161d (feat: GREEN, Task 2)
- FOUND: e757f78 (docs: deferred-items log)

---
*Phase: 10-npu-accelerated-perception*
*Completed: 2026-07-26*
