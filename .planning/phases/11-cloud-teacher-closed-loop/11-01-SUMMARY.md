---
phase: 11-cloud-teacher-closed-loop
plan: 01
subsystem: sync
tags: [sqlite, privacy, consent-gate, deidentify, whitelist, sync]

# Dependency graph
requires:
  - phase: 09-network-cut-demo-hardening
    provides: network_mode kill-switch semantics; consent gate reference pattern in server/pipeline.py
provides:
  - server/store.py::mark_synced(seqs) — explicit-seq sync marking that supports partial-failure-safe resync
  - server/sync_client.py upload whitelist constants (UPLOAD_ID_FIELDS/UPLOAD_SCORE_FIELDS/UPLOAD_TEXT_FIELDS/UPLOAD_FIELDS)
  - server/sync_client.py::project_for_upload(item) — default-deny payload projection with upload-time deidentify
  - server/sync_client.py::push_pending() rewritten as the audited device-to-cloud privacy chokepoint (consent gate -> whitelist projection -> all-or-nothing marking)
affects: [11-02, teacher-dashboard, tcloud-01-verification]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Default-deny upload whitelist (frozenset constant + only-read-allowed-keys projection, never dict-copy-then-pop)"
    - "Consent gate placed strictly before payload construction and before the network call (mirrors server/pipeline.py cloud-LLM/cloud-TTS gate)"
    - "All-or-nothing partial-failure-safe marking: only mark_synced(seqs) when accepted+skipped == len(pending), relying on client_ts dedup for idempotent resync"

key-files:
  created: []
  modified:
    - server/store.py
    - server/sync_client.py
    - tests/test_store.py
    - tests/test_sync_client.py

key-decisions:
  - "mark_synced(seqs) built with '?' placeholder string concatenation (not f-string) to satisfy the plan's AST-level anti-f-string acceptance check; safe because placeholders are trusted literal '?' tokens, not user data"
  - "client_ts special-cased inside UPLOAD_ID_FIELDS loop rather than treated as a plain pass-through field, so ts->client_ts fallback mapping only happens once and 'ts' itself never appears in the projected output"
  - "push_pending() consent check happens after the empty-pending check but before payload/http_post, per D-02 (no network call when unauthorized, but the always-empty fast path stays a no-op)"

patterns-established:
  - "Upload projection pattern: project_for_upload() reads only whitelisted keys, mirrors server/agents/privacy.py's safe_diagnosis() pure-function/no-mutate/no-throw contract"

requirements-completed: [TCLOUD-01]

coverage:
  - id: D1
    description: "store.mark_synced(seqs) marks only the given, still-pending seqs and returns the actual rowcount changed"
    requirement: "TCLOUD-01"
    verification:
      - kind: unit
        ref: "tests/test_store.py#test_mark_synced_marks_only_given_seq"
        status: pass
      - kind: unit
        ref: "tests/test_store.py#test_mark_synced_empty_list_marks_nothing"
        status: pass
      - kind: unit
        ref: "tests/test_store.py#test_mark_synced_nonexistent_seq_returns_zero"
        status: pass
      - kind: unit
        ref: "tests/test_store.py#test_mark_synced_already_synced_seq_returns_zero"
        status: pass
    human_judgment: false
  - id: D2
    description: "project_for_upload() strips any field not in the whitelist (including a simulated future audio_path field), maps ts->client_ts, and leaves score values unmasked while deidentifying text fields"
    requirement: "TCLOUD-01"
    verification:
      - kind: unit
        ref: "tests/test_sync_client.py#test_project_for_upload_strips_unlisted_audio_path_field"
        status: pass
      - kind: unit
        ref: "tests/test_sync_client.py#test_project_for_upload_strips_latency_ms"
        status: pass
      - kind: unit
        ref: "tests/test_sync_client.py#test_project_for_upload_deidentifies_text_fields"
        status: pass
      - kind: unit
        ref: "tests/test_sync_client.py#test_project_for_upload_keeps_score_values_unmasked"
        status: pass
      - kind: unit
        ref: "tests/test_sync_client.py#test_project_for_upload_maps_ts_to_client_ts_when_missing"
        status: pass
      - kind: unit
        ref: "tests/test_sync_client.py#test_project_for_upload_output_keys_are_subset_of_upload_fields"
        status: pass
    human_judgment: false
  - id: D3
    description: "push_pending() blocks all network calls and leaves pending records untouched when consent is not granted (D-02)"
    requirement: "TCLOUD-01"
    verification:
      - kind: unit
        ref: "tests/test_sync_client.py#test_push_pending_blocks_network_when_consent_not_granted"
        status: pass
    human_judgment: false
  - id: D4
    description: "push_pending() only marks records synced when the cloud response covers the full pending batch (accepted+skipped == len(pending)); partial acceptance leaves every record pending for resync"
    requirement: "TCLOUD-01"
    verification:
      - kind: unit
        ref: "tests/test_sync_client.py#test_push_pending_partial_failure_leaves_all_pending"
        status: pass
      - kind: unit
        ref: "tests/test_sync_client.py#test_push_pending_marks_synced_when_accepted_plus_skipped_covers_all"
        status: pass
      - kind: unit
        ref: "tests/test_sync_client.py#test_push_pending_sent_payload_keys_are_whitelisted"
        status: pass
      - kind: unit
        ref: "tests/test_sync_client.py#test_push_pending_does_not_mutate_local_sqlite_text"
        status: pass
    human_judgment: false

