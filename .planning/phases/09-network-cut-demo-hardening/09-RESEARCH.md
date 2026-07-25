# Phase 9: Network-Cut Demo Hardening - Research

**Researched:** 2026-07-25
**Domain:** Existing FastAPI/asyncio backend hardening (timeout tuning + trust-boundary bugfix in an already-built manual kill-switch); no new libraries.
**Confidence:** HIGH (every load-bearing claim below is traced to exact file/line in this repo; the only LOW-confidence numbers are external cloud-API latency benchmarks, clearly flagged and routed to a rehearsal checkpoint rather than presented as fact)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**斷網觸發方式（NETCUT-01）**
- **D-01（鎖定）：** kill-switch = **純軟體 toggle**，沿用/改造既有 `POST /api/network_mode`（`server/app.py:206-259`）與 `web/index.html` 的 `airplaneSwitch`/`applyMode()`（lines 684-732）。不做「真實實體斷網（拔線/關 AP）」路徑——理由：舞台上最穩定、不依賴現場 Wi-Fi 硬體，不會發生「拔了線網路不回來」的風險。
- **D-02（鎖定）：** **不加自動網路偵測安全網**。既然 kill-switch 是純軟體 toggle，`pipeline.network_mode` 直接、確定地閘門雲端呼叫（見 `server/pipeline.py:260-266`），開關狀態與行為完全一致，不需要額外偵測「現場 Wi-Fi 真的斷線」的邏輯。若未來真的改用實體斷網，才需要回頭補這層。
- **D-03（鎖定）：** 中途斷網（主持人在雲端 LLM/TTS 請求進行到一半按下開關）**採「縮短逾時自然降級」，不做 asyncio 取消/重跑機制**。理由：不做取消機制，只把雲端 LLM/TTS 逾時從目前 `_TIMEOUT_S=8.0`（`server/cloud_llm.py:21`）、`CLOUD_TTS_TIMEOUT_S=6.0`（`server/config.py:112`）、`LLM_TIMEOUT_S=8.0`（`server/pipeline.py:29`）大幅縮短（目標對齊 ROADMAP 的 <1–2 秒恢復門檻），遇斷線很快就逾時降級到 edge，避開 asyncio 取消/重跑的競態風險，且該輪對話仍能完整完成（只是走 edge 回覆）。

**主持人操作介面**
- **D-04（鎖定）：** **直接沿用學生畫面上的既有飛航模式按鈕**（`web/index.html` 的 `airplaneSwitch`）當主持人主控，不另開主持人專用頁面/路由，不做鍵盤快捷鍵。現場由主持人親自操作或站在孩子旁邊代為點擊。
- **D-05（鎖定）：** **不加防誤觸機制**。維持現狀行為（單點即切，不彈確認框、不用長按）。現場主持人全程控場；誤點也只是再點一次切回來，不需額外實作。

### Claude's Discretion
- 縮短後的具體逾時數字（如 1s / 1.5s / 2s）由 planner/executor 依 ROADMAP D-05 沿用的「<1–2 秒恢復」門檻與真機/PC 實測結果決定，本輪只鎖定方向（大幅縮短、不做取消機制）。
- `/api/status` 5 秒輪詢（`web/index.html:998`）與教師儀表板 5 秒輪詢（`web/teacher.html:631`）皆為本機 loopback/既有機制，非雲端呼叫，NETCUT-02「背景輪詢於離線視窗暫停」若有適用對象由 executor 依實際程式碼確認後決定範圍（掃描本輪未發現額外背景雲端輪詢器）。
- 現有飛航模式的 toast 文案（「✈️ 飛航模式開啟，改用邊緣端運算」）與小徽章視覺是否需微調文字以呼應「斷網示範」語境，由 executor 依既有風格調整，不視為新決策。

### Deferred Ideas (OUT OF SCOPE)
- **真實實體斷網（拔線/關 AP）作為 kill-switch**：本輪明確不選，若未來現場彩排發現純軟體 toggle 說服力不足（評審質疑「是不是其實還連著網路」），可回頭評估——需同時補上自動偵測邏輯（D-02 的前提會改變）。
- **主持人專用操作介面（獨立頁面/路由或鍵盤快捷鍵）**：本輪明確不選，優先沿用學生畫面既有按鈕。若彩排時發現小朋友頻繁誤觸影響節奏，可回頭補此項。
- **斷網視覺呈現的戲劇化改版**（大徽章/全螢幕狀態轉場）：使用者本輪未選擇討論此主題（僅選了觸發方式與操作介面），現況小徽章+文字予以沿用；若要加強觀眾/評審的「記憶點」效果，可在後續補強或於彩排後依實際觀感決定。
- **自動網路偵測安全網**：本輪因純軟體 toggle 而判定不需要，若觸發方式決策未來改變（見上），需重新評估此項。

