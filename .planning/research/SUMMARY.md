# Project Research Summary

**Project:** TalkyBuddy（說說學伴）— Milestone v2「Genio 520 決賽 Edge MVP」
**Domain:** Offline on-device edge voice AI (ASR/LLM/TTS) ported from an existing PC/cloud FastAPI prototype onto a MediaTek Genio 520 NPU+CPU board, under a ~12-day hackathon-final deadline (決賽 ≈ 2026-07-30)
**Researched:** 2026-07-18
**Confidence:** MEDIUM (official MediaTek/Google docs and the existing codebase are HIGH-confidence; several load-bearing claims — NPU op-fallback rate, Genio-520-specific latency, Yocto-on-Hti-carrier flashability — are unverified until real hardware is in hand)

## Executive Summary

This is a hardware-bring-up-plus-porting project, not a greenfield build: the existing `server/*.py` FastAPI/WebSocket voice pipeline already contains every algorithmic piece needed (ASR/TTS/LLM engine factories with graceful degradation chains, privacy guardrails, teacher-dashboard diagnosis). The milestone's job is to attach a native ALSA audio client and two new NPU-backed engine implementations to an app that already runs, unmodified in shape, on-device — not to rewrite it. The single existential risk is the "淪為音箱" (degrades into a dumb offline speaker) failure mode, which research shows has two concrete root causes: TFLite delegates silently falling back per-operator to CPU while still "working" (so a demo can look successful while the NPU claim is false), and INT8 quantization degrading Traditional-Chinese ASR/TTS quality below an audience-perceptible bar because it was only validated against generic/English calibration data. Both are cheap to prevent (per-op device-placement logging; a native-speaker A/B gate on the actual demo script) but easy to skip under time pressure, and both must be closed inside the phase that does NPU conversion, not discovered at rehearsal.

The recommended approach sequences work by risk and scoring weight, not by feature completeness across all three fronts the milestone nominally wants (edge offline loop + Nova Sonic + teacher closed-loop). Research is unanimous that the roadmap must front-load a CPU-only, fully offline turn loop (proven with already-shipped sherpa-onnx CPU ASR/TTS + llama.cpp CPU LLM) as the load-bearing baseline, treat NPU acceleration as a time-boxed, additive, droppable spike layered on top (with an explicit stop-loss date), and treat Nova Sonic + the cloud teacher loop as separable, independently-shippable, first-to-cut features if the schedule slips. A same-day spike should also resolve two unlocked technical bets before committing engineering days: (1) whether Genio 520's ONNX Runtime `NeuronExecutionProvider` already gives NPU acceleration to the existing sherpa-onnx ONNX graphs directly, making a full ONNX→TFLite conversion pipeline avoidable; and (2) whether the Hti hub G520 (a third-party Android-14-shipping carrier board, not MediaTek's own reference EVK) can actually be flashed with the official Yocto BSP within the sprint, since that flash is the prerequisite for the cleanest architecture (native Python + ALSA, no Android app-process rewrite).

Key risks and mitigations, in priority order: (1) network-cut demo hangs instead of failing cleanly — fixed by proactively detecting no-network and racing/shortening cloud-call timeouts, verified only by physically killing network mid-turn repeatedly in rehearsal, not by code review; (2) 4GB RAM budget blown by ASR+LLM+TTS coexisting — mitigated by never keeping NPU and CPU variants of the same engine resident simultaneously, selecting the backend once at startup, and measuring real peak RSS on hardware early; (3) llama.cpp mis-tuned build flags or thread counts causing 2-4x slower CPU generation than achievable — fixed by using `-march=armv8.2-a+dotprod+i8mm` (not the generic `armv8.7a` flag, which SIGILLs on Cortex-A78) and empirically tuning thread count against the 2×A78+6×A55 big.LITTLE split; (4) scope creep across all three feature fronts eating the runway needed to prove the one thing the demo cannot survive without — fixed by an explicit, written, early cutline in the roadmap itself.

## Key Findings

### Recommended Stack

