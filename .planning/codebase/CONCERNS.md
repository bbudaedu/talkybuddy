# Codebase Concerns

**Analysis Date:** 2026-07-18

## Tech Debt

**FFmpeg Subprocess Dependency for Audio Conversion:**
- Issue: WebM/Opus audio from browser MediaRecorder still requires subprocess call to `ffmpeg` for 16kHz mono WAV conversion
- Files: `server/pipeline.py` (lines 57-108, `_webm_to_wav()`)
- Impact: External binary dependency; subprocess invocation adds latency (~100-200ms) and potential failure surface; blocks PC-to-Genio 520 porting
- Fix approach: PLAN.md requires switching to Python `soundfile` library for in-memory decoding on Genio 520; currently blocked because libsndfile lacks WebM/Opus container support. Interim solution: keep ffmpeg on PC prototype; on Genio 520, capture audio directly via ALSA as 16kHz mono WAV (bypassing browser MediaRecorder entirely), eliminating both ffmpeg and WebM conversion

**Hardcoded LLM Context Window for Development:**
- Issue: `server/llm.py` line 94 sets `n_ctx=1024` tokens for Qwen2.5-1.5B-Instruct, suitable for PC development but too large for Genio 520 deployment
- Files: `server/llm.py` (line 94)
- Impact: Genio 520's CPU-only constraint requires `n_ctx=512` to prevent OOM crashes; PC prototype currently bypasses this to maintain test compatibility and response quality
- Fix approach: PC prototype should remain at 1024 for development; at Genio 520 deployment time, reduce to 512 and regenerate/revalidate all prompts and test assumptions that depend on context length. Document this in deployment runbook

**Espeak-ng-data GPL-3.0 Licensing Residue:**
- Issue: TTS backend replaced piper-tts (GPL-3.0) with sherpa-onnx (Apache-2.0), but still requires espeak-ng-data phoneme archive for synthesis
- Files: `server/config.py` (lines 23-26), `server/tts.py` (`_resolve_espeak_data_dir()` fallback)
- Impact: `scripts/setup_env.sh` still installs piper-tts package solely to access its bundled espeak-ng-data (GPL-3.0 licensed); this residual GPL dependency blocks full Apache-2.0 compliance despite swapping synthesis backend
- Fix approach: Source or build Apache/MIT-licensed espeak-ng-data replacement; update setup script to bypass piper-tts installation once espeak-ng-data source changes

**Global Singletons in app.py:**
- Issue: All engines (`asr_engine`, `llm_engine`, `tts_engine`, `cloud_*_engine`) and `pipeline` instantiated as module-level singletons in `server/app.py` (lines 51-61)
- Files: `server/app.py` (lines 51-61)
- Impact: Difficult to test concurrent sessions with different configurations; state pollution across test runs; hard to isolate engine failures; makes dependency injection testing harder
- Fix approach: Consider factory pattern or dependency injection container if multi-session/multi-tenant support becomes requirement; currently acceptable for single-instance demo deployment

**Lazy Loading Complexity in Engine Initialization:**
- Issue: Engines use private lazy-loading methods (`_get_model()`, `_ensure_model()`) that can silently fail or delay errors until first use
- Files: `server/llm.py` (lines 72-102), `server/asr.py` (_ensure_model pattern)
- Impact: Model load failures not caught at startup; `available()` may return True even if subsequent model load fails; hard to diagnose production issues; makes startup verification tests incomplete
- Fix approach: Consider moving model initialization to lifespan startup with explicit error reporting; add structured logging for model load attempts/failures

## Known Bugs

**ASR Confidence Threshold Change Lacks Test Coverage:**
- Symptoms: `ASR_CONF_THRESHOLD` changed from 0.45 → 0.5 in `server/config.py` (line 36) to match PLAN.md requirements, but no unit tests verify behavior at boundary (conf ≈ 0.5)
- Files: `server/config.py` (line 36), `server/pipeline.py` (line 201 uses this threshold)
- Trigger: Text input with ASR confidence exactly 0.5 will trigger fallback behavior; confidence 0.49 will trigger fallback, 0.50 will trigger normal processing
- Workaround: Manual testing with ASR engines returning specific confidence scores; currently blocked on proper test infrastructure that can inject mock ASR results with precise confidence values
- Fix approach: Add `test_pipeline_asr_confidence_boundary.py` with stub ASR engine returning controlled confidence values (0.49, 0.50, 0.51) and verify fallback vs. normal path selection

