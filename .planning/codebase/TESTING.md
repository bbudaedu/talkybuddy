# Testing Patterns

**Analysis Date:** 2026-07-18

## Test Framework

**Runner:**
- Framework: pytest (Python test framework)
- Async support: pytest-asyncio (enables `@pytest.mark.anyio`)
- Run command: `./run_tests.sh` or `python -m pytest -q`
- Config file: `.pytest.ini` or inline markers in `conftest.py`

**Key Tools:**
- `monkeypatch`: pytest fixture for mocking/patching functions, classes, config values
- `pytest.raises()`: Context manager for testing exception handling
- `TestClient` from starlette for HTTP endpoint testing
- WebSocket testing: `client.websocket_connect()` for WebSocket endpoints

**Run Commands:**
```bash
./run_tests.sh              # Run all tests (main + streaming/barge-in)
python -m pytest -q         # Quiet mode (brief output)
python -m pytest -v         # Verbose mode
python -m pytest tests/test_auth.py  # Single test file
python -m pytest -k "test_auth"      # By keyword matching
```

## Test File Organization

**Location & Structure:**
- Location: `tests/` directory at repo root (parallel to `server/`)
- Naming: `test_*.py` for test modules (e.g., `test_auth.py`, `test_pipeline.py`)
- One test file per server module: `server/auth.py` → `tests/test_auth.py`

**Directory Layout:**
```
tests/
├── conftest.py           # Shared fixtures & pytest configuration
├── test_auth.py          # Tests for server/auth.py
├── test_llm.py           # Tests for server/llm.py
├── test_pipeline.py      # Tests for server/pipeline.py
├── test_store.py         # Tests for server/store.py
├── test_app_live.py      # Tests for server/app.py WebSocket endpoints
└── [50+ other test files]
```

**Naming Convention:**
- Test functions: `test_<what_is_being_tested>()` (e.g., `test_password_roundtrip()`)
- Test classes: `_FakeX`, `_StubX` for mock objects (e.g., `_FakeModel`, `StubASR`, `_FakeResp`)
- Helper functions in tests: `_<purpose>()` (e.g., `_user_content()`, `_sample_interaction()`)

## Test Structure

**Suite Organization:**
All tests use pytest's functional style (not unittest-style classes). Each test is a simple function:

```python
def test_password_roundtrip():
    h = auth.hash_password("demo1234")
    assert auth.verify_password("demo1234", h) is True
    assert auth.verify_password("wrong", h) is False
```

**Test Grouping:**
Tests within a file grouped by functionality with comment dividers:

```python
# --- available() ---------------------------------------------------------

def test_available_true_with_key_and_voice(keyed):
    assert CloudTTS().available() is True


def test_available_false_without_key(monkeypatch):
    # ...


# --- synth() 成功路徑 ----------------------------------------------------

def test_synth_wraps_raw_pcm_into_valid_wav(keyed, monkeypatch):
    # ...
```

**Common Pattern:**
1. Setup (create fixtures, patch config)
2. Exercise (call function under test)
3. Assert (verify behavior)

Example from `test_llm.py`:
```python
def test_generate_with_directive_injects_strategy_block(monkeypatch):
    fake = _FakeModel("很棒！跟我說一遍：I like apples.")
    edge = EdgeLLM()
    monkeypatch.setattr(edge, "_get_model", lambda: fake)
    
    directive = "【本輪教學策略】目標：升級句型；話題：喜歡的事物。"
    out = edge.generate("我喜歡蘋果", _sc(), directive)
    
    assert "【本輪教學策略】" in _user_content(fake)
```

## Fixtures

**Global Autouse Fixtures (conftest.py):**

**`tmp_db` (autouse=True):**
- Automatically applied to all tests
- Redirects `config.DB_PATH` to a temporary directory per test
- Calls `store.init_db()` to create fresh tables
- Prevents test cross-contamination and avoids modifying `data/talkybuddy.db`