The stack is largely already locked by the project and confirmed reachable: **MediaTek IoT Yocto BSP v25.1** (first release with native Genio 520/720-EVK support, requires **Genio Tools v1.7+**) as the target OS; **TFLite (LiteRT) + Neuron Stable Delegate** fed by the **NP8 Converter (public, NDA-free)** for NPU-accelerated ASR/TTS; **llama.cpp** built as a native binary (not `llama-cpp-python`) for CPU-only LLM generation; **onnx2tf** as the ONNX→TFLite conversion hop from sherpa-onnx's existing `.onnx` graphs. A parallel B-plan worth a same-day spike: **ONNX Runtime + `NeuronExecutionProvider`**, since Genio 520/720 reportedly ship this on by default and sherpa-onnx already runs on ONNX Runtime — this could eliminate the TFLite conversion detour entirely.

**Core technologies:**
- MediaTek IoT Yocto BSP v25.1 — official Linux (Yocto Scarthgap) image with Genio 520/720-EVK support; keeps the existing Python/FastAPI server portable with minimal changes
- TFLite + Neuron Stable Delegate (NeuroPilot Public, NDA-free) — NPU-accelerated ASR/TTS inference, auto-falls-back to CPU per-op
- llama.cpp native binary (CLI/server), `-march=armv8.2-a+dotprod+i8mm` — CPU-only LLM generation (Qwen2.5-1.5B-Instruct GGUF Q4) on Cortex-A78; do NOT use `llama-cpp-python` wheels or the generic `armv8.7a` march flag (causes SIGILL)
- onnx2tf + NP8 Converter (public) — ONNX→TFLite→MDLA-schedulable conversion pipeline
- ALSA (`arecord`/`sounddevice`) on Yocto, or AAudio/Oboe on Android 14 fallback — native 16kHz mono audio I/O, replacing ffmpeg/WebM entirely

**Critical version/compatibility notes:** NP6 vs NP8 `ncc-tflite` binaries are not interchangeable (Genio 520 = NP8/MDLA5.3 only); `tflite_runtime`/`ai-edge-litert` Python bindings must match the Yocto image's bundled Python ABI; Stable Delegate was marked "experimental" as of Yocto v24.0 — re-verify maturity against v25.1 release notes on Day 1.

**What NOT to use:** MediaTek GAI-Deployment-Toolkit and Neuron SDK "All-in-One Bundle"/`ncc-tflite` DLA path are both NDA-gated and DLA has no Python API (C/C++ only via `neuronrt`) — stay on the public Stable Delegate path. Do not carry ffmpeg subprocess audio conversion onto the board. Do not run torch/torchaudio on-device (disqualifying at 4GB). Do not substitute Breeze-ASR-25/Llama-Breeze2-3B/BreezyVoice as edge models — all blow the memory budget and are explicitly deferred.

### Expected Features

**Must have (table stakes) — the demo fails without these:**
- End-to-end offline turn loop (ASR→LLM→TTS, zero network calls) that actually completes a turn on real hardware, verified with a network sniff/log audit under `TALKYBUDDY_PIPELINE_PROFILE=edge`
- Visible on-screen OFFLINE/EDGE state indicator
- Bilingual (中英) scaffolded read-along text in sync with speech
- Turn latency inside a stage-tolerable budget (~3-6s, not 15-20s)
- Existing wake-word/tap-to-toggle flow reused unchanged (no new interaction model)

**Should have (differentiators — where judging score actually moves):**
- The network-cut moment: presenter visibly disconnects network mid-demo and the device keeps holding a full bilingual conversation offline — the single highest-leverage creativity+feasibility beat
- Live A/B "NPU on vs NPU off" latency toggle as a pitch prop (build only after NPU path is stable)
- Real (non-templated) short LLM generation narrated live as proof against the "Bluetooth speaker with pre-recorded clips" failure mode
- Cloud teacher dashboard showing a diagnosis appearing after reconnect, closing the demo's narrative loop
- Explicit architecture framing ("感知在 NPU、生成在 CPU、國產晶片跑滿載") during the pitch — near-zero engineering cost, disproportionate scoring return

**Defer (explicitly out of scope for this milestone):**
- Full duplex barge-in on the edge offline loop (turn-based only for M2)
- Automatic-only network-cut detection (manual presenter-triggered kill-switch must be primary)
- On-device phoneme-level pronunciation scoring (permanently locked out of scope)
- Real-time WebSocket push for teacher dashboard (keep 5s polling)
- Multi-device/multi-Genio-520 sync
- Literal session handoff between Nova Sonic and the edge loop (stage as two sequential, clearly bounded demo beats instead)

### Architecture Approach

