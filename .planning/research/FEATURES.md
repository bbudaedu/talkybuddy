# Feature Research: v2 Genio 520 決賽 Edge MVP — NEW Edge Features

**Domain:** Offline on-device voice AI demo (hackathon final, children's language-learning toy) on MediaTek Genio 520
**Researched:** 2026-07-18
**Confidence:** MEDIUM — technical facts on NeuroPilot/Neuron Delegate/llama.cpp are cross-checked against official docs (MEDIUM/HIGH); hackathon demo-staging conventions ("network-cut" choreography, judge perception heuristics) are synthesized from general offline-AI-demo practice and product judgment, not a single authoritative source (LOW-leaning, treated as informed reasoning — flagged per claim below)

> Scope note: this file covers ONLY the 6 NEW v2 target features (edge offline loop, network-cut moment, NPU perception, Nova Sonic online value-add, cloud teacher closed-loop, offline privacy). Existing Path 1 / Path 2 / cloud LLM / B1-B3 teaching / cloud deploy features are already shipped — see `.planning/REQUIREMENTS.md` v1 section, not re-litigated here.

## Feature Landscape

### Table Stakes (Judges Assume These Exist Once You Say "Offline Edge Demo")

Missing any of these makes the demo read as fake, broken, or "just a cloud device with a chip sticker."

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| End-to-end offline turn loop (ASR→LLM→TTS, zero network calls) that actually completes a turn on stage | This is the entire premise of the milestone ("若淪為音箱則全案失敗"); a single hung turn or silent fallback-to-scaffold kills 可行20%+完成15% | HIGH | Reuses Path 1 `StreamingTurnManager`/`pipeline.py` turn shape, but must run the **edge-only leaves of the existing degrade chain** (SenseVoice-int8 local, EdgeLLM llama.cpp, sherpa-onnx local TTS) with **no cloud leaf reachable** — needs a real "airplane" test, not just code review of the fallback path |
| Visible on-screen "OFFLINE / EDGE" state indicator during the loop | Judges cannot see network state; without a badge/label the offline claim is unverifiable from the audience seats | LOW | Small UI addition to `web/*.js`; ties into the same network-status signal used for the network-cut moment (see below) — build once, reuse twice |
| Bilingual (中英) scaffolded read-along text displayed in sync with speech | This is the literal stated feature ("中英雙語鷹架帶讀"); without on-screen text the judges only hear audio and can't verify content/timing | MEDIUM | `scaffold.py` + `lesson.py` already provide B1/B3 scaffolding content for cloud path; must confirm the **edge LLM prompt** still emits parseable bilingual scaffold text at `n_ctx=512`, not just a bare English sentence |
| Turn latency inside a "conversational" budget the audience will tolerate (target ~3–6s per turn on stage, not 15–20s) | A silent 15s pause during a live demo reads as "broken," even if it eventually answers; live audiences are unforgiving of dead air | HIGH | Direct function of CPU-only llama.cpp generation speed on Cortex-A78 + tflite/NPU ASR+TTS latency; **the single biggest technical risk in this milestone** — budget short, capped-length generations (few words) deliberately, not open-ended chat |
| Session continues to work after the wake word / gate the same way Path 1 already does (tap-to-toggle or KWS) | Reviewers/press already saw Path 1/Path 2 wake flows in earlier milestone videos; an edge mode with a totally different interaction model reads as a different, less-polished product | LOW-MEDIUM | Reuse existing `WakeController`/Porcupine tap-to-toggle client flow; do not invent a third wake UX for edge-only time budget reasons |

### Differentiators (This Is Where the Judging Score Actually Moves)

These map directly onto 應用20% / 創意20% / 國產晶片+2% and are the parts of the demo judges will remember and talk about after the pitch.

| Feature | Value Proposition | Complexity | Notes |
|---------|--------------------|------------|-------|
| **The network-cut moment**: presenter visibly disconnects the device from network mid-demo (Wi-Fi toggle / unplug / airplane mode) and the device keeps holding a full bilingual conversation | This is the single highest-leverage "creativity + feasibility" beat in the whole milestone — it's the moment that makes "edge AI" a *felt* claim instead of a marketing slide; general practice in offline-AI hackathon demos is exactly this kind of live-disconnect proof (see Sources) | MEDIUM | De-risk with (a) a **manual, presenter-triggered kill switch** (adb/local script that force-flips `TALKYBUDDY_PIPELINE_PROFILE`/network flag) as the primary trigger rather than relying on live auto-detection of a flaky venue Wi-Fi/captive-portal state, (b) a rehearsed **before/after UI badge flip** (☁ Online Nova Sonic → 📴 Offline Edge) as visual confirmation, (c) full rehearsal under real venue conditions (unstable congress Wi-Fi is itself a demo risk even before you pull it) |
| Live A/B "NPU on vs NPU off" toggle showing a visible latency/perceptibility difference | "有感" (perceptible) is explicitly the pass/fail bar in PROJECT.md; a static claim ("we used the NPU") is weak, a live before/after timing comparison is a differentiator that directly serves the 國產晶片+2% bonus and 創意20% | MEDIUM | Needs the Neuron Delegate integration to already exist and a simple stopwatch/log overlay — do this **after** the NPU path is proven stable, not as the first thing built |
| Real (not templated) short LLM generation on-device, narrated live as "this sentence was never pre-written" | Distinguishes from "a Bluetooth speaker with pre-recorded TTS clips," which is exactly the failure mode PROJECT.md warns against ("若淪為音箱則全案失敗") | MEDIUM | llama.cpp + Qwen2.5-1.5B-Q4 already integrated in `server/llm.py` per codebase; work is tuning prompt/output length + `n_ctx=512` validation, not new integration |
| Cloud teacher dashboard shows a diagnosis appearing shortly after the on-stage session ends, narrated as "here's what the teacher sees the moment we reconnect" | Closes the story loop from child-facing demo to teacher/parent value; ties 應用20% (real pedagogical use case) to the edge story instead of leaving it as a separate feature | LOW-MEDIUM | Dashboard, diagnosis, and 5s poll already exist (`diagnose.py`, `store.py`, `teacher.html`); only new work is the edge→cloud **opportunistic sync of derived text/scores** once connectivity returns |
| Framing NPU perception + CPU generation as an explicit architecture diagram/label during pitch ("感知在 NPU、生成在 CPU、國產晶片跑滿載") | Judges scoring 國產晶片+2% and 可行20% want to see *why* the architecture choice is smart (NPU handles the ops it's good at; weak CPU is protected from the heavy ASR/TTS math), not just "it runs" | LOW | Pure pitch/communication work, near-zero engineering cost, disproportionate scoring return — do not skip in favor of only demoing |

### Anti-Features (Sounds Good, Will Sink a 12-Day Sprint)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|------------------|-------------|
| Full duplex barge-in on the edge offline loop (interrupt mid-TTS like Path 1's `SpeechGate`/Silero VAD) | Feels inconsistent not to have it, since Path 1 already has it | Edge CPU is already the demo's tightest resource budget (Qwen2.5-1.5B generation + tflite ASR/TTS all competing for the same weak Cortex-A78 cores + 4GB RAM); adding continuous VAD-while-speaking on top risks starving generation and causing the exact "long silent pause" failure that kills the demo | Ship a simple **turn-based** edge loop (wait for full utterance → respond) for M2; log barge-in-on-edge as an explicit v2.x follow-up, not a hackathon deliverable |
| Automatic-only network-cut detection (no manual override) | Feels more "magical"/impressive if it "just knows" the network dropped | Real venue networks are unpredictable (captive portals, DHCP lease behavior, flaky APs) — if the auto-detector doesn't fire cleanly on cue, the demo's signature moment either doesn't visibly change or worse, silently breaks mid-sentence in front of judges | Manual presenter-triggered switch as primary; auto-detection as a secondary/backup signal for the UI badge, never as the sole gate for correctness |
| On-device (edge, phoneme-level) pronunciation scoring | Natural-seeming extension of the existing cloud-side `pronunciation.py`/wav2vec2 pipeline — "why not just run it on-device too?" | Explicitly locked out-of-scope by the user (28-day MVP de-scope list, reaffirmed in PROJECT.md); wav2vec2 phoneme CTC is exactly the kind of heavy model that competes with LLM generation for the same 4GB/CPU budget with the worst ROI of any of the candidate edge features | Keep pronunciation assessment fully on the existing cloud/Nova Sonic route; edge loop gives at most an LLM-narrated qualitative comment ("good try, listen again"), not a score |
| Real-time WebSocket push for the teacher dashboard (replacing 5s polling) | Feels like "obviously better" UX and a natural companion to the "closed loop" story | Already explicitly deferred (`DASH-01`, Out of Scope in REQUIREMENTS.md); building new push infra in a 12-day sprint competing with the edge NPU work is exactly the kind of scope creep the milestone explicitly warns against | Keep 5s polling; if freshness is a concern for stage narration, trigger a manual dashboard refresh at the right beat instead of building push infra |
| Multi-device / multi-Genio-520 sync during the demo | Looks more "production-ready" | Explicitly out of scope (SYNC-01, deferred); adds a second device, second network path, and a whole new failure surface with zero judging-criteria upside for a single-demo-unit final | Single device, single student session; narrate multi-device as "roadmap," don't build it |
| Trying to run Nova Sonic S2S *as the network-cut fallback path* (i.e., using Nova Sonic online and edge loop as literal same-conversation continuation) | Seems elegant — one continuous conversation that seamlessly "downgrades" from Nova Sonic to edge mid-sentence | Nova Sonic (Path 2) and the edge loop (Path-1-like) are architecturally different pipelines (different wake words, different turn models, different prompt/session state); attempting true session handoff between them in 12 days is a research project, not a demo feature, and risks breaking both individually | Present as two **sequential, clearly bounded** demo beats: (1) "online, here's Nova Sonic" → (2) presenter cuts network → (3) "now watch the same board, offline" starts a **fresh** edge turn. The discontinuity is fine — the *board* staying alive is the point, not literal conversation continuity |

## Feature Dependencies

```
[Offline edge turn loop]
    ├──requires──> [Local ASR already shipped: sherpa-onnx SenseVoice-Small int8]  (existing, ASR-01)
    ├──requires──> [Local TTS already shipped: sherpa-onnx TTS]                    (existing, edge leaf of TTS-01 chain)
    ├──requires──> [Edge LLM already shipped: llama.cpp + Qwen2.5-1.5B via llm.py] (existing, LLM contract)
    ├──requires──> [n_ctx=512 revalidation for Genio 520]                          (v2 backlog EDGE-02, NEW work)
    ├──requires──> [TALKYBUDDY_PIPELINE_PROFILE=edge forces edge-only leaves]      (existing hook, NEW: verify no silent cloud fallback)
    └──enhances──> [Bilingual scaffold display]                                    (reuses scaffold.py/lesson.py content)

[NPU-accelerated perception]
    ├──requires──> [ASR/TTS models converted to .tflite INT8]                     (NEW — not yet done)
    ├──requires──> [Neuron Delegate / NeuroPilot ncc-tflite DLA compile step]      (NEW — MediaTek toolchain, no NDA needed)
    ├──requires──> [Genio 520 board access + Yocto/Android 14 flash]               (NEW hardware bring-up, NB-shareable work)
    └──enhances──> [Offline edge turn loop]  (perception ops accelerated; CPU stays free for LLM generation)

[Network-cut demo moment]
    ├──requires──> [Offline edge turn loop] (must actually work standalone first)
    ├──requires──> [Online value-add path: Nova Sonic S2S] (need something to cut FROM)
    ├──requires──> [Visible network-state UI badge] (NEW, small — shared with table-stakes offline indicator)
    └──enhances──> [Whole-demo narrative]; conflicts with——> [attempting literal session handoff between Path 2 and edge loop] (see anti-features)

[Cloud async teacher closed-loop]
    ├──requires──> [Existing diagnose.py + Hermes Agent/Bedrock 4-dim diagnosis]   (existing, TEACH-01 lineage)
    ├──requires──> [Existing teacher.html dashboard + 5s poll]                     (existing, keep as-is)
    ├──requires──> [NEW: edge→cloud opportunistic upload endpoint for derived text/scores only] (NEW, small)
    └──requires──> [Offline privacy: no raw child audio ever leaves device]        (design constraint, closes consent gap G1)

[Offline privacy guarantee]
    ├──requires──> [Edge turn loop keeps PCM buffers device-local, never uploaded]
    ├──enhances──> [Cloud teacher closed-loop] (only derived text/scores cross the network — the thing actually being asked for consent)
    └──relates-to──> [Existing PRIV-01/PRIV-02 de-identification + consent gate] (extends same principle to the new edge path)
```

### Dependency Notes

- **NPU perception requires a genuinely new toolchain step** (`.tflite` → INT8 quantize → `ncc-tflite` → `.dla` compile via Neuron SDK) that does not exist anywhere in the current codebase — this is the least-de-risked, highest-uncertainty dependency in the milestone (no existing spike, no benchmark numbers for this exact board in the codebase or in publicly available sources at time of research). Budget the earliest days of the sprint for a NPU-vs-CPU-fallback go/no-go spike; if `.dla` compile or the delegate's INT8 op coverage is incomplete for SenseVoice/sherpa-onnx's actual op graph, the "algorithmic fallback to CPU" is explicitly designed-for in the milestone plan — but that fallback still must hit the latency budget, so don't discover fallback-triggers-fallback (NPU fails → CPU ASR + CPU TTS + CPU LLM all fighting for the same cores) on demo day.
- **The network-cut moment requires the offline loop to be solid *before* it is dramatized.** Building the disconnect choreography before the underlying edge loop reliably completes a turn wastes rehearsal time on a moment that has nothing real to show.
- **Cloud teacher closed-loop is the cheapest of the four new feature areas** because 90% of it (diagnosis engine, dashboard, DB, 4-dim scoring via Hermes Agent+Bedrock) already exists from the prior milestone; the only genuinely new surface is a small "upload derived text when connectivity returns" endpoint plus wiring the edge session's transcript/score into the same `store.py` shape the dashboard already reads.
- **Offline privacy is a design constraint threaded through both the edge loop and the closed-loop upload**, not a separate feature to build — the acceptance test is "no PCM/audio bytes appear in any outbound network call while `TALKYBUDDY_PIPELINE_PROFILE=edge`," which is also the same constraint that resolves the pre-existing consent gap G1 flagged in PROJECT.md/CONCERNS.md.
- **Existing tech debt directly threatens the offline loop and should be resolved as part of this milestone, not deferred**: the `ffmpeg` subprocess WebM→WAV conversion (blocks Genio 520 porting per CONCERNS.md) and the `n_ctx=1024→512` cut both sit directly on the critical path of "does the edge loop even run on this board," and should be treated as implicit sub-dependencies of the offline-loop table-stakes item above, not separate optional cleanup.
- **Local browser-on-loopback is NOT the same as "online."** Because the existing web frontend talks to the on-device FastAPI/WebSocket server over `localhost`/LAN loopback, the browser UI can keep working through the network-cut moment without any code change — "network-cut" should be staged as cutting the device's *internet/cloud* uplink (Wi-Fi/mobile data to AWS/ElevenLabs/Nova Sonic), not the local browser↔server link. Confirm this explicitly in rehearsal so the team isn't surprised that the UI "still works" and mistakes that for the offline claim failing to register.

## MVP Definition

### Launch With (v1 — the 12-day demo-day minimum)

The absolute floor for "device works offline on stage and the network-cut moment lands":

- [ ] Offline edge turn loop completes a full 聽→想→說 bilingual turn with zero cloud calls, `TALKYBUDDY_PIPELINE_PROFILE=edge` verified with a network sniff/log audit — this is the single feature the whole milestone stands or falls on
- [ ] Turn latency inside a stage-tolerable budget (rehearsed, not just measured in isolation)
- [ ] On-screen network-state badge (☁ Online / 📴 Offline Edge) driven by both auto-detection and a manual override
- [ ] NPU delegate proven for **at least ASR** (perception acceleration on real hardware) with CPU fallback path exercised at least once in testing — this is the minimum credible "國產晶片" proof
- [ ] Network-cut demo choreography rehearsed end-to-end multiple times under venue-like conditions, with the manual kill-switch as primary trigger
- [ ] Cloud teacher dashboard shows a diagnosis derived from the edge session appearing after reconnect (reusing existing diagnose/dashboard, wired to a new small upload endpoint)
- [ ] Offline privacy invariant verified: no raw audio leaves the device in edge mode (closes G1)

### Add After Validation (once the above is solid, remaining sprint days)

- [ ] NPU delegate extended to TTS as well as ASR (if ASR NPU path proves reliable early)
- [ ] Live A/B "NPU on/off" latency toggle as a pitch prop
- [ ] Nova Sonic online path staged and rehearsed as the "before" half of the network-cut moment (already exists — mainly rehearsal/staging work, not new engineering)
- [ ] Polished bilingual scaffold UI (larger text, highlighting, simple animation) beyond functional correctness

### Future Consideration (explicitly deferred, do not build for this milestone)

- [ ] Full barge-in/interrupt on the edge offline loop
- [ ] On-device phoneme-level pronunciation scoring (permanently out of scope per user lock)
- [ ] Teacher dashboard real-time push (DASH-01, deferred)
- [ ] Multi-device / multi-board sync (SYNC-01, deferred)
- [ ] True session handoff between Nova Sonic and the edge loop (research-grade problem, not a 12-day feature)

## Feature Prioritization Matrix

| Feature | Judging-Criteria Fit | Implementation Cost | Priority |
|---------|---------------------|----------------------|----------|
| Offline edge turn loop (ASR→LLM→TTS on-device) | 可行20% + 完成15% (the pass/fail gate) | HIGH | P1 |
| Network-cut demo moment + manual kill-switch | 創意20% + 應用20% (the memorable hook) | MEDIUM | P1 |
| NPU delegate for ASR (perceptible chip acceleration) | 國產晶片+2% + 可行20% | HIGH (new toolchain) | P1 |
| Cloud teacher closed-loop (reuse + small upload endpoint) | 應用20% (real pedagogical value) | LOW-MEDIUM | P1 |
| Offline privacy invariant (no audio leaves device) | 主題25% (closes consent gap G1; core trust story) | LOW-MEDIUM | P1 |
| NPU delegate for TTS | 國產晶片+2% (extra credit) | MEDIUM | P2 |
| Live NPU on/off A/B toggle prop | 創意20% (memorability) | LOW-MEDIUM | P2 |
| Nova Sonic online staging as demo's "before" beat | 應用20% (reuses existing shipped feature) | LOW (rehearsal only) | P2 |
| Full barge-in on edge loop | none directly scored; general polish | HIGH | P3 (defer) |
| Real-time dashboard push | none directly scored; already deferred | MEDIUM-HIGH | P3 (defer) |

**Priority key:**
- P1: Must have — the demo does not credibly work or does not hit the winning hooks without it
- P2: Should have if P1 lands with days to spare — meaningfully raises 創意/國產晶片 scoring
- P3: Explicitly deferred — do not spend sprint time here

## Competitor / Reference-Pattern Analysis

| Pattern | How Others Do It | Our Approach |
|---------|-------------------|--------------|
| Offline voice assistant proof-of-concept demos (e.g. Raspberry-Pi-class fully-offline assistants using Vosk/eSpeak-class local ASR+TTS, demoed with Wi-Fi/data explicitly disabled before or during the walkthrough) | General pattern in the offline-AI hackathon/maker space is to test and demo in airplane mode or with connectivity physically removed, and to narrate the disconnection explicitly as the proof point | Same core pattern, but staged as a **live mid-conversation cut** (online Nova Sonic → offline edge) rather than "always offline," because our differentiator is the *transition*, not just standalone offline capability |
| Commercial voice assistants' offline fallback (e.g., on-device wake-word + limited offline command set, full capability requires cloud) | Typically offline mode is a degraded fallback (basic commands only), not full conversational generation — cloud is where the "smart" answers live | Ours inverts the usual perception: the edge path still does real LLM generation (not just canned commands), which is the point worth calling out explicitly in the pitch as harder/more impressive than typical offline fallbacks |
| Children's language-learning apps (e.g., Duolingo, ELSA Speak class of products) | Almost universally cloud-dependent for both ASR scoring and content generation; offline modes (if any) are pre-downloaded static content, not live generation | Our approach — live generative bilingual scaffolding on-device, offline — has no direct mainstream competitor doing the same at this price/hardware point; this is the actual "creativity" claim worth foregrounding to judges |

## Sources

- [tflite-neuron-delegate (MediaTek-NeuroPilot GitHub)](https://github.com/MediaTek-NeuroPilot/tflite-neuron-delegate) — confirms INT8 support and delegate mechanics (MEDIUM confidence, official repo)
- [Neuron Compiler and Runtime (NeuronSDK) — Genio IoT AI Hub docs](https://genio.mediatek.com/doc/iot-aihub/ai_hub/ai-workflow/neuron-sdk.html) — confirms `.tflite` → DLA compile step via `ncc-tflite` is required for NPU execution (MEDIUM confidence, official docs)
- [Genio 510/700-EVK ML guide (MediaTek gitlab docs)](https://mediatek.gitlab.io/aiot/doc/aiot-dev-guide/master/sw/yocto/ml-guide/ml-g700-evk.html) — same Genio family toolchain pattern (MEDIUM confidence)
- [MediaTek NPU and LiteRT — Google Developers Blog](https://developers.googleblog.com/mediatek-npu-and-litert-powering-the-next-generation-of-on-device-ai/) — confirms MediaTek NPUs are increasingly first-class LiteRT/TFLite targets, including LLM-adjacent work (MEDIUM confidence)
- General llama.cpp / Qwen2.5 ARM CPU throughput discussion ([llama.cpp Discussion #4167](https://github.com/ggml-org/llama.cpp/discussions/4167), [MyAIHardware benchmarks](https://www.myaihardware.com/llama-cpp-benchmarks)) — no exact Genio 520/Cortex-A78 + Qwen2.5-1.5B-Q4 figure found; treat throughput as unverified until measured on real hardware (LOW confidence — flagged, do not plan latency budget purely on these numbers, run an early on-device benchmark spike instead)
- Offline-AI hackathon demo pattern ([Hackster.io — Build Your Own Fully Offline AI Assistant](https://www.hackster.io/news/build-your-own-fully-offline-ai-assistant-f83efb08fbb5), general "test in airplane mode" convention) — LOW confidence (general community practice, not a single authoritative standard; treated as informed synthesis)
- `.planning/PROJECT.md`, `.planning/REQUIREMENTS.md`, `.planning/codebase/CONCERNS.md` (this repository) — HIGH confidence, primary source for existing architecture, tech debt, and locked scope decisions

---
*Feature research for: TalkyBuddy v2 — Genio 520 決賽 Edge MVP*
*Researched: 2026-07-18*