```python
@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """把 DB_PATH 導向 tmp 目錄，並建立乾淨的資料表。"""
    db_path = tmp_path / "talkybuddy_test.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    store.init_db()
    yield db_path
```

**`anyio_backend`:**
- Locks async tests to asyncio backend (not trio)
- Required by pytest-asyncio for WebSocket and async endpoint tests

```python
@pytest.fixture
def anyio_backend():
    """限定 anyio 測試只跑 asyncio backend（未安裝 trio）。"""
    return "asyncio"
```

**Custom Fixtures (specific test files):**

**`keyed` fixture (test_cloud_tts.py):**
Sets up environment for CloudTTS tests with mocked credentials:
```python
@pytest.fixture
def keyed(monkeypatch):
    """設好金鑰與 voice_id 的環境。"""
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", "test-voice")
    monkeypatch.setattr(config, "ELEVENLABS_MODEL", "eleven_v3")
    # ... more setup
```

**Fixture Usage:**
- Inject as function parameters: `def test_something(keyed, monkeypatch):`
- monkeypatch automatically available (pytest-provided)
- tmp_db automatically applied (autouse=True)

## Mocking Patterns

**Stub Classes (Contract-Based):**
Mock objects implement the same interface as real classes per CONTRACTS.md:

```python
class StubASR:
    """假 ASR：回固定 (text, confidence)，不碰真實檔案。"""
    def __init__(self, text: str = "", conf: float = 0.9):
        self._text = text
        self._conf = conf
    
    def available(self) -> bool:
        return True
    
    def transcribe(self, wav_path: str) -> tuple[str, float]:
        return (self._text, self._conf)
```

Same for `StubLLM`, `StubTTS`, `_FakeModel`, `_FakeSession`

**Monkeypatch Patterns:**

Patch config values:
```python
monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "test-key")
monkeypatch.setattr(config, "LIVE_S2S_ENABLED", False)
```

Patch instance methods:
```python
edge = EdgeLLM()
monkeypatch.setattr(edge, "_get_model", lambda: fake)
```

Patch global functions:
```python
monkeypatch.setattr(scaffold_mod, "safety_check", lambda _t: False)
monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
```

Patch module methods:
```python
monkeypatch.setattr(app_mod.store, "add_interaction", lambda d: seen.update(d) or 1)
```

**What to Mock:**
- External dependencies: `urllib.request.urlopen`, LLM models, TTS engines
- File I/O: Avoid real file reads; mock or use tmp files
- Slow operations: Model loading, network calls, heavy computation
- Config values: Use monkeypatch to test different configurations

**What NOT to Mock:**
- Core business logic: Always test scaffold, store, auth with real implementations
- Database operations: Use tmp_db fixture instead of mocking
- Type/contract validation: Test actual objects to verify they match contracts
- User-facing APIs: Use TestClient to test real HTTP endpoints

## Test Data & Factories

**Helper Functions:**
Define at module level to create sample data:

```python
def _sample_interaction(text: str = "hi", synced: bool = False) -> dict:
    return {
        "network_mode": "edge",
        "student_text": text,
        "asr_confidence": 0.9,
        "ai_response_text": "hello",
        "scores": {"fluency": 50, "vocabulary": 50, "grammar": 50},
        "latency_ms": {"asr": 100, "llm": 200, "tts_first": 150, "round_total": 500},
        "synced": synced,
    }

def _sc(target: str = "I like apples."):
    """最小 scaffold 結果：只需 target_sentence 屬性。"""
    return types.SimpleNamespace(target_sentence=target)
```

**Location:** Top of test file, before test functions

**Usage:** Pass to code under test or as fixture base:
```python
seq1 = store.add_interaction(_sample_interaction("first"))
out = edge.generate("我喜歡蘋果", _sc(), directive)
```

## Async & WebSocket Testing

