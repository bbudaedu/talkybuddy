# Architecture Research: Genio 520 Edge Integration

**Domain:** Offline edge voice-AI integration into an existing FastAPI+WebSocket voice pipeline
**Researched:** 2026-07-18
**Confidence:** HIGH (grounded in existing codebase `server/*.py`, `.planning/codebase/*`, and 4 hackathon planning docs in `~/hackathon/` that already converged on this design after an internal architecture review dated 2026-07-04)

## Headline Answer

**The FastAPI server runs ON-DEVICE, unmodified in shape.** There is no separate "leaner native runtime" to build. `server/app.py` + `server/pipeline.py` + the existing engine abstractions (`ASREngine`/`EdgeLLM`/`TTSEngine`) **are** the edge runtime — this was the design intent all along (`TALKYBUDDY_PIPELINE_PROFILE=edge`, the ffmpeg-vs-ALSA comment already in `pipeline.py`, the `n_ctx=1024→512` comment already in `llm.py`). The only genuinely NEW server-adjacent piece is **what drives audio in/out** (ALSA instead of a browser) and **two new NPU-backed engine implementations** that slot into the existing pluggable-engine pattern. Everything else is config/threshold changes to existing modules.

This flips the framing of the question: it's not "port the app to a new edge runtime," it's "attach a native audio client + NPU engines to the app that already runs there conceptually."

---

## Standard Architecture

### System Overview (existing + NEW, single diagram)