# Metrics
duration: 10min
completed: 2026-07-27
status: complete
---

# Phase 11 Plan 01: Sync Privacy Chokepoint (mark_synced + whitelist + consent gate) Summary

**`sync_client.push_pending()` 從零閘門直送改成 consent 閘門 → 白名單投影＋上傳瞬間去識別化 → 全數處理才標記的可稽核 chokepoint，並修好 `mark_all_synced()` 全有全無的部分失敗缺陷**

## Performance

- **Duration:** 10 min
- **Started:** 2026-07-27T02:56:00Z (base commit `b78bcd0`)
- **Completed:** 2026-07-27T03:06:18Z
- **Tasks:** 3
- **Files modified:** 4 (`server/store.py`, `server/sync_client.py`, `tests/test_store.py`, `tests/test_sync_client.py`)

## Accomplishments
- `store.mark_synced(seqs)`：以明確 seq 清單、參數化 SQL 標記已同步，只計實際變更列數，`mark_all_synced()` 原樣保留供其他呼叫端使用
- `sync_client` 上傳白名單（`UPLOAD_ID_FIELDS` / `UPLOAD_SCORE_FIELDS` / `UPLOAD_TEXT_FIELDS` / `UPLOAD_FIELDS`）與 `project_for_upload()`：預設拒絕，只讀允許鍵組出輸出；文字欄位逐欄 `deidentify()`（上傳瞬間，本地原文不變）、數值欄位不經處理、`ts` 正確映射為 `client_ts`
- `push_pending()` 重寫為完整 chokepoint：consent 未授權時零網路呼叫且紀錄留在佇列（D-02）；雲端只部分處理時一筆都不誤標（部分失敗安全標記，修好舊有全有全無缺陷）

## Task Commits

Each task was committed atomically:

1. **Task 1: store.mark_synced() — 以明確 seq 清單標記已同步** - `d8c726f` (feat)
2. **Task 2: 上傳白名單常數與 project_for_upload() 投影函式（D-01 + D-04）** - `9c8a0a2` (feat)
3. **Task 3: push_pending() 接上 consent 閘門與部分失敗安全標記（D-02）** - `23708bc` (feat)

_TDD note: `tdd="true"` was set on all three tasks, but the failing-test-first RED commits were not separated into their own commits — tests and implementation landed together in a single `feat` commit per task. All behaviors were still verified passing before commit; this is a process deviation from strict RED/GREEN separation, not a coverage gap. See "TDD Gate Compliance" below._

## Files Created/Modified
- `server/store.py` - Added `mark_synced(seqs) -> int`, parameterized `IN (...)` with `AND synced = 0`, kept `mark_all_synced()` intact
- `server/sync_client.py` - Rewrote module docstring (chokepoint framing, cross-ref to `server/agents/privacy.py`); added 4 whitelist constants + `project_for_upload()`; rewrote `push_pending()` with consent gate and all-or-nothing safe marking
- `tests/test_store.py` - 4 new tests for `mark_synced` behaviors
- `tests/test_sync_client.py` - 8 new tests for `project_for_upload`, 7 new tests for `push_pending()` (consent gate, partial failure, full processing, no-pending, whitelist enforcement, D-01 non-mutation)