**Async Tests:**
Mark with `pytest.mark.anyio` (from pytest-asyncio):

```python
pytestmark = pytest.mark.anyio  # Apply to all tests in file

async def test_normal_turn_uses_llm_reply_and_writes_db():
    """正常回合：ASR 信心足夠 → scaffold → LLM 加值 → TTS → 寫 DB."""
    events: list[dict] = []
    emit = await _collecting_emit(events)
    vp = VoicePipeline(StubASR(), StubLLM(reply="..."), StubTTS())
    
    result = await vp.run_turn_text("我要一個蘋果", emit)
    
    assert result.seq == 1
```

**WebSocket Testing:**
Use starlette TestClient:

```python
def test_ws_live_turn_stores_interaction(monkeypatch):
    _wire_fake(monkeypatch)
    client = TestClient(app_mod.app)
    with client.websocket_connect("/ws/live") as ws:
        ws.send_bytes(b"\x01\x02" * 8)
        ws.send_text(json.dumps({"type": "user_end"}))
        # Receive and verify
        m = ws.receive()
        if "text" in m:
            data = json.loads(m["text"])
            assert data.get("type") == "live_transcript"
        ws.send_text(json.dumps({"type": "bye"}))
```

**HTTP Testing (FastAPI):**
```python
def test_status_has_live_s2s_true(monkeypatch):
    monkeypatch.setattr(config, "LIVE_S2S_ENABLED", True)
    client = TestClient(app_mod.app)
    body = client.get("/api/status").json()
    assert body["live_s2s"] is True
```

## Error Testing

**Testing Exceptions:**

```python
def test_jwt_tampered_rejected():
    tok = auth.issue_token("X", "student")
    with pytest.raises(auth.InvalidToken):
        auth.verify_token(tok + "x")

def test_jwt_invalid_base64_rejected():
    with pytest.raises(auth.InvalidToken):
        auth.verify_token("a.b.c")
```

**Testing Fallback Behavior:**
```python
def test_generate_appends_target_when_missing(monkeypatch):
    """護欄：回覆漏掉目標句 → 自動補「跟我說一遍：<target>」。"""
    fake = _FakeModel("很棒喔，你好厲害！")  # Missing target
    edge = EdgeLLM()
    monkeypatch.setattr(edge, "_get_model", lambda: fake)
    
    out = edge.generate("我喜歡蘋果", _sc("I like apples."), "...")
    
    assert "跟我說一遍：I like apples." in out
```

## Coverage

**Current Status:** No explicit coverage target enforced

**View Coverage:** Not configured in this project

**Best Practice:** Aim for >80% coverage of critical paths (auth, store, pipeline, scaffold)

**What to prioritize:**
- All contract functions in `CONTRACTS.md`
- Error paths and fallback logic
- State machine transitions (pipeline.py)
- Safety guardrails (guardrails.py, scaffold.py)

## Test Markers

**Custom Markers (in conftest.py):**
```python
def pytest_configure(config):
    config.addinivalue_line("markers", "slow: 需載入重模型或長時間執行的測試")
```

**Usage:**
```python
@pytest.mark.slow
def test_loads_real_model():
    # Only run if explicitly requested: pytest -m slow
    pass
```

## Test Categories

**Unit Tests:**
- Single function/class tested in isolation
- Mock external dependencies
- Examples: `test_auth.py`, `test_store.py`, `test_llm.py`
- Fast execution (<1s per test)

**Integration Tests:**
- Multiple components working together
- Real engines + stubs for external services
- Examples: `test_pipeline.py`, `test_app_live.py`
- Moderate execution (1-5s per test)

**End-to-End Tests:**
- Full request flow from HTTP to response
- Uses TestClient to drive real FastAPI app
- Examples: `test_e2e.py`, WebSocket tests in `test_app_live.py`
- Slower execution (5-10s per test)

---

*Testing analysis: 2026-07-18*
