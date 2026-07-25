# Phase 10: NPU-Accelerated Perception - Pattern Map

**Mapped:** 2026-07-25
**Files analyzed:** 9 (new) + 2 (modified)
**Analogs found:** 9 / 9

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|--------------------|------|-----------|-----------------|----------------|
| `edge/npu_spike/inspect_model.py` | utility (diagnostic script) | file-I/O / transform | `edge/runtime/measure_peak_rss.py` | role-match (standalone device diagnostic, pure-function + `main()` split) |
| `edge/npu_spike/fix_shape.py` | utility (offline conversion wrapper) | transform / file-I/O | `edge/runtime/measure_peak_rss.py` | role-match (thin wrapper around external CLI tool, same structure style) |
| `edge/npu_spike/raw_neuron_session.py` | service (inference engine) | request-response (inference call) | `server/asr_sensevoice.py` | role-match (lazy-singleton ONNX-backed ASR engine; different runtime — sherpa-onnx wrapper vs raw ORT session — but same lifecycle shape: `available()` / `_ensure_model()` / `transcribe()`) |
| `edge/npu_spike/ADR-npu-path.md` | doc (decision record) | — | `edge/BOARD_BRINGUP_DECISION.md` | exact (same "decision record with evidence" doc pattern used in this repo) |
| `server/asr_npu.py` | service (ASR engine, NPU path) | request-response | `server/asr_sensevoice.py` | exact (same engine contract: `available()`, `_ensure_model()`, `transcribe(wav_path) -> (text, confidence)`) |
| `server/asr_base.py` (MODIFY: add npu branch) | service (factory) | request-response | `server/asr_base.py` (self, existing `whisper`/`sensevoice` branch) | exact — extend existing factory, don't rewrite |
| `server/config.py` (MODIFY: add `TALKYBUDDY_ASR_NPU` flag) | config | — | `server/config.py` (self, `ASR_BACKEND` / `PIPELINE_PROFILE` flag pattern) | exact |
| `server/app.py` (MODIFY: `/api/status` npu field) | route (status endpoint) | request-response | `server/app.py` `api_status()` (existing) | exact |
| `tests/test_asr_npu.py` | test | unit (pure-function, mockable) | `tests/test_asr_backend.py` | exact (same fake-recognizer / monkeypatch style, same file naming convention `test_asr_*.py`) |

## Pattern Assignments

### `server/asr_npu.py` (service, request-response)

**Analog:** `server/asr_sensevoice.py` (full file read, 118 lines)

**Module docstring / contract pattern** (lines 1-12):
```python
"""ASR 引擎（sherpa-onnx + SenseVoice-Small，OpenCC 簡轉繁）。

契約（CONTRACTS.md）：available() / transcribe(wav_path) -> (text, confidence) / _ensure_model()。
...
- import 期不可炸；任何 transcribe 失敗回 ("", 0.0)。
"""
```
Copy this contract shape verbatim for `asr_npu.py`'s docstring — same three-method contract (`available`, `_ensure_model`, `transcribe`), same "must not crash on import, must not throw on transcribe failure" guarantee. This is required because `server/asr_base.py`'s factory and `server/app.py`'s status-probing code assume every ASR engine class exposes exactly this shape.

**Lazy-singleton engine skeleton** (lines 26-33, 53-78):
```python
class SenseVoiceASREngine:
    def __init__(self) -> None:
        self._recognizer = None
        self._load_failed = False
        self._lock = threading.Lock()

    def available(self) -> bool:
        if self._recognizer is not None:
            return True
        if self._load_failed:
            return False
        try:
            import sherpa_onnx  # noqa: F401
        except Exception:
            return False
        ...

    def _ensure_model(self):
        if self._recognizer is not None:
            return self._recognizer
        if self._load_failed:
            return None
        with self._lock:
            if self._recognizer is not None:
                return self._recognizer
            if self._load_failed:
                return None
            try:
                import sherpa_onnx
                ...
            except Exception:
                self._recognizer = None
                self._load_failed = True
        return self._recognizer
```
**Adapt for `asr_npu.py`:** replace `import sherpa_onnx` + `OfflineRecognizer.from_sense_voice(...)` with `import onnxruntime as ort` + `ort.InferenceSession(model_path, providers=[("NeuronExecutionProvider", {...}), "CPUExecutionProvider"])` inside the same double-checked-locking lazy-init shape. Per RESEARCH.md, `NeuronExecutionProvider` availability must be checked via `"NeuronExecutionProvider" in ort.get_available_providers()` inside `available()` (mirrors the `import sherpa_onnx` try/except check) — this is also the exact mockable seam `tests/test_asr_npu.py::test_npu_engine_falls_back_to_cpu_on_error` needs (monkeypatch `onnxruntime.get_available_providers`).

