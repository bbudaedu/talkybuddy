# Phase 11: Cloud Teacher Closed-Loop - Pattern Map

**Mapped:** 2026-07-27
**Files analyzed:** 6 (2 modify-heavy, 4 modify-light / add)
**Analogs found:** 6 / 6

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `server/sync_client.py::push_pending()` | service | request-response (outbound HTTP + gate) | `server/pipeline.py:265-295` (cloud-LLM/cloud-TTS consent gate) | exact (same gate pattern, different call site) |
| `server/pipeline.py` (per-turn end + network_mode transition hooks) | service | event-driven | `server/pipeline.py:320-378` (`_refresh_directive` post-turn hook) & `server/app.py:254-314` (`/api/network_mode` transition) | exact |
| `server/app.py::/api/network_mode` (add opportunistic push call) | route | request-response | same handler, existing `mark_all_synced()` call site (`server/app.py:288-298`) | exact |
| `server/store.py` (student display-name source) | model | CRUD | `server/store.py::get_profile()`/`save_profile()` (`student_profile` table, lines 457-490) | role-match |
| `server/app.py` (new endpoint or extend `/api/status` for display name) | route | request-response | `server/app.py::api_diagnoses`/`api_agent_outputs` (`server/app.py:347-368`) | exact |
| `web/teacher.html` (replace hardcoded name with API fetch) | component | request-response | `web/teacher.html::refresh()` (`web/teacher.html:678-694`) | exact |
| `tests/test_sync_client.py` (new consent/deidentify/whitelist cases) | test | request-response | existing `test_push_pending_sends_unsynced_and_marks` (`tests/test_sync_client.py:7-19`) | exact |
| `tests/test_guardrails.py` or new whitelist test | test | transform | existing `deidentify`/`consent_granted` test blocks (`tests/test_guardrails.py:55-123`) | exact |

## Pattern Assignments

### `server/sync_client.py::push_pending()` (service, request-response)

**Analog:** `server/pipeline.py` cloud branches (LLM at 265-276, cloud-TTS at 440-454)

**Consent-gate pattern to copy** (`server/pipeline.py:265-276`):
```python
# LLM 加值：cloud → edge → scaffold 降級鏈；任一層逾時/例外/None 續試下一層。
# 雲端只在 network_mode=="cloud" 且取得家長同意時進入（資料出境 chokepoint）。
t_llm = time.monotonic()
llm_text: str | None = None
engines = []
if (
    self.network_mode == "cloud"
    and self.cloud_llm is not None
    and self.cloud_llm.available()
    and guardrails.consent_granted()
):
    engines.append(self.cloud_llm)
```
The invariant to replicate in `push_pending()`: **check `guardrails.consent_granted()` before the network call, and return early (no HTTP) when it is False.** This matches D-02 ("consent 未授權時... 在打網路之前就返回，不得先送出再判斷").

**Same pattern at the /api/network_mode gate** (`server/app.py:273-282`), which is the reference for "gate before mutating any cloud state":
```python
# B4-5 consent gate：切雲端前先驗家長同意；未同意 → 強制 edge-only，
# 不同步、不產雲端診斷、不動 directive 快取（資料不出境）。
if mode == "cloud" and not guardrails.consent_granted():
    pipeline.network_mode = "edge"
    return {
        "network_mode": "edge",
        "synced": 0,
        "new_diagnosis": None,
        "consent_required": True,
    }
```

**Current `push_pending()` body to modify** (`server/sync_client.py:12-25`) — note it currently has zero gates and sends `pending` (raw dicts) directly as `json["interactions"]`:
```python
def push_pending(base_url: str, token: str, http_post) -> dict:
    pending = [it for it in store.list_interactions(limit=100000) if not it.get("synced")]
    if not pending:
        return {"accepted": 0, "skipped": 0}
    payload = {"interactions": pending}
    headers = {"Authorization": f"Bearer {token}"}
    resp = http_post(f"{base_url}/api/sync", payload, headers)
    if resp.get("accepted", 0) or resp.get("skipped", 0):
        store.mark_all_synced()
    return resp
```
Required changes per CONTEXT.md decisions:
1. Insert `if not guardrails.consent_granted(): return {"accepted": 0, "skipped": 0, "consent_required": True}` immediately after the `pending` empty-check, before building `payload` — no network call when ungranted (D-02).
2. Apply `guardrails.deidentify()` to text fields only at payload-build time (D-01) — do not mutate `store` rows, only the outbound dict copies.
3. Apply an explicit whitelist constant (D-04) when building each item of `payload["interactions"]` — copy only whitelisted keys per item, `student_id` MUST be included, display name MUST NOT be (D-05).
4. Fix `mark_all_synced()` all-or-nothing bug (see Shared Patterns below) so partial cloud rejection doesn't silently drop pending rows (D-02 precondition).