```
┌───────────────────────────────────────────────────────────────────────────┐
│                    Genio 520 board (Yocto BSP aarch64, 4GB)               │
│                                                                             │
│  ┌─────────────────────────┐        ┌─────────────────────────────────┐  │
│  │  edge/runtime/           │  WS    │   server/app.py (UNCHANGED)      │  │
│  │  audio_io.py  [NEW]      │loopback│   FastAPI + uvicorn :8787        │  │
│  │  (ALSA capture/playback) │◄──────►│   /ws/talk  /ws/live  /api/*     │  │
│  │  local_client.py [NEW]   │127.0.0.1│  (device-role JWT, same as PC)  │  │
│  │  (push-to-talk loop,     │        └───────────────┬───────────────────┘  │
│  │   WS client — replaces   │                        │                     │
│  │   browser Web Audio API) │           ┌────────────┴────────────┐        │
│  └─────────────────────────┘           ▼                         ▼        │
│                                 ┌────────────────┐      ┌──────────────────┐│
│                                 │ VoicePipeline  │      │ NovaSonicSession ││
│                                 │ pipeline.py    │      │ (Path 2, needs   ││
│                                 │ [MODIFIED:     │      │  network — off   ││
│                                 │  RIFF sniff,   │      │  during offline  ││
│                                 │  skip ffmpeg]  │      │  demo, deprior.) ││
│                                 └───────┬────────┘      └──────────────────┘│
│                       ┌──────────────────┼──────────────────┐              │
│                       ▼                  ▼                  ▼              │
│              ┌────────────────┐ ┌────────────────┐ ┌────────────────┐    │
│              │ ASR factory    │ │ EdgeLLM         │ │ TTS factory     │    │
│              │ asr_base.py    │ │ llm.py          │ │ tts_base.py     │    │
│              │ [MODIFIED:     │ │ [MODIFIED:      │ │ [NEW — mirrors  │    │
│              │  new backend]  │ │  n_ctx via cfg] │ │  asr_base.py]   │    │
│              ├────────────────┤ ├─────────────────┤ ├────────────────┤    │
│              │ NPU: tflite    │ │ CPU: llama.cpp  │ │ NPU: tflite     │    │
│              │ SenseVoice/    │ │ Qwen2.5-1.5B    │ │ Piper voice     │    │
│              │ whisper-tiny   │ │ Q4, n_ctx=512   │ │ .tflite [NEW]   │    │
│              │ + Neuron       │ │ (existing,      │ │ + Neuron        │    │
│              │ Delegate [NEW] │ │  config-tuned)  │ │ Delegate [NEW]  │    │
│              │      │ fallback│ │                 │ │      │ fallback │    │
│              │      ▼         │ │                 │ │      ▼          │    │
│              │ CPU: sherpa-   │ │                 │ │ CPU: sherpa-    │    │
│              │ onnx SenseVoice│ │                 │ │ onnx (existing) │    │
│              │ (existing,     │ │                 │ │                 │    │
│              │  unchanged)    │ │                 │ │                 │    │
│              └────────────────┘ └─────────────────┘ └────────────────┘    │
│                       │                                                     │
│                       ▼                                                     │
│              ┌─────────────────────┐                                       │
│              │ SQLite (existing)   │  synced=0 rows queue here while       │
│              │ data/talkybuddy.db  │  offline (network_mode="edge")        │
│              └──────────┬──────────┘                                       │
│                         │ (audio NEVER stored here — see Privacy Boundary) │
└─────────────────────────┼───────────────────────────────────────────────────┘
                          │ background sync (connectivity permitting)
                          │ payload: derived text + scores ONLY, deidentified
                          ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                    Cloud teacher loop (async, best-effort)                  │
│  /api/sync (existing) → diagnose.py [MODIFIED: add native Bedrock          │
│  Converse call] → 4-dim diagnosis JSON → store → teacher.html dashboard    │
│  (existing, unchanged, 5s poll)                                            │
└───────────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities (NEW vs MODIFIED vs UNCHANGED)

| Component | Status | Responsibility | File |
|-----------|--------|-----------------|------|
| `server/app.py` FastAPI server | **UNCHANGED** | Runs on-device exactly as on PC/cloud VM; serves the loopback client, teacher dashboard, and (when networked) `/ws/live` | `server/app.py` |
| `edge/runtime/audio_io.py` | **NEW** | ALSA capture (16kHz mono) + ALSA playback; wraps mic/speaker as Python API | `edge/runtime/audio_io.py` |
| `edge/runtime/local_client.py` | **NEW** | Push-to-talk (GPIO/button) → WS client to `ws://127.0.0.1:8787/ws/talk` using device JWT, same wire protocol as the browser; plays back `tts_audio` via `audio_io` | `edge/runtime/local_client.py` |
| `VoicePipeline._webm_to_wav` | **MODIFIED** | Add RIFF-header sniff: if bytes already start with `RIFF`/`WAVE` (ALSA capture), skip ffmpeg subprocess entirely and read directly via `soundfile`; ffmpeg path preserved for browser webm/opus input | `server/pipeline.py` |
| ASR engine factory | **MODIFIED** | `asr_base.get_asr_engine_class()` gains a third backend id (e.g. `"npu_tflite"`) alongside existing `"sensevoice"`/`"whisper"` | `server/asr_base.py` |
| `NPUSenseVoiceEngine` (or whisper-tiny) | **NEW** | TFLite INT8 model via TFLite runtime + Neuron Delegate; same `available()/transcribe()/_ensure_model()` contract as `SenseVoiceASREngine`; on delegate init/op failure, `available()` returns False and pipeline's fallback chain uses the existing CPU `SenseVoiceASREngine` | `edge/runtime/npu_asr.py` |
| `tts_base.py` factory | **NEW** (mirrors `asr_base.py`) | `TTSEngine` today is a single hardcoded class with no backend switch; introduce the same factory pattern ASR already has, so NPU and CPU TTS become swappable | `server/tts_base.py` |
| `NPUTTSEngine` | **NEW** | TFLite INT8 Piper-voice model via Neuron Delegate; same `available()/synth()` contract as `TTSEngine`; falls back to existing CPU `TTSEngine` (sherpa-onnx) on failure | `edge/runtime/npu_tts.py` |
| `EdgeLLM` (`llama.cpp` Qwen2.5-1.5B Q4) | **MODIFIED (config only)** | Read `n_ctx` from a new `config.LLM_N_CTX` (profile-driven: 512 for edge, 1024 for cloud/dev) instead of the hardcoded `1024` | `server/llm.py`, `server/config.py` |
| `config.py` | **MODIFIED** | Add `LLM_N_CTX`, `ASR_BACKEND` third option, new `TTS_BACKEND` flag, `NPU_DELEGATE_ENABLED` | `server/config.py` |
| `sync_client.push_pending()` | **MODIFIED** | Currently posts raw `student_text` with **no** `guardrails.deidentify()` call and **no** `guardrails.consent_granted()` gate — both are required before this touches the wire per the privacy boundary (see below) | `server/sync_client.py` |
| `diagnose.py` cloud call | **MODIFIED** | Currently calls Anthropic Messages API directly (not Bedrock, not Hermes Agent, despite its docstring). Recommend: add a native `boto3 bedrock-runtime.converse()` path (see Decision Point below) | `server/diagnose.py` |
| `edge/runtime/sync_daemon.py` | **NEW** | Thin wrapper around existing `sync_client.push_pending()` on a timer/asyncio background task; only genuinely new code is the trigger loop, not the sync logic | `edge/runtime/sync_daemon.py` |
| `NovaSonicSession` / `/ws/live` | **UNCHANGED** | Already implemented; simply requires network+AWS creds to work. Not part of the offline loop. First thing cut if time runs short (per PROJECT.md) | `server/nova_sonic.py` |
| Teacher dashboard | **UNCHANGED** | `web/teacher.html`, 5s poll — already reads real diagnosis JSON | `web/teacher.html` |
| `docs/DEPLOY_EDGE.md` | **NEW** | Mirrors `docs/DEPLOY_CLOUD.md`: env vars, ALSA device setup, adb push steps, systemd/init script, NPU delegate toggle | `docs/DEPLOY_EDGE.md` |
| `edge/deploy/` | **NEW** | adb/Yocto flashing notes, `.env` template for edge profile, systemd unit or init script | `edge/deploy/` |
| `edge/models/` | **NEW** | `.tflite` INT8 ASR/TTS artifacts, tokens files; GGUF LLM weight (or symlink to top-level `models/`) | `edge/models/` |

**Why this NEW/MODIFIED split matters for the roadmap:** the true "new architecture" surface is small — one audio I/O client, one pipeline fast-path branch, two new engine backends following an existing factory pattern, and two config/privacy fixes already flagged as tech debt in `.planning/codebase/CONCERNS.md`. Nothing about `app.py`'s routing, the WebSocket contract, the degradation chains, or the DB schema needs to change.