**WebSocket Audio Debounce Race Condition:**
- Symptoms: Single binary frame without explicit `audio_end` message relies on 0.35s debounce timeout (line 43 in `server/app.py`); fast network conditions or user input variations can trigger premature or delayed processing
- Files: `server/app.py` (lines 410-487, `process_audio_buffer()` / `debounce_flush()`)
- Trigger: Rapid successive audio frames from browser followed by quick silence; or slow network causing audio frames to batch unpredictably
- Workaround: Users can force audio processing by sending explicit `{"type":"audio_end"}` message
- Fix approach: Implement VAD (voice activity detection) on browser side or server side to replace hard timeout; Genio 520 has VAD requirement in PLAN.md anyway

**Store.interaction_exists() Linear Scan Inefficiency:**
- Symptoms: `store.interaction_exists()` (line 172-180) performs `O(n)` linear scan through all interactions to detect duplicates for cross-device deduplication
- Files: `server/store.py` (lines 172-180)
- Trigger: With large interaction history (>10k records), duplicate checking becomes slow; no database index on `(student_id, device_id, client_ts)` composite key
- Workaround: Demo dataset stays small; single-device testing; duplicate detection rarely triggered in normal flow
- Fix approach: Add database index on `(student_id, device_id, client_ts)` in `init_db()`; replace linear scan with SQL query

## Security Considerations

**Environment Variables for Sensitive Credentials:**
- Risk: PICOVOICE_ACCESS_KEY, ANTHROPIC_API_KEY, AWS credentials, ELEVENLABS_API_KEY expected in environment but not validated/documented
- Files: `server/config.py` (lines 49-126)
- Current mitigation: `.gitignore` prevents `.env` commits; startup will proceed with degraded service if env vars absent
- Recommendations: 
  1. Document all required env vars in README with examples (do NOT include real values)
  2. Add startup warnings when critical credentials missing
  3. Consider using `.env.example` template file for clarity
  4. Log a summary of which services are available/degraded based on credentials at startup

**WASM Thread Isolation via COOP/COEP Headers:**
- Risk: sherpa-onnx KWS WASM requires `SharedArrayBuffer` which only works in `crossOriginIsolated` context
- Files: `server/app.py` (lines 106-119, middleware adds `COOP: same-origin`, `COEP: require-corp`)
- Current mitigation: Middleware enforces headers globally; all resources are same-origin
- Recommendations:
  1. Document why these headers are mandatory in code comments (clarity for future maintainers)
  2. Test that no cross-origin scripts/styles are accidentally loaded
  3. Verify mobile browsers (iOS Safari) support these headers correctly

**SQL Injection via JSON Payload Storage:**
- Risk: Interactions and diagnoses stored as JSON TEXT in SQLite; if JSON content is ever displayed without sanitization, could be XSS vector
- Files: `server/store.py` (lines 122-145), `server/app.py` (API endpoints returning stored JSON)
- Current mitigation: All data displayed via React/Vue in teacher.html and index.html (likely uses framework escaping); student text comes from ASR/user input only
- Recommendations:
  1. Verify all JSON display paths use framework escape (not `innerHTML` or equivalent)
  2. Add Content-Security-Policy header to prevent inline script execution
  3. Document sanitization strategy for student_text field

**Consent Gate Bypassed in edge-only Mode:**
- Risk: `CONSENT_GRANTED` flag in `config.py` (line 85) can be overridden via `TALKYBUDDY_CONSENT_GRANTED=false` to force edge-only mode, but no audit trail of when/why this was changed
- Files: `server/config.py` (line 85), `server/pipeline.py` (line 228 checks consent in cloud path)
- Current mitigation: Demo mode; no parent/school account system yet
- Recommendations:
  1. Add structured logging when consent setting changes
  2. Require explicit consent at school/parent level (not just env var)
  3. Log all cloud API calls with timestamp/student_id for audit

## Performance Bottlenecks

