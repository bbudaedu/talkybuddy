# Phase 8: CPU-Only Offline Edge Turn Loop - Pattern Map

**Mapped:** 2026-07-25
**Files analyzed:** 10 (5 modified, 5 new)
**Analogs found:** 10 / 10

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|--------------------|------|-----------|-----------------|----------------|
| `server/llm.py` (MODIFIED — `EdgeLLM`) | service | request-response (HTTP client, was in-process) | `server/cloud_llm.py` (`CloudLLM`) | exact — same public contract, stdlib `urllib` HTTP pattern |
| `server/config.py` (MODIFIED — add `LLM_SERVER_HOST/PORT/URL`) | config | request-response | `server/config.py` existing `LLM_N_CTX`/`PIPELINE_PROFILE` block | exact — same file, same `os.environ.get` idiom |
| `edge/runtime/run_llama_server.py` (NEW — `build_llama_server_argv()`) | utility | transform (argv builder, unit-testable) | `server/config.py` (env-driven value resolution) + `edge/runtime/run_edge.sh` (shell launcher) | role-match — no existing "argv builder" module, closest is config-resolution pattern |
| `edge/runtime/run_edge.sh` (MODIFIED — also launch `llama-server` + health-check wait) | config/launcher | request-response (process orchestration) | `edge/runtime/run_edge.sh` itself (current version) | exact — same file, extend in place |
| `edge/runtime/audio_io.py` (NEW — ALSA capture/playback wrapper) | utility | file-I/O / streaming | `server/pipeline.py` RIFF-sniff fast path (WAV byte handling) + `server/cloud_llm.py` (lazy subprocess/try-except degrade style) | role-match — no existing audio-capture module; borrow error-handling idiom only |
| `edge/runtime/local_client.py` (NEW — WS client loop) | service (client) | event-driven / streaming | `web/live-client.js` (`this.ws = new WebSocket(...)`, wire protocol) + `server/app.py::ws_talk` (server-side protocol definition) | role-match — same wire protocol, different tier (Python client vs JS client vs FastAPI server) |
| `edge/deploy/build.sh` (MODIFIED — add llama.cpp cross-compile + `file`/`ldd` sanity check) | config/build | batch | `edge/deploy/build.sh` itself (current version) | exact — same file, extend in place |
| `edge/deploy/push.sh` (MODIFIED — rsync native binaries too) | config/deploy | file-I/O (rsync) | `edge/deploy/push.sh` itself (current version) | exact — same file, extend in place |
| `tests/test_llm.py` (MUST REWRITE — monkeypatch HTTP call, not `_get_model`) | test | request-response | `tests/test_llm.py` itself (current version, same file being rewritten) + `tests/test_llm_n_ctx_profile.py` (profile/env reload pattern) | exact — rewrite in place, keep test names/structure |
| `tests/test_llm_n_ctx_profile.py` (MUST REWRITE — assert `--ctx-size` argv, not `Llama(n_ctx=)` kwarg) | test | transform | `tests/test_llm_n_ctx_profile.py` itself (current version) | exact — rewrite in place |

## Pattern Assignments

### `server/llm.py` (service, request-response) — `EdgeLLM` HTTP client refactor

**Analog:** `server/cloud_llm.py` (`CloudLLM`) — same directory, same contract, already does exactly what `EdgeLLM` needs to become: a stdlib-`urllib` HTTP client with `available()`/`generate()` degrade semantics.

**Imports pattern** (`server/cloud_llm.py` lines 9-16):
```python
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from server import anthropic_relay, guardrails

_log = logging.getLogger(__name__)
```
Apply to `server/llm.py`: replace `import threading` (no longer needed once no in-process model singleton/lock) with `import json`, `import urllib.error`, `import urllib.request`; keep `from server import guardrails`.

**available() pattern** (`server/cloud_llm.py` lines 50-55, adapt to HTTP health-check per Pattern 2 in RESEARCH.md):
```python
def available(self) -> bool:
    """憑證可解析（resolve_config 非 None）即 True；任何失敗回 False。"""
    try:
        return anthropic_relay.resolve_config() is not None
    except Exception:
        return False
```
`EdgeLLM.available()` becomes a short-timeout GET to `/health` wrapped the same way (see RESEARCH.md Pattern 2 code example — try/except Exception → False, no re-raise).

