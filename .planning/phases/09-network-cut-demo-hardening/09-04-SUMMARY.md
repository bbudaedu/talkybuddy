---
phase: 09-network-cut-demo-hardening
plan: 04
subsystem: docs+tooling
tags: [rehearsal-script, operational-definition, python, sqlite, real-hardware-verification]

# Dependency graph
requires:
  - phase: 09-network-cut-demo-hardening
    plan: 01
    provides: "live-session network_mode re-sync fix + JWT gate — the mechanism the type-A/type-B rehearsal scripts exercise"
  - phase: 09-network-cut-demo-hardening
    plan: 02
    provides: "CLOUD_LLM_TIMEOUT_S=1.5 / CLOUD_TTS_TIMEOUT_S=1.5 (env-overridable) — the constants M1's 3.0s theoretical ceiling is derived from"
  - phase: 09-network-cut-demo-hardening
    plan: 03
    provides: "modeBadge/toast visual cues the rehearsal scripts' steps reference (badge state, toast copy)"
provides:
  - "edge/NETWORK_CUT_REHEARSAL.md: operational M1 (fallback-decision latency, theoretical ceiling 3.0s) / M2 (audible-recovery latency, inherits Phase 8 budget, no 1-2s gate) split, two rehearsal-type scripts (A: between-turn, B: mid-speech), empty results table, decision tree"
  - "edge/runtime/dump_recent_turns.py: format_turns_table() pure function + main() CLI producing objective per-turn latency evidence for the results table"
affects: [09-network-cut-demo-hardening, demo-day-rehearsal]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pure-function/side-effecting-main split (format_turns_table vs main), mirroring edge/runtime/measure_peak_rss.py's existing structure"
    - "Lazy import of server.store inside main() so importing the module triggers no DB access"
    - "Swallow DB-read failure (uninitialized schema) as an empty result set rather than raising — rehearsal tooling must never crash mid-demo"

key-files:
  created:
    - edge/NETWORK_CUT_REHEARSAL.md
    - edge/runtime/dump_recent_turns.py
    - tests/test_dump_recent_turns.py
  modified: []

key-decisions:
  - "Adopted RESEARCH.md Open Question 1's reading (a): M1 = fallback-decision latency is the ROADMAP <1-2s target; M2 = audible-recovery latency explicitly does NOT get the 1-2s gate and instead inherits Phase 8's already-accepted edge turn budget (2.96-2.99s steady state / 5.85s cold start)"
  - "M1's type-B theoretical ceiling documented as 3.0s (CLOUD_LLM_TIMEOUT_S 1.5 + CLOUD_TTS_TIMEOUT_S 1.5), not 1.5s, because a mid-turn switch can land before either inner timeout has started counting down"
  - "main()'s store.list_interactions() call wrapped in try/except treating any read failure (including an uninitialized/table-less DB) as zero rows, beyond the plan's literal --limit-only robustness spec — the rehearsal tool must never crash mid-demo regardless of DB state (Rule 1/2 auto-fix, see Deviations)"

requirements-completed: [NETCUT-03]

coverage:
  - id: D1
    description: "edge/NETWORK_CUT_REHEARSAL.md defines M1/M2 split with M1's type-B 3.0s ceiling and M2's Phase-8-inherited budget, no 1-2s gate on M2"
    requirement: NETCUT-03
    verification:
      - kind: automated_doc_structure
        ref: "grep checks in 09-04-PLAN.md Task 1 <verify><automated> — all passed (OK)"
        status: pass
    human_judgment: false
  - id: D2
    description: "dump_recent_turns.py produces objective per-turn evidence (ts/network_mode/llm_ms/tts_first_ms/round_total_ms/synced), never raises on incomplete data"
    requirement: NETCUT-03
    verification:
      - kind: unit
        ref: "tests/test_dump_recent_turns.py (5 tests: normal input, missing latency_ms key, non-dict latency_ms, empty input, main() via tmp_db)"
        status: pass
    human_judgment: false
  - id: D3
    description: "≥3 real-hardware rehearsal repetitions (≥1 type B) with recovery-time measurement on the actual Genio 520"
    requirement: NETCUT-03
    verification:
      - kind: manual
        ref: "edge/NETWORK_CUT_REHEARSAL.md §5 results table — NOT YET FILLED, requires physical access to root@192.168.31.78"
        status: pending
    human_judgment: true
    rationale: "NETCUT-03 is real-hardware-only by definition (RESEARCH.md Environment Availability table). Cannot be executed, simulated, or estimated by an agent. See 'Pending Real-Hardware Verification' section below."

# Metrics
duration: 20min
completed: 2026-07-25
status: complete
---

# Phase 9 Plan 4: Network-Cut Rehearsal Script + Evidence Tool Summary

