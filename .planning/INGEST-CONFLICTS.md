## Conflict Detection Report

Ingest set: 30 docs (18 SPEC, 12 DOC, 0 ADR, 0 PRD). Mode: new (bootstrap).
No locked decisions, no UNKNOWN/low-confidence docs, no cross-ref cycles.
All conflicts below are SPEC-vs-SPEC competing architectures for the same
scope — preserved as competing variants (WARNINGS), never merged, never
auto-picked. Downstream (roadmapper / user) must choose per path.

### BLOCKERS (0)

(none)

### WARNINGS (4)

[WARNING] Competing companion "brain" / reply-generation backend
  Found: docs/superpowers/specs/2026-07-08-cloud-llm-bedrock-foundation-design.md routes companion/tutor LLM inference through Amazon Bedrock Converse (CloudLLM.generate drop-in for EdgeLLM).
  Found: docs/superpowers/specs/2026-07-13-chat-cloud-brain-relay-design.md routes the same chat reply path through a self-hosted Anthropic-compatible relay (cloud brain), same CloudLLM contract, different provider.
  Found: docs/superpowers/specs/2026-07-11-nova-sonic-live-s2s-design.md generates replies inside Amazon Nova Sonic S2S (no separate text-LLM step on the live path).
  Impact: Three different providers claim the same _process_text / cloud-reply scope; synthesis cannot pick one without losing intent.
  → Choose the authoritative reply backend per mode (turn-based vs live S2S), or split into distinct requirements before routing.

[WARNING] Competing full-duplex hands-free conversation transport
  Found: docs/superpowers/specs/2026-07-08-a2-2-streaming-turn-manager-design.md (+ realwire and speechgate designs) build a self-hosted full-duplex barge-in pipeline: Pipecat + FunASR STT + sherpa-onnx TTS + Silero VAD (StreamingTurnManager).
  Found: docs/superpowers/specs/2026-07-14-nova-sonic-handsfree-fullduplex-design.md implements hands-free full-duplex via Nova Sonic native VAD and native barge-in (no self-hosted STT/TTS/VAD).
  Impact: Two mutually exclusive implementations of the same "hands-free full-duplex conversation" scope. Doc dates (2026-07-08 → 2026-07-14) suggest the Nova Sonic path is the later direction, but both remain in the set.
  → Confirm which transport is current intent (or whether self-hosted A2 is superseded) before routing.

[WARNING] Competing wake-word engine
  Found: docs/superpowers/specs/2026-07-07-a1-wake-layer-design.md uses Porcupine on-device wakeword (WakeController + MicRouter).
  Found: docs/superpowers/specs/2026-07-14-nova-sonic-wake-handsfree-design.md uses sherpa-onnx KWS wakeword「說說學伴」(live-wake.js coordinator, wake-config sherpa backend).
  Impact: Two different wake engines for the same voice-wake scope; picking a wake-config backend affects the client wiring.
  → Choose one wake-word engine (or scope each to a distinct client mode) before routing.

[WARNING] Competing companion voice-output (TTS) path
  Found: docs/superpowers/specs/2026-07-08-cloud-emotional-tts-design.md routes cloud voice to ElevenLabs emotional Chinese TTS with silent edge Piper fallback.
  Found: docs/superpowers/specs/2026-07-08-a2-2-streaming-turn-manager-design.md uses sherpa-onnx TTS for the streaming turn loop.
  Found: docs/superpowers/specs/2026-07-11-nova-sonic-live-s2s-design.md produces voice natively inside Nova Sonic S2S (no separate TTS engine).
  Impact: Voice-output engine differs by path; the choice is coupled to the transport decision above and cannot be merged.
  → Decide the voice-output engine per conversation mode once the transport variant is chosen.

### INFO (4)

[INFO] Precedence SPEC > DOC applied — design specs are source-of-truth
  Note: Each plan DOC under docs/superpowers/plans/ implements a matching design SPEC under docs/superpowers/specs/ (e.g. a2-1-pipecat-spike, cloud-emotional-tts, a2-realwire, speechgate, nova-sonic-live-s2s, chat-cloud-brain-relay, live-b1-teaching, handsfree-fullduplex, wake-handsfree). No plan contradicts its design; the SPEC is authoritative and the DOC is the execution runbook. No conflict.

[INFO] Intentional degradation chains are not conflicts
  Note: CloudLLM → EdgeLLM → scaffold (chat-cloud-brain-relay-design, cloud-llm-bedrock-foundation-design), ElevenLabs cloud TTS → edge Piper (cloud-emotional-tts-design), and sherpa-onnx SenseVoice with faster-whisper feature-flag fallback (asr-sensevoice-migration-design) are deliberate fallback layers, not competing variants. Preserved as-is.

[INFO] STT model consistent across two runtime hosts
  Note: docs/superpowers/specs/2026-07-04-asr-sensevoice-migration-design.md runs SenseVoice-Small via sherpa-onnx (single-turn path); the A2 streaming specs run the same SenseVoice-Small via FunASRSTTService (Pipecat streaming path). Same model/output, different runtime wrapper — no model conflict.

[INFO] Nova Sonic S2S design evolves chronologically
  Note: docs/superpowers/specs/2026-07-11-nova-sonic-live-s2s-design.md carries a 2026-07-13 revision, and docs/superpowers/specs/2026-07-14-nova-sonic-handsfree-fullduplex-design.md cross-refs and supersedes parts of the 07-11 design; 2026-07-14-nova-sonic-wake-handsfree-design.md builds on the 07-14 handsfree design. Ref graph is a DAG (no cycles). Roadmapper should treat the latest-dated design as current intent within the Nova Sonic path.
