# External Integrations

**Analysis Date:** 2026-07-18

## APIs & External Services

**Speech Recognition:**
- SenseVoice (int8 model) - On-device ASR via sherpa-onnx (`server/asr_sensevoice.py`)
  - Model source: GitHub releases (sherpa-onnx/asr-models)
  - No API key needed; runs locally
- Faster-Whisper - Fallback ASR via CTranslate2 (`server/asr_whisper.py`)
  - Model source: Systran/faster-whisper-small (Hugging Face)
  - No API key needed; runs locally
- Fallback ASRs for low-confidence detection trigger `FALLBACK_LINES` response

**Language Model:**
- Local (Edge LLM):
  - llama.cpp - Qwen2.5-1.5B GGUF model (`server/llm.py`)
  - Client: llama-cpp-python library
  - No auth required; runs locally
  - 8-second timeout per generation
  - Graceful fallback to rule-based scaffold if unavailable

- Cloud (Anthropic Claude):
  - Service: Anthropic API (Messages endpoint)
  - Implementation: `server/cloud_llm.py` + `server/anthropic_relay.py`
  - Auth: Bearer token or API key via environment
    - `ANTHROPIC_AUTH_TOKEN` (Bearer, priority)
    - `ANTHROPIC_API_KEY` (x-api-key fallback)
  - Model: `claude-sonnet-5` (default, overridable via `ANTHROPIC_DEFAULT_OPUS_MODEL`/`ANTHROPIC_MODEL` env)
  - Endpoint: `https://api.anthropic.com/v1/messages` (overridable via `ANTHROPIC_BASE_URL` for relay support)
  - Usage: Parental scaffolding + guardrail enforcement; student text anonymized before sending
  - Timeout: 8 seconds; silently degrades to edge on failure/timeout
  - API Version: 2023-06-01

**Text-to-Speech:**
- Local (Edge TTS):
  - Implementation: `server/tts.py` using sherpa-onnx
  - Models: Piper format ONNX voices
    - `zh_CN-huayan-medium.onnx` - Chinese voice
    - `en_US-lessac-medium.onnx` - English voice
  - Sample rate: 22050Hz, 16-bit mono WAV
  - No API key needed; runs locally
  - Espeak-ng data: `models/espeak-ng-data/` (phoneme synthesis, GPL-3.0 residual risk noted)

- Cloud (ElevenLabs):
  - Service: ElevenLabs text-to-speech API
  - Implementation: `server/cloud_tts.py`
  - Auth: API key via `ELEVENLABS_API_KEY` env
  - Voice ID: `ELEVENLABS_VOICE_ID` env (default: Xb7hH8MSUJpSbSDYk0k2/Alice)
  - Model: `ELEVENLABS_MODEL` env (default: eleven_v3)
  - Endpoint: `https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=pcm_22050`
  - Features:
    - Emotion parameters: stability, style, similarity_boost (voice_settings)
    - Speaker boost: `ELEVENLABS_USE_SPEAKER_BOOST`
    - Time stretching: `CLOUD_TTS_SPEED` (post-synthesis WSOLA, default 0.90x)
  - Timeout: `CLOUD_TTS_TIMEOUT_S` (default 6.0s)
  - Silent degradation to local Piper on failure/timeout

**Large Language Model (Diagnostics):**
- Service: Anthropic Claude (same as Cloud LLM above)
- Implementation: `server/diagnose.py` (mock logic by default, real API optional)
- Auth: Same environment variables as cloud_llm
- Feature: AI-driven 14-day ability radar diagnosis + emotional status
- Default behavior: Rule-based mock output; only uses real API if `ANTHROPIC_API_KEY` present and credentials valid
- Output: JSON diagnosis object (strengths, weaknesses, emotional_status, instructions)

**Speech-to-Speech (Experimental Phase 1):**
- Service: AWS Bedrock - Amazon Nova 2 Sonic
- Implementation: `server/nova_sonic.py`
- SDK: aws_sdk_bedrock_runtime + smithy_aws_core (lazy-imported, optional)
- Auth: AWS SigV4 (environment credentials)
  - `AWS_ACCESS_KEY_ID`
  - `AWS_SECRET_ACCESS_KEY`