**Turned NETCUT-03's unverifiable "≥3 real network-cut rehearsals, each recovery <1-2s" into an executable rehearsal script with a precise M1 (fallback-decision, achievable) / M2 (audible-recovery, inherits Phase 8's budget, not gated at 1-2s) operational split, plus a device-side CLI tool that turns rehearsal timing into copy-pasteable objective evidence instead of stopwatch recollection.**

## Performance

- **Duration:** ~20 min
- **Tasks:** 2
- **Files created:** 3 (`edge/NETWORK_CUT_REHEARSAL.md`, `edge/runtime/dump_recent_turns.py`, `tests/test_dump_recent_turns.py`)

## Accomplishments

- `edge/NETWORK_CUT_REHEARSAL.md`: six sections (§0 preconditions, §1 M1/M2 operational definitions, §2 measurement method, §3 rehearsal type A, §4 rehearsal type B, §5 empty results table, §6 decision tree). §1 resolves RESEARCH.md's Open Question 1 by adopting reading (a) — M1 (pipeline commits to a non-cloud engine) is the ROADMAP `<1-2s` target; M2 (child actually hears the reply) inherits Phase 8's already-accepted 2.96-2.99s steady-state / 5.85s cold-start budget with no 1-2s gate applied, preventing the two numbers from being conflated into an impossible-and-meaningless target. §1 also documents the DB `network_mode` mid-turn read-trap (row reflects the mode at turn start, not the switch-flip moment) so a rehearsal operator doesn't misjudge a degraded turn as "didn't switch."
- `edge/runtime/dump_recent_turns.py`: `format_turns_table(rows) -> str` is a pure function (no I/O) rendering fixed columns (`# / ts / network_mode / llm_ms / tts_first_ms / round_total_ms / synced`); missing or malformed `latency_ms` values fill `-` instead of raising. `main(argv)` lazy-imports `server.store`, parses `--limit` (default 5, non-numeric falls back to default), and — beyond the plan's literal spec — also swallows any DB read failure as zero rows so the tool survives an uninitialized-schema DB on a fresh device (see Deviations).
- `tests/test_dump_recent_turns.py`: 5 tests covering all `<behavior>` cases (normal input, missing `latency_ms` key, non-dict `latency_ms`, empty input, `main()` via the `tmp_db` fixture with `capsys`).
- Full test suite: **347 passed** (up from 342 after 09-03; +5 new tests, zero regressions).

## Task Commits

Task 2 followed the RED → GREEN TDD cycle (`tdd="true"`):

1. **Task 1: `edge/NETWORK_CUT_REHEARSAL.md`**
   - `48f258b` (docs) — rehearsal script with M1/M2 split, two rehearsal types, results table, decision tree; all Task 1 `<automated>` grep checks passed including the credential-leak scan
2. **Task 2: `edge/runtime/dump_recent_turns.py` + tests**
   - `c160a76` (test, RED) — added `tests/test_dump_recent_turns.py`; confirmed failing with `ModuleNotFoundError: No module named 'edge.runtime.dump_recent_turns'` (module did not exist yet)
   - `27413f2` (feat, GREEN) — implemented `format_turns_table()` + `main()`; all 5 tests pass, full suite green (347 passed)

**Plan metadata:** (this commit, docs: complete plan)

## Files Created

- `edge/NETWORK_CUT_REHEARSAL.md` — rehearsal script (operational definitions, two scripted rehearsal types, empty results table, decision tree)
- `edge/runtime/dump_recent_turns.py` — `format_turns_table(rows: list[dict]) -> str` (pure), `main(argv: list[str] | None = None) -> int`
- `tests/test_dump_recent_turns.py` — 5 unit/integration tests

## Decisions Made

