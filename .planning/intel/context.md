# Context (DOC intel)

12 DOC documents synthesized, keyed by topic. These are implementation
runbooks, checklists, and policy/deployment docs. Where a DOC plan implements a
SPEC design, the SPEC (see constraints.md) is the source-of-truth contract and
the DOC plan is the execution runbook (precedence SPEC > DOC).

## Pipecat integration spike (runbook)
- source: docs/superpowers/plans/2026-07-08-a2-1-pipecat-spike.md
- note: Task-by-task plan for a throwaway spike testing whether Pipecat can host batch SenseVoice STT plus sentence-level interruptible sherpa TTS with barge-in. TDD, spike venv. Implements specs/2026-07-08-a2-1-pipecat-spike-design.md.

## Cloud emotional TTS (ElevenLabs) rollout (runbook)
- source: docs/superpowers/plans/2026-07-08-cloud-emotional-tts.md
- note: Task-by-task plan for cloud ElevenLabs emotional Chinese TTS (CloudTTS) with silent fallback to edge Piper; pipeline TTS routing, WAV 22050Hz/16-bit/mono synthesis, config env vars. Implements specs/2026-07-08-cloud-emotional-tts-design.md.

## A2 real-wiring slice (runbook)
- source: docs/superpowers/plans/2026-07-10-a2-realwire.md
- note: Task-by-task plan wiring the StreamingTurnManager barge-in loop onto Pipecat LocalAudioTransport with real Silero VAD, FunASR/SenseVoice STT, and sherpa-onnx TTS. Streaming venv, canned-WAV acceptance test.

## SpeechGate → turn_manager barge-in seam (runbook)
- source: docs/superpowers/plans/2026-07-11-a2-speechgate-barge-in.md
- note: Plan wiring SpeechGate as an independently tunable barge-in detector (BargeInGate → BargeInDetectedFrame) decoupled from turn-taking VAD. Implements specs/2026-07-10-a2-speechgate-barge-in-design.md.

## Nova Sonic live S2S companion (runbook)
- source: docs/superpowers/plans/2026-07-12-nova-sonic-live-s2s.md
- note: Task-by-task plan adding a full-duplex Chinese live speech-to-speech chat path via Amazon Nova Sonic bidi (/ws/live). NovaSonicSession, live system prompt scaffold, web/live-client.js AudioWorklet, server/app.py, server/config.py. Extensive test cross-refs (test_nova_sonic, test_scaffold_live_prompt, test_app_live).

## Nova Sonic live S2S — manual E2E acceptance checklist
- source: docs/superpowers/plans/2026-07-12-nova-sonic-live-s2s-e2e-checklist.md
- note: Manual end-to-end acceptance checklist for Nova Sonic live S2S mode with automatic fallback to half-duplex. Covers hold-to-talk, hands-free full-duplex, half-duplex fallback, /ws/live and /ws/talk.

## Chat via self-hosted cloud-brain relay (runbook)
- source: docs/superpowers/plans/2026-07-13-chat-cloud-brain-relay.md
- note: Plan routing real-time chat replies through a cloud brain (Anthropic-compatible relay) with fallback to local EdgeLLM then scaffold; deidentify/guardrail. Implements specs/2026-07-13-chat-cloud-brain-relay-design.md.

## Wire B1/B3 teaching content into live (runbook)
- source: docs/superpowers/plans/2026-07-14-live-b1-teaching-wiring.md
- note: Plan wiring B1 teaching content into live hands-free conversations via a new server/lesson.py and a coach-style follow-along loop; app.py ws_live, diagnose, curriculum. Implements specs/2026-07-14-live-b1-teaching-wiring-design.md.

## Nova Sonic hands-free full-duplex (runbook)
- source: docs/superpowers/plans/2026-07-14-nova-sonic-handsfree-fullduplex.md
- note: Phased plan upgrading Nova Sonic live S2S from hold-to-talk to hands-free full-duplex with continuous audio, VAD segmentation, barge-in, and an AEC spike. FastAPI WebSocket, Web Audio API. Implements specs/2026-07-14-nova-sonic-handsfree-fullduplex-design.md.

## Wake-word → hands-free full-voice loop (runbook)
- source: docs/superpowers/plans/2026-07-14-nova-sonic-wake-handsfree.md
- note: Plan for a hands-free full-voice loop: sherpa-onnx KWS wake word starts and an end phrase stops the live conversation. web/live-wake.js coordinator, wake-config API, barge-in, live_s2s mode. Implements specs/2026-07-14-nova-sonic-wake-handsfree-design.md.

## Cloud VM deployment guide
- source: docs/DEPLOY_CLOUD.md
- note: Operational runbook for deploying the cloud VM: environment variables, startup, TLS/WSS reverse proxy, edge doll sync, demo seed accounts, cloud/edge pipeline profiles.

## Privacy & data-minimization policy
- source: docs/PRIVACY.md
- note: Privacy/data-minimization policy for a children's voice-learning toy: audio never persisted, layered guardrails, de-identification before cloud, parental consent, PDPA/COPPA. Cross-ref research/b_axis/B4_隱私與Guardrails.md. Acts as a constraint on all cloud-routing paths (relay, Bedrock, Nova Sonic).