**UI-SPEC note:** `09-UI-SPEC.md` (already approved) locks the UI surface to 4 existing elements only (`modeBadge`, `airplaneState`, `airplaneSwitch`, toast), CSS/copy-level tweaks only (badge padding `3px 10px`→`4px 12px`, dot `8px`→`12px`, one-shot `.badge.pulse` animation triggered only from the click-handler's success callback — never from the 5s poll). No new UI component may be introduced. This RESEARCH.md does not re-derive the UI contract; the planner should lift it directly from 09-UI-SPEC.md.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| NETCUT-01 | 主持人手動 kill-switch 為主要斷網機制；切斷雲端 uplink 時裝置持續離線對話（瀏覽器↔本機 server loopback 不受影響） | **Critical gap found and must be fixed** (see Pitfall 1): the existing toggle only updates the module-global `pipeline.network_mode`; an already-open `/ws/talk` session's `conn_pipe.network_mode` is copied once at connect time and never re-synced, so flipping the switch mid-session currently has **no effect** on the live conversation. Concrete fix + regression test specified below. |
| NETCUT-02 | 縮短 / race 雲端 timeout 並暫停背景輪詢，避免斷網時多秒靜默 hang；提供可見的「offline mode」切換 UI / badge | Timeout targets identified with exact line numbers (Pitfall 2/3); **`LLM_TIMEOUT_S` must NOT be uniformly slashed** — it gates the edge LLM too and real-hardware data (Phase 8) shows edge LLM alone can take 4.17s. "背景輪詢暫停" is architecturally moot for the two documented 5s polls (both pure loopback, verified) but a genuine ungated background cloud side-channel (`_refresh_directive`) was found and should be gated too (Pitfall 4). UI badge is already specified in 09-UI-SPEC.md. |
| NETCUT-03 | 實體斷網彩排腳本（重複實機演練，非只自動偵測） | This is real-hardware human-verify work (Genio 520, SSH-reachable per Phase 8). Cannot be executed or fabricated by an agent. Rehearsal script structure, timing-measurement method, and an operational definition of "recovery time" are proposed below (Open Questions + Validation Architecture). Must land as a `checkpoint:human-verify` blocking task. |
</phase_requirements>

## Summary

This phase does not introduce new technology — it hardens an **already-working** manual edge/cloud toggle (`POST /api/network_mode` + `airplaneSwitch`, built in Phases 3/6) so that it behaves correctly and quickly under the specific stress of a live on-stage demo. Research here is pure codebase archaeology, not library evaluation, and it surfaced one **critical, previously-unflagged bug** that must be fixed for NETCUT-01 to actually work as demoed, plus a structural timeout-sharing trap that would break Phase 8's already-hard-won edge latency numbers if D-03 is implemented naively.

**The critical finding:** `server/app.py:369` copies the global `pipeline.network_mode` into a **per-connection** `conn_pipe.network_mode` exactly once, at WebSocket-accept time. `web/index.html` opens exactly one long-lived `/ws/talk` connection at page load (`connectWS()`, no per-turn reconnect) and keeps it open for the whole session. The `airplaneSwitch` click handler POSTs to `/api/network_mode`, which only ever writes the *global* `pipeline.network_mode` (`server/app.py:229`) — nothing re-syncs the already-open connection's copy. **As currently built, flipping the switch mid-conversation has zero effect on the ongoing session**; only a fresh page load (new WS connection) picks up the new mode. This directly contradicts the demo's entire premise ("主持人可隨時手動切斷... 對話持續離線進行") and must be the first task in this phase's plan, not an afterthought.

**The second finding:** `server/pipeline.py:255-282`'s cloud→edge→scaffold engine loop wraps **every** engine attempt — cloud and edge alike — in the *same* `asyncio.wait_for(..., timeout=LLM_TIMEOUT_S)`. CONTEXT.md's D-03 lists `LLM_TIMEOUT_S` as one of three constants to "大幅縮短," but Phase 8's real Genio 520 measurements (`edge/EDGE_TURN_LOOP_VALIDATION.md`) show the edge LLM stage alone legitimately taking up to **4170ms** on a real (non-broken) turn. Uniformly cutting `LLM_TIMEOUT_S` down to the 1-2s range to satisfy the demo's "fast cloud fallback" goal would start truncating *edge* inference too, silently degrading every offline reply to the scaffold fallback — the opposite of Phase 8's deliverable. The fix is to make the cloud engines fail fast **on their own inner timeouts** (`cloud_llm.py::_TIMEOUT_S`, `config.py::CLOUD_TTS_TIMEOUT_S` — both cloud-only, safe to slash aggressively) while leaving `pipeline.py::LLM_TIMEOUT_S` as a generous backstop that only the edge engine actually depends on.

**Primary recommendation:** (1) fix the stale `conn_pipe.network_mode` bug by re-syncing it from the global `pipeline.network_mode` immediately before each turn in `server/app.py` (two call sites); (2) shorten only the cloud-specific timeouts (`cloud_llm.py::_TIMEOUT_S` → ~2.0-2.5s, `config.py::CLOUD_TTS_TIMEOUT_S` → ~2.5-3.0s) and leave `pipeline.py::LLM_TIMEOUT_S` at 6-8s so it never starves the edge engine; (3) treat NETCUT-02's "背景輪詢暫停" as satisfied by architecture for the two documented 5s polls (both proven pure-loopback, no code change needed) but add a one-line `network_mode == "cloud"` gate to the ungated `_refresh_directive` background diagnosis call as defense-in-depth; (4) NETCUT-03 is exclusively a `checkpoint:human-verify` task on real hardware — do not attempt to simulate it.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Kill-switch trigger (host clicks toggle) | Browser / Client | — | `airplaneSwitch` click handler in `web/index.html`, pure client-side event, no new UI per 09-UI-SPEC.md |
| Network-mode state authority | API / Backend | — | `pipeline.network_mode` (global singleton, `server/app.py:56-61`) is the single source of truth the whole backend must consult; this phase's core fix is making the *live conversation* actually read from that authority instead of a stale per-connection copy |
| Cloud-call gating (LLM/TTS) | API / Backend | — | `server/pipeline.py:_process_text`/`_synth_tts` — engine-selection gate checked per-turn against `self.network_mode`; must reflect the current global authority (see fix above) |
| Timeout enforcement for cloud calls | API / Backend | — | `server/cloud_llm.py` (`urlopen` timeout), `server/cloud_tts.py`/`config.py` (`urlopen` timeout) — cloud-specific, safe to shorten independently |
| Timeout enforcement for edge calls | API / Backend | — | Same `asyncio.wait_for(LLM_TIMEOUT_S)` wrapper in `server/pipeline.py`, shared with cloud in the current loop structure — must not be starved by cloud-oriented shortening (Pitfall 2) |
| Offline/online visual state | Browser / Client | API / Backend (source of truth) | `modeBadge`/`airplaneState` render `/api/status`'s `network_mode` field (`server/app.py:146-158`, pure local read, no cloud round trip) — already fully specified in 09-UI-SPEC.md, out of scope for backend research |
| Status/diagnostics polling | Browser / Client ↔ API / Backend (loopback only) | — | `refreshStatus()` (`web/index.html:685-690`, every 5s) and teacher dashboard poll (`web/teacher.html:631`) both hit local-only endpoints (`/api/status`, `/api/diagnoses`, `/api/interactions`) on the *same device*; verified no outbound network call is made by either, so NETCUT-02's "pause polling" has no applicable target here |
| Background diagnosis refresh (side-channel) | API / Backend | Database / Storage | `VoicePipeline._refresh_directive()` (`server/pipeline.py:311-345`) fires every 5 successful turns via `asyncio.create_task`, calling `diagnose.generate_diagnosis()` which makes its **own, ungated** cloud HTTP call if credentials + consent are present — independent of `network_mode` (Pitfall 4) |
| Real-hardware rehearsal timing | Human / Physical stage | Genio 520 device | NETCUT-03 is not automatable; belongs entirely to a `checkpoint:human-verify` task executed on the real board |

## Standard Stack

No new libraries or dependencies are introduced by this phase. All work is constant-tuning and a one-line state-sync fix inside the existing Python stdlib (`asyncio`, `urllib.request`) and existing FastAPI/Starlette app. `npm view`/`pip index versions` verification and the Package Legitimacy Gate are **not applicable**.

### Touched existing modules (for planner reference, not new stack)

| Module | Role in this phase | Confidence |
|--------|--------------------|------------|
| `server/pipeline.py` | Engine-selection loop, `LLM_TIMEOUT_S`, per-connection `network_mode` state, `_refresh_directive` side-channel | [VERIFIED: server/pipeline.py] |
| `server/cloud_llm.py` | `_TIMEOUT_S` (cloud LLM `urlopen` timeout) | [VERIFIED: server/cloud_llm.py] |
| `server/cloud_tts.py` + `server/config.py` | `CLOUD_TTS_TIMEOUT_S` (cloud TTS `urlopen` timeout) | [VERIFIED: server/cloud_tts.py, server/config.py] |
| `server/app.py` | `/api/network_mode` handler, global `pipeline` singleton, per-connection `conn_pipe` creation and turn dispatch (the bug site) | [VERIFIED: server/app.py] |
| `web/index.html` | `airplaneSwitch`/`applyMode()`/`refreshStatus()` — already built, in scope only for optional copy tweaks per 09-UI-SPEC.md | [VERIFIED: web/index.html] |
| `server/diagnose.py` | `generate_diagnosis()` — the ungated background cloud call (Pitfall 4) | [VERIFIED: server/diagnose.py] |

## Package Legitimacy Audit

**Not applicable.** This phase installs no new packages (npm/pip/cargo). All changes are to existing first-party Python constants and one FastAPI WebSocket handler already in the repository. No `package-legitimacy check` run was required.

## Architecture Patterns

### System Architecture Diagram — kill-switch data flow (current, broken, vs. fixed)

```
CURRENT (broken for NETCUT-01):

  [Host clicks airplaneSwitch]
          │  POST /api/network_mode {mode:"edge"}
          ▼
  server/app.py:229  pipeline.network_mode = "edge"   ← updates GLOBAL singleton only
          │
          X   (nothing propagates from here to any already-open WS connection)
          │
  [Already-open /ws/talk session]
  conn_pipe.network_mode  ← was copied ONCE at app.py:369, at connect time
          │
          ▼
  server/pipeline.py:_process_text()
    if self.network_mode == "cloud" ...   ← reads the STALE per-connection copy
          │
          ▼
  Cloud LLM/TTS still attempted — kill-switch has no effect on the live turn


FIXED (required for NETCUT-01):

  [Host clicks airplaneSwitch]
          │  POST /api/network_mode {mode:"edge"}
          ▼
  server/app.py:229  pipeline.network_mode = "edge"   ← GLOBAL singleton, single source of truth
          │
  [Already-open /ws/talk session, NEXT incoming frame]
          │
  server/app.py: conn_pipe.network_mode = pipeline.network_mode   ← NEW: re-sync before each turn
          │        (added immediately before both run_turn_audio() and run_turn_text() call sites)
          ▼
  server/pipeline.py:_process_text()
    if self.network_mode == "cloud" ...   ← now reads the CURRENT global state
          │
          ▼
  Cloud engines list is empty when mode=="edge" → only edge LLM/TTS attempted,
  loopback WS to the browser is entirely unaffected either way.
```

### Recommended Project Structure

No new files/folders. Changes are localized edits to:
```
server/
├── app.py          # add 1-line network_mode re-sync before both run_turn_* call sites (~L417, ~L475)
├── pipeline.py      # LLM_TIMEOUT_S: leave generous (do NOT slash below edge's real-world worst case);
│                     #   optionally gate _refresh_directive's cloud call on network_mode
├── cloud_llm.py     # _TIMEOUT_S: shorten, ideally promote to env-configurable constant
├── cloud_tts.py     # (no direct change; consumes config.CLOUD_TTS_TIMEOUT_S)
└── config.py        # CLOUD_TTS_TIMEOUT_S: shorten default; optionally add CLOUD_LLM_TIMEOUT_S env var
web/
└── index.html        # optional: toast copy tweak only, per 09-UI-SPEC.md (no new elements)
```

### Pattern 1: Re-sync per-connection state from a global authority at turn start, not at connect time

**What:** `conn_pipe` objects are created once per WebSocket lifetime but must reflect a value (`network_mode`) that can change mid-lifetime via an unrelated HTTP endpoint. The fix is to treat the global `pipeline.network_mode` as the single live source of truth and copy it into the connection's pipeline object **immediately before every turn**, not just once at connect time.
**When to use:** Any time a long-lived stateful object (WS session, streaming connection) needs to reflect a value mutated by a separate, short-lived HTTP request.
**Example:**
```python
# Source: server/app.py (existing structure), fix location for NETCUT-01
async def process_audio_buffer() -> None:
    if not audio_buffer:
        return
    data = bytes(audio_buffer)
    audio_buffer.clear()
    try:
        conn_pipe.network_mode = pipeline.network_mode  # NEW — re-sync before this turn
        result = await conn_pipe.run_turn_audio(data, emit)
        await send_turn_result(result, include_asr=True)
    except Exception:
        await emit({"type": "state", "state": "idle"})

# ... same one-line addition immediately before the `text_input` branch's
# `result = await conn_pipe.run_turn_text(text, emit)` call (server/app.py:475)
```
This is the **minimal, surgical** fix — it does not change `VoicePipeline`'s public contract, does not touch the engine-selection logic in `pipeline.py`, and does not require tracking a registry of live connections. Both call sites (`process_audio_buffer` and the `text_input` handler) need the same one-line addition.

### Pattern 2: Give cloud engines their own short, independent timeout; keep the shared engine-loop timeout generous

**What:** `server/pipeline.py`'s `for engine in engines: ... asyncio.wait_for(..., timeout=LLM_TIMEOUT_S)` applies one constant to every engine attempt, cloud and edge alike. Cloud-specific fast-fail must happen via each cloud engine's **own internal timeout** (already present: `cloud_llm.py::_TIMEOUT_S` wraps its own `urlopen`; `cloud_tts.py` uses `config.CLOUD_TTS_TIMEOUT_S` the same way), not by lowering the shared outer wrapper.
**When to use:** Whenever a retry/fallback loop iterates over engines with very different latency profiles (fast-fail cloud vs. slower-but-reliable local) and shares one outer timeout.
**Example:**
```python
# Source: server/pipeline.py:269-281 (existing structure, comment added for clarity)
for engine in engines:
    try:
        candidate = await asyncio.wait_for(
            asyncio.to_thread(engine.generate, result.asr_text, sc, self._directive),
            timeout=LLM_TIMEOUT_S,  # KEEP GENEROUS (6-8s) — this is the edge engine's real
                                     # safety margin too (Phase 8 measured edge LLM up to 4.17s).
                                     # Cloud engines fail fast on their OWN inner urlopen
                                     # timeout (cloud_llm.py::_TIMEOUT_S, shortened separately)
                                     # long before this outer wrapper would ever fire for them.
        )
    except Exception:
        candidate = None
    if candidate and isinstance(candidate, str) and candidate.strip():
        llm_text = candidate
        break
```

### Anti-Patterns to Avoid
- **Uniformly slashing `LLM_TIMEOUT_S` to hit the "<1-2s" demo goal:** breaks the edge engine's own real-world latency margin (measured up to 4.17s on real Genio 520 hardware, `edge/EDGE_TURN_LOOP_VALIDATION.md` line 57) and would silently degrade every *offline* reply to the scaffold fallback, which is the opposite of what Phase 8 delivered. Shorten the cloud-only inner timeouts instead.
- **Building a new detection/polling subsystem for "is the network really down":** explicitly rejected by locked decision D-02. The existing `network_mode` flag (once the re-sync bug from Pattern 1 is fixed) already deterministically gates every cloud call; there is nothing left to "detect."
- **Adding `asyncio.Task.cancel()` / request-cancellation machinery for in-flight cloud calls on switch-flip:** explicitly rejected by locked decision D-03. `asyncio.wait_for()` timing out does **not** actually stop the underlying blocking `urllib.request.urlopen()` call running inside its `asyncio.to_thread()` worker thread — it only lets the *awaiting coroutine* move on ([CITED: Python asyncio timeout semantics — see Sources]). D-03's "shorten timeouts, don't cancel" approach sidesteps this correctly: the abandoned thread simply keeps running until its own (now much shorter) inner `urlopen` timeout elapses, and its eventual result is discarded because the pipeline has already moved to the next engine in the loop.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Detecting whether the real network is actually down | Custom polling/heartbeat/ping subsystem | The existing `pipeline.network_mode` flag, once correctly propagated to live connections (Pattern 1) | D-02 locked decision: the software toggle *is* the ground truth for this demo's cloud-call gating; there is no real disconnection to detect |
| Aborting an in-flight cloud HTTP call on switch-flip | `asyncio.Task.cancel()` / socket-level abort plumbing around `urllib.request` | Shortened per-call timeouts (`_TIMEOUT_S`, `CLOUD_TTS_TIMEOUT_S`) that let the abandoned background thread self-terminate quickly and have its result silently discarded | D-03 locked decision explicitly rejects cancellation to avoid asyncio race conditions; `urllib.request.urlopen`'s blocking call inside a thread pool worker cannot be cleanly cancelled from the event loop anyway |
| Confirming stage-visible "online/offline" state | New badge/animation component | Existing `modeBadge`/`airplaneState`, per 09-UI-SPEC.md's "Badge Visual Strengthening" section (padding/dot-size/one-shot pulse, all within existing color tokens) | 09-UI-SPEC.md already locked this; re-deriving it here would duplicate an approved contract |

**Key insight:** Every "build new thing" temptation in this phase (detection subsystem, cancellation machinery, new UI) has already been explicitly rejected by a locked decision in 09-CONTEXT.md or 09-UI-SPEC.md. The actual engineering work is entirely subtractive/corrective: fix one state-propagation bug, retune three timeout constants (only two of which are safe to move aggressively), and optionally close one narrow background-task gap.

## Common Pitfalls

### Pitfall 1: `conn_pipe.network_mode` is frozen at WebSocket-connect time and never re-synced (CRITICAL — blocks NETCUT-01)
**What goes wrong:** The host flips `airplaneSwitch` mid-demo expecting the live conversation to go offline immediately. Nothing changes — the child keeps talking to the cloud LLM/TTS exactly as before, because the WS session in progress is reading a `network_mode` value that was copied once when the browser first connected (`server/app.py:369`), not the live global the toggle actually updates (`server/app.py:229`).
**Why it happens:** `web/index.html`'s `connectWS()` opens a single long-lived `/ws/talk` connection at page load with no per-turn reconnect (verified: `boot()` → `initModes()` → `connectWS()`, called once; reconnection logic at `web/index.html:436-460` only fires on unexpected `onclose`, not on a mode switch). `server/app.py:365-369` creates one `VoicePipeline` per WS connection and copies the *current* global mode into it exactly once — there is no code path anywhere that re-reads `pipeline.network_mode` into an existing `conn_pipe` after that.
**How to avoid:** Re-sync `conn_pipe.network_mode = pipeline.network_mode` immediately before each turn, at both call sites (`process_audio_buffer()` before `conn_pipe.run_turn_audio(...)`, `server/app.py:~417`; the `text_input` branch before `conn_pipe.run_turn_text(...)`, `server/app.py:~475`). This must be the **first task** in this phase's plan — everything else (timeout tuning, rehearsal) is moot if the switch doesn't actually take effect on a live session.
**Warning signs:** Manual test — open the student page, start a cloud-mode conversation, flip the switch mid-session (without reloading the page), and confirm the very next reply's `network_mode` (visible in `/api/status` and in the stored interaction row) actually changed. Today it will not.

### Pitfall 2: `LLM_TIMEOUT_S` is shared between cloud and edge engines — don't crush it to hit the demo's fast-fallback goal
**What goes wrong:** CONTEXT.md's D-03 names `server/pipeline.py:29` (`LLM_TIMEOUT_S=8.0`) as one of three timeouts to "大幅縮短." If the planner reduces it to, say, 1.5s to satisfy the "<1-2s recovery" framing, every edge-mode turn now risks hitting that same timeout and silently falling through to the scaffold reply — because `LLM_TIMEOUT_S` wraps **every** engine in the `for engine in engines:` loop (`server/pipeline.py:269-281`), not just the cloud one.
**Why it happens:** The engine-selection loop was designed as a uniform retry/fallback chain (cloud→edge→scaffold) sharing one timeout constant, which was a reasonable simplification when both timeouts were 8.0s and roughly matched. It stops being safe once cloud and edge latency profiles diverge sharply, which is exactly what D-03 asks for.
**How to avoid:** Leave `LLM_TIMEOUT_S` at a value comfortably above the edge LLM's real worst-case latency. Phase 8's real-hardware validation (`edge/EDGE_TURN_LOOP_VALIDATION.md:57`) measured the LLM stage alone at **4170ms** on the audience-visible first real turn after boot warmup (`latency_ms: {'asr': 405, 'llm': 4170, 'tts_first': 1209, 'round_total': 5852}`), and 1739-1834ms on later steady-state turns. Recommend leaving `LLM_TIMEOUT_S` at **6-8s** (i.e., touch it minimally or not at all) and doing the actual fast-fail work via the cloud-specific inner timeouts (`cloud_llm.py::_TIMEOUT_S`, `config.py::CLOUD_TTS_TIMEOUT_S`), which are cloud-only and safe to cut aggressively.
**Warning signs:** After changing timeouts, re-run Phase 8's real-turn latency check on the Genio 520 (or at minimum the existing `tests/test_pipeline.py::test_llm_timeout_falls_back_to_scaffold_text` pattern extended with a realistic edge-latency stub) to confirm edge-mode turns are not spuriously hitting the scaffold fallback.

### Pitfall 3: Shortening the outer `asyncio.wait_for` timeout does not stop the underlying blocking cloud HTTP call
**What goes wrong:** It's tempting to assume that once `asyncio.wait_for(..., timeout=X)` raises, the cloud HTTP request has been aborted. It has not — `urllib.request.urlopen()` running inside the `asyncio.to_thread()` worker thread keeps executing until *it's own* timeout (or the request completes), consuming a thread-pool slot the whole time.
**Why it happens:** `asyncio.wait_for()` cancels the *awaiting coroutine*, injecting a `CancelledError` at the next `await` point — but a blocking stdlib call like `urlopen()` has no internal `await` points for that cancellation to land on, so the thread runs to its own completion regardless [CITED: Python asyncio.wait_for/to_thread cancellation semantics — see Sources].
**How to avoid:** This is exactly why D-03 rejected an explicit cancellation mechanism and chose "shorten timeouts" instead — it sidesteps the problem rather than fighting it. Make sure the **inner** timeout (`cloud_llm.py::_TIMEOUT_S`, `CLOUD_TTS_TIMEOUT_S`) is also shortened, not just the outer `pipeline.py` wrapper; otherwise an abandoned cloud request from a mid-turn switch-flip can still occupy a thread-pool slot for up to the old 8s/6s even though the pipeline itself has already moved on and served an edge reply.
**Warning signs:** Rapid repeated toggling during rehearsal (multiple demo turns each abandoning a cloud call) could pile up several 6-8s-blocked background threads on a resource-constrained Genio 520; watch for delayed responsiveness on subsequent turns if only the outer timeout was shortened.

### Pitfall 4: A background diagnosis-refresh task calls the cloud API independent of `network_mode` (defense-in-depth gap, not a hard blocker)
**What goes wrong:** Every 5 successful turns, `VoicePipeline._process_text()` fires `asyncio.create_task(self._refresh_directive())` (`server/pipeline.py:311-314`, `DIRECTIVE_REFRESH_EVERY=5` at line 35), which calls `diagnose.generate_diagnosis()`. That function's cloud path (`server/diagnose.py:610-628`) is gated **only** by `anthropic_relay.resolve_config()` (i.e., whether `ANTHROPIC_API_KEY` is set) and `guardrails.consent_granted()` — it never checks `network_mode` at all. If cloud credentials happen to be configured on the same device (plausible once Phase 11's opportunistic-sync feature lands on the same box) and consent was already granted earlier in the session, this background task will make a real outbound HTTPS call (`_API_TIMEOUT_SEC=12`, `server/diagnose.py:58`) during the "we are offline" demo window — silently, since it's fire-and-forget and failure doesn't affect the visible turn.
**Why it happens:** This code path predates the phase's kill-switch narrative; it was built (Phase 5, B1 background tutor updates) with only the consent gate as its data-exfiltration chokepoint, matching PRIV-01/02's original design, not the newer "the toggle means literally zero outbound calls" narrative this phase introduces.
**How to avoid:** This does **not** contradict D-02 (which is specifically about the primary conversational LLM/TTS path already gated at `pipeline.py:260-266`) — it's a separate call site the original citation didn't cover. Recommend a small, optional, low-risk addition: gate the `_refresh_directive` trigger (or `generate_diagnosis`'s cloud branch) on `self.network_mode == "cloud"` too, for consistency with "the switch is the single source of truth for whether cloud calls happen." Today (Phase 9, edge-only demo device with no cloud credentials configured, per Phase 8's zero-cloud tcpdump PASS) this is low real-world risk; it becomes more relevant once Phase 11 adds cloud credentials to the same device.
**Warning signs:** If Phase 11's opportunistic-sync credentials land on the demo device before the finals, silently re-test Phase 8's zero-cloud tcpdump audit methodology (`edge/EDGE_TURN_LOOP_VALIDATION.md`) across a ≥5-turn session in edge mode to confirm this side-channel doesn't fire.