**Directive Refresh Background Task Without Cancellation:**
- Problem: `pipeline._refresh_directive()` (lines 283-308 in `server/pipeline.py`) spawns `asyncio.create_task()` to update teaching strategy in background every 5 turns; if refresh takes longer than 5-turn interval, tasks can accumulate
- Files: `server/pipeline.py` (lines 276-278, 283-308)
- Cause: No task cancellation or deduplication; `_directive_refreshing` flag prevents concurrent runs but doesn't limit task queue depth
- Improvement path: 
  1. Track spawned task and cancel previous one before spawning new one
  2. Add metric to monitor pending background tasks
  3. Consider moving to separate thread pool if task load grows

**TTS Synthesis Blocks on Both Edge and Cloud Sequentially:**
- Problem: `_synth_tts()` (lines 310-332 in `server/pipeline.py`) tries cloud TTS first, waits for result, then tries edge TTS as fallback; if cloud TTS hangs for CLOUD_TTS_TIMEOUT_S (6s), user experiences 6s delay before hearing edge voice
- Files: `server/pipeline.py` (lines 310-332)
- Cause: Sequential try/fallback instead of race condition (fastest wins)
- Improvement path: Use `asyncio.wait(..., return_when=FIRST_COMPLETED)` to start both cloud/edge in parallel and use whichever completes first within timeout

**WebM→WAV Conversion with 10s Timeout on Network Lag:**
- Problem: `_webm_to_wav()` uses `FFMPEG_TIMEOUT_S = 10.0` (line 30 in `server/pipeline.py`) as hard cutoff for subprocess ffmpeg; if user has slow upload or ffmpeg process starts slow, legitimate audio is dropped
- Files: `server/pipeline.py` (lines 29-30, 81-90)
- Cause: Fixed timeout doesn't account for file size or network conditions; no adaptive timeout
- Improvement path: 
  1. Log actual ffmpeg duration and file size to identify real timeout vs. slow environments
  2. Consider increasing timeout to 15s for robustness
  3. Add telemetry to detect if timeouts correlate with specific file sizes

**Store.list_diagnoses() and list_interactions() Load All Records into Memory:**
- Problem: `list_diagnoses()` (line 221-232) and `list_interactions()` (line 156-169) fetch all rows, load into Python list, then filter/slice
- Files: `server/store.py` (lines 156-169, 221-232)
- Cause: No SQL LIMIT/WHERE clause; scalability issue if diagnosis/interaction history grows to 100k+ records
- Improvement path:
  1. Add `limit` parameter to SQL query (currently Python-side slice)
  2. Add `student_id` filtering to SQL WHERE clause (currently Python-side filter)
  3. Add database index on `student_id`

## Fragile Areas

**VoicePipeline State Machine with Manual Lock Management:**
- Files: `server/pipeline.py` (lines 111-281)
- Why fragile: 
  - Async lock (`_lock`) and state flags (`_turn_count`, `_directive_refreshing`) managed manually across multiple methods
  - `asyncio.Lock` used for half-duplex but doesn't prevent interleaving of background tasks spawned by `_refresh_directive()`
  - If `run_turn_audio()` or `run_turn_text()` raises exception before lock is released, subsequent calls will deadlock
- Safe modification:
  1. Use context manager (already done with `async with self._lock`) but verify all exception paths release lock
  2. Add assertion/test that lock is always released even on exception
  3. Consider wrapping in try/finally if any code path between `async with` entry and exit can raise

**ASR Engine Multi-Backend Switching via Feature Flag:**
- Files: `server/config.py` (line 39), `server/asr.py` (ASR_BACKEND config), `server/asr_base.py` (get_asr_engine_class())
- Why fragile:
  - Two ASR backends (SenseVoice, Whisper) can be swapped via config; switching requires model file presence
  - If SenseVoice model missing but config says `ASR_BACKEND=sensevoice`, engine will return `available()=False`, causing pipeline to fall back to scaffold only
  - No explicit error message telling user which ASR backend failed to load
- Test coverage: `tests/test_asr_backend.py` exists but may not cover all config combinations
- Safe modification:
  1. Log which ASR backend was attempted and why it failed (missing model, import error, etc.)
  2. Add test matrix covering: SenseVoice missing + config=sensevoice, Whisper missing + config=whisper, both missing, both present
  3. Document fallback chain clearly in README

