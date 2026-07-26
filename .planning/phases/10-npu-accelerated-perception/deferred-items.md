# Deferred Items — Phase 10 (npu-accelerated-perception)

Out-of-scope discoveries logged during execution, per executor scope-boundary rules
(only fix issues directly caused by the current task's changes).

## From 10-01 execution (2026-07-26)

Pre-existing, environment-only test failures/collection-errors found while running the
full suite (`pytest`) to confirm no regression from this plan's changes. None touch
`edge/npu_spike/` or `tests/test_npu_spike_tools.py`; all are caused by dev-sandbox
missing optional dependencies, not by any code change in this plan.

- `tests/test_audio_io.py`, `tests/test_pipeline_wav_fastpath.py` — collection error:
  `ModuleNotFoundError: No module named 'soundfile'` (dev sandbox lacks `soundfile`).
- `tests/test_asr_backend.py::test_sensevoice_opencc_s2twp`,
  `tests/test_asr_backend.py::test_sensevoice_transcribe_converts_to_traditional` —
  `SenseVoiceASREngine._ensure_opencc()` returns `None` (dev sandbox lacks `opencc`).
- `tests/test_nova_sonic.py` (7 tests) — Bedrock/Nova Sonic client tests fail without
  AWS credentials/mocked bidi client available in this sandbox.
- `spike/a2_pipecat/tests/test_interruptible_synth.py` (3 tests) — collection error,
  unrelated pipecat spike dependency gap.

**Verified isolated:** `pytest tests/test_npu_spike_tools.py -x` — 15/15 passed.
`pytest --ignore=tests/test_audio_io.py --ignore=tests/test_pipeline_wav_fastpath.py` —
342 passed, 2 skipped, 9 pre-existing failures (all listed above), 3 pre-existing
collection errors (all listed above). None caused by this plan.
