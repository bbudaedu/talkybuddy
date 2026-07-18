# Pitfalls Research

**Domain:** Offline on-device voice AI (ASR/LLM/TTS) on MediaTek Genio 520 NPU+CPU, ported from a PC/cloud FastAPI prototype, under a 12-day hackathon-final deadline
**Researched:** 2026-07-18
**Confidence:** MEDIUM (official MediaTek/Genio docs + community threads cross-checked; some llama.cpp/quantization specifics are general ARM knowledge, not Genio-520-specific benchmarks — flagged LOW where so)

## Critical Pitfalls

### Pitfall 1: NPU "success" that is actually silent full CPU fallback ("淪為音箱" root cause #1)

**What goes wrong:**
The team converts SenseVoice/TTS graphs to `.tflite`, wires up the Neuron Delegate, sees the demo run and sounds fine — but one or more operators in the graph are unsupported by the Neuron Delegate, so those ops (or the whole graph) silently execute on CPU instead of NPU. The pipeline still "works," latency may even look acceptable on a short utterance, and nobody notices that the "NPU acceleration" checkbox for the pitch (國產晶片 +2 分, 端側智慧須有感) was never actually true. Then on-stage, under real load (longer utterance, LLM+TTS competing for the same CPU cores), the un-accelerated path is measurably slower or drops quality, and the judges' probing question ("是不是真的跑在 NPU 上？") has no good answer.

**Why it happens:**
TFLite delegates silently fall back per-operator by design — that's the "safe" behavior for correctness, but it means `available()==True` / "it ran" is not evidence of NPU execution. Conversion pipelines (ONNX→TFLite, or PyTorch→ONNX→TFLite for SenseVoice/TTS) frequently hit ops that were never validated for MDLA/APU (custom attention variants, certain normalization ops, dynamic shapes common in streaming ASR).

**How to avoid:**
- Use `ncc-tflite` / `benchmark_model` with `--use_delegate=stable_delegate` (per MediaTek Genio Community guidance) to print exactly which ops fall back, per model, BEFORE demo week.
- Add an explicit runtime check/log line: "NPU ops: X/Y accelerated" surfaced in a debug overlay or stage-side terminal, so the team (and if needed, judges) can see real hardware utilization, not just "it worked."
- Budget for an early spike (Day 1-2) that gets the *smallest possible* SenseVoice-encoder or TTS-vocoder subgraph running end-to-end through Neuron Delegate with a measured NPU-vs-CPU op count — don't wait until the full graph is converted to discover this.
- Prefer the **ONNX Runtime `NeuronExecutionProvider`** path if available (see Pitfall 3) as a lower-conversion-risk alternative/fallback to full TFLite conversion, since sherpa-onnx already ships on ONNX Runtime.

**Warning signs:**
- Conversion "succeeds" with warnings you didn't read.
- No profiling/logging shows per-op device placement.
- Demo latency is suspiciously similar whether `TALKYBUDDY_PIPELINE_PROFILE=edge` claims NPU on or off.

**Phase to address:**
Phase 0/1 spike (NPU perception feasibility spike) — before committing to full model conversion or building downstream features on top of an unverified NPU path.

---

### Pitfall 2: INT8 quantization silently degrading Chinese ASR/TTS quality below the "有感" bar