- Region: `BEDROCK_REGION` env (default: us-east-1)
- Model: `amazon.nova-2-sonic-v1:0` (overridable via `NOVA_SONIC_MODEL_ID` env)
- Voice: `tiffany` (overridable via `NOVA_SONIC_VOICE` env)
- Protocol: Bidirectional streaming (InvokeModelWithBidirectionalStream)
- Feature: Live conversational S2S (simultaneous listen & respond)
- Availability: Enabled if AWS credentials present AND SDK importable (graceful fallback to edge if not)

**Wake-Word Detection (Client-side):**
- Picovoice Porcupine:
  - Implementation: `web/porcupine-engine.js`
  - Auth: Access key via `PICOVOICE_ACCESS_KEY` env, delivered to browser via `/api/wake-config`
  - Built-in keyword: `WAKE_KEYWORD_BUILTIN` env (default: Bumblebee)
  - Custom `.ppn`: `WAKE_KEYWORD_PUBLIC_PATH` env (URL path in static assets)
  - Parameters: `porcupine_params.pv` model file (static asset)
  - Sensitivity: `WAKE_SENSITIVITY` env (0-1, default 0.6)
  - WASM/JavaScript SDK: Running in browser

- Sherpa-onnx KWS (experimental):
  - Implementation: `web/sherpa-engine.js`
  - Enabled: `WAKE_SHERPA_ENABLED` env (default: true)
  - Keywords: `WAKE_SHERPA_KEYWORDS` env (pinyin + labels, default: "sh uō sh uō x ué b àn @說說學伴")
  - Base URL: `WAKE_SHERPA_BASE_URL` env (default: `/static/vendor/sherpa-kws/`)
  - Threshold: `WAKE_SHERPA_THRESHOLD` env (default: 0.25)
  - Score: `WAKE_SHERPA_SCORE` env (default: 1.0)
  - WASM/JavaScript: Running in browser

## Data Storage

**Databases:**
- SQLite (local file-based)
  - Path: `data/talkybuddy.db`
  - Tables: `interactions` (seq, payload JSON, synced flag), `diagnoses` (date, payload JSON)
  - Threading: Protected by `threading.Lock` for concurrent access
  - Scope: Per device (`DEVICE_ID` env) + per student (`STUDENT_ID` env)
  - No external cloud database; SQLite is single-file local storage
  - Seed data: 14-day diagnoses + 20 historical interactions on first startup

**File Storage:**
- Local filesystem only (no cloud object storage)
  - Models directory: `models/` (GGUF, ONNX, voice files, metadata caches)
  - Data directory: `data/` (SQLite DB)
  - Logs directory: `logs/` (if needed)
  - Cache: `models/_sherpa_cache/` (patched ONNX + tokens.txt for sherpa-onnx)

**Caching:**
- In-memory: Engine instances (ASR, LLM, TTS) singleton in `server/app.py`
- Disk: Model file caching via huggingface_hub (HF_HOME default location)
- WebSocket: Client-side browser cache (implicit via service worker if deployed)

## Authentication & Identity

**Server Auth:**
- API endpoints: No authentication (demo mode; production would add auth layer)
- WebSocket: No token validation (device_id/student_id fixed in CONTRACTS.md)

**External Service Auth:**
- Anthropic: Bearer token (`ANTHROPIC_AUTH_TOKEN`) or API key (`ANTHROPIC_API_KEY`)
- AWS Bedrock: SigV4 signing (SDK handles via AWS credentials)
- ElevenLabs: API key header (x-api-key)
- Picovoice: Access key delivered to browser (WASM SDK validates)

**Internal Identity:**
- Device ID: `DEVICE_ID` env (fixed: GENIO-520-X992 for demo)
- Student ID: `STUDENT_ID` env (fixed: STUDENT-AMING-004 for demo)
- No multi-device/multi-user support in current phase

## Monitoring & Observability

**Error Tracking:**
- None (no third-party error tracking service)
- Logging: Python stdlib logging to console/file

**Logs:**
- Python logging module
- Logger names: `talkybuddy.wake`, `talkybuddy.pipeline`, etc.
- Levels: DEBUG, INFO, WARNING, ERROR
- Output: Console (Uvicorn) + optional file in `logs/`
- No structured logging (JSON) or centralized log aggregation

**Diagnostics:**
- Health checks: `GET /api/status` (backend availability)
- Network mode: `GET /api/network_mode` (edge vs cloud)
- Manual verification scripts: `scripts/verify_*.py` (integration smoke tests)

## CI/CD & Deployment

