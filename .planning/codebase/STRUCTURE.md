# Codebase Structure

**Analysis Date:** 2026-07-18

## Directory Layout

```
talkybuddy/
├── server/                          # FastAPI backend + core logic
│   ├── app.py                       # Main entry point (HTTP/WS routes, lifespan)
│   ├── pipeline.py                  # VoicePipeline state machine (turn orchestration)
│   ├── store.py                     # SQLite persistence layer
│   ├── config.py                    # Global configuration (paths, thresholds, env vars)
│   ├── auth.py                      # JWT token handling
│   ├── guardrails.py                # Safety & consent gates
│   │
│   ├── asr.py                       # ASR engine factory
│   ├── asr_base.py                  # ASREngine abstract + backend selection
│   ├── asr_whisper.py               # faster-whisper implementation
│   ├── asr_sensevoice.py            # sherpa-onnx SenseVoice implementation
│   │
│   ├── llm.py                       # EdgeLLM (local GGUF via llama-cpp-python)
│   ├── cloud_llm.py                 # CloudLLM (Bedrock Claude via anthropic-relay)
│   │
│   ├── tts.py                       # TTSEngine (Piper via sherpa-onnx)
│   ├── cloud_tts.py                 # CloudTTS (ElevenLabs API)
│   ├── timestretch.py               # WSOLA pitch-preserving time stretch for Cloud TTS
│   │
│   ├── nova_sonic.py                # NovaSonicSession (AWS Bedrock Nova real-time S2S)
│   │
│   ├── scaffold.py                  # Rule-based fallback, vocabulary, safety checks
│   ├── diagnose.py                  # Learning assessment generation (4-dim scores)
│   ├── lesson.py                    # Curriculum-based lesson selection per session
│   ├── curriculum.py                # Curriculum data (topics, CEFR levels, target forms)
│   ├── profile.py                   # Student profile building from history
│   ├── pronunciation.py             # Pronunciation scoring (Parselmouth/DTW-based)
│   │
│   ├── anthropic_relay.py           # Cloud LLM routing (Bedrock Claude + local fallback)
│   ├── sync_client.py               # Device sync client (for puppet/Genio)
│   ├── project_inventory.py         # Project metadata (version, etc.)
│   ├── diagnose.py                  # (See above under "Supporting Services")
│   │
│   ├── streaming/                   # Real-time bidirectional components
│   │   ├── run_realwire.py          # Real-wire test runner (hardwired turn manager + pipeline)
│   │   ├── harness.py               # Streaming harness for A2 pipecat spike
│   │   ├── turn_manager.py          # Turn boundary detection & sequencing
│   │   ├── vad.py                   # Voice activity detection
│   │   ├── reply_source.py          # Text response generation source
│   │   ├── batch_reply_source.py    # Batch variant of reply_source
│   │   ├── interruptible_synth.py   # Barge-in aware TTS
│   │   ├── interruptible_tts.py     # TTS interruption handling
│   │   ├── barge_in_gate.py         # Barge-in detection gate
│   │   └── tests/                   # Streaming tests
│   │       └── test_interruptible_synth.py
│   │
│   └── __init__.py                  # Package init (empty)
│
├── web/                             # Frontend (Vanilla JS + HTML5)
│   ├── index.html                   # Student interface (main page)
│   ├── teacher.html                 # Teacher dashboard
│   ├── live-client.js               # WebSocket client for /ws/talk
│   ├── live-client.test.mjs         # live-client tests (ESM)
│   ├── live-wake.js                 # Wake word detection orchestrator
│   ├── live-wake.test.mjs           # live-wake tests
│   ├── mic-router.js                # Microphone routing logic
│   ├── mic-router.test.mjs          # mic-router tests
│   ├── porcupine-engine.js          # Porcupine wake detection (Picovoice)
│   ├── porcupine-engine.test.mjs    # porcupine-engine tests
│   ├── sherpa-engine.js             # Sherpa KWS wake detection (sherpa-onnx WASM)
│   ├── sherpa-engine.test.mjs       # sherpa-engine tests
│   ├── sherpa-loader.js             # Sherpa WASM module loader
│   ├── sherpa-loader.test.mjs       # sherpa-loader tests
│   ├── wake-controller.js           # High-level wake coordination
│   ├── wake-controller.test.mjs     # wake-controller tests
│   ├── live-capture-processor.js    # Audio capture & preprocessing
│   └── vendor/                      # Third-party libs (WebAudio, etc.)
│       └── sherpa-kws/              # Sherpa WASM binaries & models
│
├── tests/                           # Backend pytest suite
│   ├── conftest.py                  # pytest fixtures (monkeypatch, temp DB, etc.)
│   ├── __init__.py                  # Package marker
│   │
│   ├── test_app_cloud_llm.py        # Cloud LLM routing tests
│   ├── test_app_cloud_tts.py        # Cloud TTS fallback tests
│   ├── test_app_authz.py            # Auth/authz endpoint tests
│   ├── test_ws_identity.py          # WebSocket identity verification
│   ├── test_cross_origin_isolation.py # COOP/COEP header tests
│   │
│   ├── test_pipeline_profile.py     # Pipeline + profile integration
│   ├── test_pipeline_directive.py   # Directive refresh & caching
│   ├── test_e2e.py                  # End-to-end turn flow
│   │
│   ├── test_asr_backend.py          # ASR backend selection
│   ├── test_pronounciation.py       # Pronunciation scoring
│   ├── test_nova_sonic.py           # Nova Sonic session mocking
│   │
│   ├── test_scaffold_live_prompt.py # Scaffold + live system prompt
│   ├── test_diagnose_relay.py       # Diagnose with cloud Bedrock
│   ├── test_anthropic_relay.py      # Anthropic relay routing
│   │
│   ├── test_profile.py              # Profile building logic
│   ├── test_profile_store.py        # Profile ↔ store integration
│   ├── test_guardrails.py           # Consent & safety gates
│
├── spike/                           # Experimental spikes & research
│   ├── a1_sherpa_kws/               # Sherpa-onnx KWS integration spike
│   │   ├── kws_detect.py
│   │   └── laptop_pack/
│   │
│   ├── a1_openwakeword/             # OpenWakeWord alternative spike
│   │   └── detect.py
│   │
│   ├── a2_pipecat/                  # Pipecat-AI streaming framework spike
│   │   ├── run_spike.py
│   │   ├── spike_parts.py
│   │   ├── probe_pipecat.py
│   │   ├── sherpa_voice.py
│   │   ├── interruptible_tts.py
│   │   ├── interruptible_synth.py
│   │   └── tests/
│   │
│   └── pron_assess/                 # Pronunciation assessment spike
│       ├── spike.py
│       └── spike_b.py
│
├── bench/                           # Benchmarks & performance tests
│   ├── (various benchmark scripts)
│
├── scripts/                         # Utility scripts
│   ├── (deployment, setup scripts)
│
├── docs/                            # Documentation
│   ├── CONTRACTS.md                 # System contracts & APIs
│   ├── PLAN.md                      # Project roadmap
│   ├── PLAN_ALIGNMENT.md            # Phase alignment documentation
│   └── (additional spec docs)
│
├── logs/                            # Runtime log files (gitignored)
├── models/                          # Model files (large, mostly gitignored)
│   ├── qwen2.5-1.5b-instruct-q4_k_m.gguf
│   ├── zh_CN-huayan-medium.onnx
│   ├── en_US-lessac-medium.onnx
│   ├── sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/
│   ├── espeak-ng-data/
│   └── _sherpa_cache/               # Sherpa metadata cache
│
├── data/                            # Runtime data (gitignored)
│   └── talkybuddy.db                # SQLite database
│
├── .planning/                       # GSD planning & codebase maps
│   └── codebase/                    # (THIS DIRECTORY)
│       ├── ARCHITECTURE.md
│       └── STRUCTURE.md
│
├── .git/                            # Git repository
├── .gitignore                       # Git ignore rules
├── run_tests.sh                     # Test runner script
├── README.md                        # Project overview
├── CONTRACTS.md                     # Main contracts document (copy in docs/)
├── PLAN_ALIGNMENT.md                # Phase tracking document
└── .venv_ready                      # Marker file for environment readiness
```