**What goes wrong:**
INT8 post-training quantization is applied to SenseVoice (or the TTS acoustic model) using default calibration data (often English-centric or generic audio), and Traditional Chinese / Taiwanese-accented speech recognition accuracy or synthesized Mandarin prosody quality drops enough that the demo sounds like a degraded toy — exactly the "淪為音箱" failure the milestone calls out as project-ending. Because English benchmarks (e.g., LibriSpeech, general TTS MOS scores) show "negligible loss" from INT8, the team may assume Chinese quality is equally safe without ever testing it in Traditional Chinese with real classroom-register speech (children's voices, code-switched Chinese/English).

**Why it happens:**
Quantization accuracy loss is model- and *language-distribution*-dependent; calibration datasets used for public INT8 examples are rarely tonal-language / child-voice representative. Nobody budgets time to A/B FP32-onnx vs INT8-tflite output on the actual target utterances (the demo script's lines) until it's too late to re-quantize.

**How to avoid:**
- Quantize using a calibration set built from the ACTUAL demo script phrases (Traditional Chinese + English scaffolding lines), not generic corpora.
- Run a side-by-side A/B (FP32 baseline vs INT8 candidate) specifically on the demo script text/audio, scored qualitatively by a native Traditional-Chinese speaker on the team — not just WER on an English test set.
- Keep the FP32 ONNX (CPU) path as an instantly-switchable fallback (`TALKYBUDDY_PIPELINE_PROFILE`) so if INT8 quality regresses close to demo day, you can degrade gracefully to CPU FP32 rather than ship a "音箱"-quality NPU path.
- Treat "TTS sounds natural in Traditional Chinese" as an explicit acceptance gate with a person listening, not an automated metric alone.

**Warning signs:**
- Quantization script runs with default/example calibration data copy-pasted from a tutorial.
- No native speaker has actually listened to INT8 output on the demo script.
- ASR confidence scores or TTS naturalness are only checked against English test phrases.

**Phase to address:**
Phase covering NPU ASR/TTS conversion — must include a "Chinese quality gate" checkpoint before the phase is marked done, not deferred to demo rehearsal.

---

### Pitfall 3: Assuming TFLite is the only/best path — ignoring ONNX Runtime NeuronExecutionProvider

**What goes wrong:**
The locked plan is "ASR/TTS → `.tflite` INT8 via NeuroPilot/Neuron Delegate." But sherpa-onnx (already used in the existing PC codebase) runs natively on ONNX Runtime, and MediaTek's own Genio Community documentation states Genio 520/720 are "the only Genio platforms with ONNX Runtime NPU acceleration (MDLA `NeuronExecutionProvider`) on by default," built into Yocto since PR4. If the team spends days doing ONNX→TFLite conversion (custom op mapping, shape rewrites, re-export scripts) when a lower-risk `onnxruntime + NeuronExecutionProvider` path may get NPU acceleration with far less conversion surgery, that's wasted runway in a 12-day sprint.

**Why it happens:**
The `.tflite` route was locked as a plan-level decision (probably from MediaTek's general marketing/NDA-free NeuroPilot Public messaging) without a hands-on spike comparing conversion effort of both routes.

**How to avoid:**
- Day 1-2 spike: try `onnxruntime-genio` (or whatever the Genio-Yocto packaged ORT build is called) with `NeuronExecutionProvider` directly on the existing sherpa-onnx SenseVoice ONNX export, before investing in a full TFLite conversion pipeline.
- Only fall back to full TFLite conversion if ORT+NeuronEP proves unsupported for the specific ops SenseVoice/TTS need.
- Document the actual decision (with the spike's timing/quality numbers) as a new ADR — the current PROJECT.md decision is a plan-level assumption, not yet empirically validated.

**Warning signs:**
- Conversion script work balloons past 1-2 days with no NPU-execution win yet.
- Team hasn't tried the ORT NeuronExecutionProvider path at all before committing to TFLite.

**Phase to address:**
Phase 0/1 spike — resolve BEFORE the "NPU perception" phase's implementation work begins; this could change scope/effort for that whole phase.

---

### Pitfall 4: 4GB RAM budget blown by ASR + LLM + TTS coexisting (silent OOM-kill mid-demo)

**What goes wrong:**
Each engine (SenseVoice/TFLite runtime + delegate buffers, llama.cpp Qwen2.5-1.5B Q4 with KV cache, TTS acoustic+vocoder model) is individually within budget, but when all three are resident simultaneously — plus Android OS/services baseline (research shows OS commonly consumes 2-4GB before any AI model loads on a 4GB device) — the process gets OOM-killed by the Android low-memory killer mid-turn, or triggers swap-thrashing that manifests as a multi-second stall on stage. This is worse than a crash because it can look like "hanging" rather than a clean error the audience can forgive.

**Why it happens:**
Each model's memory footprint is measured in isolation during development (e.g., testing ASR alone, then LLM alone) but never load-tested with all three loaded concurrently plus Android's actual background footprint on the exact target ROM (Android 14 stock vs Yocto). The existing codebase already keeps `asr_engine`, `llm_engine`, `tts_engine` as always-resident global singletons (per CONCERNS.md) — a pattern that's fine on PC (ample RAM) but directly collides with the 4GB edge budget.

**How to avoid:**
- Instrument actual peak RSS (not estimated) for the full pipeline (ASR decode + LLM generate + TTS synth back-to-back, worst case overlapping) on real Genio 520 hardware as early as possible — this is not a task to leave for "integration phase."
- Decide explicitly: are all 3 models resident simultaneously, or is there a load/unload strategy (e.g., release ASR buffers before LLM generation starts, release LLM KV cache before TTS synthesis)? Given the existing global-singleton pattern, this requires an actual code change, not just config.
- Reduce llama.cpp `n_ctx` to 512 as already flagged in CONCERNS.md — but note that if peak budget is still tight, prompt+scaffold length needs re-validation at n_ctx=512 (the existing 1024-token prompts likely will not fit).
- Add process-level memory alerting/logging (`/proc/self/status VmRSS` sampled periodically) during rehearsal, so a slow memory creep is caught in testing, not on stage.
- Reserve explicit headroom: target sum of peaks ≤ 3.0-3.2GB (leaving ~800MB-1GB for Android services), matching the milestone's own 2.6-3.1GB estimate — but that estimate must be validated on hardware, not assumed.

**Warning signs:**
- Team has only ever run ASR, LLM, TTS in isolation, never chained in one process under real audio load.
- No memory-pressure or OOM testing done on the actual Genio 520 board (only on dev PC/cloud).
- `n_ctx` still at 1024 later than the phase meant to lower it.

**Phase to address:**
Dedicated "4GB memory validation" milestone-level gate — should run in parallel with/immediately after NPU perception spike, and again after CPU generation integration, and once more with all three chained end-to-end before demo rehearsal phase.

---

### Pitfall 5: llama.cpp aarch64 build not actually using ARM SIMD extensions (silent CPU-generation slowness)

**What goes wrong:**
llama.cpp is cross-compiled or built without the correct ARMv9/Cortex-A78-specific flags (e.g., missing `-march=` NEON/dotprod/i8mm flags, or building a generic aarch64 binary via a container image that doesn't match the target CPU features), so Qwen2.5-1.5B Q4 inference runs 2-4x slower than it could. Combined with only 2 "big" Cortex-A78 cores (Genio 520 is 2×A78 + 6×A55 — the A55 cores are much weaker for LLM decode), naive thread-count settings (e.g., `-t 8` using all cores including slow A55s) can *hurt* rather than help, because heterogeneous big.LITTLE scheduling isn't automatically optimal for llama.cpp's thread pool.

**Why it happens:**
llama.cpp's default build flags target the build machine, not always the deploy target; cross-compiling for Android/Yocto aarch64 without explicitly passing Cortex-A78 tuning flags is an easy miss. Naive assumption "more threads = faster" doesn't hold on big.LITTLE SoCs — A55 cores can bottleneck the thread pool via synchronization overhead rather than helping.

**How to avoid:**
- Explicitly benchmark thread counts 1, 2, 4 (matching the 2 A78 cores, then including A55s) on real hardware and pick empirically, not by defaulting to `nproc`.
- Verify the build actually uses NEON/dotprod/i8mm via `cmake`/build log inspection, not assumption; confirm with `llama-bench` on-device before wiring into the pipeline.
- Measure and report first-token latency (prefill) separately from steady-state tokens/sec — for a short demo utterance, first-token latency (which the audience perceives as "is it thinking or frozen?") matters more than throughput. A 1.5s prefill stall reads as "broken" on stage even if steady-state generation is fine.
- Given n_ctx=512 target, keep prompts/scaffold short and consider caching the system-prompt KV state across turns if the framework supports it, to cut prefill cost per turn.

**Warning signs:**
- No `llama-bench` numbers exist for the actual Genio 520 board — only PC dev numbers.
- Thread count set to a fixed high number "because it worked on PC."
- Demo rehearsal video shows a visible pause before the LLM starts "speaking."

**Phase to address:**
CPU generation (llama.cpp) phase — must include an on-device benchmarking task with explicit go/no-go latency threshold (e.g., "first token < 800ms, else fall back to scaffold-only response") before being marked complete.

---

### Pitfall 6: Android 14 audio stack assumptions don't transfer to Yocto BSP (or vice versa)

**What goes wrong:**
The existing prototype's audio pipeline assumes browser `MediaRecorder` → WebM/Opus → ffmpeg subprocess → 16kHz WAV (a PC/web assumption). Porting to Android 14 requires switching to `AudioRecord`/`AAudio` Java/NDK APIs with `RECORD_AUDIO` runtime permission, while porting to Yocto BSP (the likely fallback per the locked decision) requires switching again to raw ALSA capture — these are two *different* native audio stacks, and code/config written for one (buffer sizes, sample format, permission flow, low-latency mode flags) does not carry over. If the team builds against Android 14 first and then the "4GB/效能預期不足" trigger forces a Yocto re-flash mid-sprint, the audio capture layer may need a full rewrite, not just a recompile.

**Why it happens:**
The locked decision explicitly says "先測 Android 14 → 改燒官方 Yocto BSP" — i.e., a planned pivot mid-project. Teams underestimate how much audio I/O code is OS-specific (permissions model, AudioManager modes, ALSA device enumeration, `arecord`/`aplay` vs Android media APIs) and treat the switch as "just redeploy," not "re-architect the capture layer."

**How to avoid:**
- Design the audio capture layer behind a thin abstraction/interface from day one (already partially true given `TALKYBUDDY_PIPELINE_PROFILE`), so swapping Android AudioRecord for ALSA `arecord`/`libasound` is a plugin swap, not a rewrite of pipeline.py.
- Decide the Android-vs-Yocto go/no-go criteria EARLY (e.g., by end of Day 3-4) based on actual measured performance/memory on Android 14 stock — don't let the decision drift to Day 8+ when there's no runway left to redo the audio layer.
- If going Yocto, validate ALSA capture latency and format (endian, sample rate, mono/stereo) against what ASR/VAD expect — this is a common source of "audio sounds garbled/slow" bugs when porting from a browser-based pipeline.
- Eliminate the ffmpeg/WebM step entirely on-device (already flagged in CONCERNS.md) — direct native capture avoids the whole conversion path, but that means the pipeline's audio-ingestion code path for edge must diverge structurally from the browser-based PC path, so both should be tested independently.

**Warning signs:**
- Audio capture code has Android-specific and Yocto-specific branches unify/tested only in theory, never both actually run on hardware.
- Decision to switch BSP happens later than ~Day 4 of the 12-day sprint.
- `adb`-based deploy/test loop for Android 14 hasn't been rehearsed at all before Yocto becomes "the plan."

**Phase to address:**
Device runtime + deploy pipeline phase (first phase in roadmap) — should explicitly gate "Android 14 go/no-go" decision early with a hard deadline, not let it be an open question through the whole sprint.

---

### Pitfall 7: The network-cut demo moment fails silently instead of failing loudly/gracefully

**What goes wrong:**
The single biggest scripted "wow" moment (斷網橋段) is: judges/presenter cut network, and the system keeps working entirely offline, proving edge intelligence is real. But if any component has a silent cloud dependency the team forgot about (a health-check ping, a telemetry call, an unexpected DNS lookup with a long OS-level timeout, the teacher-dashboard polling loop still firing in the background, cloud LLM/TTS fallback code path still reachable with a long default timeout before falling back to edge), the system doesn't fail cleanly — it hangs for the cloud timeout window (e.g., existing `CLOUD_TTS_TIMEOUT_S=6s` seen in codebase) before silently recovering, which on stage reads as "it's broken" for 6+ seconds, exactly when everyone is watching most closely.

**Why it happens:**
Cloud/edge fallback logic (CloudLLM→EdgeLLM→scaffold; ElevenLabs→edge Piper/sherpa) was designed for resilience under normal network flakiness, not for an instant, deliberate, total cutoff — the timeouts (6-10s per component, per CONCERNS.md's ffmpeg 10s / cloud TTS 6s timeouts) stack if multiple cloud calls happen sequentially rather than in parallel/raced.

**How to avoid:**
- Explicitly test the exact demo action ("kill wifi/ethernet now") against the real running system repeatedly, not just conceptually reason about the fallback chain. Physically disconnect network mid-turn during rehearsal, multiple times, including mid-utterance and mid-generation.
- Shorten all cloud-call timeouts specifically for the demo build (e.g., 1-2s not 6-10s) OR — better — detect network-down proactively (e.g., a fast local network-reachability check before attempting any cloud call) so the "no network" case takes a fast path, not a timeout path.
- Race cloud vs edge calls (`asyncio.wait(FIRST_COMPLETED)`) instead of sequential try/fallback — already flagged as a performance bottleneck in CONCERNS.md for TTS; this becomes a demo-correctness issue, not just a performance one, when network is fully down.
- Explicitly disable/pause any background polling (teacher dashboard refresh, directive-refresh background task) during the offline demo window so no confusing errors surface in logs/UI visible to judges.
- Have a rehearsed verbal fallback line ready for the presenter in case of any hiccup ("讓我再說一次" style recovery), since live demos always carry residual risk even after mitigation.

**Warning signs:**
- Team has never literally unplugged/disabled network and run the full demo script end to end.
- Timeouts for cloud calls are still at PC-development defaults (6-10s) going into demo week.
- No proactive network-reachability check exists; the only signal of "no network" is a cloud call timing out.

**Phase to address:**
A dedicated "network-cut demo hardening" phase, ideally its own phase near the end of the roadmap but with the underlying timeout/race-fallback code changes done earlier (in the phase that builds cloud/edge fallback for the edge pipeline) — this pitfall needs both an early code fix and a late rehearsal gate.

---

### Pitfall 8: 12-day scope creep — building "all three" (Path1 edge + Nova Sonic + teacher loop) in parallel instead of sequenced with a hard fallback order

**What goes wrong:**
PROJECT.md itself flags this tension: "三者都要" (edge offline + Nova Sonic S2S + cloud teacher loop) is explicitly marked "Pending（範圍風險待 roadmap 排序）." If the roadmap doesn't sequence these with unambiguous priority and a real cutline, the team risks spending days 1-8 on all three fronts simultaneously, discovering on day 9-10 that the edge NPU/CPU path (the actual demo-losing risk, per "若淪為音箱則全案失敗") isn't solid, with no days left to fix it because effort was split across Nova Sonic polish and teacher-dashboard features that are explicitly "加值" (nice-to-have) per the same document.

**Why it happens:**
Hackathon team psychology tends to want to protect all invested feature work rather than ruthlessly cut scope; "keep everything a little bit working" feels safer than "commit early to cutting X," even though the latter is what actually protects the must-work core.

**How to avoid:**
- Roadmap should put the edge NPU+CPU perceptible-intelligence path as Phase 1 (riskiest, must-prove first), with an explicit go/no-day checkpoint (e.g., "by Day 5, if NPU-accelerated ASR/TTS + CPU LLM offline loop isn't demoable, STOP all other work and fix this").
- Nova Sonic S2S and teacher-dashboard closed-loop should be structured as clearly separable, independently-shippable phases that can be dropped entirely without touching the core edge phase's code — verify this decoupling explicitly (they already sit behind separate WebSocket endpoints `/ws/talk` vs `/ws/live`, which helps).
- Build the on-stage network-cut demo script itself as an early phase artifact (a literal checklist/script), not a late add-on — this forces the team to know exactly what "done" looks like and catches integration gaps early.
- Explicitly timebox research/spikes (NPU conversion, ORT vs TFLite choice, Android-vs-Yocto decision) to their own short phase with a hard exit date, since these are exactly the areas prone to open-ended rabbit-holing.

**Warning signs:**
- By the midpoint of the 12 days, no working (even ugly) offline edge loop exists yet, but Nova Sonic or teacher dashboard features are already polished.
- No documented "if we're behind on day N, cut Y" decision exists in the roadmap.
- Team members split across all three fronts with none marked "blocking/critical path."

**Phase to address:**
Roadmap structure itself — this is a cross-cutting concern for phase ordering rather than a single phase's job; the roadmapper should sequence Phase 1 = edge core loop (NPU+CPU+network-cut), later phases = Nova Sonic and teacher loop, explicitly marked as droppable.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|-----------------|------------------|
| Skip per-op NPU/CPU placement logging, trust "it ran" | Saves setup time | Can't prove/debug real NPU usage; risks shipping a disguised CPU-only demo | Never for this milestone — logging is cheap, the risk it prevents is project-ending |
| Keep global engine singletons as-is on edge | No refactor needed | Can't unload/reload models to manage the 4GB budget between ASR/LLM/TTS phases of a turn | Only if memory validation on hardware shows headroom is comfortable (unlikely per estimates) |
| Reuse PC-development llama.cpp `n_ctx=1024` prompts unmodified at n_ctx=512 on edge | Faster porting | Prompts truncate silently, degrading LLM output quality — another path to "淪為音箱" | Never — must re-validate all prompts at n_ctx=512 explicitly |
| Test network-cut demo "in theory" (code review only) | Saves rehearsal time | On-stage silent multi-second hang exactly during the highest-visibility moment | Never — this is the single highest-leverage rehearsal for the whole event |
| Convert to `.tflite` without first trying ONNX Runtime NeuronExecutionProvider | Follows the already-locked plan | Wastes days on conversion effort that may be avoidable given ORT NPU support already documented for Genio 520 | Acceptable only after a quick (1-2 day) spike shows ORT+NeuronEP doesn't cover needed ops |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|-------------------|
| Neuron Delegate / NeuroPilot | Assume `.tflite` conversion "succeeding" means NPU acceleration | Verify per-op device placement with `benchmark_model --use_delegate=stable_delegate`; log NPU-vs-CPU op ratio at runtime |
| sherpa-onnx models on Genio | Force ONNX→TFLite conversion when ONNX Runtime + NeuronExecutionProvider may already give NPU accel with less conversion risk | Spike ORT+NeuronEP first; only convert to TFLite if op coverage is insufficient |
| llama.cpp cross-compile for Genio 520 | Build with generic aarch64 flags, no Cortex-A78-specific tuning | Explicitly pass ARM tuning flags, verify with on-device `llama-bench`, tune thread count empirically (don't default to `nproc`, respect big.LITTLE 2×A78/6×A55 split) |
| Android 14 ↔ Yocto BSP audio | Assume audio capture code "just works" across both OSes | Abstract audio capture behind an interface; test AudioRecord/AAudio and ALSA paths independently on real hardware before committing to either |
| Cloud/edge fallback chains (CloudLLM→EdgeLLM→scaffold, ElevenLabs→edge TTS) | Sequential try-then-fallback with PC-era timeouts (6-10s) left unchanged for demo | Race cloud vs edge with `asyncio.wait(FIRST_COMPLETED)`; shorten timeouts or proactively detect network-down for the demo build |
| Teacher dashboard / background refresh tasks | Leave 5s polling and directive-refresh background tasks running during offline demo | Explicitly pause/disable background cloud-dependent tasks during the network-cut demo window |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|-----------------|
| All 3 models (ASR+LLM+TTS) always resident in RAM | Slow/OOM-killed process, especially after multiple turns (fragmentation) | Load/unload strategy or explicit peak-RSS validation on real hardware; consider releasing ASR buffers before LLM decode | Breaks once combined peak nears/exceeds ~3.2GB out of 4GB total |
| llama.cpp using all 8 cores (2×A78+6×A55) indiscriminately | Generation is slower than expected despite "quantized" model | Empirically tune thread count on-device; likely 2-4 threads pinned to A78 cores is optimal, not 8 | Breaks as soon as thread count > available fast cores causes scheduling/sync overhead |
| n_ctx=512 with unmodified 1024-token prompts | Silent truncation, LLM output becomes incoherent or drops instructions | Re-author/shorten prompts specifically for 512-token budget; test with actual scaffold content | Breaks immediately at deployment, not gradually |
| Sequential cloud-then-edge fallback timeouts | Multi-second stalls whenever any cloud call is attempted with no network | Race parallel calls; detect no-network proactively | Breaks exactly during the network-cut demo, the worst possible moment |
| ffmpeg subprocess audio conversion carried over to edge | Added ~100-200ms latency + external binary dependency + failure surface (already flagged in CONCERNS.md) | Eliminate entirely on-device via native ALSA/AudioRecord capture at 16kHz mono, bypass browser MediaRecorder/WebM path | Breaks porting effort as soon as team tries to deploy to Genio 520 without addressing it |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Assuming "edge/offline" mode has zero cloud calls without verifying at the code level | Silent telemetry/analytics calls (as seen in real-world "AuraVoice" case study) undermine both privacy claims and the network-cut demo | Audit all outbound network calls in the edge build (grep for HTTP/WS client usage); explicitly disable any that aren't essential |
| Consent gate bypassed via env var with no audit trail (existing CONCERNS.md finding) | Edge-only mode reachable without recorded parental consent state, risk if teacher/judge Q&A probes privacy claims | Add structured logging when consent flag changes; document exactly how edge mode's "no audio leaves device" claim is enforced in code, ready to explain if judges ask |
| Derived text/scores uploaded to cloud teacher loop without re-verifying de-identification on edge build | Milestone's explicit privacy promise ("只上傳衍生文字/分數") could be broken by leftover PC-era code paths (e.g., accidental audio logging for debugging ASR errors, per CONCERNS.md's "No Audio/Video Recording for Audit Trail" gap) | Explicitly verify no raw audio artifact exists during edge demo session (check `logs/`, temp dirs); enable debug audio logging only in dev config not the demo build |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-------------------|
| Long, silent pause during first-token LLM generation | Judges/audience read "it's frozen" and lose confidence exactly at the "brain thinking" beat | Add a lightweight "thinking" audio/visual cue during prefill latency; ensure first-token benchmark meets an explicit go/no-go threshold |
| No visible signal of NPU-vs-CPU execution during demo | The 端側智慧 accelerator story ("國產晶片") is invisible/unverifiable to judges | Add a small on-screen/console indicator (even a debug HUD) showing "NPU: ON" with op-count, so judges can *see* the differentiator, not just hear a claim |
| Network-cut moment recovers slowly/confusingly | The intended "wow" moment instead reads as a bug | Rehearse repeatedly with real network kill; shorten timeouts; consider a visible "離線模式已啟動" UI cue the moment the cutover is detected, turning the failure mode into a feature moment |
| Fallback chain (SenseVoice→faster-whisper, cloud TTS→edge TTS) triggers invisibly with degraded quality | User/judges hear a worse voice or transcription without knowing why, may misattribute to "the demo is just bad" | Surface fallback state in logs/HUD during rehearsal so team catches unexpected degradations before stage day |

## "Looks Done But Isn't" Checklist

- [ ] **NPU acceleration claim:** Often "done" means "conversion succeeded," not "operators actually execute on NPU" — verify with per-op device placement logs/benchmark_model output, not just a successful `.tflite` load.
- [ ] **Chinese ASR/TTS quality after INT8:** Often tested only against generic/English benchmarks — verify by a native Traditional-Chinese speaker listening to the exact demo-script audio on the INT8 edge build.
- [ ] **4GB memory budget:** Often validated per-model in isolation — verify with all three engines (ASR+LLM+TTS) loaded and exercised back-to-back on the real Genio 520 board, watching peak RSS.
- [ ] **Network-cut demo moment:** Often "should work" based on code review of the fallback chain — verify by physically killing network mid-turn, multiple times, including mid-generation, and timing the actual recovery.
- [ ] **llama.cpp on-device performance:** Often only benchmarked on a PC/dev machine — verify first-token latency and tokens/sec with `llama-bench` on the actual Genio 520 hardware, not extrapolated from other ARM boards.
- [ ] **Android 14 vs Yocto BSP decision:** Often left open "we'll decide later" — verify a concrete go/no-go decision has been made with a specific date, backed by actual measured Android 14 performance/memory data, not assumption.
- [ ] **Audio capture on target OS:** Often only tested via browser `MediaRecorder` (PC path) — verify native AudioRecord/AAudio (Android) or ALSA (Yocto) capture is exercised end-to-end with real hardware mic, not simulated with prerecorded files only.
- [ ] **Scope cutline for Nova Sonic / teacher loop:** Often assumed "we'll figure out what to cut if we run out of time" — verify an explicit, written cutline and go/no-day checkpoint exists in the roadmap before day 1.

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|----------------|------------------|
| NPU conversion fails or silently CPU-falls-back late in the sprint | HIGH | Fall back to CPU-only ONNX Runtime path for ASR/TTS (already proven on PC via sherpa-onnx); reframe NPU story as "NPU-accelerated where supported, CPU fallback for remaining ops" — still true and defensible, but requires honest re-scoping of the pitch, done early not on stage |
| INT8 quality unacceptable close to demo day | MEDIUM | Revert to FP32 ONNX CPU path via existing `TALKYBUDDY_PIPELINE_PROFILE` switch; accept slower/CPU-bound ASR/TTS rather than a degraded-sounding NPU path — the switch itself must have been kept working, not deleted |
| Memory OOM discovered late | MEDIUM-HIGH | Reduce n_ctx further (e.g., 256), drop to a smaller LLM quantization (Q4→Q3 or a smaller model), or serialize model loading/unloading between ASR/LLM/TTS phases of a turn — all require code changes, so discovering this late is costly; better to catch in Phase 1 |
| llama.cpp too slow for acceptable first-token latency | MEDIUM | Shrink prompt/scaffold further, reduce n_ctx, consider skipping LLM generation entirely for the demo's simplest turns and using pre-scripted scaffold responses as an honest "reduced generation" fallback — still better than a frozen stage moment |
| Network-cut demo hangs during rehearsal | LOW-MEDIUM | Shorten timeouts, add proactive network-down detection, race parallel calls — all code-level fixes; if truly late, simplest fix is reducing/removing any remaining cloud-call attempt in the offline demo build entirely |
| Android 14 proves too slow/OOM close to the "先測" checkpoint | HIGH | Execute the already-planned pivot to official Yocto BSP flash — but only survivable if the audio-capture abstraction was built decoupled from Android-specific APIs from day one (see Pitfall 6) |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|-------------------|----------------|
| Silent CPU fallback disguised as NPU success | Phase 1: NPU perception spike | Per-op device placement log/benchmark showing NPU op ratio > 0, checked into rehearsal notes |
| INT8 quantization degrading Chinese ASR/TTS quality | Phase 1/2: NPU ASR/TTS conversion | Native-speaker A/B listening test on demo-script audio, FP32 vs INT8, signed off before phase closes |
| TFLite-only conversion path assumed without evaluating ORT NeuronExecutionProvider | Phase 1: NPU perception spike (before conversion work starts) | Written decision/ADR comparing ORT+NeuronEP vs TFLite conversion effort and coverage |
| 4GB RAM OOM with all engines coexisting | Phase 2: memory validation gate (parallel/after NPU + CPU generation phases) | Measured peak RSS on real Genio 520 hardware with all 3 engines chained, logged and under budget (~3.0-3.2GB) |
| llama.cpp slow / high first-token latency | Phase 2: CPU generation (llama.cpp) phase | `llama-bench` numbers on real hardware; explicit go/no-go threshold (e.g., first token < 800ms) |
| Android 14 vs Yocto BSP porting mismatch | Phase 0: device runtime + deploy pipeline phase | Explicit dated go/no-go decision + working adb deploy loop demonstrated on Android 14 before Day 5 |
| Network-cut demo silent failure | Phase covering cloud/edge fallback logic (code fix) + dedicated demo-hardening/rehearsal phase (late) | Repeated physical network-kill rehearsal with recorded recovery time < 1-2s, run at least 3x successfully |
| 12-day scope creep across 3 fronts (edge/Nova Sonic/teacher loop) | Roadmap structure (cross-cutting, addressed via phase ordering) | Written cutline in roadmap; by the midpoint checkpoint, working (even rough) offline edge core loop exists before Nova Sonic/teacher-loop polish continues |

## Sources

- [MediaTek-NeuroPilot/tflite-neuron-delegate (GitHub)](https://github.com/MediaTek-NeuroPilot/tflite-neuron-delegate) — MEDIUM
- [How to Identify Unsupported Operators — MediaTek Genio Community](https://genio-community.mediatek.com/t/how-to-identify-unsupported-operators-when-ncc-tflite-returns-fail-to-create-tflite-context-error/2020) — MEDIUM
- [Workaround for Unsupported Ops — MediaTek Genio Community](https://genio-community.mediatek.com/t/workaround-for-unsupported-ops/1348) — MEDIUM
- [Accelerating AI on Genio with ONNX Runtime NeuronExecutionProvider — MediaTek Genio Community](https://genio-community.mediatek.com/t/accelerating-ai-on-genio-with-the-onnx-runtime-neuronexecutionprovider/1347) — MEDIUM
- [How to Deploy ONNX Runtime on Genio Platform — MediaTek Genio Community](https://genio-community.mediatek.com/t/how-to-deploy-onnx-runtime-on-genio-platform-and-what-platforms-are-supported/450/2) — MEDIUM
- [Genio 520: Power-Efficient IoT Platform for Edge GenAI (MediaTek official)](https://genio.mediatek.com/genio-520) — MEDIUM
- [Genio 520 Evaluation Kit Quick Start Guide — MediaTek official docs](https://mediatek.gitlab.io/genio/doc/android/qsg/qsg_genio_520_intro.html) — MEDIUM
- [k2-fsa/sherpa-onnx (GitHub)](https://github.com/k2-fsa/sherpa-onnx) — MEDIUM
- [Execution Providers — React Native Sherpa-ONNX docs](https://www.mintlify.com/xdcobra/react-native-sherpa-onnx/guides/execution-providers) — LOW
- [Post-training quantization — Google AI Edge docs](https://ai.google.dev/edge/litert/conversion/tensorflow/quantization/post_training_quantization) — MEDIUM
- [LoRA-INT8 Whisper: Low-Cost Cantonese ASR for Edge Devices (PMC/MDPI)](https://pmc.ncbi.nlm.nih.gov/articles/PMC12431075/) — LOW (Cantonese, not Mandarin/Traditional-Chinese specific, but same quantization-tonal-language concern)
- [Deploy LLM chatbot with llama.cpp using KleidiAI on Arm servers — Arm Learning Paths](https://learn.arm.com/learning-paths/servers-and-cloud-computing/llama-cpu/llama-chatbot/) — LOW (server-class Arm, not mobile SoC, directional only)
- [Performance of llama.cpp on Snapdragon X Elite/Plus — ggml-org/llama.cpp Discussion #8273](https://github.com/ggml-org/llama.cpp/discussions/8273) — LOW (different SoC, directional only)
- [On-Device LLMs: State of the Union, 2026](https://v-chandra.github.io/on-device-llms/) — LOW
- [AAudio | Android NDK — Android Developers](https://developer.android.com/ndk/guides/audio/aaudio/aaudio) — MEDIUM
- Real-world "AuraVoice" offline-smart-speaker telemetry case study (via web search synthesis; original source unclear, treat as illustrative, not verified) — LOW
- Internal project sources: `.planning/PROJECT.md`, `.planning/codebase/CONCERNS.md` (existing tech debt: ffmpeg subprocess, n_ctx=1024, global singletons, espeak-ng-data GPL residue, cloud-fallback timeouts) — HIGH (primary source, direct codebase read)

---
*Pitfalls research for: Offline edge voice AI on MediaTek Genio 520 (NPU+CPU), 12-day hackathon-final port*
*Researched: 2026-07-18*