**Deidentify call signature to copy** (`server/guardrails.py:102-123`):
```python
def deidentify(text) -> str:
    """遮罩明顯個資（人名/電話/住址），保留詞庫學習詞。純字串處理、不炸。"""
    if not text:
        return text or ""
    ...
```
Call as `guardrails.deidentify(item.get("student_text", ""))` etc. — it is a pure string transform, safe to call per-field.

---

### Two-layer trigger — `network_mode` transition + per-turn hook (D-03)

**(a) network_mode edge→cloud transition analog** — `server/app.py::api_network_mode` (`server/app.py:254-314`). This is the existing hook site; opportunistic `push_pending()` call should sit alongside the existing `mark_all_synced()` / `generate_diagnosis()` sequence at line 288-298:
```python
pipeline.network_mode = mode
if mode == "edge":
    return {"network_mode": "edge", "synced": 0, "new_diagnosis": None}

# cloud：補同步 + 產出新診斷（mock Hermes/Bedrock 雲端層）
just_synced = store.mark_all_synced()
new_diag = None
try:
    ...
except Exception:
    # 診斷失敗不影響同步結果（demo 韌性優先）
    new_diag = None
```
Note this handler already calls `store.mark_all_synced()` directly (not `sync_client.push_pending()`) — it's an in-process transition, not an HTTP round-trip to a remote cloud endpoint. `push_pending()` is the device-side (edge box → cloud server) HTTP client; the planner needs to decide whether D-03(a) means "call `sync_client.push_pending()` from here" (if this endpoint models the device call-out) or whether this endpoint IS the cloud-receiving side and the trigger belongs on the device runtime loop instead. Given `server/app.py:375 /api/sync` is the receiving endpoint and `sync_client.py` is explicitly the device-side sender, the D-03(a) hook most likely belongs wherever the device polls/observes `network_mode` (search for the device runtime's network-mode read loop, e.g. `edge/runtime/` — not found in server/, flag for planner to locate the device-side poller).

**(b) per-turn end hook analog** — `server/pipeline.py::_refresh_directive` trigger site (`server/pipeline.py:320-324`):
```python
# B1：每 N 個成功回合，背景（不 await）觸發導師更新 companion_directive
self._turn_count += 1
if DIRECTIVE_REFRESH_EVERY > 0 and self._turn_count % DIRECTIVE_REFRESH_EVERY == 0:
    asyncio.create_task(self._refresh_directive())
```
This is the established "per-turn end hook, fire-and-forget background task" pattern — copy the shape (`asyncio.create_task(...)`, guarded by a condition, not awaited) for D-03(b)'s "each turn end, if online and pending, push once" trigger. The condition should be `self.network_mode == "cloud" and store.pending_count() > 0` instead of the turn-count modulo.

**Background task safety pattern to copy** — `_refresh_directive()` itself (`server/pipeline.py:329-378`) shows the established idiom for background cloud work: a re-entrancy guard flag (`self._directive_refreshing`), full `try/except/finally`, and an explicit `_log.exception(...)` on failure (never a silent `except: pass`) — reuse this shape for the opportunistic push background task.

---

### `server/store.py` — student display-name source (D-05)

**Analog:** `student_profile` table + `get_profile()`/`save_profile()` (`server/store.py:139-144`, `457-490`)

Table already exists:
```python
conn.execute(
    "CREATE TABLE IF NOT EXISTS student_profile ("
    " student_id TEXT PRIMARY KEY,"
    " payload TEXT NOT NULL,"
    " updated_at TEXT NOT NULL)"
)
```
`payload` is freeform JSON (no schema) — same shape as `interactions.payload`. Minimal-change option per CONTEXT.md discretion: add a `display_name` key to the `student_profile` payload dict and read it via `store.get_profile(student_id)["display_name"]`, following the exact `get_profile`/`save_profile` read/write pattern already in place (lines 457-490 above). This avoids a new table/migration.

