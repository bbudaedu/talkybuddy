# Phase 9: Network-Cut Demo Hardening - Pattern Map

**Mapped:** 2026-07-25
**Files analyzed:** 6 (all modifications to existing files; no new files this phase)
**Analogs found:** 6 / 6 (self-analogous — every touched file already contains the pattern to extend, in-file)

## Scope Note

This phase is corrective/tuning work on an already-built feature (RESEARCH.md: "pure codebase archaeology, not library evaluation"). There are **no new files** to create. Every "pattern assignment" below is therefore an **in-file extension pattern**: the analog for each edit is the surrounding code in the same file (existing sibling call sites, existing constant declarations, existing test cases). CONTEXT.md's D-01/D-04 explicitly reject new UI/routes; 09-UI-SPEC.md locks the UI surface to 4 existing elements with no new component. RESEARCH.md independently confirms "No new files/folders."

## File Classification

| File to Modify | Role | Data Flow | Change Type | Closest In-File Analog |
|---|---|---|---|---|
| `server/app.py` | route handler / WS controller | request-response + event-driven (WS) | add 2× one-line state re-sync | sibling call site (the other of the two, each is the other's analog) |
| `server/pipeline.py` | service (state machine) | CRUD + request-response | tune constant only, no structural change | the constant's own existing declaration + comment convention |
| `server/cloud_llm.py` | service (external HTTP client) | request-response | tune constant only | the constant's own existing declaration + comment convention |
| `server/config.py` | config | — | tune default only | sibling `*_TIMEOUT_S` env-driven constants in same file |
| `tests/test_e2e.py` | test (integration, WS) | event-driven | add 1 new test function | `test_ws_talk_text_input_full_flow` (same file) |
| `tests/test_pipeline_cloud.py` / new `tests/test_pipeline_timeout_isolation.py` | test (unit) | request-response | add 1-2 new test functions | `tests/test_pipeline.py::test_llm_timeout_falls_back_to_scaffold_text` |

## Pattern Assignments

### `server/app.py` — NETCUT-01 fix (Pitfall 1, CRITICAL, must land first)

**Analog:** the file's own existing per-connection state model, `server/app.py:365-369`.

**Current (broken) connect-time-only copy** (lines 364-369):
```python
    # 每連線一個獨立 VoicePipeline（共用引擎、綁 student_id），解單例污染
    conn_pipe = VoicePipeline(
        asr_engine, llm_engine, tts_engine,
        cloud_tts=cloud_tts_engine, cloud_llm=cloud_llm_engine, student_id=sid,
    )
    conn_pipe.network_mode = pipeline.network_mode  # 承接目前模式
```
This line stays (it's a correct *initial* value) — the bug is that nothing re-reads `pipeline.network_mode` afterward.

**Fix site 1 — `process_audio_buffer()`** (lines 410-421), insert the re-sync as the first statement inside the `try:` block, immediately before `run_turn_audio`:
```python
    async def process_audio_buffer() -> None:
        """把緩衝的錄音整包送進 pipeline（空緩衝直接略過）。"""
        if not audio_buffer:
            return
        data = bytes(audio_buffer)
        audio_buffer.clear()
        try:
            conn_pipe.network_mode = pipeline.network_mode  # NEW: 每輪前重新承接全域模式
            result = await conn_pipe.run_turn_audio(data, emit)
            await send_turn_result(result, include_asr=True)
        except Exception:
            # 單輪失敗不斷線：回 idle 讓前端解除等待
            await emit({"type": "state", "state": "idle"})
```

**Fix site 2 — `text_input` branch** (lines 472-478), same one-line addition, same position (first line inside `try:`):
```python
            elif mtype == "text_input":
                text = str(payload.get("text", "") or "")
                try:
                    conn_pipe.network_mode = pipeline.network_mode  # NEW: 同上，第二個呼叫點
                    result = await conn_pipe.run_turn_text(text, emit)
                    await send_turn_result(result, include_asr=False)
                except Exception:
                    await emit({"type": "state", "state": "idle"})
```

**Existing state-authority pattern to preserve** — `POST /api/network_mode` (lines 206-259) already writes only the *global* `pipeline.network_mode` (line 229: `pipeline.network_mode = mode`); this is intentionally left unchanged. The global remains the single source of truth; the fix only adds *readers* of it, never a second writer.

**Convention note:** both `try:`/`except Exception:` blocks already swallow all errors and emit `{"type": "state", "state": "idle"}` — the new line must go *inside* the existing `try:`, not before it, to preserve this error-handling contract (a broken re-sync should degrade the same way any other turn failure does, not crash the WS loop).

---

### `server/pipeline.py` — `LLM_TIMEOUT_S` (Pitfall 2, do NOT slash)

**Analog:** the constant's own existing declaration and comment style, lines 28-29:
```python
# LLM 加值生成的逾時秒數（契約：>8s 即降級用 scaffold 結果；測試可 monkeypatch）
LLM_TIMEOUT_S: float = 8.0
```
**Guidance for the plan:** leave this at 6-8s (touch minimally, if at all). It gates **every** engine in the shared loop at lines 269-281 (`for engine in engines: ... asyncio.wait_for(..., timeout=LLM_TIMEOUT_S)`), including edge, which Phase 8 hardware data shows can legitimately take up to 4170ms. Do not treat this as one of the "縮短" targets despite CONTEXT.md D-03 naming it — RESEARCH.md's Pitfall 2/5 supersedes a literal reading, and the reconciliation should be called out explicitly in the plan.

**Comment-update pattern:** if the constant's meaning changes (e.g., docstring clarifying it's now edge-oriented / shared-loop-oriented rather than "the" timeout), follow the existing inline-comment convention shown above — Traditional Chinese, parenthetical contract note, mentions test-monkeypatch use.

**Existing engine-loop structure to leave untouched** (lines 269-281) — do not refactor into per-engine timeouts; RESEARCH.md's Pattern 2 explicitly recommends keeping this loop's structure and only tuning the *inner* per-engine timeouts (`cloud_llm.py::_TIMEOUT_S`, `config.py::CLOUD_TTS_TIMEOUT_S`) instead.

---

### `server/cloud_llm.py` — `_TIMEOUT_S` (safe to shorten aggressively)

**Analog:** the constant's own declaration, lines 20-21:
```python
# 雲端呼叫逾時（秒）；與 pipeline 外層 LLM_TIMEOUT_S 對齊，雙保險。
_TIMEOUT_S = 8.0
```
Used at line 86: `with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:`.

**Guidance:** shorten toward ~2.0-2.5s (RESEARCH.md A1, LOW-MEDIUM confidence on the exact number — final value must be confirmed against real venue network conditions during NETCUT-03 rehearsal). If promoting to env-configurable per RESEARCH.md's Recommended Project Structure note, follow `server/config.py`'s existing `os.environ.get(...)` pattern (see below) rather than inventing a new config-loading style. The inline comment ("雙保險" — double-insurance, aligned with outer `LLM_TIMEOUT_S`) should be updated since after this phase the two are **intentionally decoupled** (inner short, outer generous) — do not leave a stale comment implying they still track each other.

---

### `server/config.py` — `CLOUD_TTS_TIMEOUT_S` (safe to shorten aggressively)

**Analog:** sibling env-driven float constants in the same file, lines 111-115:
```python
# 雲端合成逾時（秒）；逾時即降級回邊緣。
CLOUD_TTS_TIMEOUT_S: float = float(os.environ.get("CLOUD_TTS_TIMEOUT_S", "6.0"))
# 發音評測（B 軸背景，見 server/pronunciation.py）逾時（秒）；含首輪模型載入。
# 逾時→該輪 pron=None 照寫 transcript，避免分數與 interaction 脫鉤。
PRON_SCORE_TIMEOUT_S: float = float(os.environ.get("PRON_SCORE_TIMEOUT_S", "15.0"))
```
**Guidance:** change only the string default `"6.0"` → target ~2.5-3.0s (RESEARCH.md A1), keeping the exact `float(os.environ.get("...", "<default>"))` pattern — this is the established project convention (see also `server/config.py:85,134,140,146-148` per RESEARCH.md Sources) and must not be replaced with a different config-access style. `server/cloud_tts.py:99` consumes this constant unchanged (`urlopen(req, timeout=CLOUD_TTS_TIMEOUT_S)`); no code change needed there beyond the constant's new value.

---

### `tests/test_e2e.py` — NETCUT-01 regression test (Wave 0, required)

**Analog:** `test_ws_talk_text_input_full_flow` (lines 156-192, same file), which already establishes the exact TestClient + WS pattern needed:
```python
def test_ws_talk_text_input_full_flow(monkeypatch):
    from starlette.testclient import TestClient
    from server import app as app_module
    monkeypatch.setattr(app_module.llm_engine, "available", lambda: False)
    monkeypatch.setattr(app_module.tts_engine, "available", lambda: False)
    monkeypatch.setattr(app_module.asr_engine, "available", lambda: False)
    tok = auth.issue_token("STUDENT-AMING-004", "student")
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/talk?token={tok}") as ws:
            ws.send_json({"type": "text_input", "text": "我要一個蘋果"})
            # ... receive/assert loop
```
Also reuse the file's existing autouse fixture (lines 25-30) which resets `pipeline.network_mode` to `"edge"` before/after each test — required since `pipeline` is a module-level singleton shared across tests:
```python
@pytest.fixture(autouse=True)
def _reset_network_mode():
    """app.py 的 pipeline 是模組級單例，跨測試共用；每個測試前後重設為 edge，避免互相汙染。"""
    pipeline.network_mode = "edge"
    yield
    pipeline.network_mode = "edge"
```

**New test shape (per RESEARCH.md Code Examples section):** open WS once → send `text_input` with `network_mode=="cloud"` (monkeypatch a distinguishable cloud stub reply, confirm cloud engine invoked) → **without closing the WS**, POST `/api/network_mode {"mode":"edge"}` on the *same* `TestClient` → send a second `text_input` on the *same still-open* WS → assert the cloud stub was **not** invoked for the second turn. This is the exact regression scenario Pitfall 1 currently breaks; suggested test name: `test_network_mode_switch_affects_live_ws_session` (already named in RESEARCH.md's Test Map).

**Also extend (lower priority, Wave 0 optional):** `test_get_api_status_shape` (lines 55-60 onward) to add an explicit "no `urllib.request.urlopen` call" assertion via monkeypatch-spy, per RESEARCH.md's Wave 0 Gaps.

---

### `tests/test_pipeline.py` / `tests/test_pipeline_cloud.py` — timeout-isolation regression test (Wave 0, required)

**Analog:** `test_llm_timeout_falls_back_to_scaffold_text` (`tests/test_pipeline.py:209-223`, exact monkeypatch pattern to extend):
```python
async def test_llm_timeout_falls_back_to_scaffold_text(monkeypatch):
    """LLM 生成逾時（> LLM_TIMEOUT_S）→ 降級用 scaffold 文字，不因逾時而拋例外。"""
    monkeypatch.setattr(pipeline_mod, "LLM_TIMEOUT_S", 0.05)
    text = "我要一個蘋果"
    expected_reply = scaffold.respond(text).reply_text
    events: list[dict] = []
    emit = await _collecting_emit(events)
    slow_llm = StubLLM(reply="太慢了，不該被採用", available=True, delay_s=0.3)
    vp = VoicePipeline(StubASR(), slow_llm, StubTTS())
    result = await vp.run_turn_text(text, emit)
    assert result.fallback is False
    assert result.reply_text == expected_reply
```
**New test (per RESEARCH.md Code Examples):** construct a `VoicePipeline` with a slow `cloud_llm` stub and a fast, real-ish edge `llm` stub, set `network_mode="cloud"`, `monkeypatch.setattr` `cloud_llm.py`'s `_TIMEOUT_S` short while leaving `pipeline_mod.LLM_TIMEOUT_S` at its generous default — assert the turn still completes via the **edge** engine within a bounded wall-clock window (i.e., edge is not starved by a shared timeout). Existing `StubLLM`/`StubASR`/`StubTTS`/`_collecting_emit` helpers in `tests/test_pipeline.py` should be reused rather than reimplemented — check `tests/test_pipeline_cloud.py` for whether a `StubCloudLLM` already exists there (existing sibling test file, same `tests/` directory, `-k timeout` selector per RESEARCH.md's Test Map).

---

## Shared Patterns

### State-authority re-sync (the core fix)
**Source:** `server/app.py:229` (`pipeline.network_mode = mode`, the one true writer) vs. `server/app.py:369` (the stale one-time-copy site).
**Apply to:** both WS turn-dispatch call sites in `server/app.py` (`process_audio_buffer`, `text_input` branch). Do not add a third writer anywhere — `conn_pipe.network_mode` must only ever be *read from* `pipeline.network_mode`, never written independently.

### Timeout-constant declaration style
**Source:** `server/pipeline.py:28-29`, `server/cloud_llm.py:20-21`, `server/config.py:111-115`.
**Pattern:** module-level float constant, Traditional Chinese inline comment stating (a) what it gates and (b) the fallback behavior on expiry, immediately above the declaration. `config.py`'s constants additionally wrap in `float(os.environ.get("NAME", "default"))` for env-overridability — follow this specifically for any constant the plan chooses to make configurable (RESEARCH.md's structure note suggests doing this for `cloud_llm.py::_TIMEOUT_S` too, promoting it out of a bare literal).
**Apply to:** any of the three timeout constants touched in this phase.

