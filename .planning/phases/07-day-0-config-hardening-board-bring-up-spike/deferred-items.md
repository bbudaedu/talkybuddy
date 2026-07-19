# Deferred Items — Phase 07

Out-of-scope discoveries logged during plan execution (not fixed; scope boundary per executor deviation rules).

## From 07-01 (LLM_N_CTX profile-driven + pipeline RIFF-sniff fast path)

**Pre-existing test failures in `server/streaming/` and `spike/a2_pipecat/` — unrelated to this plan's files.**

- Full-suite run (`.venv/bin/python -m pytest -q`) via `/home/budaedu/hackathon/talkybuddy/.venv`: 11 failed, 317 passed, 6 errors.
- All failures/errors are confined to `server/streaming/tests/*` (barge-in gate, turn manager, VAD, realwire synth, sherpa voice locate) and `spike/a2_pipecat/tests/*` (interruptible synth) — none of these files were touched by 07-01 (which only modified `server/config.py`, `server/llm.py`, `server/pipeline.py`).
- Verified pre-existing (not a regression from this plan): checked out `server/pipeline.py` at the pre-plan commit (`07d0e17`) and confirmed the diff introduced by this plan is additive-only (new `_is_wav_riff`/`WavSpecMismatchError`/fast-path branch); `server/streaming/tests/test_isolation.py` (the one streaming test that does import `server.pipeline`) passes cleanly.
- Likely cause (not investigated further, out of scope): missing/incompatible audio model assets (sherpa voice files) or `pipecat-ai` API drift in this sandbox environment, unrelated to config/LLM/pipeline profile work.
- Recommendation: triage in a phase/plan that owns `server/streaming/` (Path 1 self-hosted streaming barge-in), not Phase 07 Plan 01.

Failing tests (full list):
- `server/streaming/tests/test_barge_in_gate.py::test_speech_emits_barge_in_detected`
- `server/streaming/tests/test_barge_in_gate.py::test_gate_bargein_stops_clean`
- `server/streaming/tests/test_barge_in_gate.py::test_gate_no_bargein_all_sentences`
- `server/streaming/tests/test_realwire_synth.py::test_criterion_a_has_reply`
- `server/streaming/tests/test_realwire_synth.py::test_criterion_b_barge_in_clean_stop`
- `server/streaming/tests/test_run_realwire.py::test_build_processors_shape`
- `server/streaming/tests/test_sherpa_voice_locate.py::test_load_zh_voice_succeeds`
- `server/streaming/tests/test_turn_manager.py::test_no_bargein_synthesizes_all_sentences`
- `server/streaming/tests/test_turn_manager.py::test_bargein_at_second_sentence_stops_clean`
- `server/streaming/tests/test_turn_manager.py::test_transport_vad_frame_no_longer_triggers_bargein`
- `server/streaming/tests/test_vad.py::test_speech_wav_emits_started_then_stopped`
- `server/streaming/tests/test_interruptible_synth.py::*` (3 tests, ERROR)
- `spike/a2_pipecat/tests/test_interruptible_synth.py::*` (3 tests, ERROR)