**Hosting:**
- Local development: Uvicorn server on http://localhost:8787
- LAN access: `http://<device-ip>:8787` (same Wi-Fi)
- Target platform: MediaTek Genio 520 board (future, not yet deployed)
- Current: PC/laptop prototype

**CI Pipeline:**
- None (no GitHub Actions, GitLab CI, etc.)
- Manual testing: `run_tests.sh` → pytest

**Deployment:**
- Scripts: `scripts/run.sh` (Uvicorn start)
- Startup: FastAPI lifespan context manager (`lifespan()` in `server/app.py`)
  - Initializes DB (`store.init_db()`)
  - Seeds demo data (`store.seed_demo()`)
  - Prewarns engines in background thread
- No containerization (Dockerfile not present)

## Environment Configuration

**Required env vars:**
- `ANTHROPIC_AUTH_TOKEN` OR `ANTHROPIC_API_KEY` - Claude API authentication (optional; if absent, cloud LLM disabled)
- `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` - Bedrock/Nova Sonic (optional; if absent, S2S disabled)
- `ELEVENLABS_API_KEY` - ElevenLabs TTS (optional; if absent, cloud TTS disabled)
- `PICOVOICE_ACCESS_KEY` - Porcupine wake-word (optional; if absent, fallback to Sherpa-onnx KWS)

**Optional tuning:**
- `TALKYBUDDY_PIPELINE_PROFILE` - "edge" or "cloud" (default: edge)
- `TALKYBUDDY_CONSENT_GRANTED` - "1"/"0"/"true"/"false" (default: true for demo)
- `LIVE_S2S_ENABLED` - "1"/"0" (default: true if Bedrock credentials available)
- `BEDROCK_REGION` - AWS region (default: us-east-1)
- `ANTHROPIC_BASE_URL` - Custom Claude endpoint (relay support)
- `ANTHROPIC_DEFAULT_OPUS_MODEL` / `ANTHROPIC_MODEL` - Model override
- `ELEVENLABS_MODEL`, `ELEVENLABS_VOICE_ID`, `ELEVENLABS_STABILITY`, `ELEVENLABS_STYLE`, etc. - TTS tuning
- `CLOUD_TTS_TIMEOUT_S`, `CLOUD_TTS_SPEED` - TTS performance
- `PRON_SCORE_TIMEOUT_S` - Pronunciation assessment timeout
- `WAKE_SENSITIVITY`, `WAKE_KEYWORD_BUILTIN`, `WAKE_SHERPA_ENABLED`, etc. - Wake-word tuning

**Secrets location:**
- Environment variables only (no `.env` file committed)
- In `.gitignore`: `.env`, `.env.*`, credential files
- Production: Deploy secrets via container env, AWS Secrets Manager, or CI/CD platform

## Webhooks & Callbacks

**Incoming:**
- None (API is request-response only, no webhook ingestion)

**Outgoing:**
- None (no third-party notifications or callbacks)

**WebSocket Callbacks:**
- `/ws/talk` - Bidirectional frame-based interaction (student ↔ server)
  - Text frames: `{"type":"text_input"|"audio_end", "data": "..."}` (client → server)
  - Binary frames: WebM/OGG audio blob (client → server)
  - Server responses: JSON events (`state`, `asr_result`, `reply`, `tts_audio`, `busy`, `tts_unavailable`)

## Fallback & Degradation

**Consent Gate (Privacy):**
- If `TALKYBUDDY_CONSENT_GRANTED=false`: Forces edge-only mode (no cloud data upload)

**Network Degradation:**
- Manual "airplane mode" toggle (`network_mode="edge"`) or auto-detect offline
- Interactions written to SQLite with `synced=false` flag
- Diagnostics deferred until cloud reconnection

**Engine Cascading:**
| Component | Failure | Fallback |
|-----------|---------|----------|
| Cloud LLM | Timeout/Error | Rule-based scaffold engine (`scaffold.py`) |
| Cloud TTS | Timeout/Error | Local Piper TTS (sherpa-onnx) |
| Local TTS | Missing model | Browser `speechSynthesis` API (if available) |
| ASR SenseVoice | Missing model | faster-whisper (if available) |
| Both ASRs | Unavailable | Quick-sentence buttons (text input) |
| Local LLM | llama-cpp-python failed | Rule-based scaffold only |
| Nova Sonic S2S | Credentials/SDK missing | Async turn-taking pipeline |

---

*Integration audit: 2026-07-18*