**Core HTTP call pattern** (`server/cloud_llm.py` lines 57-98 — request build, POST, timeout, degrade):
```python
body = json.dumps({...}).encode("utf-8")
req = urllib.request.Request(
    cfg["url"], data=body, headers=cfg["headers"], method="POST",
)
with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
    payload = json.loads(resp.read().decode("utf-8"))

text = _extract_text(payload).strip()
if not text:
    return None
if not guardrails.passes_guardrail(text):
    return None
if target and target not in text:
    text = f"{text} 跟我說一遍：{target}"
return text
```
`EdgeLLM.generate()` keeps its own prompt-assembly logic (`_SYSTEM_PROMPT`, `user_prompt`, `directive_block` — DO NOT change, `tests/test_llm.py` directive-injection tests depend on it unchanged) but swaps `model.create_chat_completion(...)` for a new private `_call_llama_server(messages) -> str`, mirroring `CloudLLM`'s request/response/guardrail/target-append tail exactly (lines 89-98 are near-identical to what `EdgeLLM.generate` already does at lines 148-159 — keep that tail, only replace the call in the middle).

**Error handling pattern** (`server/cloud_llm.py` lines 99-101):
```python
except Exception:
    _log.exception("CloudLLM generate 失敗，降級回 edge/scaffold")
    return None
```
`EdgeLLM.generate()` already has this exact idiom (lines 160-162) — keep as-is, just ensure the new `_call_llama_server` HTTP exceptions (`urllib.error.URLError`, timeout) are caught by the same outer `try/except Exception`.

**Timeout param note:** `_GENERATE_TIMEOUT_S = 8.0` stays as the outer wall-clock budget (unchanged, lines 25); pass `timeout=7.5` (slightly under budget, per RESEARCH.md Pattern 2) to `urllib.request.urlopen` inside `_call_llama_server`, following `CloudLLM`'s `_TIMEOUT_S = 8.0` → `urlopen(..., timeout=_TIMEOUT_S)` convention (lines 21, 86) but shaved down since `EdgeLLM` also does an outer `time.monotonic()` check.

---

### `server/config.py` (config) — new `LLM_SERVER_HOST`/`LLM_SERVER_PORT`/`LLM_SERVER_URL`

**Analog:** same file, existing profile/env block (lines 131-140):
```python
PIPELINE_PROFILE: str = os.environ.get("TALKYBUDDY_PIPELINE_PROFILE", "edge")

_LLM_N_CTX_DEFAULT = 512 if PIPELINE_PROFILE == "edge" else 1024
LLM_N_CTX: int = int(os.environ.get("TALKYBUDDY_LLM_N_CTX", str(_LLM_N_CTX_DEFAULT)))
```
Apply identical `os.environ.get(name, default)` idiom for the new values, e.g.:
```python
LLM_SERVER_HOST: str = os.environ.get("TALKYBUDDY_LLM_SERVER_HOST", "127.0.0.1")
LLM_SERVER_PORT: int = int(os.environ.get("TALKYBUDDY_LLM_SERVER_PORT", "8080"))
```
Naming convention: prefix `TALKYBUDDY_` for every new env var (all existing vars in this file follow this — `TALKYBUDDY_PIPELINE_PROFILE`, `TALKYBUDDY_LLM_N_CTX`, `TALKYBUDDY_CONSENT_GRANTED`, `TALKYBUDDY_LLM_THREADS` per RESEARCH.md Pattern 3). Keep `LLM_N_CTX` as-is (still consumed by the CLI-argv builder, not removed from config.py — RESEARCH.md is explicit that `config.py` remains the single source of truth even though the *consumption point* moves to a shell-launched argv).

---

### `edge/runtime/run_llama_server.py` (NEW, utility, transform) — argv builder

**No direct analog exists** (first "build a CLI argv as a testable Python function" module in the repo). Closest structural pattern: `server/config.py`'s env-driven default resolution, combined with RESEARCH.md's own worked example (Pattern 2/Pitfall 2, `08-RESEARCH.md` lines 296-307, 341-349):
```python
def build_llama_server_argv(model_path, ctx_size, host, port, threads) -> list[str]:
    return [
        "./llama-server",
        "--model", str(model_path),
        "--ctx-size", str(ctx_size),
        "--host", host,
        "--port", str(port),
        "--threads", str(threads),
    ]
```
This module exists specifically so `tests/test_llm_n_ctx_profile.py` (rewritten) can assert `"--ctx-size" in argv and argv[argv.index("--ctx-size") + 1] == "999"` without shelling out. Keep the function pure (no I/O), consistent with how `server/config.py` keeps value-resolution free of side effects.