## Directory Purposes

**server/:**
- Purpose: FastAPI application + all business logic
- Contains: HTTP/WS routes, pipeline, engines, data layer, rules, auth
- Key files: `app.py` (entry), `pipeline.py` (core), `store.py` (persistence)

**web/:**
- Purpose: Browser frontend (student & teacher interfaces)
- Contains: HTML pages, JavaScript client logic, wake detection engines, audio I/O
- Key files: `index.html` (student), `live-client.js` (WebSocket), `live-wake.js` (activation)

**tests/:**
- Purpose: Pytest suite for backend
- Contains: Unit tests, integration tests, end-to-end tests
- Pattern: One test file per module or feature area (not by layer)

**spike/:**
- Purpose: Experimental implementations (not production code)
- Contains: Research code for KWS alternatives, streaming frameworks, pronunciation scoring
- Status: Reference/archive; not imported by server or tests

**models/:**
- Purpose: LLM, ASR, TTS model weights (large files, mostly .gitignored)
- Contains: GGUF, ONNX, WAV files; Sherpa cache directory
- Access: Via config.py path constants (LLM_GGUF, PIPER_ZH, SENSEVOICE_DIR, etc.)

**data/:**
- Purpose: Runtime database and state (gitignored)
- Contains: SQLite file (talkybuddy.db), synced state
- Access: Via config.DB_PATH and store.py API