### Pitfall 5: ROADMAP's "主動網路偵測" language is superseded by 09-CONTEXT.md's D-02 — do not plan a detection subsystem
**What goes wrong:** `.planning/ROADMAP.md`'s Phase 9 success criterion #2 reads "雲端呼叫 timeout 已縮短 / race，且已具備主動網路偵測" (shortened/raced cloud timeouts **and active network detection**). Read literally, this implies building new logic to detect real Wi-Fi state. That would directly contradict 09-CONTEXT.md's locked D-02 ("不加自動網路偵測安全網...邏輯已完全確定：開關=一定不打雲端，不需要額外偵測邏輯").
**Why it happens:** ROADMAP.md was drafted before `/gsd-discuss-phase 9` locked the kill-switch mechanism as a pure software toggle. Once that mechanism was fixed (D-01), "detection" became unnecessary by construction: `pipeline.network_mode` (once Pitfall 1's fix lands) *is* the single, deterministic answer to "should this call go to the cloud," with no ambiguity for anything to detect.
**How to avoid:** The planner should read ROADMAP's "主動網路偵測" as **satisfied by** — not requiring separate work beyond — the correctly-propagated `network_mode` flag. Do not create a task for network detection/polling of real connectivity. If this reading needs sign-off, surface it explicitly at plan-review time citing this reconciliation rather than silently dropping the ROADMAP line.
**Warning signs:** A plan task titled anything like "add network connectivity probe/heartbeat" is a signal this reconciliation was missed.

## Code Examples

### The two `network_mode` re-sync fix sites (NETCUT-01, exact locations)
```python
# Source: server/app.py (existing code, fix annotated) — binary/audio turn path
async def process_audio_buffer() -> None:
    """把緩衝的錄音整包送進 pipeline（空緩衝直接略過）。"""
    if not audio_buffer:
        return
    data = bytes(audio_buffer)
    audio_buffer.clear()
    try:
        conn_pipe.network_mode = pipeline.network_mode  # <-- ADD: re-sync from global before each turn
        result = await conn_pipe.run_turn_audio(data, emit)
        await send_turn_result(result, include_asr=True)
    except Exception:
        await emit({"type": "state", "state": "idle"})
```
```python
# Source: server/app.py (existing code, fix annotated) — text_input (quick-phrase) turn path
elif mtype == "text_input":
    text = str(payload.get("text", "") or "")
    try:
        conn_pipe.network_mode = pipeline.network_mode  # <-- ADD: same re-sync, second call site
        result = await conn_pipe.run_turn_text(text, emit)
        await send_turn_result(result, include_asr=False)
    except Exception:
        await emit({"type": "state", "state": "idle"})
```

### Existing test pattern to extend for the timeout-fallback regression (already in repo)
```python
# Source: tests/test_pipeline.py:209-223 (existing pattern — extend for cloud-specific timeout tests)
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
This exact `monkeypatch.setattr(pipeline_mod, "LLM_TIMEOUT_S", ...)` pattern is the model for a new cloud-specific test: construct a `VoicePipeline` with a slow `cloud_llm` stub and a fast, real-ish edge `llm` stub, set `network_mode="cloud"`, monkeypatch `cloud_llm.py::_TIMEOUT_S` short, and assert the turn still completes via the edge engine within a bounded wall-clock window.

### Existing WS integration test pattern to extend for NETCUT-01's regression test
```python
# Source: tests/test_e2e.py:156-192 (existing pattern for /ws/talk integration tests)
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
The new NETCUT-01 regression test should follow this shape but: (1) open the WS once, (2) send a first `text_input` while `network_mode=="cloud"` and a distinguishable cloud-stub reply, confirm the cloud engine was invoked, (3) **without closing the WS**, POST `/api/network_mode {"mode":"edge"}` on the *same* `TestClient`, (4) send a second `text_input` on the *same still-open* WS connection, and assert the cloud stub was **not** invoked for the second turn (only the edge stub was) — this is the exact scenario Pitfall 1 currently breaks.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Recommended shortened cloud LLM timeout (~2.0-2.5s) and cloud TTS timeout (~2.5-3.0s) are based on general published latency ranges for Anthropic Messages API short completions (~0.6-3s) and ElevenLabs non-streaming short-text synthesis (~1-3s), **not** measured on this project's actual demo network path (Genio 520 → Tailscale/venue WiFi → cloud provider) | Common Pitfalls (timeout values), Open Questions | If the real demo-venue round trip regularly exceeds the chosen timeout, normal (genuinely online) cloud-mode turns will spuriously fall back to edge quality — a quality regression, not an availability failure, but still worth avoiding. Must be measured during rehearsal before locking the final number. |
| A2 | Treating ROADMAP.md's "已具備主動網路偵測" success-criterion language as satisfied by (not requiring work beyond) the correctly-propagated `network_mode` flag, per D-02 | Common Pitfalls (Pitfall 5) | If this reading is wrong and the ROADMAP criterion was intentionally meant to require separate real connectivity detection, the phase would need additional detection logic — directly contradicting the locked D-02 decision. Recommend flagging at plan-review rather than silently resolving either way. |
| A3 | The `_refresh_directive` background diagnosis call (Pitfall 4) poses low real-world risk *today* because the actual demo device has no `ANTHROPIC_API_KEY` configured (consistent with Phase 8's zero-cloud tcpdump PASS) | Common Pitfalls (Pitfall 4) | If cloud credentials are in fact present on the demo device (e.g., pre-staged ahead of Phase 11), this background side-channel could make a real, silent outbound call during the "offline" demo window, undermining the "kill switch = zero cloud calls" narrative even though the switch itself works correctly. |
| A4 | "Recovery time <1-2s" (ROADMAP success criterion #4 / D-03's target) operationally means "time from switch-flip (or mid-turn cloud attempt) to the pipeline committing to a non-cloud engine and beginning edge processing" — i.e., approximately the shortened cloud timeout value itself — **not** "time until audible TTS resumes," since Phase 8's own accepted edge budget (2.96-2.99s steady-state, up to 5.85s cold-start) already exceeds a literal 1-2s audible-recovery bar | Open Questions | If the user actually meant "audible speech resumes within 1-2s," the phase's success criterion is currently unachievable given Phase 8's already-accepted edge latency numbers, and this mismatch needs to be resolved with the user before the rehearsal script is written, not discovered during rehearsal. |

## Open Questions

1. **What does "recovery time <1-2 秒" (NETCUT-03 / ROADMAP success criterion #4) actually measure?**
   - What we know: Phase 8 already established and accepted edge-mode turn latency at 2.96-2.99s steady-state (GO) and 5.85s cold-start-after-warmup (NO-GO, with an accepted operational workaround: host does one throwaway warm-up turn first). Those numbers cannot shrink to 1-2s without re-opening Phase 8's scope.
   - What's unclear: whether "recovery" means (a) time until the pipeline *decides* to stop waiting on cloud and commit to edge (bounded by the new short cloud timeout, achievable at 1-2s), or (b) time until the child actually *hears* the edge reply (bounded by Phase 8's edge turn budget, NOT achievable at 1-2s on a cold turn).
   - Recommendation: adopt reading (a) as the phase's contract (consistent with D-03's own framing, "遇斷線很快就逾時降級到 edge"), and have the NETCUT-03 rehearsal script explicitly measure **both** metrics separately so the distinction is documented rather than conflated: (i) fallback-decision latency (target ~1-2s, driven by the new cloud timeout), and (ii) time-to-audible-edge-reply (inherits Phase 8's already-accepted 3s-steady-state/5.85s-cold-start budget, with the "pre-warm before the audience-visible first turn" mitigation carried forward). Confirm this split with the user before finalizing the rehearsal checklist.

2. **What exact numeric values should `cloud_llm.py::_TIMEOUT_S` and `config.py::CLOUD_TTS_TIMEOUT_S` land on?**
   - What we know: general published cloud-API latency ranges (Assumption A1) suggest 2.0-3.0s is a defensible starting point, comfortably below the old 8.0s/6.0s and comfortably above typical genuine round-trip time.
   - What's unclear: this project's actual demo-network round trip is unmeasured (no `ANTHROPIC_API_KEY`/`ELEVENLABS_API_KEY` configured in the available dev sandbox to test against; the real path is Genio 520 → Tailscale/venue WiFi → cloud, untested).
   - Recommendation: land on a starting value (2.0-2.5s LLM / 2.5-3.0s TTS) as a code default, but treat the *final* number as something the NETCUT-03 rehearsal script must empirically confirm against real venue network conditions before finals day — this is exactly the kind of number CONTEXT.md's Claude's Discretion note anticipated needing "真機/PC 實測結果."

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Genio 520 real hardware (SSH root@192.168.31.78, Tailscale-routed) | NETCUT-03 rehearsal | ✓ (per Phase 8 08-05, confirmed reachable 2026-07-25) | Yocto `Rity Demo Layer 25.1.1-release (scarthgap)` | None — NETCUT-03 is real-hardware-only by definition, cannot be simulated |
| `ANTHROPIC_API_KEY` (for measuring real cloud LLM latency ahead of picking the final timeout) | Empirically validating Assumption A1/A2's timeout recommendation | ✗ (not set in this research sandbox) | — | Use the published-range estimate as an initial code default (2.0-2.5s); confirm/adjust during rehearsal on a machine where credentials are actually configured |
| `ELEVENLABS_API_KEY` (for measuring real cloud TTS latency) | Same as above | ✗ (not set in this research sandbox) | — | Same fallback — initial estimate (2.5-3.0s), confirm during rehearsal |
| `pytest` / `.venv` | Automated regression tests for NETCUT-01/02 (not NETCUT-03) | Present per `run_tests.sh` (`.venv/bin/python -m pytest -q`); not independently re-verified in this sandbox (`python3 -m pytest` absent outside the project venv, as expected) | — | None needed — existing project convention |

**Missing dependencies with no fallback:**
- None. NETCUT-03's real-hardware requirement is expected to be satisfied at execution time on the actual Genio 520, not in this research/planning environment.

**Missing dependencies with fallback:**
- Cloud API credentials for empirical timeout tuning — fall back to published-range estimates now, confirm empirically during the NETCUT-03 rehearsal window (see Open Question 2).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (+ `pytest-asyncio` for async tests, already in use — see `tests/test_pipeline.py`) |
| Config file | `tests/conftest.py` (autouse `tmp_db` fixture, `anyio_backend` pinned to asyncio); no root `pytest.ini` — `run_tests.sh` invokes `python -m pytest -q` directly |
| Quick run command | `.venv/bin/python -m pytest -q tests/test_e2e.py tests/test_pipeline.py tests/test_pipeline_cloud.py tests/test_cloud_llm.py -k "network_mode or timeout or LLM_TIMEOUT"` |
| Full suite command | `./run_tests.sh` (runs main `.venv` suite + `server/streaming/tests/` together) |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| NETCUT-01 | Flipping `/api/network_mode` mid-session changes the **next turn on an already-open `/ws/talk` connection**, not just new connections | integration (WS) | `pytest tests/test_e2e.py::test_network_mode_switch_affects_live_ws_session -x` | ❌ Wave 0 — new test, extend `tests/test_e2e.py`'s existing WS pattern (`test_ws_talk_text_input_full_flow`) |
| NETCUT-02 | Cloud LLM engine attempt aborts within the new short timeout and the turn still completes via edge/scaffold, without the edge engine itself being starved by the same constant | unit | `pytest tests/test_pipeline_cloud.py -k timeout -x` | 🟡 Partially — `tests/test_pipeline.py::test_llm_timeout_falls_back_to_scaffold_text` covers the generic pattern; needs a **new** cloud-specific variant asserting edge engine is NOT starved when `LLM_TIMEOUT_S` stays generous while `cloud_llm._TIMEOUT_S` is short — Wave 0 |
| NETCUT-02 | The two documented 5s polls (`/api/status`, teacher dashboard) never make an outbound cloud call regardless of `network_mode` | unit/smoke | `pytest tests/test_e2e.py::test_get_api_status_shape -x` (extend to assert no `urlopen` call via monkeypatch-spy) | 🟡 Existing shape test present (`tests/test_e2e.py:55`); add an explicit "no cloud call" assertion — Wave 0 (small addition, low priority given architectural proof already in this doc) |
| NETCUT-02 (optional hardening) | `_refresh_directive`'s background cloud call respects `network_mode` (Pitfall 4 fix, if adopted) | unit | `pytest tests/test_app_directive.py -k network_mode -x` | ❌ Wave 0 — only needed if the planner adopts the Pitfall 4 recommendation |
| NETCUT-03 | ≥3 real physical rehearsal repetitions including mid-speech disconnect, each recovery within the operationally-defined bound | manual (real hardware) | N/A — not automatable | N/A — `checkpoint:human-verify` task, not a test file |

### Sampling Rate
- **Per task commit:** quick run command above (network_mode/timeout-scoped subset)
- **Per wave merge:** `./run_tests.sh` (full suite)
- **Phase gate:** Full suite green before `/gsd-verify-work`; NETCUT-03's `checkpoint:human-verify` task must be separately closed with real-hardware evidence (timing log/video) before the phase is considered complete — it cannot be satisfied by any automated test.

### Wave 0 Gaps
- [ ] New WS integration test in `tests/test_e2e.py` covering "switch flip affects an already-open session's next turn" — covers NETCUT-01 (this is the regression test for Pitfall 1's fix; without it, the bug could silently regress)
- [ ] New cloud-vs-edge timeout isolation test (likely in `tests/test_pipeline_cloud.py` or a new `tests/test_pipeline_timeout_isolation.py`) asserting a slow cloud stub does not starve a normal-speed edge stub when `cloud_llm._TIMEOUT_S` is short but `LLM_TIMEOUT_S` stays generous — covers NETCUT-02 / Pitfall 2
- [ ] Optional: extend `tests/test_e2e.py::test_get_api_status_shape` (and a teacher-dashboard equivalent if one exists) with an explicit assertion that no `urllib.request.urlopen`/network call occurs during the poll — covers NETCUT-02's "background polling" clause with direct evidence rather than architectural reasoning alone
- [ ] Optional (only if Pitfall 4 fix adopted): test asserting `_refresh_directive`'s cloud branch is skipped when `network_mode != "cloud"`

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V1 Architecture, Design and Threat Modeling | **yes** | The `network_mode` flag is not merely a UX toggle — per PROJECT.md's privacy constraints (child voice data, PDPA/COPPA), it is a **trust boundary control** the operator relies on to assert "no child-derived data is leaving this device right now." Pitfall 1's bug means that boundary control was not actually enforced on live connections; the fix restores the intended trust-boundary integrity. |
| V2 Authentication | no (unaffected) | `/ws/talk` already requires a bearer token (`auth.verify_token`, `server/app.py:355-362`); this phase does not change authentication |
| V4 Access Control | yes (adjacent) | `guardrails.consent_granted()` remains the hard data-exfiltration gate for cloud LLM/TTS (`server/pipeline.py:264`, `:361`) and for `diagnose.generate_diagnosis()` (`server/diagnose.py:621`); this phase does not weaken or bypass it — the `network_mode` fix is an *additional*, narrower gate layered on top for the demo scenario specifically |
| V5 Input Validation | yes (unchanged) | `NetworkModeBody` (`server/app.py:191-194`) already validates `mode in ("edge","cloud")` server-side via the existing `HTTPException(400)` branch; no new input surface is introduced by this phase |
| V6 Cryptography | no (unaffected) | No cryptographic changes in this phase |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Trust-boundary bug: operator believes cloud calls are disabled but a stale per-connection state still permits them (Pitfall 1) | Information Disclosure | Re-sync per-connection `network_mode` from the single global authority before every turn (Pattern 1 fix); this is a correctness fix that closes a real, if narrow, privacy-relevant gap (child-derived text could reach the cloud LLM after the operator asserted "we are offline") |
| Ungated background side-channel makes an outbound cloud call independent of the demo's stated network state (Pitfall 4) | Information Disclosure (narrow/low-likelihood today) | Gate `_refresh_directive`'s cloud branch on `network_mode == "cloud"` too, for defense-in-depth consistency with the primary conversational path's existing gate |
| Thread-pool exhaustion from repeated abandoned cloud calls if only the outer `asyncio.wait_for` timeout is shortened, not the inner `urlopen` timeout (Pitfall 3) | Denial of Service (low severity, self-inflicted, single-device) | Shorten both the inner (`cloud_llm.py::_TIMEOUT_S`, `CLOUD_TTS_TIMEOUT_S`) and understand the outer (`LLM_TIMEOUT_S`) wrapper's role, so abandoned background threads self-terminate quickly rather than accumulating |

## Sources

### Primary (HIGH confidence — direct codebase verification, this repo)
- `server/app.py` — `/api/network_mode` handler (lines 191-259), global `pipeline` singleton (56-61), `/api/status` handler (146-158), per-connection `conn_pipe` creation and the two turn-dispatch call sites (355-484)
- `server/pipeline.py` — `VoicePipeline` class (150-370): `network_mode` field (166), `_process_text` cloud→edge→scaffold engine loop (234-317, gate at 260-266, loop at 269-281), `_synth_tts` (346-370), `_refresh_directive` background task (311-345), `LLM_TIMEOUT_S` (29), `DIRECTIVE_REFRESH_EVERY` (35)
- `server/cloud_llm.py` — `_TIMEOUT_S` (21), `CloudLLM.generate()`'s `urlopen(req, timeout=_TIMEOUT_S)` (86)
- `server/cloud_tts.py` — `CloudTTS.synth()`'s `urlopen(req, timeout=CLOUD_TTS_TIMEOUT_S)` (99)
- `server/config.py` — `CLOUD_TTS_TIMEOUT_S` env-driven default (112), `TALKYBUDDY_*` naming convention examples (85, 134, 140, 146-148)
- `server/diagnose.py` — `generate_diagnosis()` (610-628), `_API_TIMEOUT_SEC=12` (58), cloud-call gate (`resolve_config` + `consent_granted()`, no `network_mode` check)
- `web/index.html` — `connectWS()`/reconnect logic (413-460), `initModes()`/`boot()` single-connect flow (866-1001), `refreshStatus()` (685-690), `applyMode()` (692-704), `airplaneSwitch` click handler (707-732)
- `web/teacher.html` — 5s poll (`setInterval(refresh, 5000)`, line 631) hitting local `/api/diagnoses`/`/api/interactions`
- `edge/EDGE_TURN_LOOP_VALIDATION.md` — real Genio 520 latency table (lines 29-38), warmup mitigation real-turn log showing `llm: 4170ms` (line 57)
- `tests/test_pipeline.py:209-223`, `tests/test_e2e.py:55-192`, `tests/test_app_profile.py`, `tests/conftest.py` — existing test patterns and confirmed absence of a live-session network-mode-switch regression test
- `.planning/phases/09-network-cut-demo-hardening/09-CONTEXT.md`, `09-UI-SPEC.md`, `.planning/REQUIREMENTS.md`, `.planning/ROADMAP.md`, `.planning/STATE.md`, `.planning/PROJECT.md`

### Secondary (MEDIUM confidence)
- None — this phase had no library/framework documentation lookup; all findings are either direct codebase verification (Primary) or general external benchmarks explicitly flagged LOW below.

### Tertiary (LOW confidence — general published benchmarks, not measured on this project's actual network path; see Assumptions A1/A2)
- WebSearch: Anthropic Claude Messages API time-to-first-token / short-completion latency ranges (Claude Haiku ~0.6-0.9s, Sonnet ~1.4s TTFT; general short-completion round trip ~1-3s) — [docs.claude.com](https://docs.claude.com/en/docs/test-and-evaluate/strengthen-guardrails/reduce-latency), [SigNoz guide](https://signoz.io/guides/claude-api-latency/), [Artificial Analysis](https://artificialanalysis.ai/providers/anthropic)
- WebSearch: ElevenLabs non-streaming TTS latency for short text (~1-3s non-streaming total; turbo models ~250-300ms generation + 400-800ms non-streaming overhead) — [elevenlabs.io/docs latency optimization](https://elevenlabs.io/docs/eleven-api/guides/how-to/best-practices/latency-optimization), [PlayHT blog](https://play.ht/blog/elevenlabs-text-to-speech-latency/), [Sherlock Calls guide](https://www.usesherlock.ai/blog/how-to-reduce-elevenlabs-latency)
- WebSearch: `asyncio.wait_for()`/`asyncio.to_thread()` cancellation semantics confirming a timed-out outer wrapper does not stop the underlying blocking call — [Better Stack Python timeouts guide](https://betterstack.com/community/guides/scaling-python/python-timeouts/), [pythontutorial.net asyncio.wait_for guide](https://www.pythontutorial.net/python-concurrency/python-asyncio-wait_for/), [Python bug tracker issue 43389](https://bugs.python.org/issue43389)

## Metadata

**Confidence breakdown:**
- Standard stack: N/A (no new stack) — HIGH confidence that no new dependencies are needed, verified by full read of every touched module
- Architecture / bug findings (Pitfalls 1-5): HIGH — every claim traced to exact file/line in this repository, cross-checked against actual UI boot flow and real Phase 8 hardware measurement data, not inferred
- Timeout numeric recommendations: LOW-MEDIUM — directional guidance (shorten cloud-only timeouts, keep `LLM_TIMEOUT_S` generous) is HIGH confidence; the specific seconds values are general published-benchmark estimates requiring real-hardware/rehearsal confirmation before being locked (see Assumptions A1/A2, Open Question 2)
- Pitfalls: HIGH — Pitfall 1 in particular is independently reproducible by code inspection alone (no speculation required: the connect-time-only copy and the absence of any re-sync call are both directly visible in `server/app.py`)

**Research date:** 2026-07-25
**Valid until:** 14 days (this research is tightly coupled to the current commit's exact code; any further change to `server/pipeline.py`/`server/app.py`'s network_mode handling before this phase is planned/executed should trigger a re-check of the cited line numbers)