**Critical constraint (RESEARCH.md Open Question 2):** the caller providing `host` MUST default to `"127.0.0.1"`, never `"0.0.0.0"` — do not mirror `run_edge.sh`'s `--host 0.0.0.0` (line 31) here, that convention is uvicorn-specific and would expose an unauthenticated LLM endpoint.

---

### `edge/runtime/run_edge.sh` (MODIFIED, launcher) — also launch `llama-server`

**Analog:** current file itself (`edge/runtime/run_edge.sh`, full 32 lines already read). Extend using the same idioms already present:
- Relative self-location (`SCRIPT_DIR`/`TARGET_ROOT` via `BASH_SOURCE[0]`, lines 16-18) — reuse for locating the pushed `llama-server` binary path.
- venv-first-fallback-to-system pattern (lines 25-29) — same `if [ -x ... ]` style should gate whether `llama-server` binary is executable before `exec`-ing it.
- `set -euo pipefail` at top (line 14) — keep.

**Health-check wait pattern** (from RESEARCH.md Code Examples, `08-RESEARCH.md` lines 424-437 — copy verbatim as the concrete snippet to insert before starting uvicorn):
```bash
./llama-server --model /path/to/model.gguf --ctx-size 512 \
  --host 127.0.0.1 --port 8080 --threads 4 &
LLAMA_SERVER_PID=$!

for i in $(seq 1 30); do
  curl -sf http://127.0.0.1:8080/health && break
  sleep 1
done
```
Do not change the final `exec "${PYTHON_BIN}" -m uvicorn server.app:app --host 0.0.0.0 --port 8787` line (line 31) — uvicorn's `0.0.0.0` bind is an existing, already-accepted risk (per 07-03 decisions), unrelated to the new llama-server bind which MUST be `127.0.0.1`.

---

### `edge/runtime/audio_io.py` (NEW, utility, file-I/O/streaming) — ALSA capture/playback wrapper

**No direct analog** (first audio-capture module). Borrow two things from existing code:
1. **WAV byte handling contract** — `server/pipeline.py`'s RIFF-sniff fast path expects raw 16k mono WAV bytes (see CONTEXT.md/RESEARCH.md references); `audio_io.capture_16k_mono_wav() -> bytes` must produce exactly this format, no ffmpeg conversion (per `edge/runtime/provision_device.sh` lines 21-23 "不裝 ffmpeg" comment — treat as a hard constraint already established for this codebase).
2. **Try/except degrade idiom** for the `sounddevice`-vs-`arecord`/`aplay` fallback (D-04/Pitfall 5) — mirror `EdgeLLM.available()` / `CloudLLM.available()` style (`try: ... except Exception: return False/None`), e.g. attempt `import sounddevice`, on `ImportError`/`OSError` fall back to `subprocess.run(["arecord", ...])`.

**Subprocess pattern reference:** no existing subprocess-based audio call in repo; use stdlib `subprocess.run(["arecord", "-D", "default", "-f", "S16_LE", "-r", "16000", "-c", "1", "-d", str(seconds), out_path], check=True)` per RESEARCH.md Standard Stack table (`alsa-utils` supporting library entry) — this is a RESEARCH.md-cited pattern, not a codebase analog, since no prior subprocess-audio code exists.

---

### `edge/runtime/local_client.py` (NEW, service/client, event-driven/streaming) — WS client loop

**Analog (wire protocol, server side):** `server/app.py::ws_talk` (lines 341-480) — defines the protocol this new client must speak: binary WAV frame(s) followed by `{"type": "audio_end"}` text frame; server replies with `{"type": "tts_audio", ...}` (line 404) among other message types.

**Analog (wire protocol, existing client side):** `web/live-client.js` line 71:
```js
this.ws = new WebSocket(liveWsURL() + (this.cb.continuous ? "?mode=continuous" : ""));
```
Shows the existing convention for connecting to a `/ws/*` endpoint from a client; `local_client.py` is the same idea in Python via the `websockets` package, targeting `/ws/talk` instead of `/ws/live`.