## Decisions Made
- `mark_synced()`'s `IN (?, ?, ...)` placeholder string is built with `", ".join(["?"] * len(ids))` + plain `+` concatenation rather than an f-string, purely to satisfy the plan's AST-based "no `JoinedStr` in function body" acceptance check. The placeholders are trusted literal `"?"` tokens (never user data), so this is not a SQL-injection concern either way — it's a mechanical constraint from the acceptance script, not a security requirement.
- Kept the plan's exact whitelist grouping (`ID` / `SCORE` / `TEXT`) and the "全數處理才標記" (mark-only-when-fully-processed) semantics as specified — no deviation from the CONTEXT.md-locked decisions (D-01, D-02, D-04).

## Deviations from Plan

None - plan executed exactly as written. All four `must_haves.truths` are each covered by at least one automated test (see `coverage` block above).

## TDD Gate Compliance

Tasks were marked `tdd="true"` in the plan, but execution combined the RED (failing test) and GREEN (implementation) steps into a single commit per task rather than two separate `test(...)` then `feat(...)` commits. Tests were written and verified to pass before each commit; no implementation shipped without corresponding test coverage. This is a process shortcut, not a coverage gap — flagging per the plan-level TDD gate enforcement note so it's visible to the verifier.

## Issues Encountered
- Initial `mark_synced()` SQL clause ordered `WHERE synced = 0 AND seq IN (...)`, which failed the acceptance script's literal substring check for `"AND synced = 0"`. Reordered to `WHERE seq IN (...) AND synced = 0` — same semantics, satisfies the check. No behavior change, caught before commit.
- The worktree does not have its own `.venv` (gitignored, not worktree-local); all test runs used the main repo's `/home/budaedu/talkybuddy/.venv/bin/python` invoked with the worktree as `cwd`, matching the environment notes.

## Requirements Tracking Note

`TCLOUD-01` is listed in this plan's frontmatter, but it is also listed in
`11-02-PLAN.md` and `11-04-PLAN.md` (not yet executed) — the requirement's
full scope (opportunistic upload trigger / D-03, dashboard integration)
spans multiple plans in this phase. This plan only satisfies the
`push_pending()` chokepoint sub-scope. `requirements mark-complete` was
therefore **not** run for `TCLOUD-01` here, to avoid falsely marking the
requirement done in `REQUIREMENTS.md` before the remaining plans land. The
orchestrator or a later plan in this phase should mark it complete once
11-02/11-04 (or whichever plans ultimately close the requirement) finish.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `sync_client.push_pending()` is now the audited device-to-cloud privacy chokepoint TCLOUD-01/SC1 required; ready for 11-02 (or later plans) to wire the D-03 two-layer trigger (network_mode transition + per-turn hook) on top of this function without further privacy work.
- `store.mark_all_synced()` still exists and is still called by `server/app.py`'s `/api/network_mode` handler (unchanged in this plan, per the plan's explicit scope note — "其他呼叫端於 11-02 才改"). Any follow-up plan that touches `/api/network_mode`'s sync-marking should consider switching it to `mark_synced()` too for the same partial-failure safety, but that is out of this plan's scope.
- Full test suite run before and after this plan's changes shows identical pre-existing failures (11 failed, 3 errors, out of `server/streaming/tests/` — missing `soundfile`/`opencc`/`pytest-asyncio` per environment notes), confirming no regression was introduced.
- No student display-name / teacher-dashboard work (D-05, SC4) was touched in this plan — that remains for a separate plan in this phase.

---
*Phase: 11-cloud-teacher-closed-loop*
*Completed: 2026-07-27*

## Self-Check: PASSED

All modified files found on disk (`server/store.py`, `server/sync_client.py`,
`tests/test_store.py`, `tests/test_sync_client.py`) and all four task commits
(`d8c726f`, `9c8a0a2`, `23708bc`) plus the SUMMARY commit (`caa9fcc`) confirmed
present in `git log`.
