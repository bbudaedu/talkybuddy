# Technology Stack

**Analysis Date:** 2026-07-18

## Languages

**Primary:**
- Python 3.x - Backend server logic, ASR/LLM/TTS engines, CLI scripts
- JavaScript (vanilla) - Frontend UI, wake-word detection, WebSocket communication

**Secondary:**
- Bash - Build/deployment scripts (`scripts/setup_env.sh`, `scripts/run.sh`)

## Runtime

**Environment:**
- Python 3.x with venv isolation (`.venv`)
- Browser environment (HTML5 WebAPI: Web Audio API, WebSocket, Web Workers)

**Package Manager:**
- pip - Python dependency management
- Lockfile: Not explicit (venv-based, `setup_env.sh` pins package versions)

## Frameworks

**Core:**
- FastAPI 0.109+ - HTTP & WebSocket server (`server/app.py`)
- Uvicorn - ASGI server with WebSocket support

**Testing:**
- pytest - Test runner
- pytest-asyncio - Async test support
- httpx - HTTP test client with ASGI transport
- Starlette TestClient - WebSocket testing

**Build/Dev:**
- setup_env.sh - Environment initialization (Python venv, model downloads, dependency installation)
- Bash scripts - Installation, running, verification

## Key Dependencies

**Core Web:**
- fastapi - HTTP/WebSocket server framework
- uvicorn[standard] - ASGI server implementation
- websockets - WebSocket protocol support
- pydantic - Data validation and settings management

**Speech Processing:**
- sherpa-onnx - ASR/TTS engine (Apache-2.0), loads ONNX models
- faster-whisper - ASR fallback (Systran/faster-whisper small model)
- SenseVoice - ASR int8 model (sherpa-onnx compatible, ~226MB)
- opencc - Simplified to Traditional Chinese conversion (OpenCC s2twp)

**Language Model:**
- llama-cpp-python - Local LLM inference engine for Qwen2.5-1.5B GGUF
- Qwen2.5-1.5B-Instruct GGUF - Local edge LLM (~1.1GB)

**Cloud/API:**
- aws_sdk_bedrock_runtime - AWS Bedrock SDK (for Nova Sonic S2S)
- smithy_aws_core - Smithy SDK core (Bedrock dependency)
- anthropic (indirect via urllib) - Anthropic API client (via manual urllib.request in `cloud_llm.py`)

**Audio/Media:**
- numpy - Numeric operations, audio processing
- soundfile - Audio file I/O
- huggingface_hub - Model downloading from Hugging Face

**Streaming/Advanced:**
- pipecat-ai[funasr] - Speech pipeline orchestration (A2 barge-in phase)
- torch (CPU) - PyTorch runtime (pipecat dependency)
- torchaudio - Audio processing (pipecat dependency)
- funasr - Streaming ASR service (FunASR integration)

**Data:**
- sqlite3 - Database (stdlib, built-in)

**Utilities:**
- asyncio - Async runtime (stdlib)
- threading - Multithreading for engine prewarming
- aiofiles - Async file I/O (if used)

## Configuration

**Environment:**
- Environment variables injected at startup (no `.env` file committed):
  - `PICOVOICE_ACCESS_KEY` - Wake-word detection
  - `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_API_KEY` - Claude API authentication
  - `ANTHROPIC_BASE_URL` - Custom Claude API endpoint (relay support)
  - `ANTHROPIC_DEFAULT_OPUS_MODEL` / `ANTHROPIC_MODEL` - Model override
  - `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` - Bedrock authentication
  - `ELEVENLABS_API_KEY` - ElevenLabs TTS API key
  - `ELEVENLABS_VOICE_ID` - Voice identifier (default: Alice/Xb7hH8MSUJpSbSDYk0k2)
  - `ELEVENLABS_MODEL` - Model selection (default: eleven_v3)
  - `BEDROCK_REGION` - AWS region (default: us-east-1)
  - `TALKYBUDDY_CONSENT_GRANTED` - Parental consent gate (default: True for demo)
  - `LIVE_S2S_ENABLED` - Nova Sonic S2S toggle
  - `TALKYBUDDY_PIPELINE_PROFILE` - Deployment profile: "edge" or "cloud"
  - Various tuning params: `WAKE_SENSITIVITY`, `CLOUD_TTS_TIMEOUT_S`, `PRON_SCORE_TIMEOUT_S`, etc.

**Build:**
- `scripts/setup_env.sh` - Bash script for venv setup, model downloads, dependency installation
- `setup_env.sh` stages:
  1. Create `.venv` and install base packages (pip, wheel, setuptools, cmake, ninja)
  2. Install ASR/TTS packages (faster-whisper, sherpa-onnx, piper-tts, opencc)
  3. Download models via huggingface_hub
  4. Download SenseVoice int8 model from GitHub releases
  5. Optional: Compile llama-cpp-python (fails gracefully)
- No dockerfile, pyproject.toml, or setup.py (venv-based approach)

## Platform Requirements

**Development:**
- Python 3.7+ (f-strings, dataclasses used)
- Linux/macOS/Windows PC (x86-64)
- ffmpeg - Audio conversion (`ffmpeg` subprocess in pipeline for webm/ogg to wav)
- 4GB+ RAM for model loading (ASR + LLM + TTS simultaneous)
- GPU optional (CPU-only verified, CPU torch installed)

**Production:**
- Target: MediaTek Genio 520 board with NPU (future phase, not yet deployed)
- Current prototype: PC/laptop with FastAPI server accessible over LAN
- Deployment: Uvicorn on port 8787 (configurable)

---

*Stack analysis: 2026-07-18*