**Core pattern** (RESEARCH.md Pattern 1 code example, `08-RESEARCH.md` lines 214-233 — copy as the concrete skeleton, already vetted against the real wire protocol in `server/app.py`):
```python
import asyncio, base64, json
import websockets
from edge.runtime import audio_io

async def run_loop():
    async with websockets.connect("ws://127.0.0.1:8787/ws/talk") as ws:
        while True:
            await audio_io.wait_for_button_or_vad_trigger()
            wav_bytes = audio_io.capture_16k_mono_wav()
            await ws.send(wav_bytes)
            await ws.send(json.dumps({"type": "audio_end"}))
            async for msg in ws:
                event = json.loads(msg)
                if event["type"] == "tts_audio":
                    audio_io.play_wav_bytes(base64.b64decode(event["wav_b64"]))
                if event["type"] == "idle":
                    break
```
**Verify against `server/app.py` before finalizing:** confirm the actual `tts_audio` payload key name (`wav_b64` vs other) by reading `server/app.py` lines ~395-410 at implementation time — RESEARCH.md's skeleton is illustrative, not verified byte-for-byte against the current `ws_talk` handler.

**Startup-ordering constraint** (RESEARCH.md Pattern 1 trade-off): `local_client.py` must not connect until uvicorn's health check passes — reuse the same `curl`-loop idiom shown in the `run_llama_server` health-check snippet above, adapted to poll `server/app.py`'s existing health endpoint (check `server/app.py` for its actual health-check route name before wiring this in `run_edge.sh`).

---

### `edge/deploy/build.sh` (MODIFIED, build) — add llama.cpp cross-compile + sanity check

**Analog:** current file itself (32 lines, already read). Existing structure to extend in place:
```bash
SRC_PATHS=(
  "server"
  "edge/runtime"
)

for p in "${SRC_PATHS[@]}"; do
  if [ ! -e "${REPO_ROOT}/${p}" ]; then
    echo "ERROR: 找不到預期載荷路徑：${p}" >&2
    exit 1
  fi
  echo "  - ${p} (OK)"
done
```
The file's own TODO comment (lines 27-30) explicitly flags this as the Phase 8 insertion point: `"TODO：llama.cpp / sherpa-onnx native binary 交叉編譯屬 Phase 8"`. Add the `cmake`/cross-compile invocation from RESEARCH.md Standard Stack (`08-RESEARCH.md` lines 90-115) plus the `file`/`ldd` fast-screen from Pitfall 1 (`08-RESEARCH.md` lines 326-334) as new steps, keeping the same `echo "  - X (OK)"` / `exit 1` error-reporting idiom already used for `SRC_PATHS` checks.

---

### `edge/deploy/push.sh` (MODIFIED, deploy) — rsync native binaries

**Analog:** current file itself (43 lines, already read). Existing rsync-per-path pattern to replicate for the new binaries:
```bash
echo "  - rsync edge/runtime/ -> ${TARGET_ROOT}/edge/runtime"
rsync -az --exclude='__pycache__' --exclude='*.pyc' edge/runtime/ "${SSH_TARGET}:${TARGET_ROOT}/edge/runtime/"
```
Add an analogous block pushing the cross-compiled `llama-server`/`llama-bench` binaries (e.g. `edge/deploy/bin/` or wherever `build.sh` places them), same `rsync -az` flags, same "confirm SSH first" ordering (lines 21-22) preserved before any new rsync block.

---

### `tests/test_llm.py` (MUST REWRITE, test)

**Analog:** the file itself, current version (92 lines, already read) — rewrite in place, preserve test function names and the `_sc()`/`_user_content()` helpers where possible.

**What changes:** replace `_FakeModel.create_chat_completion` interception with monkeypatching the new private HTTP method directly, per RESEARCH.md Pattern 2 explicit guidance (`08-RESEARCH.md` lines 292-293):
```python
monkeypatch.setattr(edge, "_call_llama_server", lambda messages: "...")
```
Current pattern to replace:
```python
fake = _FakeModel("很棒！跟我說一遍：I like apples.")
edge = EdgeLLM()
monkeypatch.setattr(edge, "_get_model", lambda: fake)
```
New pattern (keep `_FakeModel`-style capture-of-messages by turning the lambda into a small closure/class capturing `messages` arg instead of intercepting `create_chat_completion` kwargs):
```python
def _fake_call(messages):
    _fake_call.captured = messages
    return "很棒！跟我說一遍：I like apples."
edge = EdgeLLM()
monkeypatch.setattr(edge, "_call_llama_server", _fake_call)
```
Keep `monkeypatch.setattr(scaffold_mod, "safety_check", lambda _t: False)` unchanged (guardrail plumbing is untouched by this refactor) and keep all assertions on `_user_content` equivalent logic (now reading from `_fake_call.captured` instead of `fake.captured_messages`).

---

### `tests/test_llm_n_ctx_profile.py` (MUST REWRITE, test)