- Adopted RESEARCH.md Open Question 1's recommended reading (a) as the phase's contract: M1 = fallback-decision latency (ROADMAP `<1-2s` target, achievable), M2 = audible-recovery latency (inherits Phase 8's accepted edge budget, explicitly not gated at 1-2s). Both are recorded on every results-table row so the distinction stays documented rather than conflated.
- Documented M1's type-B theoretical ceiling as **3.0s** (`CLOUD_LLM_TIMEOUT_S` 1.5 + `CLOUD_TTS_TIMEOUT_S` 1.5), not just 1.5s — a mid-turn switch can land at the very start of the LLM call, meaning the turn may still have to absorb both inner timeouts sequentially depending on which stage was in flight.
- `main()`'s DB read wrapped in `try/except Exception` treating any failure (including a completely uninitialized DB with no `interactions` table) as an empty result set. This goes beyond the plan's literal `<behavior>` spec (which only named `--limit` parsing as needing to not raise) but is required by the plan's own acceptance criterion ("空 DB 時印出表頭 + 「（無互動紀錄）」" — not just literally-empty-but-initialized DB) and by T-09-13's disposition in the threat model (tool must not interrupt the rehearsal on incomplete/absent data). See Deviations below.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing critical functionality] `main()` needed to tolerate a completely uninitialized DB (no `interactions` table), not just an empty one**
- **Found during:** Task 2 GREEN-phase manual verification (`python -m edge.runtime.dump_recent_turns --limit 5` against this dev sandbox's checked-in-but-uninitialized `data/talkybuddy.db`)
- **Issue:** `store.list_interactions()` raised `sqlite3.OperationalError: no such table: interactions` because `store.init_db()` had never run against this particular DB file. The plan's acceptance criterion explicitly requires "`--limit 5` 可執行且不拋例外（空 DB 時印出表頭 + 「（無互動紀錄）」）" — a rehearsal tool that crashes on an uninitialized DB fails this criterion and, more importantly, would be exactly the kind of demo-day interruption T-09-13 (DoS via incomplete real-machine data) is meant to guard against.
- **Fix:** Wrapped the `store.list_interactions(limit=limit)` call in `main()` with `try/except Exception: rows = []`, so any DB read failure degrades to "no interactions" output rather than a traceback.
- **Files modified:** `edge/runtime/dump_recent_turns.py`
- **Commit:** `27413f2` (included in the GREEN commit, not a separate fix commit — caught before the commit, not after)

## Pending Real-Hardware Verification (NETCUT-03 — NOT completed by this agent)

**This is the honest, explicit boundary of what was and was not done in this plan.**

Completed (fully automatable, done and verified):
- The rehearsal script itself (`edge/NETWORK_CUT_REHEARSAL.md`) — operational definitions, step-by-step scripts for both rehearsal types, decision tree, and an empty results table ready to be filled in.
- The device-side evidence tool (`edge/runtime/dump_recent_turns.py`) and its full unit-test coverage — verified working via `.venv/bin/python -m pytest` and a manual CLI smoke test in this sandbox.

**NOT completed — requires a human physically at the Genio 520 device:**
- `edge/NETWORK_CUT_REHEARSAL.md` §5's results table is **empty** (only the example row, explicitly marked `<!-- 範例 -->`/non-real). No real M1/M2 numbers have been recorded.
- **≥3 real-hardware rehearsal repetitions (with ≥1 of type B, mid-speech switching) on `root@192.168.31.78` have NOT been run.** This agent did not SSH into the device, did not toggle `airplaneSwitch` on a live student page, and did not run `dump_recent_turns.py` against real interaction data. No numbers were fabricated, estimated, or invented in place of real measurements — per this task's explicit instruction and the plan's own threat-model disposition (T-09-09: "不得由 agent 代跑、代量或推估任何真機數字").
- This matches `human_verify_mode: end-of-phase` (per `.planning/config.json`) and the plan's own framing: "本 plan 不放 blocking checkpoint task；真機演練以 Task 1 的 `<verify><human-check>` 承接，由 `/gsd-verify-work` 在 phase 結束時收取證據."

**What a human needs to do to close this out:**
1. SSH-reachable Genio 520 at `root@192.168.31.78` (Tailscale-routed, per `edge/BOARD_BRINGUP_DECISION.md`), `run_edge.sh` running, student page logged in.
2. Follow `edge/NETWORK_CUT_REHEARSAL.md` §0 (including the mandatory warm-up turn), then §3 (type A) at least once and §4 (type B) at least once, for ≥3 total repetitions.
3. After each repetition, run `ssh root@192.168.31.78 "cd /root/talkybuddy && .venv/bin/python -m edge.runtime.dump_recent_turns --limit 5"` and paste the output into the corresponding results-table row's evidence column.
4. Fill in M1/M2/GO-NO-GO per §6's decision tree; if any timeout constant is retuned, record the before/after values per §6's instructions.

## Issues Encountered

None beyond the one auto-fixed item above.

## User Setup Required

**Real-hardware rehearsal (see "Pending Real-Hardware Verification" above) — this is user-required, not optional, before NETCUT-03 can be marked GO.** No other external service configuration required for this plan's automated deliverables.

## Next Phase Readiness

- `edge/NETWORK_CUT_REHEARSAL.md` and `edge/runtime/dump_recent_turns.py` are both ready to use as-is on the real Genio 520 — no further code changes anticipated before rehearsal.
- Phase 9's three requirements (NETCUT-01, NETCUT-02, NETCUT-03) are now all addressed by delivered artifacts: NETCUT-01/02 fully closed with automated regression tests (09-01/09-02); NETCUT-03's tooling and script are closed, but its real-hardware evidence collection remains an explicit open item for `/gsd-verify-work` at phase end.
- Full test suite green: 347 passed, no regressions.
- No blockers for phase completion beyond the pending human real-hardware rehearsal.

---
*Phase: 09-network-cut-demo-hardening*
*Completed: 2026-07-25*

## Self-Check: PASSED

All created files and both task commits (48f258b, c160a76, 27413f2) verified present.