---

## Recommended Project Structure

```
talkybuddy/
├── server/                        # UNCHANGED shape; targeted edits only
│   ├── app.py                     # unchanged — runs as-is on-device
│   ├── pipeline.py                # MODIFIED: RIFF-sniff fast path in _webm_to_wav
│   ├── config.py                  # MODIFIED: LLM_N_CTX, TTS_BACKEND, NPU_DELEGATE_ENABLED
│   ├── asr_base.py                # MODIFIED: 3rd backend id "npu_tflite"
│   ├── tts_base.py                # NEW: factory mirroring asr_base.py
│   ├── llm.py                     # MODIFIED: n_ctx reads config, not hardcoded
│   ├── sync_client.py             # MODIFIED: deidentify() + consent_granted() gate added
│   └── diagnose.py                # MODIFIED: native Bedrock Converse call added
├── edge/                          # NEW top-level — all edge-only artifacts (LOCKED layout)
│   ├── runtime/
│   │   ├── audio_io.py            # ALSA capture (16kHz mono) + playback
│   │   ├── local_client.py        # push-to-talk loop; WS client to loopback /ws/talk
│   │   ├── npu_asr.py             # TFLite ASR + Neuron Delegate engine
│   │   ├── npu_tts.py             # TFLite TTS + Neuron Delegate engine
│   │   └── sync_daemon.py         # background timer calling sync_client.push_pending()
│   ├── models/
│   │   ├── sensevoice-int8.tflite # (or whisper-tiny.tflite if SenseVoice conversion fails)
│   │   ├── tts-int8.tflite
│   │   └── *.tokens.txt
│   └── deploy/
│       ├── flash_yocto.md         # BSP flashing notes
│       ├── edge.env.example       # env template (mirrors docs/DEPLOY_CLOUD.md's table)
│       ├── talkybuddy-edge.service # systemd unit (or init script if Yocto lacks systemd)
│       └── adb_push.sh
├── docs/
│   ├── DEPLOY_CLOUD.md            # existing, unchanged
│   └── DEPLOY_EDGE.md             # NEW — mirrors DEPLOY_CLOUD.md structure
├── models/                        # existing PC-prototype model dir, untouched
└── web/                           # existing browser client, untouched (still used for
                                    # teacher dashboard + optional PC-side demo fallback)
```

### Structure Rationale

- **`edge/` is additive, not invasive.** Every file under it is either brand new or a thin adapter; nothing in `edge/` requires forking `server/`. This keeps the existing PC/cloud prototype's test suite green throughout the 12-day sprint — a hard requirement given there's no time to re-validate two divergent codebases.
- **`edge/runtime/` holds the only code that *replaces* browser behavior** (ALSA instead of Web Audio API/MediaRecorder). Everything downstream of the WebSocket boundary (ASR→scaffold→LLM→TTS→DB) is reused verbatim.
- **`edge/models/` is separate from `models/`** because the artifacts are format-different (`.tflite` INT8 vs `.onnx`/`.gguf`) and because keeping them physically separate makes it trivial to `rsync`/`adb push` only the edge subset to the board without dragging PC-only files.
- **`edge/deploy/` mirrors the intent of `docs/DEPLOY_CLOUD.md`** (env vars table, startup command, account/token flow) but for the on-device profile — hence the paired `docs/DEPLOY_EDGE.md`.

---

## Architectural Patterns

### Pattern 1: Loopback WebSocket Client Replaces Browser (not a new server)

**What:** The on-device "ears and mouth" (`edge/runtime/local_client.py`) is a WebSocket *client* connecting to `ws://127.0.0.1:8787/ws/talk` with a device-role JWT (the `device:GENIO-520-X992` account already seeded per `docs/DEPLOY_CLOUD.md` §5), sending binary WAV frames + `{"type":"audio_end"}` exactly like `web/*.js` does today, and playing back the JSON `tts_audio` response through ALSA instead of the browser `<audio>`/Web Audio queue.

**When to use:** Whenever the physical form factor (doll/board with mic+speaker, no screen, no browser) needs to drive the same server logic a browser drives today.