**Diagnose.py Mock vs. Real Claude API Switching:**
- Files: `server/diagnose.py` (lazy import of Anthropic SDK, mock fallback)
- Why fragile:
  - If `ANTHROPIC_API_KEY` set but SDK not installed, or API key invalid, fallback to mock silently happens
  - Teacher end sees mock diagnosis without knowing it's not real; no indicator
  - If Anthropic API changes (model discontinuation, rate limit), demo breaks without clear error
- Safe modification:
  1. Add explicit logging/telemetry for "mock diagnosis used" vs. "real API used"
  2. Separate concerns: mock logic in one module, real API call in another
  3. Add API health check endpoint that teachers can use to verify Anthropic connectivity

**Web Client WASM Threading + Playback Queue Race:**
- Files: `web/live-client.js` (lines 56-150), `web/live-capture-processor.js`
- Why fragile:
  - Audio capture via AudioWorklet and playback via AudioContext scheduling race on shared buffers
  - `PlaybackQueue.drain()` called while playback might still be consuming previous chunk
  - WebAssembly.Memory shared across capture/playback processors
- Safe modification:
  1. Add guards to prevent concurrent access to playback queue
  2. Test with simulated network delays and audio gaps
  3. Document expected behavior when one direction (capture/playback) lags the other

## Scaling Limits

**Single SQLite Database File for All Data:**
- Current capacity: Acceptable for <100k interaction records (~10MB database file), single device, single student
- Limit: SQLite performance degrades noticeably >1M records; no built-in horizontal scaling; single writer lock becomes bottleneck if multiple Genio 520 devices write to same database
- Scaling path:
  1. At 100k interactions: Add database indexes on commonly-queried columns (student_id, device_id, date)
  2. At 1M+ interactions: Migrate to server database (PostgreSQL, etc.); implement replication/sync protocol for multiple Genio 520 devices
  3. Consider sharding by student_id or device_id if number of concurrent students >> 1

**LLM Context Window Fixed at 1024 Tokens:**
- Current capacity: Suitable for single-turn responses; prompt + response fit comfortably in 1024 tokens for 國小 (elementary school) level English
- Limit: Cannot support multi-turn conversation history without dropping old turns; if curriculum requires remembering >2 previous interactions, prompts will be truncated
- Scaling path:
  1. Short term: Document that LLM is single-turn only (inherent design, not bug)
  2. Medium term: Add summarization layer to compress older turns into tokens
  3. Long term: For Genio 520, evaluate if n_ctx=512 allows any multi-turn history at all; if not, accept single-turn constraint

**Nova Sonic Live S2S Bandwidth for Multiple Simultaneous Users:**
- Current capacity: Single WebSocket connection at 16kHz PCM capture + 24kHz PCM playback = ~480kbps concurrent; single Genio 520 device can handle one active user
- Limit: If multiple students share one Genio 520 (classroom scenario), only one can use S2S at a time; others wait or fall back to pipeline mode
- Scaling path:
  1. Queue incoming requests and serialize (accept latency)
  2. Distribute across multiple Genio 520 devices per classroom
  3. Investigate if Nova Sonic can be pooled across clients (cloud-side multiplexing, requires AWS/Bedrock architecture change)

**Teacher Dashboard 5-Second Poll Interval Not Real-Time:**
- Current capacity: Acceptable for 1-2 classrooms monitoring; 5s latency means interaction data is 0-5s stale
- Limit: If 50+ students, each classroom teacher refreshing every 5s, creates 10 HTTP requests/second per teacher; becomes noticeable on slow networks
- Scaling path:
  1. Add WebSocket-based push notifications for teacher dashboard (replaces polling)
  2. Add aggregation layer (count interactions per minute, rather than per-interaction updates)
  3. Consider caching aggregated metrics in Redis if deployment grows

## Dependencies at Risk

**llama-cpp-python Requires C++ Compilation:**
- Risk: Installation fails on environments without MSVC/gcc/clang toolchain; no pre-built wheels for all platforms
- Impact: If LLM installation fails, entire LLM layer disabled (degrades to scaffold only); demo still works but loses LLM enhancements
- Migration plan: Pre-build and host platform-specific wheels; or switch to `ollama` server backend (moves compilation out of demo setup)

