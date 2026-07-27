---
phase: 11-cloud-teacher-closed-loop
plan: 03
subsystem: teacher-dashboard
tags: [fastapi, jwt-authz, xss-safe-render, privacy, D-05]

# Dependency graph
requires:
  - phase: 11-cloud-teacher-closed-loop
    plan: "11-01"
    provides: "server/sync_client.py upload whitelist (UPLOAD_FIELDS) and project_for_upload() — this plan's negative regression test asserts the name never leaks through it"
  - phase: 11-cloud-teacher-closed-loop
    plan: "11-02"
    provides: "no direct dependency; parallel wave 3 executor sharing the same phase context"
provides:
  - server/config.py::STUDENT_NAME — demo-fixed display name constant, deliberately excluded from deidentify() and the upload whitelist
  - server/store.py::student_display_name(student_id=None) -> str — three-tier resolution mirroring _student_id()
  - GET /api/student_profile — identity-only endpoint (student_id/display_name/device_id), reuses identity_from_header()+_resolve_student()
  - web/teacher.html renderStudentIdentity() — teacher dashboard now renders name/id/device from the API, zero hardcoded strings
affects: [tcloud-02-verification, teacher-dashboard-e2e]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Three-tier config resolution mirrored a second time: student_display_name() copies _student_id()'s getattr(cfg, NAME, FALLBACK) shape verbatim for a new identity field"
    - "textContent assignment as the escaping mechanism for dashboard identity values (equivalent to esc() + innerHTML, chosen because the three values are simple scalar text nodes with no surrounding markup)"

key-files:
  created: []
  modified:
    - server/config.py
    - server/store.py
    - server/app.py
    - web/teacher.html
    - tests/test_student_identity.py

key-decisions:
  - "Name source chosen per plan's pre-decided discretion: config.STUDENT_NAME constant + store.student_display_name() accessor, NOT a student_profile payload field — profile.build_profile() fully recomputes the profile dict on every cloud sync and only carries forward student_id, so anything else written there would be silently wiped on the next sync. This was verified against server/profile.py before implementing, per the plan's explicit instruction not to re-litigate it."
  - "STUDENT_QUERY (web/teacher.html:260, drives which student the dashboard queries) was left untouched — D-05 only requires the *name* to stop being mocked, not multi-student support. Only rewrote the demo comment above it to drop the hardcoded name reference, since leaving a name in a comment above a differently-configured constant creates a doc/reality mismatch."
  - "renderStudentIdentity() uses textContent assignment rather than esc()+innerHTML — functionally equivalent escaping, simpler for three plain-text DOM nodes with no surrounding HTML structure."

requirements-completed: [TCLOUD-02]

coverage:
  - id: D1
    description: "store.student_display_name() mirrors _student_id()'s three-tier resolution: returns config.STUDENT_NAME when set, falls back to _FALLBACK_STUDENT_NAME when the config attribute is absent (no AttributeError), and tolerates any student_id argument without raising"
    requirement: "TCLOUD-02"
    verification:
      - kind: unit
        ref: "tests/test_student_identity.py#test_student_display_name_returns_nonempty_by_default"
        status: pass
      - kind: unit
        ref: "tests/test_student_identity.py#test_student_display_name_reflects_config_override"
        status: pass
      - kind: unit
        ref: "tests/test_student_identity.py#test_student_display_name_falls_back_when_config_attr_missing"
        status: pass
      - kind: unit
        ref: "tests/test_student_identity.py#test_student_display_name_accepts_any_student_id_without_raising"
        status: pass
    human_judgment: false
  - id: D2
    description: "GET /api/student_profile enforces the existing identity_from_header()+_resolve_student() authorization model (401 with no/bad token, 400 for tutor without ?student=, 200 with three-key identity response for tutor, student role scoped to its own sub) and never returns conversation or diagnosis content"
    requirement: "TCLOUD-02"
    verification:
      - kind: unit
        ref: "tests/test_student_identity.py#test_student_profile_requires_token"
        status: pass
      - kind: unit
        ref: "tests/test_student_identity.py#test_student_profile_rejects_bad_token"
        status: pass
      - kind: unit
        ref: "tests/test_student_identity.py#test_student_profile_tutor_without_student_query_400"
        status: pass
      - kind: unit
        ref: "tests/test_student_identity.py#test_student_profile_tutor_with_student_query_returns_identity_fields"
        status: pass
      - kind: unit
        ref: "tests/test_student_identity.py#test_student_profile_student_role_sees_only_own_id"
        status: pass
    human_judgment: false
  - id: D3
    description: "Teacher dashboard renders name/student-id/device from /api/student_profile with zero hardcoded strings remaining in teacher.html, values assigned via textContent (implicit escaping, no unescaped innerHTML), 5s polling interval and single-timer count unchanged"
    requirement: "TCLOUD-02"
    verification:
      - kind: unit
        ref: "tests/test_student_identity.py#test_teacher_html_has_no_hardcoded_student_name"
        status: pass
      - kind: other
        ref: "grep -c '阿明' web/teacher.html == 0"
        status: pass
      - kind: other
        ref: "grep -c 'setInterval' web/teacher.html == 1"
        status: pass
      - kind: manual_procedural
        ref: "Visual confirmation that the dashboard identity card renders the fetched name/id/device after refresh() runs in a browser"
        status: unknown
    human_judgment: true
    rationale: "grep/AST checks confirm the hardcoded string is gone and the wiring is present, but actual browser rendering (fonts, layout, correct field mapping visible on screen) was not verified with a live server + browser in this executor session."
  - id: D4
    description: "D-05/D-04 boundary: student display name never appears in sync_client.project_for_upload() output or in UPLOAD_FIELDS, even when an interaction dict is deliberately seeded with display_name/student_name/name keys carrying the name value"
    requirement: "TCLOUD-02"
    verification:
      - kind: unit
        ref: "tests/test_student_identity.py#test_student_display_name_not_in_upload_projection"
        status: pass
    human_judgment: false