`_student_id()` fallback pattern (`server/store.py:37,67-69`) is the existing "single demo student" convention:
```python
_FALLBACK_STUDENT_ID = "STUDENT-AMING-004"
...
def _student_id() -> str:
    return getattr(cfg, "STUDENT_ID", _FALLBACK_STUDENT_ID) if cfg else _FALLBACK_STUDENT_ID
```
A `_FALLBACK_STUDENT_NAME = "阿明"` constant mirroring this would be a reasonable analog for a hardcoded fallback display name, used only when no `student_profile.display_name` is set yet (e.g., before seeding).

---

### `server/app.py` — endpoint exposing display name

**Analog:** `api_diagnoses` / `api_agent_outputs` (`server/app.py:347-368`) — same auth + `_resolve_student` pattern:
```python
@app.get("/api/diagnoses")
async def api_diagnoses(student: str | None = None,
                        authorization: str | None = Header(default=None)):
    """全部診斷（date 升冪）；student 讀自己，tutor/device 需帶 ?student=。"""
    claims = identity_from_header(authorization)
    sid = _resolve_student(claims, student)
    return store.list_diagnoses(student_id=sid)
```
Follow this exact shape for whatever endpoint exposes the name (either a new `/api/student_profile` GET, or add `display_name` to an existing response like `/api/status` at `server/app.py:177` which already returns `network_mode`). `_resolve_student(claims, student_query)` (`server/app.py:327-334`) is the shared auth-scoping helper to reuse, not reinvent.

---

### `web/teacher.html` — replace hardcoded name with API read

**Analog:** `refresh()` function (`web/teacher.html:678-694`), which already does the `fetch(..., {headers: authHeaders()}).then(r => r.json())` → `state.x = ...` → `renderAll()` pattern for `/api/diagnoses`, `/api/interactions`, `/api/agent_outputs`:
```javascript
function refresh() {
  var q = encodeURIComponent(STUDENT_QUERY);
  return Promise.all([
    fetch('/api/diagnoses?student=' + q, { headers: authHeaders() }).then(function (r) { return r.json(); }),
    fetch('/api/interactions?limit=20&student=' + q, { headers: authHeaders() }).then(function (r) { return r.json(); }),
    fetch('/api/agent_outputs?limit=10&student=' + q, { headers: authHeaders() }).then(function (r) { return r.json(); })
  ]).then(function (res) {
    state.diagnoses = Array.isArray(res[0]) ? res[0] : [];
    ...
    renderAll();
  }).catch(function () {
    document.getElementById('offline').classList.add('show');
  });
}
```
Add a 4th `Promise.all` entry fetching the display-name endpoint, store into `state.profile` (or similar), and in the render function (search for where `document.querySelector('.stu-name')` or similar would go near where `阿明` is currently hardcoded at line 196) substitute `esc(state.profile.display_name || '…')`.

**Hardcoded values to remove:**
- `web/teacher.html:196` — `<div class="stu-name">阿明</div>` → render from fetched state.
- `web/teacher.html:200` — `學生編號 <code>STUDENT-AMING-004</code>` → can stay as `STUDENT_QUERY` (student_id is not PII-sensitive per D-05 table) but should be rendered dynamically too for consistency, not hardcoded twice.
- `web/teacher.html:260` — `var STUDENT_QUERY = 'STUDENT-AMING-004';` — this is the query param driving which student to fetch; it is a legitimate demo-mode constant (single hardcoded student for the finals), NOT the same issue as the display-name hardcode. CONTEXT.md D-05 only requires the *name* to stop being mocked, not necessarily `STUDENT_QUERY` itself — flag this distinction for the planner so scope doesn't creep into multi-student support (deferred, see CONCERNS.md).

---

## Shared Patterns

### Consent gate before any cloud/network call
**Source:** `server/pipeline.py:271-274` (LLM), `server/pipeline.py:449-452` (cloud TTS), `server/app.py:275` (`/api/network_mode`)
**Apply to:** `server/sync_client.py::push_pending()`, and wherever D-03(a)/(b) triggers call it.
```python
guardrails.consent_granted()   # check first, no side effects yet
# ... only if True: proceed to network / cloud call
```