### try/except-swallow + idle-emit error handling (WS turn dispatch)
**Source:** `server/app.py:410-421` and `472-478` (both existing `except Exception: await emit({"type": "state", "state": "idle"})`).
**Apply to:** the new re-sync line must be placed *inside* this existing try block, not before it — preserves the "single turn failure never kills the WS loop" contract already established for both call sites.

### Test isolation for the module-level `pipeline` singleton
**Source:** `tests/test_e2e.py:25-30` (`_reset_network_mode` autouse fixture).
**Apply to:** any new test in `tests/test_e2e.py` that touches `/api/network_mode` or relies on a particular starting `network_mode` — already autouse, no extra wiring needed, but be aware cross-test pollution is the reason this fixture exists.

## No Analog Found

None. Every file in scope is a modification to existing, already-patterned code; RESEARCH.md independently confirms no new files/folders/libraries are introduced by this phase.

## Explicitly Out of Scope (per D-01/D-04/09-UI-SPEC.md — do not create new analog-seeking work for these)
- No new UI component/page/route (`web/index.html`'s `airplaneSwitch`/`applyMode()`/`modeBadge`/toast are reused as-is; only CSS/copy-level tweaks per 09-UI-SPEC.md, no new element).
- No network-detection subsystem (D-02).
- No `asyncio.Task.cancel()` / request-cancellation machinery (D-03).
- `server/diagnose.py`'s `_refresh_directive` cloud-gate (Pitfall 4) is optional/defense-in-depth per RESEARCH.md — if the plan adopts it, the analog is the existing `network_mode == "cloud"` gate already used at `server/pipeline.py:260-266` (same file, same conditional style: `if self.network_mode == "cloud" and ... and guardrails.consent_granted():`).

## Metadata

**Analog search scope:** `server/app.py`, `server/pipeline.py`, `server/cloud_llm.py`, `server/cloud_tts.py`, `server/config.py`, `server/diagnose.py`, `web/index.html`, `tests/test_e2e.py`, `tests/test_pipeline.py`, `tests/test_pipeline_cloud.py`
**Files scanned:** 10 (all directly cited in RESEARCH.md's Sources section; independently re-verified by direct Read in this pass, not re-derived from RESEARCH.md summaries alone)
**Pattern extraction date:** 2026-07-25
