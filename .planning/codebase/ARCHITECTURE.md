<!-- refreshed: 2026-07-18 -->
# Architecture

**Analysis Date:** 2026-07-18

## System Overview

```text
┌─────────────────────────────────────────────────────────────┐
│              Web Frontend (Vanilla JS + HTML5)               │
│  index.html (Student) | teacher.html (Teacher) | live-*.js  │
│  Web Audio API | Wake Detection | WebSocket Client          │
└───────────────────────────┬─────────────────────────────────┘
                            │
         ┌──────────────────┴──────────────────┐
         │  HTTP + WebSocket (FastAPI)         │
         ▼                                      ▼
┌─────────────────────────────────────────────────────────────┐
│                FastAPI Application Server                    │
│  app.py: HTTP Routes + WS Endpoints                         │
│  ├─ GET / (student page)                                    │
│  ├─ GET /teacher (teacher page)                             │
│  ├─ REST API: /api/status, /api/login, /api/interactions   │
│  ├─ WS /ws/talk (turn-based dialogue)                       │
│  └─ WS /ws/live (real-time S2S via Nova Sonic)             │
└──────────────────┬───────────────────────────────────────────┘
                   │
         ┌─────────┴──────────┬──────────────┐
         ▼                    ▼              ▼
┌──────────────────┐  ┌──────────────────┐  ┌────────────────┐
│  VoicePipeline   │  │  NovaSonicSession│  │  Auth & Guards │
│  pipeline.py     │  │  nova_sonic.py   │  │  auth.py       │
│                  │  │                  │  │  guardrails.py │
│ • ASR stage      │  │ • Real-time S2S  │  │                │
│ • Thinking stage │  │ • Bidi streaming │  │ • JWT verify   │
│ • TTS stage      │  │ • Turn management│  │ • Consent gate │
│ • DB write       │  │ • Barge-in       │  │ • Safety check │
└────────┬─────────┘  └──────────────────┘  └────────────────┘
         │
    ┌────┴─────┬──────────┬──────────┬─────────────┐
    ▼          ▼          ▼          ▼             ▼
┌────────┐ ┌────────┐ ┌────────┐ ┌─────────┐ ┌──────────┐
│  ASR   │ │  LLM   │ │  TTS   │ │ Scaffold│ │ Diagnose │
│Engine  │ │Engines │ │Engines │ │Fallback │ │Assessment│
├────────┤ ├────────┤ ├────────┤ ├─────────┤ ├──────────┤
│ asr.py │ │llm.py  │ │tts.py  │ │scaffold │ │diagnose  │
│         │ │cloud   │ │cloud   │ │.py      │ │.py       │
│• Whisper│ │llm.py  │ │tts.py  │ │         │ │          │
│• Sense  │ │        │ │        │ │• Vocab  │ │• 4-dim   │
│ Voice   │ │• Edge  │ │• Edge  │ │• Safety │ │  scores  │
│• Conf   │ │• Cloud │ │• Cloud │ │• Rules  │ │• Trends  │
│ checks  │ │ (Bedro)│ │(ElevenL)         │ │• Directive
└────────┘ └────────┘ └────────┘ └─────────┘ └──────────┘
    │          │          │
    └──────────┴──────────┴─────────────────────────┐
                                                   │
                                    ┌──────────────┴────────┐
                                    ▼                       ▼
                            ┌──────────────────┐  ┌─────────────┐
                            │  Store (SQLite)  │  │   Profile   │
                            │  store.py        │  │ Lesson Mgmt │
                            │                  │  ├─────────────┤
                            │ • init_db()      │  │ lesson.py   │
                            │ • add_interaction│  │ profile.py  │
                            │ • list_*         │  │ curriculum.py
                            │ • sync ops       │  └─────────────┘
                            │                  │
                            │ Two tables:      │
                            │ - interactions   │
                            │ - diagnoses      │
                            └──────────────────┘
                                    │
                                    ▼
                          ┌──────────────────┐
                          │   data/           │
                          │talkybuddy.db      │
                          │ (SQLite 3)        │
                          └──────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| **FastAPI App** | HTTP/WS routing, CORS, lifespan management | `server/app.py` |
| **VoicePipeline** | Turn orchestration (ASR→LLM→TTS), semi-duplex state machine | `server/pipeline.py` |
| **ASREngine** | Audio transcription with confidence scores | `server/asr.py`, `server/asr_base.py`, `server/asr_whisper.py`, `server/asr_sensevoice.py` |
| **EdgeLLM** | Local GGUF model inference | `server/llm.py` |
| **CloudLLM** | Cloud LLM via anthropic-relay (Bedrock Claude) | `server/cloud_llm.py` |
| **TTSEngine** | Local TTS via Piper/sherpa-onnx | `server/tts.py` |
| **CloudTTS** | Cloud TTS via ElevenLabs API | `server/cloud_tts.py` |
| **NovaSonicSession** | Real-time bidirectional S2S (AWS Bedrock Nova) | `server/nova_sonic.py` |
| **Scaffold** | Rule-based fallback, vocabulary, safety checks | `server/scaffold.py` |
| **Diagnose** | Learning assessment generation | `server/diagnose.py` |
| **Lesson** | Curriculum-based material selection | `server/lesson.py` |
| **Profile** | Student learning profile building | `server/profile.py` |
| **Store** | SQLite persistence and querying | `server/store.py` |
| **Auth** | JWT token issuing and verification | `server/auth.py` |
| **Guardrails** | Consent gates and safety policies | `server/guardrails.py` |
| **Web Client** | Vanilla JS frontend with Web Audio API | `web/*.js`, `web/index.html`, `web/teacher.html` |

## Pattern Overview

**Overall:** Layered architecture with dependency injection

**Key Characteristics:**
- **Pluggable engines**: ASR, LLM, TTS implementations can be swapped via config/DI
- **Semi-duplex**: VoicePipeline enforces single concurrent turn per session via asyncio.Lock
- **Graceful degradation**: Cloud → edge → scaffold fallback chain; never blocks on external services
- **Stateless services**: Most modules are pure functions; state lives in Pipeline + Store + Profile
- **Async/await**: FastAPI + asyncio for concurrent WebSocket connections
- **Rule-based core**: Scaffold engine is the unbreakable foundation (no dependencies, always available)

## Layers

**Web Layer:**
- Purpose: Browser interface for student and teacher; real-time audio I/O via Web Audio API
- Location: `web/`
- Contains: HTML pages, JavaScript client logic, wake detection modules, audio routing
- Depends on: HTTP/WebSocket server (FastAPI app)
- Used by: Students, teachers

**API Layer (FastAPI):**
- Purpose: HTTP and WebSocket routing, request/response serialization, CORS isolation, lifespan hooks
- Location: `server/app.py`
- Contains: Route handlers, middleware, WebSocket endpoint logic
- Depends on: VoicePipeline, Auth, Store, Config
- Used by: Web frontend, device clients (sync API), teacher dashboard

**Orchestration Layer (VoicePipeline):**
- Purpose: Coordinate single turns through ASR → scaffold → LLM → TTS pipeline
- Location: `server/pipeline.py`
- Contains: Turn state machine, engine sequencing, timing, fallback logic
- Depends on: ASR, LLM, TTS, Scaffold, Config
- Used by: WebSocket handlers in `app.py`

**Engine Layer:**
- Purpose: Provide pluggable implementations of speech and language processing
- Location: `server/asr*.py`, `server/llm.py`, `server/cloud_llm.py`, `server/tts.py`, `server/cloud_tts.py`, `server/nova_sonic.py`
- Contains: Model loading, inference, error handling, API calls
- Depends on: Config, external models and APIs
- Used by: VoicePipeline, app.py (direct for status checks)

**Rule/Content Layer:**
- Purpose: Deterministic response generation, vocabulary, safety, curriculum logic
- Location: `server/scaffold.py`, `server/diagnose.py`, `server/lesson.py`, `server/curriculum.py`
- Contains: Hardcoded vocab, word lists, scoring algorithms, diagnostic rules
- Depends on: Config, Store (diagnose/lesson read history)
- Used by: VoicePipeline (scaffold), app.py (diagnose in sync endpoints), lesson building

**Persistence Layer:**
- Purpose: SQLite database access with thread safety
- Location: `server/store.py`
- Contains: Schema management, interaction/diagnosis CRUD, deduplication logic
- Depends on: Config (DB path)
- Used by: All layers that need to read/write data

**Profile & Identity Layer:**
- Purpose: Student learning profile, lesson selection, authentication
- Location: `server/profile.py`, `server/lesson.py`, `server/auth.py`, `server/guardrails.py`
- Contains: Profile building, directive formatting, JWT handling, consent checking
- Depends on: Store, Config, Diagnose
- Used by: VoicePipeline (for personalization), API handlers (for auth)

## Data Flow

### Primary Request Path (Turn-Based Dialogue: /ws/talk)

1. **Connection Accept** (`server/app.py:345`) — WebSocket connects with token query param
2. **Auth Verify** (`server/app.py:356-362`) — JWT decoded; policy close if invalid
3. **Pipeline Init** (`server/app.py:365-369`) — Per-connection VoicePipeline created, inherits network_mode
4. **Audio Receive** (`server/app.py:441-453` or `server/app.py:456-479`) — Binary frames accumulated or text JSON parsed
5. **Audio Buffering** (`server/app.py:447-452`) — Debounce timer started for single binary (no audio_end)
6. **Audio Processing** (`server/pipeline.py:144-176`) — webm→ffmpeg→16kHz WAV
7. **ASR Transcription** (`server/pipeline.py:163-174`) — ASREngine.transcribe() returns (text, conf)
8. **Text Processing** (`server/pipeline.py:198-281`):
   - Low-confidence check → fallback line if conf < 0.5
   - Scaffold.respond() → rule-based response + scores
   - LLM try chain: Cloud (if mode=="cloud" ∧ consent) → Edge → fallback to scaffold
   - TTS synthesis: Cloud (if available) → Edge
9. **DB Write** (`server/pipeline.py:256-273`) — store.add_interaction() persists turn
10. **Directive Refresh** (`server/pipeline.py:276-278`) — Background task every N turns
11. **Response Emit** (`server/app.py:384-408`) — asr_result → reply → tts_audio sent as JSON frames
12. **Idle State** (`server/app.py:421` or `server/pipeline.py:280`) — Emitted to signal turn complete

### Live Real-Time S2S Path (/ws/live)

1. **Connection Accept & Auth** (`server/app.py:497-523`) — Token verified, consent/availability gates
2. **Lesson Build** (`server/app.py:527`) — build_lesson() from diagnoses + profile
3. **Session Start** (`server/app.py:613`) — NovaSonicSession.start() with system_prompt
4. **Continuous Mode** (if `?mode=continuous`, `server/app.py:614-643`):
   - Uplink Task: Receives user PCM → session.send_audio()
   - Downlink Task: Drains session.events_continuous() → emits transcript/audio/turn_end
   - Pronunciation Tee: User PCM buffered separately for async scoring
5. **Turn-Based Mode** (`server/app.py:644-667`):
   - Receive loop until user_end message
   - session.end_user_turn() signals turn boundary
   - drain_events() iterates model events
6. **Turn End** (`server/app.py:540-555`) — Pronunciation score computed (if enabled), turn_user/turn_asst persisted
7. **Session Close** (`server/app.py:677-679`) — NovaSonicSession.close() cleanup
8. **Diagnosis Write** (`server/app.py:681`) — _run_live_diagnosis() async backgrounded

### State Management

- **Per-connection**: VoicePipeline holds `_lock` (semi-duplex), `_directive` cache, `_turn_count` for refresh throttle
- **Global singleton**: `pipeline` at module level in `app.py` (used to sync network_mode across connections)
- **Database**: interactions + diagnoses tables (SQLite with thread-safe lock in `store.py`)
- **Profile**: Computed on-demand from interactions + diagnoses in `profile.build_profile()` or per-lesson in `lesson.build_lesson()`

## Key Abstractions

**TurnResult:**
- Purpose: Encapsulate single-turn output (ASR text, reply, TTS bytes, scores, latency)
- Location: `server/pipeline.py:36-48`
- Pattern: Dataclass; passed through pipeline stages, emitted to client

**ScaffoldResult:**
- Purpose: Return value from rule-based fallback engine
- Location: `server/scaffold.py:22-28`
- Pattern: Dataclass containing reply text, TTS segments (zh/en), scores dict, safety flag

**VoicePipeline:**
- Purpose: Orchestrate single turn, manage semi-duplex concurrency, fallback chain
- Location: `server/pipeline.py:111-333`
- Pattern: Class with dependency injection (__init__ receives asr/llm/tts/cloud engines)

**Lesson:**
- Purpose: Represent a lesson (topic, target sentence, directive for this session)
- Location: `server/lesson.py:13-18`
- Pattern: Dataclass; computed per live session from diagnoses + profile

## Entry Points

**HTTP GET / (Student Page):**
- Location: `server/app.py:130-133`
- Triggers: Browser navigates to root URL
- Responsibilities: Return `web/index.html` (page+stylesheet+scripts bundled)

**HTTP GET /teacher (Teacher Page):**
- Location: `server/app.py:136-139`
- Triggers: Browser navigates to /teacher
- Responsibilities: Return `web/teacher.html`

**WebSocket /ws/talk:**
- Location: `server/app.py:344-487`
- Triggers: Browser connects with `?token=JWT`
- Responsibilities: Accept audio/text frames, run pipeline turns, emit results; half-duplex state machine

**WebSocket /ws/live:**
- Location: `server/app.py:489-684`
- Triggers: Browser connects with `?token=JWT&mode=continuous` (optional)
- Responsibilities: Bidirectional real-time S2S via Nova Sonic; handle uplink/downlink concurrency; score pronunciation

**Startup (lifespan):**
- Location: `server/app.py:94-100`
- Triggers: FastAPI app starts
- Responsibilities: init_db() (schema), seed_demo() (demo data), pre-warm engines in daemon thread

## Architectural Constraints

- **Threading:** Python GIL; asyncio single-threaded event loop for I/O, subprocess/threads for CPU-bound (ffmpeg, ASR, TTS)
- **Global state:** Module-level singletons in `app.py:51-59` (asr_engine, llm_engine, tts_engine, cloud_tts_engine, cloud_llm_engine, pipeline); per-connection Pipeline instances decouple student identity
- **Circular imports:** None detected; `scaffold.py` imports nothing from server modules; `diagnose.py` imports `anthropic_relay` (not LLM)
- **Sync/async boundary:** Store and models run in asyncio.to_thread(); Auth/guardrails are sync; app.py is fully async
- **Concurrency:** Each WebSocket connection gets its own VoicePipeline with asyncio.Lock; global pipeline used only for network_mode broadcast
- **Error recovery:** No exceptions propagate to server (all caught in WebSocket handlers); low-confidence ASR → fallback; model unavailable → scaffold; API timeout → cascade to next engine or fallback

## Anti-Patterns

### Model Lazy-Loading Without Explicit Seam

**What happens:** ASR/LLM/TTS models loaded on first request in production, not at startup. Results in first-user latency spike.

**Why it's wrong:** Unpredictable startup time; no early indication if model files missing. Pre-warming thread in lifespan mitigates but doesn't guarantee seam is clearly visible.

**Do this instead:** Keep `_prewarm_engines()` in lifespan (`server/app.py:73-91`); add explicit logging per engine loaded; consider pre-loading critical models (e.g., ASR for SenseVoice int8 is large) before yielding in lifespan if startup SLA is tight.

### Synchronous Database Access in Async Context

**What happens:** `store.py` uses synchronous sqlite3 API; all access wrapped in asyncio.to_thread() in pipeline.

**Why it's wrong:** Works but verbose; every DB call needs thread wrapper. Maintainers must remember to thread-wrap new store calls.

**Do this instead:** Consider migrating to async SQLite driver (aiosqlite) or keeping current pattern but add `async_store.py` convenience wrapper that auto-threads all store methods.

### Dependency Injection Partially Applied

**What happens:** VoicePipeline receives ASR/LLM/TTS via __init__ (good testability), but config is module-global. Tests must mock config module.

**Why it's wrong:** Tests of scaffold, diagnose, lesson can't easily change config constants (ASR_CONF_THRESHOLD, VOCAB, etc.) without monkeypatching.

**Do this instead:** Already addressed in tests via monkeypatch fixtures (see tests/conftest.py). Pattern is sound; no change needed.

## Error Handling

**Strategy:** Graceful degradation with fallback chains

**Patterns:**
- **ASR failure** → Empty text + conf=0 → scaffold fallback line + no DB write
- **LLM timeout/error** → Tries cloud (if available) → tries edge → falls back to scaffold result
- **TTS failure** → tts_wav=None → client-side silence or text-only mode
- **Database failure** → result.seq=0 (no persistence, turn still completes)
- **WebSocket exception** → Caught in try/except, connection closed gracefully; finally block cleans up tasks
- **Model unavailable** → available() check returns False; engine skipped in fallback chain

## Cross-Cutting Concerns

**Logging:** 
- App uses Python logging module; INFO for lifecycle (wake events), ERROR for exceptions
- All exceptions in WebSocket handlers logged (if not caught in inner try/except)
- Real-wire tests use logging to trace turn flow

**Validation:**
- Auth: JWT signature + expiry verified via `auth.verify_token()` on every protected endpoint
- Input: ASR text stripped; LLM output checked for non-empty string before use; payload sizes implicitly limited by WebSocket frame max
- Safety: `scaffold.safety_check()` blocks forbidden words; `guardrails.consent_granted()` gates cloud features

**Authentication:**
- JWT issued via `auth.issue_token(sub, role)` (called by /api/login)
- Token passed as query param (?token=...) in WebSocket connections
- Roles: student (self-access), tutor/device (query-param student selection)
- None (no token) → policy close (1008) on /ws/talk; /ws/live closes with live_error

**Personalization:**
- Profile built from interaction + diagnosis history via `profile.build_profile()`
- Lesson selected per-session from latest diagnosis + profile via `lesson.build_lesson()`
- Directive (B1 companion strategy) cached in Pipeline._directive; refreshed every N turns in background
- CEFR level and target_form passed down to prompt injection

---

*Architecture analysis: 2026-07-18*