**Error-handling / never-throw pattern** (lines 94-117, `transcribe`):
```python
def transcribe(self, wav_path: str) -> tuple[str, float]:
    recognizer = self._ensure_model()
    if recognizer is None:
        return ("", 0.0)
    try:
        ...
        return (text, 1.0) if text else ("", 0.0)
    except Exception:
        return ("", 0.0)
```
Copy this outer try/except-wraps-everything shape exactly — NPU-02 explicitly requires "falls back to CPU / never crashes," and this is the established project convention for ASR engine failure handling (never let a transcribe-time exception propagate).

**NEW element not present in the analog — per-op placement metric:** `asr_npu.py` must additionally expose something like `last_placement() -> {"provider": [node_names], ...}` or `npu_ops_accelerated / npu_ops_total` counters, populated by parsing ORT's verbose `VerifyEachNodeIsAssignedToAnEp` log lines (see RESEARCH.md Pattern 1). This has no existing in-repo analog — it is new surface area. Structure the parser as a pure function (see `edge/runtime/measure_peak_rss.py` pattern below) so it is unit-testable without a real device/session.

---

### `server/asr_base.py` (MODIFY — factory, request-response)

**Analog:** self (existing file, full 30 lines already read)

**Existing branch pattern to extend** (lines 12-29):
```python
def get_asr_engine_class(backend: str | None = None) -> type:
    if backend is None:
        try:
            from server.config import ASR_BACKEND
            backend = ASR_BACKEND
        except Exception:
            backend = "sensevoice"
    if backend == "whisper":
        from server.asr_whisper import WhisperASREngine
        return WhisperASREngine
    from server.asr_sensevoice import SenseVoiceASREngine
    return SenseVoiceASREngine
```
Add an `"npu"` branch above the final fallback, following the exact same lazy-import-inside-the-branch style (`from server.asr_npu import NPUASREngine`), preserving the "不自動 fallback／未知值走主力" comment convention. Do NOT change `ASR_BACKEND`'s default; per CONTEXT.md D-02 stop-loss, the NPU path must remain strictly opt-in via a new flag, not the default factory selection — see `TALKYBUDDY_ASR_NPU` flag design below, which should gate this at the `config.py` level, not inside the factory's backend string alone (both can coexist: `ASR_BACKEND="npu"` OR a boolean flag — planner should pick one, but the factory-branch mechanics are identical either way).

---

### `server/config.py` (MODIFY — config, feature flag)

**Analog:** self (existing patterns, `PIPELINE_PROFILE` and `ASR_BACKEND`)

**Feature-flag pattern to copy** (lines 38-39, 135):
```python
# ASR 後端選擇：feature flag，可切換 sherpa-onnx SenseVoice 或 faster-whisper fallback
ASR_BACKEND = "sensevoice"  # "sensevoice" | "whisper"；切回 whisper 僅需改此值
...
PIPELINE_PROFILE: str = os.environ.get("TALKYBUDDY_PIPELINE_PROFILE", "edge")
```
Add `TALKYBUDDY_ASR_NPU` as an env-backed boolean using the existing `_env_bool()` helper (lines 78-82) already defined in this file:
```python
TALKYBUDDY_ASR_NPU: bool = _env_bool("TALKYBUDDY_ASR_NPU", False)
```
Default `False` — matches D-02's stop-loss spirit (NPU path opt-in only, CPU-only Phase 8 baseline remains default). Reuse `_env_bool`, don't write a new parsing helper.

---

### `server/app.py` (MODIFY — route, request-response)

**Analog:** self, `api_status()` (lines 146-158, full function read)

**Existing status-field pattern:**
```python
@app.get("/api/status")
async def api_status():
    """引擎可用性 + 網路模式 + 待同步筆數。"""
    return {
        "asr": bool(asr_engine.available()),
        "llm": bool(llm_engine.available()),
        "tts": bool(tts_engine.available()),
        "cloud_tts": bool(cloud_tts_engine.available()),
        "cloud_llm": bool(cloud_llm_engine.available()),
        "network_mode": pipeline.network_mode,
        "pending": store.pending_count(),
        "live_s2s": bool(config.LIVE_S2S_ENABLED and nova_sonic.available()),
    }
```
Add an `"npu"` key following the same `bool(x.available()) and ...`-style boolean gate, but since NPU-02 requires a *ratio*, not just a boolean, follow the nested-dict sub-pattern already used for `wake-config`'s `"sherpa": {...}` block (lines 176-182 of `api_wake_config`):
```python
"npu": {
    "enabled": bool(config.TALKYBUDDY_ASR_NPU),
    "ops_accelerated": <int, from asr_npu engine's placement metric>,
    "ops_total": <int>,
},
```
This directly satisfies RESEARCH.md's "NPU: ON, X/Y ops accelerated" HUD requirement and matches this file's existing nested-object convention for compound status fields.