**Trade-offs:**
- ✅ Zero changes to `app.py` routing, auth, or the WS wire contract — the full state machine (semi-duplex lock, ASR→scaffold→LLM→TTS chain, DB write, directive refresh, guardrails) is reused for free.
- ✅ The same server binary can *simultaneously* serve the loopback client (offline demo) and a remote browser (teacher dashboard, or a phone on the same LAN for judges to watch a live transcript) — useful for the demo's "鏡頭 4 教師閉環" beat without any new plumbing.
- ⚠️ Adds ~1-3ms of loopback TCP overhead vs. an in-process function call — negligible against the KPI targets (TTS first-audio <300ms, round-trip <1.5-2s).
- ⚠️ Requires the FastAPI/uvicorn process to actually be running and healthy before `local_client.py` starts — sequence this in the systemd unit (`After=network.target`, but no network dependency needed since it's loopback).

**Example (conceptual):**
```python
# edge/runtime/local_client.py
async def run_loop():
    ws = await connect(f"ws://127.0.0.1:8787/ws/talk?token={DEVICE_TOKEN}")
    while True:
        await wait_for_button_press()
        wav_bytes = audio_io.capture_until_release()   # 16kHz mono WAV
        await ws.send(wav_bytes)
        await ws.send_json({"type": "audio_end"})
        async for msg in ws:
            event = json.loads(msg)
            if event["type"] == "tts_audio":
                audio_io.play(base64.b64decode(event["audio_b64"]))
            if event["type"] == "idle":
                break
```

### Pattern 2: NPU Engines Slot into the Existing Pluggable-Engine Contract

**What:** `server/asr_base.py`'s `get_asr_engine_class()` factory + the `available()/transcribe()/_ensure_model()` contract already exist precisely to support backend swapping (`sensevoice` vs `whisper`). Add a third backend rather than inventing a new abstraction. TTS currently lacks this factory (`TTSEngine` is hardcoded in `app.py`'s singleton); introduce `tts_base.py` as a **direct structural copy** of `asr_base.py`'s pattern before adding the NPU TTS engine, so the same "swap by config, degrade gracefully" story applies to both.

**When to use:** Any new inference backend (NPU delegate, a future ASR/TTS model) that must not break the existing degradation chain guarantee.

**Trade-offs:**
- ✅ Preserves the codebase's one hard invariant: `available() == False` → pipeline transparently tries the next engine → scaffold is always the final floor. NPU op-fallback risk (flagged in every one of the 4 hackathon docs as the #1 technical unknown) is absorbed by this same mechanism — no new fallback logic needs to be invented.
- ✅ Matches `.planning/codebase/CONCERNS.md`'s own recommendation pattern ("ASR Engine Multi-Backend Switching via Feature Flag") — the fix for that fragility (log which backend was attempted and why it failed) should be applied to the NPU engines from day one, not retrofitted.
- ⚠️ The internal architecture review (`~/hackathon/說說學伴_二開選型與架構建議書.md`, 2026-07-04) found real prior-art evidence that MediaTek's public forums report Whisper-decoder→TFLite conversion failures and `apusys` memory errors, and that MDLA has no Python API (only C/C++) — meaning the NPU engine's `_ensure_model()` may need a small C-extension/subprocess shim rather than a pure-Python TFLite Interpreter call. Budget for this explicitly; do not assume `tflite-runtime` + `Interpreter(experimental_delegates=[...])` "just works" on Neuron Delegate the way it does on generic Android NNAPI.
- ⚠️ SenseVoice's non-autoregressive architecture has **no public precedent** for NPU conversion (per the same review) — whisper-tiny/base is the safer INT8/TFLite conversion target if SenseVoice conversion stalls; keep both as candidate `.tflite` sources and pick whichever survives Day-1 op-compatibility testing.

**Example (conceptual, following existing `SenseVoiceASREngine` shape):**
```python
# edge/runtime/npu_asr.py
class NPUSenseVoiceEngine:
    def available(self) -> bool:
        try:
            import tflite_runtime.interpreter as tflite  # or full tensorflow
        except Exception:
            return False
        # ... check .tflite file exists + delegate loads without op errors
    def transcribe(self, wav_path: str) -> tuple[str, float]:
        # same (text, confidence) contract as SenseVoiceASREngine.transcribe
        ...
```

### Pattern 3: Structural Privacy Boundary — Audio Never Persists, Only Derived Text Crosses the Wire

**What:** This is **already true today**, not a new invariant to build: `pipeline.py` writes ASR audio to a `tempfile`, transcribes it, and calls `os.unlink(wav_path)` in a `finally` block before the turn even finishes (`server/pipeline.py:159-171`). `store.add_interaction()` only ever persists `student_text`, `ai_response_text`, `scores`, `latency_ms` — never audio bytes. `TurnResult.tts_wav` is an in-memory `bytes` object returned to the caller and never written to disk by the pipeline.

**What's missing (2 concrete gaps to close for M2):**
1. `sync_client.push_pending()` posts `store.list_interactions()` rows to `/api/sync` **without** calling `guardrails.deidentify()` on `student_text` first. `guardrails.deidentify()` already exists and is already used by `cloud_llm.py` before its own network call — the same call needs to be added to `sync_client.py`'s payload construction.
2. `sync_client.push_pending()` has **no `guardrails.consent_granted()` check** at all — it will happily sync even if consent was revoked, because consent is currently only checked at the *live conversation* layer (`pipeline.py:228`), not the *background sync* layer. This is exactly the "consent gate 缺口" the milestone context calls out (PROJECT.md: "順帶收斂 G1 consent 缺口").

**When to use:** Every new code path that touches `sync_client`, a future `sync_daemon.py`, or any endpoint that forwards device data to the cloud teacher loop.

**Trade-offs:** None — this is a pure hardening addition; deidentify() is already proven low-risk (used in the existing cloud LLM path) and consent_granted() is a one-line guard.

---

## Data Flow

### Offline Loop (primary — no network required, must never block on network)