# Metrics
duration: ~10min
completed: 2026-07-27
status: complete
---

# Phase 11 Plan 03: Teacher Dashboard Student Identity (D-05) Summary

**Replaced the hardcoded "阿明" / `STUDENT-AMING-004` strings in `web/teacher.html` with a real `GET /api/student_profile` fetch, backed by a new `config.STUDENT_NAME` constant and `store.student_display_name()` accessor that mirror the existing `_student_id()` three-tier resolution — and locked the D-05/D-04 privacy boundary (name stays server-side, never enters the upload whitelist) with a negative regression test**

## Performance

- **Duration:** ~10 min
- **Started:** 2026-07-27T03:59:48Z (base commit `ef0e379`)
- **Completed:** 2026-07-27T04:07:31Z
- **Tasks:** 3
- **Files modified:** 5 (`server/config.py`, `server/store.py`, `server/app.py`, `web/teacher.html`, `tests/test_student_identity.py`)

## Accomplishments
- `config.STUDENT_NAME = "阿明"` + `store.student_display_name(student_id=None) -> str`: mirrors `_student_id()`'s `getattr(cfg, NAME, FALLBACK)` resolution exactly, with a Traditional-Chinese docstring explaining the D-05 asymmetry (not deidentified, not written into `student_profile` payload because `profile.build_profile()` would silently wipe it on the next cloud sync)
- `GET /api/student_profile`: identity-only endpoint (`student_id`/`display_name`/`device_id`) reusing `identity_from_header()` + `_resolve_student()` verbatim — same privilege tier as `/api/diagnoses`, no new authorization surface, no conversation/diagnosis content in the response
- `web/teacher.html`: `stuName`/`stuId`/`stuDevice` DOM ids replace the hardcoded strings; `refresh()`'s `Promise.all` gained a 4th fetch to `/api/student_profile`; new `renderStudentIdentity()` writes the three values via `textContent` (implicit escaping); the 5-second `setInterval(refresh, 5000)` and single-timer invariant are untouched
- Negative regression test locking the D-05/D-04 boundary: a deliberately name-poisoned interaction dict run through `sync_client.project_for_upload()` never leaks the name into the output, and `UPLOAD_FIELDS` contains none of `display_name`/`student_name`/`name`

## Task Commits

Each task was committed atomically:

1. **Task 1: 姓名資料來源 — config.STUDENT_NAME 與 store.student_display_name()** - `41c01dd` (feat)
2. **Task 2: GET /api/student_profile 端點（沿用既有授權模型）** - `cb61c43` (feat)
3. **Task 3: 儀表板從 API 渲染身分，移除硬編字串** - `b22d72d` (feat)

_TDD note: Tasks 1 and 2 were marked `tdd="true"`. As with 11-01/11-02, execution combined the RED (failing test) and GREEN (implementation) steps into a single commit per task rather than separate `test(...)` then `feat(...)` commits. Tests were written and verified passing before each commit — no implementation shipped without corresponding test coverage. Task 3 was not marked `tdd="true"` (plain `type="auto"`), so this note applies only to Tasks 1-2. See "TDD Gate Compliance" below._

