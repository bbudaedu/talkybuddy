# Constraints (SPEC intel)

18 SPEC documents synthesized. Each entry preserves the source's technical
contract. Where two SPECs describe competing architectures for the same scope,
both are preserved here verbatim and the conflict is surfaced in
../INGEST-CONFLICTS.md (competing variants are NOT merged).

## ASR migration to sherpa-onnx + SenseVoice-Small (implementation plan)
- source: docs/superpowers/plans/2026-07-04-asr-sensevoice-migration.md
- type: api-contract
- content: Migrate ASR from faster-whisper to sherpa-onnx + SenseVoice-Small int8 with OpenCC s2twp, keeping whisper as a feature-flag fallback (ASR_BACKEND). Fixed ASREngine interface (available/transcribe/_ensure_model); server/asr backend factory. Global constraints and pinned tech-stack versions the system must honor.

## ASR migration to sherpa-onnx + SenseVoice-Small + OpenCC (design)
- source: docs/superpowers/specs/2026-07-04-asr-sensevoice-migration-design.md
- type: api-contract
- content: Design of ASR engine migration from faster-whisper to sherpa-onnx SenseVoice-Small int8 plus OpenCC, whisper kept as feature-flag fallback. Defines ASREngine interface contract, architecture file-split (server/asr.py, asr_base.py, asr_whisper.py, asr_sensevoice.py), config/data schemas, error-handling and test plans (pipeline.py, app.py, config.py, setup_env.sh, test_pipeline.py, test_asr_backend.py).

## A1 wake layer — Porcupine + tap-to-toggle (implementation plan)
- source: docs/superpowers/plans/2026-07-07-a1-wake-layer.md
- type: api-contract
- content: Add Porcupine voice-wake plus tap-to-toggle push to the browser client feeding the existing single-turn pipeline. Defines WakeController, MicRouter, /api/wake-config endpoint with request/response schemas, and non-functional constraints. FastAPI server + browser client.

## A1 wake layer — Porcupine voice wake + tap-to-toggle (design)
- source: docs/superpowers/specs/2026-07-07-a1-wake-layer-design.md
- type: api-contract
- content: Client-side wake-layer spec: Porcupine on-device voice wakeword plus tap-to-toggle push feeding the existing single-turn pipeline. WakeController state machine, MicRouter, web/index.html client, /ws/talk audio-upload contract, tap-to-toggle push UX. Cross-ref CONTRACTS.md.

## A2-1 Pipecat integration spike (go/no-go design)
- source: docs/superpowers/specs/2026-07-08-a2-1-pipecat-spike-design.md
- type: protocol
- content: Throwaway go/no-go spike verifying Pipecat can host batch SenseVoice STT (FunASRSTTService) plus sentence-level interruptible sherpa-onnx TTS (SherpaInterruptibleTTSService) under programmatic barge-in/interruption, using StubLLMService, without touching _process_text. Spike lives in a2_pipecat.

## A2-2 StreamingTurnManager + barge-in loop (implementation plan)
- source: docs/superpowers/plans/2026-07-08-a2-2-streaming-turn-manager.md
- type: protocol
- content: Full-duplex streaming turn loop in server/streaming/ with Silero VAD barge-in and interruptible sentence-by-sentence TTS. Components StreamingTurnManager, InterruptibleSynth, SpeechGate, ReplySource; Pipecat 1.5.0 pipeline, sherpa-onnx TTS, FunASRSTTService. Defines Consumes/Produces interfaces, function signatures, barge-in concurrency ordering, invariants.

## A2-2 StreamingTurnManager + barge-in loop (design)
- source: docs/superpowers/specs/2026-07-08-a2-2-streaming-turn-manager-design.md
- type: protocol
- content: Server-side full-duplex streaming turn-loop design with barge-in cancellation. Defines component boundaries, interfaces and data flow: StreamingTurnManager, ReplySource, InterruptibleSynth, Silero VAD, TurnResult, Pipecat. References spike/a2_pipecat SPIKE-RESULT and server/streaming modules.

## Cloud emotional TTS (ElevenLabs) minimal landing (design)
- source: docs/superpowers/specs/2026-07-08-cloud-emotional-tts-design.md
- type: api-contract
- content: Route cloud-mode TTS to ElevenLabs emotional Chinese voice with silent fallback to edge Piper. Defines module contracts (server/cloud_tts.py available()/synth()), API endpoint, config schema (server/config.py, server/app.py), WAV 22050Hz/16-bit/mono output contract, error-handling table, and test plan. Contains a user-confirmed decision section; status "設計待實作".

## cloud_llm Bedrock FM foundation, subproject A (implementation plan)
- source: docs/superpowers/plans/2026-07-08-cloud-llm-bedrock-foundation.md
- type: api-contract
- content: Cloud-mode companion/tutor LLM inference routes through Amazon Bedrock Converse with a drop-in EdgeLLM-compatible contract and graceful local fallback. CloudLLM.generate(...) -> str | None, config constants, LLM_CLOUD_PROVIDER flag semantics, 8.0s timeout SLO, guardrails. Targets server/cloud_llm.py, network_mode cloud.

