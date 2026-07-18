# Decisions (ADR intel)

No formal ADR documents were present in this ingest set (0 ADR classifications).
The entries below are decisions embedded inside SPEC design documents. They are
recorded here for downstream traceability with `status: proposed` — none carry
a locked ADR frontmatter, so none are treated as LOCKED. Downstream may promote
any of these to a locked ADR.

## Route A — /ws/live as the pronunciation-assessment main line
- source: docs/superpowers/specs/2026-07-14-pronunciation-assessment-design.md
- status: proposed
- decision: Adopt "route A" — the /ws/live (Nova Sonic) path is the main line into which local acoustic pronunciation scoring (server/pronunciation.py) is wired, tapping the PCM buffer and feeding diagnose scoring. Marked user-confirmed 2026-07-14 in the source.
- scope: pronunciation assessment integration point, /ws/live pipeline

## Cloud emotional TTS via ElevenLabs with silent edge fallback
- source: docs/superpowers/specs/2026-07-08-cloud-emotional-tts-design.md
- status: proposed
- decision: In cloud network mode, route TTS to ElevenLabs emotional Chinese voice with silent fallback to edge Piper. Source records these under a "使用者決策（已確認）" (user-confirmed) section; overall document status reads "設計待實作" (design pending implementation), so not locked.
- scope: cloud-mode voice output, TTS provider selection

## Nova Sonic live S2S decision summary (Phase 1 vertical slice)
- source: docs/superpowers/specs/2026-07-11-nova-sonic-live-s2s-design.md
- status: proposed
- decision: Deliver a Phase 1 full-duplex Chinese speech-to-speech companion via a new /ws/live WebSocket using Amazon Nova Sonic bidi, with scaffolded English coaching prompt and transcript persistence. Source contains a "決策摘要" (decision summary) plus a 2026-07-13 revision section that overrides parts of the original text.
- scope: live conversation path, Nova Sonic S2S adoption
