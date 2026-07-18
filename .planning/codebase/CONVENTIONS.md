# Coding Conventions

**Analysis Date:** 2026-07-18

## Naming Patterns

**Files:**
- Python modules: `snake_case.py` (e.g., `auth.py`, `cloud_tts.py`, `asr_sensevoice.py`)
- Test files: `test_*.py` (e.g., `test_auth.py`, `test_pipeline.py`)
- Configuration: `config.py` at package root

**Functions:**
- Public functions: `snake_case` (e.g., `hash_password()`, `verify_token()`, `transcribe()`)
- Private/helper functions: `_snake_case` (e.g., `_b64url()`, `_load_config()`, `_get_model()`)
- Async functions: Follow same `snake_case` pattern

**Variables & Constants:**
- Module-level constants: `UPPER_CASE` (e.g., `SECRET`, `ASR_CONF_THRESHOLD`, `PICOVOICE_ACCESS_KEY`)
- Local variables: `snake_case` (e.g., `asr_text`, `reply_text`, `capture_messages`)
- Private module state: `_snake_case` (e.g., `_lock`, `_conn`, `_conn_path` in `store.py`)
- Type hints use standard Python types and custom dataclasses

**Types & Classes:**
- Custom exceptions: `PascalCase` (e.g., `InvalidToken`, `WebSocketDisconnect`)
- Classes: `PascalCase` (e.g., `CloudTTS`, `EdgeLLM`, `VoicePipeline`, `ScaffoldResult`)
- Dataclass instances follow class naming

## Code Style

**Encoding & Imports:**
- All files must declare UTF-8 encoding at top: `# -*- coding: utf-8 -*-`
- First import: `from __future__ import annotations` for modern type hint syntax
- Import order:
  1. Standard library imports (`import asyncio`, `import json`, `import sqlite3`)
  2. Third-party imports (`from fastapi import FastAPI`, `from pydantic import BaseModel`)
  3. Local imports from `server` package (`from server import config, store`)
  4. Specific module imports (`from server.llm import EdgeLLM`)

**Line Length:** No strict limit enforced; code naturally wraps at ~100 characters for readability

**Docstrings:**
- Module docstrings required; written in Traditional Chinese with English identifiers
- Format: `"""Description. Details about purpose and key functions."""`
- Example from `store.py`: Explains contract functions, design points, threading model
- Function docstrings optional but recommended for complex logic; use inline comments instead
- Example: `"""建立資料表（若不存在）。"""` (brief, Chinese)

## Code Organization

**Section Dividers:**
- Use comment dividers to group related functions: `# ---- section_name ----`
- Example from `auth.py`: `# ---- 密碼 ----`, `# ---- JWT（HS256）----`, `# ---- 帳號 ----`
- Example from `config.py`: `# --------------- A1 喚醒層 ----`, `# --- Nova Sonic 即時 S2S 陪聊 ---`

**Helper Functions:**
- Small utilities or setup functions in test files prefixed with `_` (e.g., `_b64url()`, `_sample_interaction()`)
- Classes used as stubs/fakes also prefixed: `_FakeModel`, `_FakeResp`, `StubASR`

**Module-Level State:**
- Global singletons initialized at module level (e.g., `pipeline = VoicePipeline(...)` in `app.py`)
- Thread-safe access via `threading.Lock()` in `store.py`: `_lock = threading.Lock()`
- Connection management wrapped in safe functions: `_get_conn()`

## Error Handling

**Patterns:**
- Custom exceptions inherit from `Exception`: `class InvalidToken(Exception): pass`
- Try/except with specific exception types; avoid bare `except:`
- Example pattern:
  ```python
  def verify_password(pw: str, stored: str) -> bool:
      try:
          _, salt_hex, hash_hex = stored.split("$")
      except ValueError:
          return False
      # ... validation logic
  ```
- Graceful degradation for optional dependencies (ASR/LLM/TTS engines):
  ```python
  def available(self) -> bool:
      # Return False if model not loaded; pipeline has fallback
      return self._model is not None
  ```
- Config-driven fallbacks: Load from environment first, fall back to defaults
  ```python
  ELEVENLABS_API_KEY: str = os.environ.get("ELEVENLABS_API_KEY", "")
  CONSENT_GRANTED: bool = _env_bool("TALKYBUDDY_CONSENT_GRANTED", True)
  ```

**Context Managers:**
- Use `with` for resource management: `with _lock:`, `with wave.open(io.BytesIO(wav), "rb"):`
- Async context managers: `@asynccontextmanager` decorator for lifespan hooks

## Logging

**Framework:** Python's built-in `logging` module

**Patterns:**
- Module-level logger: `logger = logging.getLogger("talkybuddy.wake")`
- Used for background task status and error reporting
- Example: `logger.info()`, `logger.warning()` (not used extensively in source, but available)

## Comments

**When to Comment:**
- Module docstrings: Always; describe contract and design
- Complex logic: Inline comments explaining "why" not "what"
- Non-obvious configuration: Extensive comments in `config.py` explaining feature flags and env vars
- Example from `config.py`:
  ```python
  # Bedrock 服務 region（Nova Sonic live S2S 沿用；陪聊/診斷雲端腦走 anthropic-relay...）。
  BEDROCK_REGION: str = os.environ.get("BEDROCK_REGION", "us-east-1")
  ```

**Style:**
- Comments in Traditional Chinese for domain-specific context
- English for code identifiers and technical terms
- Section dividers: `# ---- section ----` (4 dashes + space)

## Type Hints

**Usage:**
- Function signatures use type hints: `def verify_password(pw: str, stored: str) -> bool:`
- Return types explicit: `-> str | None`, `-> dict`, `-> bytes | None`
- Union types use pipe: `str | None` (enabled by `from __future__ import annotations`)
- Collection types: `list[tuple[str, str]]`, `dict[str, int]`, `tuple[str, float]`
- Optional use: `dict | None` preferred over `Optional[dict]`

**Patterns:**
- Custom dataclasses for structured data: `ScaffoldResult` with `@dataclass` decorator
- Generic types in type hints: `VoicePipeline(asr_engine, llm_engine, tts_engine)`

## Module Design

**Exports:**
- Modules export main classes/functions at module level
- Example: `class CloudTTS:` in `cloud_tts.py` is primary export
- Private helpers: `_FakeResp` in test files, `_b64url()` in `auth.py`

**Barrel Files:**
- Minimal use of barrel imports; most imports explicit from submodules
- `server/__init__.py` exists but may be empty
- Imports prefer specificity: `from server.llm import EdgeLLM` over `from server import *`

**Lazy Loading:**
- All heavy dependencies (ASR/LLM/TTS) lazy-loaded in functions, not module-level imports
- Each engine provides `available()` to check if model is loaded
- Graceful fallback if models missing or environment incomplete

**Contract-Driven Design:**
- Core modules implement contracts defined in `CONTRACTS.md`
- Example: `scaffold.py` implements `ScaffoldResult`, `respond()`, `split_tts_segments()`
- All implementations follow documented method signatures exactly

---

*Convention analysis: 2026-07-18*