```
[GPIO/button press] → edge/runtime/audio_io.capture_until_release()
    ↓ 16kHz mono WAV bytes (native, no ffmpeg)
[edge/runtime/local_client.py] → WS send binary + audio_end → ws://127.0.0.1:8787/ws/talk
    ↓ (loopback — same process, same machine)
[server/app.py ws_talk handler] → VoicePipeline.run_turn_audio()
    ↓
[pipeline._webm_to_wav — MODIFIED] → RIFF sniff → skip ffmpeg → soundfile read directly
    ↓
[ASR factory] → try NPU tflite engine (Neuron Delegate) → on failure/op-fallback →
                CPU SenseVoiceASREngine (existing, unchanged)
    ↓ (text, confidence)
[scaffold.respond()] → rule-based reply + scores (existing, unchanged, unbreakable floor)
    ↓
[LLM chain] → network_mode == "edge" → CloudLLM SKIPPED entirely (no consent check
              needed to reach it — it's simply not in `engines` list) → EdgeLLM
              (llama.cpp, n_ctx=512) → scaffold fallback if EdgeLLM also fails
    ↓ reply text
[TTS factory] → try NPU tflite TTS (Neuron Delegate) → on failure →
                CPU TTSEngine sherpa-onnx (existing, unchanged)
    ↓ WAV bytes
[store.add_interaction(synced=False)]  ← queues here; never touches network
    ↓
[server WS response: tts_audio event] → loopback → edge/runtime/local_client.py
    ↓
[edge/runtime/audio_io.play()] → speaker
```

**Total new network dependency in this loop: zero.** This is what makes the "flip to airplane mode mid-conversation" demo beat (鏡頭 2) trivially reliable — it was already the architecture's invariant on PC (`network_mode` gates every cloud call site), the edge port just needs to *default* `network_mode="edge"` (already done via `config.default_network_mode()`) and *never construct* a `CloudLLM`/`CloudTTS`/Nova-Sonic call in that mode (already the pipeline's existing behavior).

### Cloud Teacher Loop (async, best-effort, derived-data-only)

```
[edge/runtime/sync_daemon.py — timer, e.g. every 60s or on network-up event]
    ↓ calls existing sync_client.push_pending()
[sync_client.py — MODIFIED: add guardrails.consent_granted() gate +
 guardrails.deidentify(student_text) before payload construction]
    ↓ HTTPS POST {device_id, seq, student_text(deidentified), scores} — NEVER audio
[cloud VM /api/sync (existing)] → dedupe by (device_id, seq) → store
    ↓
[diagnose.py — MODIFIED: native boto3 bedrock-runtime.converse() call,
 see Decision Point below] → 4-dim diagnosis JSON (fluency/vocabulary/grammar/
 pronunciation-proxy) → store.add_diagnosis()
    ↓
[teacher.html — existing, unchanged, 5s poll] → radar chart + trend + directive
    ↓ (background, next session)
[profile.build_profile() + lesson.build_lesson() — existing, unchanged]
    ↓ companion_directive cached in VoicePipeline._directive
[next offline turn — directive injected into EdgeLLM prompt, still fully offline]
```

**Key invariant carried forward unmodified:** raw audio never leaves the device — it never even leaves the current turn's stack frame. Only text derived from ASR output and numeric scores cross the wire, matching PROJECT.md's constraint verbatim ("兒童語音不出裝置；只上傳衍生文字/分數").

---

## Decision Point for Roadmap: Hermes Agent vs. Direct Bedrock Converse

PROJECT.md's milestone context lists **"雲端非同步教師閉環：Hermes Agent + Bedrock 產出四維診斷"** as a target feature, and marks it explicitly as **"Pending（範圍風險待 roadmap 排序）"** in the Key Decisions table — i.e., not yet locked.

The project's own prior architecture review (`~/hackathon/說說學伴_二開選型與架構建議書.md`, dated 2026-07-04, cross-referencing 6 research reports) **rejected Hermes Agent** for this milestone, with concrete findings:
- Hermes Agent's actual architecture is a single-user desktop assistant (Electron app, per-machine profile), not a multi-tenant "one agent per student" backend service — running it for N students means N processes, which is unbudgeted ops complexity for a 12-day sprint.
- The current codebase's `diagnose.py` **already** calls a cloud LLM directly (Anthropic Messages API, via `anthropic_relay`) for its non-mock path — not Bedrock, not Hermes. Its docstring's claim of "mock Hermes Agent + Bedrock Claude" is aspirational/stale, not implemented.
- The review's recommendation: skip the Hermes Agent runtime entirely, add a native `boto3 bedrock-runtime.converse()` call (with `toolChoice` for structured diagnosis JSON output) as a peer/replacement path in `diagnose.py`, alongside the existing rule-based mock fallback (already present, already tested, do not touch).

**Recommendation for this research:** implement the lower-risk direct-Bedrock path as the M2 baseline (this closes the "無原生 Bedrock Converse 後端，僅 relay" gap already logged in `ROADMAP.md` Phase 3, and genuinely uses AWS Bedrock as several evaluators may check for). Treat Hermes Agent as an explicit stretch/cut item, not a dependency for the "teacher closed-loop" demo beat — the mock diagnosis path already produces a fully-formed JSON the dashboard can render, so the cloud call is an enhancement, not a blocker, consistent with every degradation-chain pattern already in this codebase.