The FastAPI server (`server/app.py`, `pipeline.py`, existing `asr_base.py`/`llm.py`/engine factories) runs on-device unmodified in shape — this is not a "port to a leaner runtime" problem, it's "attach a native audio client + two new NPU engine backends to an app that already runs there conceptually." All genuinely new code lives under a locked `edge/` top-level folder (`edge/runtime/`, `edge/models/`, `edge/deploy/` + a new `docs/DEPLOY_EDGE.md`), kept strictly additive so the existing PC/cloud test suite stays green throughout the sprint.

**Major components:**
1. `edge/runtime/audio_io.py` + `local_client.py` (NEW) — ALSA capture/playback + a WebSocket client replacing the browser, talking to the existing `/ws/talk` endpoint over loopback with zero protocol changes
2. `asr_base.py`/new `tts_base.py` factories (MODIFIED/NEW) — NPU TFLite+Neuron-Delegate engines slot in as a third backend using the existing `available()/transcribe()/synth()` duck-typed contract, so the existing CPU-fallback degradation chain absorbs NPU failure for free
3. `pipeline.py` RIFF-sniff fast path (MODIFIED) — skips ffmpeg entirely for native WAV input, closing the CONCERNS.md-flagged Genio-520-porting blocker
4. `config.py` (MODIFIED) — `LLM_N_CTX` (512 for edge), `TTS_BACKEND`, `NPU_DELEGATE_ENABLED` as profile-driven flags
5. `sync_client.py` (MODIFIED, privacy gap) — currently posts `student_text` with **no** `guardrails.deidentify()` call and **no** `guardrails.consent_granted()` gate; both must be added before any cloud sync — this closes the pre-existing G1 consent gap
6. `diagnose.py` (MODIFIED) — add a native `boto3 bedrock-runtime.converse()` call; recommend skipping Hermes Agent entirely (single-user desktop-assistant architecture, not multi-tenant-ready; already resolved by the project's own internal 2026-07-04 architecture review) — the existing mock/rule-based diagnosis path is a safe fallback, not a blocker

**Suggested build order (dependency-ordered, demo-hook-first):** Day 0 pure-config changes on PC (no hardware risk) → Day 1 board bring-up + Yocto flash → Day 2-4 CPU-only offline loop (the non-negotiable critical path) → Day 4-5 network-cut beat (nearly free once the loop works) → Day 5-8 NPU spike (time-boxed, stop-loss enforced, ships CPU-only baseline if not working by Day 8) → Day 6-10 cloud teacher loop (parallel track) → Day 9-11 Nova Sonic (first cut if behind) → Day 11-12 rehearsal + backup video.

### Critical Pitfalls

1. **Silent CPU fallback disguised as NPU success** — TFLite delegates fall back per-operator by design; "it ran" is not evidence of NPU execution. Avoid by logging per-op device placement (`benchmark_model --use_delegate=stable_delegate`) from Day 1, before building anything on top of an unverified NPU path.
2. **INT8 quantization silently degrading Chinese ASR/TTS quality** — public calibration datasets are rarely tonal-language/child-voice representative. Avoid with an explicit native-Traditional-Chinese-speaker A/B (FP32 vs INT8) on the actual demo script, as a hard acceptance gate before the NPU phase is marked done — not deferred to rehearsal.
3. **Committing to full TFLite conversion without first spiking ONNX Runtime + `NeuronExecutionProvider`** — Genio 520/720 reportedly have this on by default, and sherpa-onnx already runs on ORT; a 1-2 day spike could eliminate the conversion detour. Document the decision as an ADR either way.
4. **4GB RAM budget blown by ASR+LLM+TTS coexisting** — each engine fits in isolation but not necessarily together plus OS baseline. Avoid by measuring real peak RSS on hardware early, never keeping NPU+CPU variants of the same engine resident simultaneously, and selecting the backend once at startup (not per-turn racing).
5. **Network-cut demo moment fails silently (hangs) instead of failing loudly/gracefully** — stacked cloud-call timeouts (6-10s each) read as "broken" on stage at exactly the highest-visibility moment. Avoid with proactive network-down detection, shortened/raced timeouts, and repeated physical network-kill rehearsals — never verified by code review alone.
6. **12-day scope creep across edge/Nova Sonic/teacher-loop fronts** — protecting all three simultaneously risks discovering on Day 9-10 that the one must-work core (edge offline loop) isn't solid, with no runway left. Avoid with an explicit written cutline and go/no-go checkpoint in the roadmap itself, sequenced before Day 1.

## Implications for Roadmap

Based on combined research, suggested phase structure (sequencing chosen to front-load scoring-weighted, highest-risk work and give every later phase an explicit droppable/cut status):

### Phase 0: Config Hardening + Board/Toolchain Bring-Up Spike
**Rationale:** Zero-hardware-risk config fixes can land immediately on the existing PC prototype under the existing test suite; the Yocto-flashability of the third-party Hti carrier board and the TFLite-vs-ORT-NeuronEP choice are the two biggest unknowns that change downstream phase scope — both must be resolved before committing conversion/porting effort.
**Delivers:** `config.LLM_N_CTX` profile-driven default, `pipeline.py` RIFF-sniff fast path, `tts_base.py` factory (mirroring `asr_base.py`, CPU-only backend for now), `sync_client.py` deidentify+consent gate, `edge/` folder skeleton + `docs/DEPLOY_EDGE.md`; a dated go/no-go decision on Android-14-vs-Yocto flashing; a written ADR on ORT-NeuronEP vs TFLite-conversion.
**Addresses:** Offline privacy guarantee (closes G1 consent gap); device runtime + deploy pipeline table-stakes feature.
**Avoids:** Pitfall 3 (TFLite-only commitment without spiking ORT), Pitfall 6 (Android-vs-Yocto decision drifting past Day 4), the n_ctx=1024-carried-over tech-debt trap.

### Phase 1: CPU-Only Offline Edge Turn Loop (the existential demo hook)
**Rationale:** This is the single feature the whole milestone stands or falls on ("若淪為音箱則全案失敗"); must be proven on real hardware using already-validated CPU engines before any NPU work begins, per the architecture research's explicit anti-pattern warning against gating the demo on NPU success.
**Delivers:** `edge/runtime/audio_io.py` + `local_client.py` (ALSA capture/playback + loopback WS client); full 聽→想→說 bilingual turn completing offline on real Genio 520 hardware with `TALKYBUDDY_PIPELINE_PROFILE=edge`; llama.cpp built with correct `-march` flags and empirically-tuned thread count; on-device `llama-bench` numbers with an explicit first-token latency go/no-go threshold.
**Addresses:** Offline edge turn loop, turn latency budget, bilingual scaffold display (all table stakes).
**Avoids:** Pitfall 4 (4GB OOM — validate peak RSS with all engines chained), Pitfall 5 (llama.cpp mis-tuned build/threads), the ffmpeg-carryover tech debt.

### Phase 2: Network-Cut Demo Hardening
**Rationale:** Nearly free once Phase 1 works (the architecture already gates every cloud call behind `network_mode`), but the *choreography and failure-mode hardening* is a distinct, high-rehearsal-cost deliverable that must not be bundled with feature work.
**Delivers:** Manual presenter-triggered kill-switch as primary trigger; shortened/raced cloud-call timeouts; proactive network-down detection; visible network-state UI badge (☁/📴); disabled background polling during the demo window; ≥3 successful physical network-kill rehearsals with recovery time <1-2s.
**Addresses:** The network-cut demo moment (top differentiator).
**Avoids:** Pitfall 7/UX pitfall — silent multi-second hangs during the highest-visibility moment.

### Phase 3: NPU-Accelerated Perception (time-boxed, additive, droppable)
**Rationale:** Highest-uncertainty dependency in the milestone (no benchmark precedent for this exact board), but only worth +2% scoring bonus versus Phase 1's pass/fail weight — must be sequenced as a spike layered on an already-working CPU baseline, with a hard stop-loss date.
**Delivers:** `.tflite` INT8 conversion of SenseVoice (or whisper-tiny as fallback candidate) and TTS via chosen path (TFLite+Neuron Delegate, or ORT+NeuronEP per Phase 0's ADR); `edge/runtime/npu_asr.py`/`npu_tts.py` wired into the existing ASR/TTS factories as an additional backend selected once at startup; per-op device-placement logging showing real NPU op ratio; native-Traditional-Chinese-speaker A/B quality gate (FP32 vs INT8) on the actual demo script, signed off before the phase closes.
**Addresses:** NPU perception (國產晶片 bonus), live A/B NPU toggle prop.
**Avoids:** Pitfall 1 (silent CPU fallback disguised as success), Pitfall 2 (INT8 quality collapse on Chinese audio). **Stop-loss: if not demonstrably working by the mid-sprint checkpoint, ship the CPU-only baseline from Phase 1 — it is already a complete, scoreable demo.**

### Phase 4: Cloud Teacher Closed-Loop
**Rationale:** Cheapest of the remaining feature areas (90% already exists — diagnosis engine, dashboard, DB, 4-dim scoring); runs in parallel with Phases 1-3 on PC/cloud VM concurrently with board work, so it doesn't compete for hardware-bring-up time.
**Delivers:** Native `boto3 bedrock-runtime.converse()` call in `diagnose.py` (skip Hermes Agent per the project's own internal architecture review); `edge/runtime/sync_daemon.py` timer wrapper around existing `sync_client.push_pending()`; end-to-end verification that the teacher dashboard renders real (not mock) data derived from an edge session.
**Addresses:** Cloud async teacher closed-loop, offline privacy invariant (only derived text/scores cross the wire).
**Avoids:** Security-mistake pattern of assuming "just numbers" don't need deidentification — reuses Phase 0's consent/deidentify gate fix.

### Phase 5: Nova Sonic Online Staging + Final Rehearsal (lowest priority, first cut if behind)
**Rationale:** No new architecture required (already fully implemented, just needs network+credentials); explicitly the first thing PROJECT.md marks as droppable if time runs short. Bundling final rehearsal here ensures the whole demo script — including the Phase 2 network-cut beat — gets multiple full run-throughs under venue-like conditions before demo day.
**Delivers:** Nova Sonic path rehearsed and staged as the "before" half of the network-cut moment; 60-90s backup demo video recorded (single-point-of-failure mitigation); at least two full end-to-end rehearsals.
**Addresses:** Path 2 Nova Sonic connected S2S value-add.
**Avoids:** Pitfall 8 (scope creep) — by being sequenced last and explicitly marked cuttable, it cannot compete with Phase 1-3 for runway.

### Phase Ordering Rationale

- Phases 0→1 are strictly sequential and non-negotiable: nothing else can be demoed without a working offline loop, and the board/toolchain unknowns in Phase 0 change the scope of every later phase.
- Phase 2 (network-cut) is placed immediately after Phase 1 rather than at the end, because its underlying code fix (timeout/race logic) is cheap and best done while the offline loop is fresh in mind, even though its rehearsal-heavy verification naturally recurs again in Phase 5.
- Phase 3 (NPU) is deliberately sequenced *after* a working CPU baseline exists, per the architecture research's explicit anti-pattern warning — it is additive, not gating, and carries an explicit stop-loss.
- Phases 4 and 5 are structured as parallel/droppable tracks (already living behind separate WebSocket endpoints `/ws/talk` vs `/ws/live` architecturally, which the research confirms de-risks dropping either independently) so schedule slippage in Phase 3 doesn't cascade into cutting the core offline loop.
- This ordering directly encodes PROJECT.md's own stated tension resolution: "roadmap 將把 demo 勝負手排最前確保可上台，Nova Sonic 為加值、時間不足時第一個可犧牲."

### Research Flags

Phases likely needing deeper research during planning (`/gsd-plan-phase --research-phase <N>`):
- **Phase 0:** Yocto-on-Hti-carrier flashing is genuinely unprecedented (third-party board, not MediaTek's reference EVK) — device-tree/audio-codec adaptation risk is real and undocumented publicly.
- **Phase 3:** NPU op-compatibility for SenseVoice/whisper-tiny on Neuron Stable Delegate has no confirmed public precedent; the Python-vs-C/C++ API question (MDLA reportedly C/C++-only for some paths) needs hands-on validation, not just doc-reading.

Phases with standard patterns (skip research-phase, established patterns exist in-codebase or in official docs):
- **Phase 1:** CPU-only engines (sherpa-onnx, llama.cpp) are already proven at PC scale in this project; porting is a build-flag/tuning exercise, not new algorithm work.
- **Phase 2:** Timeout/race-condition fixes and demo choreography are standard async-Python patterns already partially present in the codebase's degradation chains.
- **Phase 4:** Diagnosis/dashboard/DB already exist and work; only new surface is one boto3 call and one small upload endpoint, both well-documented AWS SDK patterns.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | MEDIUM | Official MediaTek/Google docs cross-checked with community forum reports (several MediaTek-staff-answered threads raise confidence); hardware-specific claims (Stable Delegate maturity on v25.1, exact op coverage) unverifiable until the physical board + NeuroPilot Public account are in hand |
| Features | MEDIUM | Technical facts (NeuroPilot/Neuron Delegate/llama.cpp) are HIGH; demo-staging conventions (network-cut choreography, judge perception heuristics) are informed product judgment/general offline-AI-demo practice, not a single authoritative source |
| Architecture | HIGH | Grounded directly in the existing codebase (`server/*.py` read firsthand) and a prior internal architecture review (2026-07-04) that already converged on this design after cross-referencing 6 research reports |
| Pitfalls | MEDIUM | Official Genio Community docs + MediaTek-staff-answered threads are solid; some llama.cpp/quantization specifics are general ARM knowledge extrapolated to this SoC family, not Genio-520-specific benchmarks |

**Overall confidence:** MEDIUM — the architecture and existing-codebase reuse story is HIGH confidence and low-risk; the two genuinely open bets (Yocto-on-Hti-carrier flashability, and real-world NPU op-fallback rate/latency on this exact board) are unverifiable from documentation alone and are correctly flagged across all four research files as Day-0/Day-1 spikes, not assumptions to roadmap around.

### Gaps to Address

- **No Genio-520-specific latency/WER benchmarks exist anywhere** (llama.cpp tokens/sec, NPU op-fallback ratio, ASR WER on Traditional Chinese) — must be measured on real hardware in Phase 1/3, not assumed from PC dev numbers or other ARM SoCs' figures.
- **Hti hub G520 carrier-board Yocto-flash risk** is the single biggest schedule risk identified across STACK.md and ARCHITECTURE.md — needs a Day-1 spike with an explicit, dated Android-14-fallback decision documented, not left open through the sprint.
- **TFLite vs ONNX Runtime NeuronExecutionProvider** — the milestone's own locked plan (TFLite conversion) may be avoidable; this needs a 1-2 day spike and a written ADR before Phase 3 begins, since it changes that phase's actual scope.
- **Chinese INT8 quality gate has no existing test harness** — the demo-script audio needs a native-speaker-scored A/B process built from scratch; there is no automated substitute for this per the research (WER on English test sets is explicitly called out as insufficient evidence).
- **Hermes Agent vs direct Bedrock is still formally "Pending" in PROJECT.md's Key Decisions table** despite the architecture research's clear recommendation (direct `boto3 bedrock-runtime.converse()`) — this should be resolved as an explicit roadmap decision, not carried forward as an open question into Phase 4 planning.

## Sources

### Primary (HIGH confidence)
- Direct codebase read: `server/app.py`, `pipeline.py`, `asr_base.py`, `asr_sensevoice.py`, `llm.py`, `tts.py`, `config.py`, `guardrails.py`, `sync_client.py`, `cloud_llm.py`, `store.py`, `nova_sonic.py`
- `.planning/PROJECT.md`, `.planning/REQUIREMENTS.md`, `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/CONCERNS.md`
- `~/hackathon/說說學伴_技術SPEC_v2.md`, `~/hackathon/說說學伴_28天決賽MVP規劃書.md`, `~/hackathon/說說學伴_二開選型與架構建議書.md` (2026-07-04 internal review, cross-referencing 6 prior research reports), `~/hackathon/說說學伴_決賽評分對照與demo腳本.md`
- Hti `G520 Mediatek AIoT Module Spec_V1.1.pdf`, `HTIService API Programming User Guide_v0.0.2.pdf`

### Secondary (MEDIUM confidence)
- MediaTek Genio Community threads (staff-answered): NPU-without-NDA path, unsupported-operator identification, ONNX Runtime NeuronExecutionProvider deployment
- genio.mediatek.com official IoT AI Hub docs (Neuron SDK, related resources, NDA-gating matrix)
- Google Developers Blog / LiteRT MediaTek NeuroPilot docs
- onnx2tf (PINTO0309, GitHub), llama.cpp `docs/android.md` (ggml-org, GitHub)
- Google AI Edge post-training quantization docs

### Tertiary (LOW confidence, flagged for validation)
- Single/unresolved community reports (Whisper-decoder TFLite conversion failure, Qwen2.5 `compile_generative.sh` on Genio 520) — used only to corroborate scope exclusions, not as positive recommendations
- General llama.cpp ARM throughput discussions on other SoCs (Snapdragon X Elite, Arm servers) — directional only, no Genio-520 figures exist
- General offline-AI hackathon demo staging conventions ("airplane mode" testing) — informed synthesis, not a single authoritative standard

---
*Research completed: 2026-07-18*
*Ready for roadmap: yes*