---

### `edge/npu_spike/inspect_model.py` / `fix_shape.py` / `raw_neuron_session.py` (utility scripts, Day-1/Day-2 spike)

**Analog:** `edge/runtime/measure_peak_rss.py` (full file read, 131 lines)

**Structure to copy — pure functions + single `main()` I/O entrypoint** (lines 30-67, 96-130):
```python
def read_peak_rss_kb(pid: int, proc_root: str = "/proc") -> int | None:
    """...一律回傳 None（不拋例外——量測工具本身不該因單一行程消失而整批失敗）。"""
    ...

def main() -> None:
    """裝置上人工執行取數的進入點..."""
    ...

if __name__ == "__main__":
    main()
```
Every `edge/npu_spike/*.py` script should follow this exact shape: all real logic in pure, injectable, testable functions (e.g. `parse_input_signature(onnx_model)`, `build_fixed_shape_argv(...)`, `parse_ep_placement_log(log_text) -> dict[str, list[str]]`), with a thin `main()` doing the actual device/file I/O and printing human-readable PASS/FAIL-style output — matching this repo's established "diagnostic tool must not crash the rehearsal/spike" convention (see also `edge/runtime/dump_recent_turns.py`'s identical split, module docstring lines 10-13: "純函式...無 I/O 副作用、可單元測試；main() 才是唯一觸及...的進入點").

**Subprocess-call safety pattern** (lines 69-93, `_pgrep_first_pid`):
```python
try:
    result = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=5)
except (OSError, subprocess.SubprocessError):
    return None
if result.returncode != 0:
    return None
```
If `fix_shape.py` shells out to `python3 -m onnxruntime.tools.make_dynamic_shape_fixed` or `raw_neuron_session.py` shells out to any CLI, use fixed argv lists (never shell=True / string interpolation) — this repo has an explicit no-shell-interpolation convention (comment at `measure_peak_rss.py` line 13-14, also referenced against `edge/runtime/audio_io.py`).

---

### `edge/npu_spike/ADR-npu-path.md` (doc, decision record)

**Analog:** `edge/BOARD_BRINGUP_DECISION.md` (not read in full — title/role match only, sufficient for doc-structure guidance)

Follow the same "hardware/decision record with dated evidence, not just a conclusion" shape already established by `BOARD_BRINGUP_DECISION.md` and `edge/NETWORK_CUT_REHEARSAL.md` (both are decision/rehearsal records with concrete on-device evidence sections, not narrative docs). NPU-01's ADR must include: (1) the raw diagnostic script output (providers list + verbose log excerpt showing `VerifyEachNodeIsAssignedToAnEp`), (2) the Day-1 checkpoint pass/fail verdict per D-02, (3) explicit "stop-loss triggered" note if applicable.

---

### `tests/test_asr_npu.py` (test, unit)

**Analog:** `tests/test_asr_backend.py` (full file read, 102 lines)

**Fake-object + monkeypatch pattern to copy** (lines 30-43, 57-63, 73-94):
```python
class _FakeResult:
    def __init__(self, text): self.text = text

class _FakeStream:
    def __init__(self, text): self.result = _FakeResult(text)
    def accept_waveform(self, sample_rate, samples): pass

class _FakeRecognizer:
    def __init__(self, text): self._text = text
    def create_stream(self): return _FakeStream(self._text)
    def decode_stream(self, stream): pass

def test_sensevoice_available_false_when_model_missing(monkeypatch, tmp_path):
    from server import config
    from server.asr_sensevoice import SenseVoiceASREngine
    monkeypatch.setattr(config, "SENSEVOICE_DIR", tmp_path / "nope")
    eng = SenseVoiceASREngine()
    assert eng.available() is False

def test_sensevoice_transcribe_returns_zero_when_model_unavailable():
    from server.asr_sensevoice import SenseVoiceASREngine
    eng = SenseVoiceASREngine()
    eng._load_failed = True
    assert eng.transcribe("dummy.wav") == ("", 0.0)
```
For `asr_npu.py`, mirror this exactly: (a) a fake/stub for `onnxruntime.InferenceSession` + `onnxruntime.get_available_providers` via monkeypatch to test `available()` returns `False` when `"NeuronExecutionProvider"` is absent from the provider list (no real ORT/hardware needed); (b) a `test_parse_ep_placement_log` pure-function test feeding fixture log text (per RESEARCH.md Wave 0 Gaps) — no engine instantiation needed, matching the pure-function testing style of `edge/runtime/measure_peak_rss.py`'s `read_peak_rss_kb`; (c) reuse the exact `test_factory_returns_*_class` pattern (lines 7-16, 45-54) in `test_asr_base.py`-adjacent tests to verify `get_asr_engine_class("npu")` routes correctly once the new branch is added.