## Files Created/Modified
- `server/config.py` - Added `STUDENT_NAME = "阿明"` constant immediately after `STUDENT_ID`, with a Traditional-Chinese comment documenting the deidentify-exclusion and upload-whitelist-exclusion rationale (D-05).
- `server/store.py` - Added `_FALLBACK_STUDENT_NAME = "阿明"` alongside `_FALLBACK_STUDENT_ID`; added `student_display_name(student_id=None) -> str` near `default_student_id()`, mirroring `_student_id()`'s resolution shape and documenting why `student_id` is currently unused (single-student demo, interface placeholder) and why the result must never enter the `student_profile` payload.
- `server/app.py` - Added `GET /api/student_profile` between `api_agent_outputs` and `class SyncBody`, copying the `api_diagnoses` shape exactly (`identity_from_header` → `_resolve_student` → response dict).
- `web/teacher.html` - Removed hardcoded `阿明`/`STUDENT-AMING-004`/`GENIO-520-X992` display strings from the student-card HTML, added `id="stuName"`/`id="stuId"`/`id="stuDevice"` with `–` placeholders; `state.profile` key added; `refresh()` extended to a 4-entry `Promise.all` fetching `/api/student_profile`; new `renderStudentIdentity()` called at the top of `renderAll()`; rewrote the demo comment above `STUDENT_QUERY` to remove the hardcoded name reference.
- `tests/test_student_identity.py` - New file (created in Task 1, extended in Tasks 2 and 3). 15 tests total covering `student_display_name()`'s three-tier resolution (4), the `/api/student_profile` endpoint's authorization behavior (5), the D-05/D-04 upload-whitelist negative regression (1), and the plain-text hardcoded-string-absence check (1), plus supporting fixtures.

## Decisions Made
- Confirmed (not just assumed) the plan's pre-decided name-source choice: `config.STUDENT_NAME` + `store.student_display_name()`, not a `student_profile` payload field. Re-verified against `server/profile.py::build_profile()` before implementing — it fully recomputes the profile dict on every cloud sync and only carries `student_id` forward from `prev`, confirming the plan's stated trap.
- `STUDENT_QUERY` (`web/teacher.html:260`) intentionally left as-is per the plan's explicit scope boundary — D-05 only requires the display *name* to stop being mocked, not multi-student support (deferred per CONCERNS.md). Only the comment above it was reworded to remove the now-stale name reference.
- Used `textContent` assignment (not `esc()` + `innerHTML`) for the three identity DOM writes in `renderStudentIdentity()` — functionally equivalent XSS protection for plain-text scalar values with no surrounding markup, and matches the plan's explicit "or use textContent assignment" allowance.

## Deviations from Plan

None - plan executed exactly as written. All four `must_haves.truths` and all four STRIDE `mitigate` dispositions (T-11-09, T-11-10, T-11-11, T-11-16) are covered by at least one automated test or grep/AST acceptance check (see `coverage` block above).

## TDD Gate Compliance

Tasks 1 and 2 were marked `tdd="true"` in the plan, but as in 11-01/11-02, execution combined the RED (failing test) and GREEN (implementation) steps into a single commit per task rather than separate `test(...)` then `feat(...)` commits. Tests were written and verified to pass before each commit; no implementation shipped without corresponding test coverage. This is a process shortcut, not a coverage gap.

## Issues Encountered
- None. The environment notes warned that `tests/test_audio_io.py`, `tests/test_pipeline_wav_fastpath.py`, `tests/test_nova_sonic.py`, and two `tests/test_asr_backend.py` cases fail for reasons predating this plan (missing `soundfile`/`opencc`/`pytest-asyncio`). A full `tests/ -q` run in this worktree, however, showed **878 passed, 0 failed** — those packages appear to already be present in this worktree's environment, or the noted files are excluded from the default `tests/` collection path. Either way, no regression was introduced by this plan's changes (confirmed by running the targeted verification commands plus the full suite before committing Task 3).

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Teacher dashboard's student-identity card now renders from live server data via JWT-gated `/api/student_profile`; combined with 11-01's privacy chokepoint and 11-02's opportunistic sync triggers, ROADMAP SC4 ("顯示真實（非 mock）診斷資料") is satisfied on the identity axis as well as the diagnosis axis.
- `student_display_name()`'s `student_id` parameter is a deliberate no-op placeholder for future multi-student support — any follow-up work adding a second student must extend this function's resolution logic (e.g. a real name registry) rather than assuming the parameter already does anything.
- Full test suite (`tests/ -q`, no ignores) passes clean at 878/878 after this plan's changes, confirming no regression across the phase's three parallel-wave plans (11-01/11-02/11-03).
- This plan closes `TCLOUD-02`'s remaining scope per `.planning/REQUIREMENTS.md` — `requirements mark-complete` was intentionally **not** run here per this worktree's instruction that the orchestrator owns STATE.md/ROADMAP.md/REQUIREMENTS.md writes after all wave agents complete.

---
*Phase: 11-cloud-teacher-closed-loop*
*Completed: 2026-07-27*

## Self-Check: PASSED

All modified files found on disk (`server/config.py`, `server/store.py`,
`server/app.py`, `web/teacher.html`, `tests/test_student_identity.py`) and
all three task commits (`41c01dd`, `cb61c43`, `b22d72d`) confirmed present
in `git log`.