## cloud_llm Bedrock FM foundation, subproject A (design)
- source: docs/superpowers/specs/2026-07-08-cloud-llm-bedrock-foundation-design.md
- type: api-contract
- content: Technical design connecting companion (陪聊) and tutor (導師) agents' cloud inference to Amazon Bedrock Converse API. Architecture, contracts, and B4 safety-floor/guardrails wiring. Touches pipeline._process_text, diagnose.generate_diagnosis, network_mode cloud path. Cross-refs research 16/06/11.

## A2 real-wiring slice — barge-in loop on real mic/speaker (design)
- source: docs/superpowers/specs/2026-07-10-a2-realwire-design.md
- type: protocol
- content: Wire the StreamingTurnManager barge-in loop to real mic/speaker via Pipecat LocalAudioTransport and Silero VAD, with FunASRSTTService and SherpaInterruptibleTTSService. Defines run_realwire.py and the streaming pipeline wiring.

## A2 SpeechGate barge-in threshold seam (design)
- source: docs/superpowers/specs/2026-07-10-a2-speechgate-barge-in-design.md
- type: protocol
- content: Wire SpeechGate into StreamingTurnManager as an independent, tunable barge-in detector via a new BargeInGate processor emitting BargeInDetectedFrame, decoupled from turn-taking Silero VAD. Located in server/streaming.

## Nova Sonic realtime S2S companion, Phase 1 vertical slice (design)
- source: docs/superpowers/specs/2026-07-11-nova-sonic-live-s2s-design.md
- type: protocol
- content: Phase 1 design for a Nova Sonic full-duplex Chinese speech-to-speech companion via a new /ws/live WebSocket, with scaffolded English coaching and transcript persistence. NovaSonicSession, build_live_system_prompt, web/live-client.js AudioWorklet PCM pipeline, bidi protocol, config keys, TDD strategy. Contains a 決策摘要 table and a 2026-07-13 revision that overrides parts of the original.

## Chat reply via self-hosted cloud-brain relay (design)
- source: docs/superpowers/specs/2026-07-13-chat-cloud-brain-relay-design.md
- type: api-contract
- content: Route chat replies through a self-hosted Anthropic-compatible relay (cloud brain) with consent gate, de-identification, guardrails, and cloud→edge→scaffold fallback. Defines CloudLLM contract, anthropic_relay module, pipeline fallback, diagnose.py wiring. Cross-ref server/CONTRACTS.md.

## Wire B1/B3 teaching content into live conversation (design)
- source: docs/superpowers/specs/2026-07-14-live-b1-teaching-wiring-design.md
- type: api-contract
- content: Add server/lesson.py to select teaching material, rewrite the live system prompt (scaffold.build_live_system_prompt) into a coach follow-along loop, and write back diagnosis at live wrap-up to form an adaptive closed loop. Touches server/app.py ws_live, background diagnose, B1/B3 teaching loop.

## Nova Sonic hands-free full-duplex upgrade (design)
- source: docs/superpowers/specs/2026-07-14-nova-sonic-handsfree-fullduplex-design.md
- type: protocol
- content: Upgrade Nova Sonic S2S from hold-to-talk to hands-free full-duplex using native VAD and real barge-in, with echo cancellation (AEC). Touches server/app.py ws_live, server/nova_sonic.py, web/live-client.js, web/index.html. Cross-ref 2026-07-11-nova-sonic-live-s2s-design.md (supersedes parts of it).

## Wake-word → hands-free full-voice loop, Nova Sonic + sherpa KWS (design)
- source: docs/superpowers/specs/2026-07-14-nova-sonic-wake-handsfree-design.md
- type: protocol
- content: Use sherpa KWS wake word「說說學伴」to enter Nova Sonic hands-free full-voice conversation; farewell phrase (matchFarewell) ends and returns to IDLE. Defines live-wake.js coordinator interface, index.html enterLiveMode wiring, wake-config sherpa backend, degradation, and tests. Cross-ref 2026-07-14-nova-sonic-handsfree-fullduplex-design.md.

## Pronunciation assessment (acoustic) (design)
- source: docs/superpowers/specs/2026-07-14-pronunciation-assessment-design.md
- type: api-contract
- content: Local acoustic pronunciation-scoring module server/pronunciation.py (wav2vec2 phoneme model, g2p_en ARPAbet, CTC decode) wired into the /ws/live Nova Sonic pipeline via a PCM tee buffer, feeding diagnose scoring. Embeds a pinned "route A" architecture decision (user-confirmed 2026-07-14; see decisions.md). Cross-refs spike/pron_assess SPIKE-RESULT, PRIVACY.md.