## Shared Patterns

### Engine contract (available / _ensure_model / transcribe)
**Source:** `server/asr_sensevoice.py` (docstring lines 1-12, full class)
**Apply to:** `server/asr_npu.py`
Every ASR engine in this codebase — including the new NPU engine — MUST implement exactly `available() -> bool`, `_ensure_model()`, `transcribe(wav_path: str) -> tuple[str, float]`. This is enforced implicitly by `server/asr_base.py`'s factory and `server/app.py`'s status/preload code, which call these methods polymorphically without isinstance checks.

### Never-throw / silent-degrade convention
**Source:** `server/asr_sensevoice.py` lines 94-117; `edge/runtime/dump_recent_turns.py` lines 21-32, 98-103
**Apply to:** `asr_npu.py`, all `edge/npu_spike/*.py` scripts
Every public method/diagnostic entrypoint wraps its risky logic in broad `try/except Exception` and degrades to a safe default value (`("", 0.0)`, `None`, `"-"`, empty list) rather than propagating — this is a deliberate, repeated convention across this codebase's ASR and rehearsal-tooling layers, not accidental omission of error handling. New NPU code must match it, especially since NPU-02 explicitly forbids "silent fake success" but does NOT forbid "silent safe degrade to CPU" — the distinction is: degrade silently is fine, CLAIM success silently is not (must log/report the real per-op ratio).

### Feature-flag env var convention
**Source:** `server/config.py` `_env_bool()` (lines 78-82), `ASR_BACKEND`/`PIPELINE_PROFILE` (lines 38-39, 135)
**Apply to:** new `TALKYBUDDY_ASR_NPU` flag
Reuse `_env_bool(name, default)` for any new boolean toggle; reuse the `os.environ.get("TALKYBUDDY_...", default)` + inline comment convention for any new string/numeric config value. Do not introduce a new config-loading mechanism.

### Nested status sub-object for compound telemetry
**Source:** `server/app.py` `api_wake_config()` `"sherpa": {...}` (lines 176-182)
**Apply to:** new `"npu"` field in `api_status()`
When a status field needs more than a single bool (here: enabled + ops_accelerated + ops_total), nest it as a sub-dict rather than flattening into top-level keys — matches the existing `sherpa` sub-object precedent.

### Pure-function + thin main() split for device/CLI scripts
**Source:** `edge/runtime/measure_peak_rss.py` (full file), `edge/runtime/dump_recent_turns.py` (full file, module docstring lines 10-13)
**Apply to:** all four new `edge/npu_spike/*.py` files
Keep every parsing/computation function pure and independently unit-testable (injectable `proc_root`-style parameters instead of hardcoded paths); reserve `main()` purely for real I/O (subprocess calls, SSH-only device state, model file loads) and human-readable PASS/FAIL-style console output. Fixed-argv subprocess calls only, never shell=True/string interpolation.

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| Per-op placement log parser (`VerifyEachNodeIsAssignedToAnEp` parsing, likely a function inside `asr_npu.py` or a new `edge/npu_spike/parse_ep_log.py`) | utility (log parsing) | transform | No existing in-repo code parses ONNX Runtime verbose logs; must be built from scratch per RESEARCH.md Pattern 1 and Wave 0 Gaps — follow the "pure function, feed fixture text, no hardware" testing shape from `measure_peak_rss.py` even though there is no direct precedent for the parsing logic itself |
| FP32 vs INT8 A/B harness script (NPU-03) | utility (manual test harness) | batch / transform | No existing FP32/INT8 comparison tooling in this repo; RESEARCH.md Pitfall N3 requires it to reuse the SAME new raw-session code path (both precisions), not compare against the old sherpa-onnx CPU path — structure as another `edge/npu_spike/` script following the same pure-function/thin-main split, but there is no prior A/B-harness analog to copy beyond that generic shape |

## Metadata

**Analog search scope:** `server/` (asr_sensevoice.py, asr_base.py, asr.py, config.py, app.py), `edge/` (runtime/, npu_spike/ target dir did not yet exist), `tests/` (test_asr_backend.py)
**Files scanned:** 9 read in full (asr_sensevoice.py, asr_base.py, config.py, app.py excerpt, measure_peak_rss.py, dump_recent_turns.py, test_asr_backend.py) + directory listing of `edge/`
**Pattern extraction date:** 2026-07-25