**docs/:**
- Purpose: Design docs, specs, contracts
- Key files: `CONTRACTS.md` (API + data formats), `PLAN.md` (roadmap)

**.planning/codebase/:**
- Purpose: GSD codebase maps (generated by /gsd-map-codebase)
- Contains: ARCHITECTURE.md, STRUCTURE.md, CONVENTIONS.md, etc. (if generated)

## Key File Locations

**Entry Points:**

- **HTTP Server:** `server/app.py:103` (FastAPI app instantiation)
- **Lifespan:** `server/app.py:94-100` (startup/shutdown hooks)
- **WebSocket /ws/talk:** `server/app.py:344-487` (turn-based dialogue)
- **WebSocket /ws/live:** `server/app.py:489-684` (real-time S2S)
- **Web Student UI:** `web/index.html`
- **Web Teacher UI:** `web/teacher.html`

**Configuration:**

- **Config constants:** `server/config.py`
- **Database schema:** `server/store.py:98-140` (init_db)
- **Environment variables:** Documented in `server/config.py` (PICOVOICE_ACCESS_KEY, TALKYBUDDY_CONSENT_GRANTED, LIVE_S2S_ENABLED, etc.)

**Core Logic:**

- **Turn orchestration:** `server/pipeline.py:111-333` (VoicePipeline class)
- **ASR routing:** `server/asr.py` + `server/asr_base.py:get_asr_engine_class()`
- **LLM routing:** `server/llm.py` (EdgeLLM) + `server/cloud_llm.py` (CloudLLM via anthropic_relay)
- **TTS routing:** `server/tts.py` (EdgeTTS) + `server/cloud_tts.py` (CloudTTS)
- **Scaffold fallback:** `server/scaffold.py:198-299` (respond + safety_check)
- **Diagnosis generation:** `server/diagnose.py:135-300` (generate_diagnosis)
- **Lesson selection:** `server/lesson.py:47-68` (build_lesson)
- **Profile building:** `server/profile.py` (build_profile)

**Persistence:**

- **Database API:** `server/store.py:98-300` (init_db, add_interaction, list_interactions, etc.)
- **Profile storage:** `server/store.py` (save_profile, get_profile) + `server/profile.py`

**Testing:**

- **Test fixtures:** `tests/conftest.py` (monkeypatch, temp DB, pipeline stubs)
- **E2E tests:** `tests/test_e2e.py`
- **WebSocket tests:** `tests/test_ws_identity.py`
- **Component tests:** `tests/test_*.py` (one per major component)

## Naming Conventions

**Files:**

- **Module:** `snake_case.py` (e.g., `asr_sensevoice.py`, `cloud_llm.py`)
- **Tests:** `test_<component>.py` (e.g., `test_pipeline_directive.py`, `test_scaffold_live_prompt.py`)
- **Data/Config:** All caps for constants (e.g., `MODELS_DIR`, `ASR_CONF_THRESHOLD`)
- **WebJS:** kebab-case for logical modules (e.g., `live-client.js`, `wake-controller.js`)

**Directories:**

- **Package dirs:** lowercase plural or semantic (e.g., `server`, `tests`, `spike`, `models`, `docs`, `web`)
- **Feature groups:** Named by feature area (e.g., `streaming/`, `spike/a2_pipecat/`)