### Background fire-and-forget task with re-entrancy guard + logged failure
**Source:** `server/pipeline.py::_refresh_directive` (`server/pipeline.py:320-378`)
**Apply to:** D-03(b) per-turn-end opportunistic push hook.
```python
if self._directive_refreshing:
    return
self._directive_refreshing = True
try:
    ...
except Exception:
    _log.exception("...失敗，沿用前一版...")  # never silent except: pass
finally:
    self._directive_refreshing = False
```

### mark_all_synced() partial-failure bug (must fix, blocks D-02)
**Source:** `server/store.py:252` region (`mark_all_synced`), current bug described in CONTEXT.md `<specifics>`:
> `server/sync_client.py:23-24` 目前只要 `accepted` 或 `skipped` 任一大於 0，就把**全部** pending 標記已同步。

Current buggy caller code (`server/sync_client.py:23-24`):
```python
if resp.get("accepted", 0) or resp.get("skipped", 0):
    store.mark_all_synced()
```
This must change to only mark the specific accepted/skipped records synced (by seq, per CONTEXT.md's "由 executor 決定" note), not all pending — otherwise partially-rejected records are silently lost, violating D-02.

### Test convention: dependency injection for external calls
**Source:** `tests/test_sync_client.py:7-19` (`push_pending(base_url, token, http_post)` with `fake_post` closure), `tests/test_guardrails.py:104-123` (`monkeypatch.setattr(config, "CONSENT_GRANTED", False)` for gate tests)
**Apply to:** new tests for consent-gate-blocks-network, deidentify-applied-at-payload-build, whitelist-strips-unlisted-field.
```python
def test_push_pending_sends_unsynced_and_marks():
    store.add_interaction({"student_text": "t1", "device_id": "D1", "client_ts": "2026-07-10T10:00:00"})
    sent = {}

    def fake_post(url, json, headers):
        sent["url"] = url
        sent["count"] = len(json["interactions"])
        return {"accepted": sent["count"], "skipped": 0}

    res = sync_client.push_pending("http://cloud", "tok", fake_post)
    assert sent["count"] == 1
    assert res["accepted"] == 1
    assert store.pending_count() == 0
```
For the consent gate test, follow `tests/test_guardrails.py:111-115` shape:
```python
def test_consent_granted_respects_config_false(monkeypatch):
    from server import config
    monkeypatch.setattr(config, "CONSENT_GRANTED", False)
    assert guardrails.consent_granted() is False
```
Combine both idioms: `monkeypatch.setattr(config, "CONSENT_GRANTED", False)` + assert `fake_post` is never called (e.g., a `called = []` sentinel list that stays empty) — this directly tests D-02's "must return before hitting network."

For the whitelist test (D-04 requirement per CONTEXT.md: "新增一個未列入白名單的欄位，必須被剝除"), add an interaction with an extra non-whitelisted field (e.g., `audio_path`) and assert it is absent from the `fake_post`-captured payload while `student_id` is present.

## No Analog Found

| File | Role | Data Flow | Reason |
|---|---|---|---|
| Device-side `network_mode` poller (D-03(a) actual trigger site) | unclear (edge runtime, not found under `server/`) | event-driven | `server/app.py::api_network_mode` mutates `pipeline.network_mode` in-process on the server, but `sync_client.push_pending()` is described in its own docstring as the device-side HTTP client to a remote cloud. No existing code in this repo shows a device process observing `network_mode` and reacting — this repo appears to run pipeline + app.py together (single-process demo), so D-03(a)'s "device notices edge→cloud transition" is likely most naturally implemented as: call `sync_client.push_pending()` (or a local equivalent using `store` directly, bypassing HTTP since it's same-process) right inside `api_network_mode`'s cloud branch, alongside the existing `mark_all_synced()` call. Planner should confirm this collapses to "call the opportunistic-push helper directly in `api_network_mode`" rather than needing a separate device-side loop, given the demo topology is a single Genio 520 process serving both device and teacher dashboard.

## Metadata

**Analog search scope:** `server/`, `web/teacher.html`, `tests/`
**Files scanned:** `server/sync_client.py`, `server/guardrails.py`, `server/pipeline.py`, `server/app.py`, `server/store.py`, `web/teacher.html`, `tests/test_sync_client.py`, `tests/test_guardrails.py`
**Pattern extraction date:** 2026-07-27