**faster-whisper Depending on CTranslate2:**
- Risk: CTranslate2 backend may be discontinued or have long-term support issues; Whisper model format could change in future OpenAI versions
- Impact: If fallback to Whisper is needed (SenseVoice unavailable), and CTranslate2 breaks, ASR falls back to scaffold only
- Migration plan: Keep SenseVoice as primary; monitor faster-whisper GitHub for deprecation notices; consider native OpenAI Whisper API as ultimate fallback if local inference becomes unreliable

**sherpa-onnx WASM Module Size (Mozilla hosted CDN):**
- Risk: WASM modules (~5MB+) downloaded from Mozilla CDN on first load; if CDN down or bandwidth limited, KWS wake layer fails
- Impact: Wake word detection (Porcupine + KWS fallback) becomes unavailable; users can still use manual push-to-talk button as fallback
- Migration plan:
  1. Bundle WASM files in `web/static` instead of CDN
  2. Add fallback to cached version if CDN fetch fails
  3. Pre-load WASM on app startup to detect failures early

## Test Coverage Gaps

**ASR Confidence Threshold Boundary:**
- What's not tested: Interaction behavior when ASR confidence is exactly at or near the threshold (0.5)
- Files: `server/pipeline.py` (line 201)
- Risk: Changes to threshold value or comparison logic (`<` vs. `<=`) could silently break fallback behavior without test failure
- Priority: High (affects user experience; currently behavior-driven by single config value with no test protection)
- Recommendation: Add `test_pipeline_asr_confidence.py` with stub ASR returning [0.49, 0.50, 0.51] and verify fallback vs. normal paths

**Multi-Concurrent WebSocket Sessions:**
- What's not tested: Two or more simultaneous `/ws/talk` connections with overlapping audio processing
- Files: `server/app.py` (lines 345-487, `ws_talk` handler), `server/pipeline.py` (semi-duplex lock)
- Risk: Half-duplex lock may not properly serialize requests from different connections; stress test with 5+ concurrent clients needed
- Priority: Medium (demo is single-session, but multi-device scenario requires validation)
- Recommendation: Add `test_ws_concurrent_sessions.py` with simultaneous WebSocket clients

**Cloud TTS Fallback to Edge on Timeout:**
- What's not tested: Behavior when cloud TTS hangs/times out and edge TTS must take over
- Files: `server/pipeline.py` (lines 310-332), `test_pipeline_cloud_tts.py` exists but may not cover timeout scenario
- Risk: If cloud TTS times out, edge TTS might not be available or might also timeout, leaving tts_wav=None and user hears nothing
- Priority: Medium (affects user experience when network is degraded)
- Recommendation: Mock cloud TTS with delay > CLOUD_TTS_TIMEOUT_S, verify edge TTS activates and produces audio

**Diagnose Directive Refresh Background Task:**
- What's not tested: `_refresh_directive()` background task behavior under load or when DB query is slow
- Files: `server/pipeline.py` (lines 283-308), `test_pipeline_directive.py` exists but might not cover concurrent task scenarios
- Risk: If diagnosis DB query locks, subsequent turns could have stale directive or no directive while refresh is in progress
- Priority: Medium (impacts multi-turn quality but not core functionality)
- Recommendation: Add stress test with concurrent turns and verify directive updates complete without blocking main pipeline

## Missing Critical Features

**No Cross-Device Synchronization Pressure Test:**
- Problem: PLAN.md describes network-resilient sync via `seq`/`device_id` deduplication, but PC prototype is single-device; Genio 520 multi-device scenario untested
- Blocks: Deployment of multiple Genio 520 devices sharing same cloud backend; risk of data loss or duplication if devices reconnect
- Recommendation: Add integration test with 2+ mock devices simulating network disconnect/reconnect with conflicting interaction timestamps

**No LLM Output Validation Framework:**
- Problem: LLM responses checked only for safety keywords; no grammar/coherence/appropriateness scoring
- Blocks: Implementing quality gates (e.g., "reject if response contains confusing pronouns")
- Recommendation: Build test harness that scores LLM outputs against teacher-curated "good response" examples; establish SLA for quality

**No Audio/Video Recording for Audit Trail:**
- Problem: Teacher end has no way to audit "what did the student actually say vs. what ASR heard"
- Blocks: Investigation of ASR errors; legal/ethical audit if needed
- Recommendation: Add optional `record_audio` flag to config; save original webm/ogg audio (with student_id hash) to `logs/` for debugging window (72 hours)

---

*Concerns audit: 2026-07-18*