**Python Identifiers:**

- **Classes:** PascalCase (e.g., `VoicePipeline`, `TTSEngine`, `ASREngine`, `TurnResult`)
- **Functions:** snake_case (e.g., `respond()`, `generate_diagnosis()`, `build_lesson()`)
- **Constants:** SCREAMING_SNAKE_CASE (e.g., `FALLBACK_LINES`, `ASR_CONF_THRESHOLD`, `VOCAB`)
- **Private:** Leading underscore (e.g., `_webm_to_wav()`, `_process_text()`, `_emit_state()`)

**TypeScript/JavaScript:**

- **Classes:** PascalCase (e.g., `LiveClient`, `WakeController`)
- **Functions:** camelCase (e.g., `startRecording()`, `sendAudio()`)
- **Constants:** SCREAMING_SNAKE_CASE (e.g., `AUDIO_DEBOUNCE_MS`)
- **WebSocket messages:** {"type": "...", data: ...} — lowercase `type` field

## Where to Add New Code

**New Feature (Full Stack):**

1. **API Route:** Add to `server/app.py` (HTTP @app.get/post or WebSocket @app.websocket)
2. **Business Logic:** Create new module in `server/` (e.g., `server/new_feature.py`)
3. **Persistence:** Add table/columns to `server/store.py:init_db()` if needed; add CRUD functions
4. **Frontend:** Add page or JS module to `web/` (e.g., `web/new-feature.js`)
5. **Tests:** Add test file `tests/test_new_feature.py` with fixtures from `conftest.py`
6. **Config:** Add any constants or env vars to `server/config.py`

**New ASR/LLM/TTS Backend:**

1. **Backend Implementation:** Create `server/asr_<name>.py` (inherit from ASREngine) or `server/llm_<name>.py`, etc.
2. **Factory Update:** Modify `server/asr_base.py:get_asr_engine_class()` to handle new backend name
3. **Config Update:** Add backend enum or string constant to `server/config.py` (e.g., `ASR_BACKEND = "sensevoice" | "new_backend"`)
4. **Tests:** Add `tests/test_asr_<name>.py` with mocked model loads

**New Rule or Vocabulary:**

1. **Scaffold Update:** Add to `server/scaffold.py:VOCAB` (zh→en dict) or `FORBIDDEN_ZH/FORBIDDEN_EN`
2. **Curriculum Update:** Add to `server/curriculum.py` (topics, CEFR levels, target forms)
3. **Tests:** Add/update `tests/test_scaffold_*.py` with new word/rule cases

**New Endpoint (Student/Teacher/Tutor):**

1. **Route:** Add to `server/app.py` with appropriate auth guard (identity_from_header, _resolve_student)
2. **Data Access:** Call `server/store.py` functions or compute from profile
3. **Tests:** Add to `tests/test_app_*.py` with JWT token in Header

**WebSocket Real-Time Feature:**

1. **Handler:** Add logic to `/ws/talk` or `/ws/live` in `server/app.py`
2. **Streaming Components:** Use `server/streaming/*` modules if turn/VAD/barge-in needed
3. **Tests:** Add to `tests/test_ws_*.py` with mock WebSocket client

## Special Directories

**models/:**
- Purpose: Model weights and metadata (not source code)
- Generated: Yes (download/extract from releases)
- Committed: No (.gitignore excludes *.gguf, *.onnx, large directories)
- Access: Via `config.py` constants

**data/:**
- Purpose: SQLite database and runtime state
- Generated: Yes (first start creates schema; seed_demo populates)
- Committed: No (.gitignore excludes *.db)
- Backup: Not automated; users must export via API or manual DB dump

**logs/:**
- Purpose: Runtime logs (Python logging)
- Generated: Yes (if logging to file configured)
- Committed: No (.gitignore excludes logs/)

**spike/:**
- Purpose: Research/exploration (not active codebase)
- Generated: No (checked in for reference)
- Committed: Yes (but not imported by server or tests)
- Cleanup: Archive or delete once feature proven/rejected

**.planning/:**
- Purpose: GSD orchestration files
- Generated: Yes (by /gsd-map-codebase, /gsd-plan-phase, etc.)
- Committed: Yes (planning documents are part of repo)

---

*Structure analysis: 2026-07-18*