**Analog:** the file itself, current version (112 lines, already read) — rewrite in place; keep the profile/env-reload tests (`test_default_profile_llm_n_ctx_is_512`, `test_edge_profile_llm_n_ctx_is_512`, `test_cloud_profile_llm_n_ctx_is_1024`, `test_env_override_wins_over_*`) completely unchanged — these test `config.py` only and are unaffected by the HTTP refactor.

**What changes:** only `test_get_model_uses_config_llm_n_ctx` (lines 93-112), which currently does:
```python
monkeypatch.setattr("llama_cpp.Llama", _FakeLlama)
monkeypatch.setattr(EdgeLLM, "_model", None)
edge = EdgeLLM()
model = edge._get_model()
assert model.kwargs["n_ctx"] == 999
```
Replace with an assertion against the new `build_llama_server_argv()` function (per RESEARCH.md Pitfall 2, `08-RESEARCH.md` lines 341-349):
```python
from edge.runtime.run_llama_server import build_llama_server_argv

argv = build_llama_server_argv(model_path="/fake/model.gguf", ctx_size=config.LLM_N_CTX, host="127.0.0.1", port=8080, threads=4)
assert argv[argv.index("--ctx-size") + 1] == "999"
```
Remove `_FakeLlama`/`_FakeGguf` classes (no longer needed — no `Llama(...)` object is constructed anywhere in this refactor).

## Shared Patterns

### Lazy-import + degrade-to-None/False (applies to `server/llm.py`, `edge/runtime/audio_io.py`)
**Source:** `server/llm.py::available()` (lines 56-70), `server/cloud_llm.py::available()` (lines 50-55) — both use `try: ... except Exception: return False`. Every new network/subprocess call point (`EdgeLLM._call_llama_server`, `audio_io`'s `sounddevice`-vs-`arecord` fallback) must follow this exact idiom: never let an exception propagate out of a degrade-capable boundary.

### Env-var config resolution (`TALKYBUDDY_*` prefix, `os.environ.get(name, default)`)
**Source:** `server/config.py` lines 51-140 (every single config value in this file follows this convention). Apply to all new config additions (`LLM_SERVER_HOST/PORT`, thread count).

### stdlib-only HTTP (no `requests`/`httpx`)
**Source:** `server/cloud_llm.py` lines 1-16 — the project already has a working precedent for stdlib-only HTTP client code (`urllib.request`/`urllib.error`), used as direct justification in RESEARCH.md to avoid adding new pip dependencies for `EdgeLLM`'s HTTP client. Apply to `server/llm.py::_call_llama_server`.

### Guardrail/target-append tail (do not modify)
**Source:** `server/llm.py::generate()` lines 148-159 and `server/cloud_llm.py::generate()` lines 89-98 — identical tail logic (`guardrails.passes_guardrail(text)` check, target-sentence append-if-missing). This logic is untouched by the HTTP refactor and must be preserved verbatim in the rewritten `EdgeLLM.generate()`.

### Shell script structure (`set -euo pipefail`, self-locating `SCRIPT_DIR`/`REPO_ROOT`, numbered `echo "=== [n/N] ... ==="` section headers)
**Source:** all four existing `edge/deploy/*.sh` and `edge/runtime/*.sh` scripts follow this identical structure. Apply to any new scripts (`run_llama_server.sh` if created as a shell wrapper) and to the modifications of `build.sh`/`push.sh`/`run_edge.sh`.

## No Analog Found

None — every file has at least a role-match or exact analog. Files without a *direct* prior implementation (`audio_io.py`, `local_client.py`, `run_llama_server.py`) still have concrete protocol/idiom analogs documented above (server-side WS contract in `server/app.py`, config-resolution idiom in `server/config.py`, degrade idiom in `server/llm.py`/`server/cloud_llm.py`) and RESEARCH.md-provided code skeletons that the planner should treat as the primary source for these three genuinely-new modules.

## Metadata

**Analog search scope:** `server/`, `edge/runtime/`, `edge/deploy/`, `tests/`, `web/` (JS wire-protocol reference only)
**Files scanned:** `server/llm.py`, `server/cloud_llm.py`, `server/config.py`, `server/app.py` (ws_talk section), `edge/runtime/run_edge.sh`, `edge/runtime/provision_device.sh`, `edge/deploy/build.sh`, `edge/deploy/push.sh`, `tests/test_llm.py`, `tests/test_llm_n_ctx_profile.py`, `web/live-client.js`
**Pattern extraction date:** 2026-07-25