---

## Memory-Budget-Driven Component Layout (4GB Genio 520)

Sourced from `~/hackathon/說說學伴_技術SPEC_v2.md` §4 and `28天決賽MVP規劃書.md` §4.1 (peak estimate, cross-checked across both docs):

| Component | Landing Zone | Estimate | Status |
|-----------|--------------|----------|--------|
| OS + resident services (Yocto) | — | ~600MB | fixed cost, not tunable |
| Python/FastAPI/uvicorn runtime | CPU | ~300–500MB | existing, unchanged |
| ASR (NPU tflite INT8) or CPU SenseVoice fallback | NPU/CPU | 80–150MB | NEW (NPU) / existing (CPU fallback) |
| LLM weights (Qwen2.5-1.5B GGUF Q4) | CPU | ~1.1GB | existing, unchanged |
| LLM KV cache (**n_ctx=512**, down from 1024) | CPU | ~150–400MB (lower bound after ctx cut) | MODIFIED (config change only) |
| TTS (NPU tflite or CPU sherpa-onnx Piper voice) | NPU/CPU | 150–300MB | NEW (NPU) / existing (CPU fallback) |
| **Peak total** | | **~2.6–3.1GB** | **4GB feasible, headroom required** |

**Layout implications for build order:**
1. **`n_ctx=512` must land before any on-device LLM testing** — this is a pure config change (`config.LLM_N_CTX`), zero code risk, and directly buys back ~150-250MB of headroom vs. the PC-prototype default of 1024. Do this first, on Day 0, before touching hardware.
2. **Never load both NPU and CPU variants of the same engine simultaneously.** The fallback pattern (Pattern 2 above) must *release* the failed NPU model's memory before falling back to CPU, not keep both resident. Lazy-loading (already the existing pattern in every engine class) naturally avoids this as long as the NPU engine's `_ensure_model()` doesn't get called speculatively alongside the CPU one — the ASR/TTS factories should select **one** backend at startup based on `config.NPU_DELEGATE_ENABLED` + a one-time Day-1 compatibility probe, not race both at runtime.
3. **`mmap` loading for the GGUF weights** (llama.cpp supports this natively via `Llama(..., use_mmap=True)`, which is the library default) keeps the 1.1GB weight file from being fully resident if the OS needs to reclaim pages — already the default behavior, just don't override it.
4. **Do not add Llama-Breeze2-3B or BreezyVoice as the primary edge models** (both docs flag these explicitly as memory-budget-breaking, Apache-2.0/MediaTek "quality upgrades unrelated to scoring" — the scoring rubric's "國產晶片加分" is tied to the **chip platform**, not model choice, per both `SPEC_v2.md` §2 and `決賽評分對照與demo腳本.md` §2). Keep Qwen2.5-1.5B Q4 as locked.

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Building a Separate "Edge Server" or Rewriting `app.py`

**What people might do:** Assume the constrained device needs a stripped-down, hand-rolled server (e.g., a bare asyncio loop without FastAPI) because "4GB is tight."
**Why it's wrong:** FastAPI/uvicorn's memory footprint (~300-500MB, already in the budget table above) is a fixed, already-measured cost that every other component in the budget already assumes is present. Rewriting it buys no memory headroom and reintroduces every state-machine bug the existing `pipeline.py` has already fixed (semi-duplex lock, degradation chain ordering, directive-refresh background task, guardrails wiring).
**Instead:** Deploy the existing `server/app.py` unmodified; spend the saved engineering time on the NPU engine spike, which is the actual unproven risk.

### Anti-Pattern 2: Racing NPU and CPU Fallback at Runtime Instead of Selecting Once at Startup

**What people might do:** Try the NPU engine on every single turn, falling back to CPU inline (`try NPU; except: try CPU`) to "always get the best available accelerator."
**Why it's wrong:** Given the 4GB budget's razor-thin headroom, keeping *two* ASR (or TTS) model families warm simultaneously (NPU delegate session + CPU sherpa-onnx recognizer) risks OOM under load, and per-turn probing adds latency variance that directly threatens the <2s single-turn KPI.
**Instead:** Probe NPU delegate compatibility **once**, at process startup (or via a Day-1 standalone compatibility-test script that's part of `edge/deploy/`), and pin the engine selection for the session via `config.NPU_DELEGATE_ENABLED`. Only fall back to CPU automatically if the NPU engine's `_ensure_model()` fails at startup — not per-turn.

### Anti-Pattern 3: Gating the Offline Demo on NPU Working

**What people might do:** Sequence the Genio 520 bring-up so that "get NPU ASR/TTS working" blocks "get the offline conversational loop working on real hardware."
**Why it's wrong:** Per the internal architecture review, NPU op-compatibility for both Whisper-family and SenseVoice-family architectures on Neuron Delegate has **no confirmed public precedent** and known failure modes (decoder conversion failures, `apusys` memory errors). If this becomes a blocking dependency, the entire demo (which the scoring rubric weights far higher: 完成度 15% + 應用性 20% + 可行性 20% all depend on *a working offline demo existing at all*, vs. +2 max for the chip bonus) is at risk.
**Instead:** Build and validate the full offline loop on **CPU-only engines first** (both already exist and work today on PC — `SenseVoiceASREngine` + `TTSEngine` + `EdgeLLM`). Treat NPU wiring as a strictly additive, time-boxed spike (per the existing risk table's own stop-loss: "3-5 天停損") layered on top of an already-working, demo-ready CPU baseline.

### Anti-Pattern 4: Skipping the Consent/Deidentify Gate on the Sync Path Because "It's Just Numbers"

**What people might do:** Assume `sync_client.push_pending()` is already privacy-safe because it never sends audio.
**Why it's wrong:** `student_text` (the ASR transcript) can itself contain a child's spoken name, address fragments, etc. `guardrails.deidentify()` exists precisely to strip this before any cloud call — but `sync_client.py` currently bypasses it, and has no `consent_granted()` check at all, unlike every other cloud call site in the codebase.
**Instead:** Route every `sync_client` payload construction through `guardrails.deidentify()` + gate the entire sync attempt behind `guardrails.consent_granted()`, matching the pattern already established in `cloud_llm.py` and `pipeline.py`.

---

## Integration Points

### External Services / SDKs

| Service/SDK | Integration Pattern | Notes |
|---------|---------------------|-------|
| TFLite runtime + Neuron Delegate (NeuroPilot Public, no NDA) | New `edge/runtime/npu_*.py` modules load `.tflite` via `tflite_runtime.Interpreter(experimental_delegates=[NeuronDelegate])` or MediaTek's public wrapper | Day-1 risk: some ops may have no Python binding (MDLA reportedly C/C++-only per internal review) — validate before committing to Python-only implementation; may need a thin native shim |
| llama.cpp (`llama-cpp-python`) | Already integrated (`server/llm.py`); only the `n_ctx` constructor arg changes | `.planning/codebase/CONCERNS.md` already flags this dependency needs a C++ toolchain at install time — confirm aarch64/Yocto has gcc/clang available, or vendor a prebuilt aarch64 wheel |
| AWS Bedrock (`boto3`) | New direct `bedrock-runtime.converse()` call in `diagnose.py`, parallel to (not replacing) the existing rule-based mock | Region should be Taiwan/AP per `SPEC_v2.md` §7 data-residency requirement; existing `config.BEDROCK_REGION` env var already exists (used by Nova Sonic) — reuse it |
| ALSA (`pyalsaaudio` or `sounddevice` backed by PortAudio-ALSA) | New `edge/runtime/audio_io.py` | Must confirm ALSA device nodes exist under whichever Yocto BSP image ships; if only PulseAudio/PipeWire is present, adjust library choice accordingly — verify on Day 1 hardware bring-up, don't assume |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| `edge/runtime/local_client.py` ↔ `server/app.py` | Loopback WebSocket (`ws://127.0.0.1:8787/ws/talk`), same JSON+binary framing as browser client | No new protocol; reuses device JWT already seeded (`device:GENIO-520-X992`) |
| ASR/TTS factories ↔ NPU/CPU engine implementations | Existing `available()/transcribe()/synth()` duck-typed contract | No interface change; this is the entire point of the pattern |
| `VoicePipeline` ↔ audio input | Currently: bytes in, WAV path out, via `_webm_to_wav()`. MODIFIED: adds a zero-subprocess fast path for pre-formatted WAV | Backward compatible — browser/webm callers unaffected |
| `sync_client.py` ↔ cloud `/api/sync` | HTTPS POST, existing endpoint, existing `(device_id, seq)` idempotency key | MODIFIED: payload must pass through `guardrails.deidentify()` first; call must be gated by `guardrails.consent_granted()` |
| `diagnose.py` ↔ Bedrock | New boto3 call, parallel path to existing Anthropic-relay call | Both can coexist behind a config flag during a transition/A-B period if useful for risk reduction |
| Android 14 bring-up (pre-BSP) ↔ Yocto BSP (target) | **Not an architectural integration point** — treat as a disposable smoke-test track only | Per PROJECT.md: "先測 Android 14，因 4GB/效能預期改燒官方 Yocto BSP 映像。" Running the same Python/FastAPI stack under stock Android 14 would require Termux or a proot/chroot userland — do not invest engineering time making the edge runtime "Android-native"; the moment BSP flashing succeeds, standard Linux userland (identical to the PC prototype's OS assumptions) applies and this constraint disappears |

---

## Suggested Build Order (dependency-ordered, demo-hook-first, 12 days)

This sequencing front-loads the elements the scoring rubric weights heaviest (完成度 15% + 應用性 20% + 可行性 20%, all contingent on *a working offline demo existing*) before the elements worth less but higher-risk (國產晶片 NPU bonus, max +2).

1. **Day 0 (no board needed, parallel with hardware shipping/BSP prep):** Land all pure-config/pure-refactor changes on the existing PC prototype, fully covered by the existing test suite: `config.LLM_N_CTX` (profile-driven default), `pipeline.py` RIFF-sniff fast path, `tts_base.py` factory (mirroring `asr_base.py`) with the existing `TTSEngine` as its default backend (no behavior change yet), `sync_client.py` deidentify+consent gate. Stand up `edge/` folder skeleton + `docs/DEPLOY_EDGE.md`. Zero hardware risk, closes 4 of the "known tech debt" items from `CONCERNS.md` in one pass.
2. **Day 1 (board bring-up, blocking):** Flash official Yocto BSP (do not sink time into a general-purpose Android 14 rewrite — Android 14 is a disposable smoke test only, see Integration Points above). Confirm `server/app.py` boots via uvicorn on-device, confirm ALSA device nodes for mic+speaker exist.
3. **Day 2–4 (the existential demo hook — CPU-only, must complete before anything NPU-related):** Build `edge/runtime/audio_io.py` + `local_client.py`. Wire against the **existing, already-proven** CPU engines (`SenseVoiceASREngine`, `TTSEngine` sherpa-onnx, `EdgeLLM` with `n_ctx=512`). DoD: press button, speak a bilingual mixed sentence, hear the scaffolded reply, entirely offline, on real Genio 520 hardware.
4. **Day 4–5 (network-cut demo beat — nearly free once step 3 works):** Verify airplane-mode behavior is a non-event (it already is, architecturally — `network_mode="edge"` never constructs a cloud call). Add a visible/audible indicator (LED, log line) for the live-demo narration. Rehearse the beat.
5. **Day 5–8 (NPU spike — time-boxed, additive, stop-loss enforced):** Convert SenseVoice or whisper-tiny to `.tflite` INT8; wire `edge/runtime/npu_asr.py` + `npu_tts.py` through Neuron Delegate; measure op-fallback ratio and latency delta vs. the CPU baseline from step 3. Wire into the ASR/TTS factories as an additional backend, selected once at startup (Anti-Pattern 2). **Hard stop-loss: if not demonstrably working by Day 8, ship the CPU-only baseline from step 3** — it is already a complete, scoreable demo.
6. **Day 6–10 (cloud teacher loop — parallel track, runs on PC/cloud VM concurrently with board work in steps 2–5):** Add native `boto3 bedrock-runtime.converse()` call to `diagnose.py` (per the Decision Point above — skip Hermes Agent). Wire `edge/runtime/sync_daemon.py` as a timer around the existing `sync_client.push_pending()`. Confirm teacher dashboard renders real (not seeded/mock) data end-to-end at least once.
7. **Day 9–11 (Nova Sonic Path 2 — lowest priority, first cut if behind schedule):** No new architecture required; simply requires the on-device server to have network connectivity and AWS credentials provisioned when demoing this beat. Per PROJECT.md, this is explicitly the first thing to drop if time is short.
8. **Day 11–12 (rehearsal + fallback):** Record the 60-90s backup demo video (mandated by every hackathon doc as the single-point-of-failure mitigation for live hardware). Run at least two full rehearsals including the network-cut beat.

**Critical-path dependency chain:** step 1 → step 2 → step 3 is strictly sequential and non-negotiable (nothing else can be demoed without it). Steps 5, 6, 7 are parallelizable against each other and against step 4 once step 3's DoD is met.

---

## Sources

- `server/app.py`, `server/pipeline.py`, `server/asr_base.py`, `server/asr_sensevoice.py`, `server/llm.py`, `server/tts.py`, `server/config.py`, `server/guardrails.py`, `server/sync_client.py`, `server/cloud_llm.py`, `server/store.py`, `server/nova_sonic.py` — direct codebase read, 2026-07-18 (HIGH confidence, primary source)
- `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/CONCERNS.md` — codebase-mapping agent output, 2026-07-18 (HIGH confidence, curated)
- `.planning/PROJECT.md`, `.planning/ROADMAP.md` — milestone context and locked decisions, 2026-07-18 (HIGH confidence, authoritative for scope)
- `docs/DEPLOY_CLOUD.md` — existing deploy doc used as the mirroring template for `docs/DEPLOY_EDGE.md` (HIGH confidence, primary source)
- `~/hackathon/說說學伴_技術SPEC_v2.md` (2026-07 revision) — hardware spec, memory budget, dual-mode state machine, telemetry schema (HIGH confidence, project-internal spec, cross-checked against codebase)
- `~/hackathon/說說學伴_28天決賽MVP規劃書.md` — MVP scope cuts, memory budget breakdown, phased milestone precedent (HIGH confidence, project-internal, superseded in places by the 二開建議書 below but memory/scope figures corroborated across both)
- `~/hackathon/說說學伴_決賽評分對照與demo腳本.md` — scoring rubric weighting, demo shot-list, chip-bonus clarification (HIGH confidence, project-internal, directly drives build-order prioritization)
- `~/hackathon/說說學伴_二開選型與架構建議書.md` (2026-07-04 internal architecture review, cross-referencing 6 prior research reports) — Pipecat/Hermes Agent rejection rationale, NPU op-compatibility risk findings, confirms current codebase already reflects its recommendations (HIGH confidence, most rigorously cross-checked of the four docs; superseding source for the Hermes Agent decision point)

---
*Architecture research for: TalkyBuddy v2 — Genio 520 決賽 Edge MVP integration*
*Researched: 2026-07-18*
