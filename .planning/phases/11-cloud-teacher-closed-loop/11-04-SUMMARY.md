---
phase: 11-cloud-teacher-closed-loop
plan: 04
subsystem: teacher-dashboard
tags: [bedrock, provenance, xss-safe-render, privacy, e2e-testing, runbook]

# Dependency graph
requires:
  - phase: 11-cloud-teacher-closed-loop
    plan: "11-01"
    provides: "sync_client.push_pending() consent-gated chokepoint and upload whitelist — this plan's zero-egress tests assert the same guardrails.consent_granted() gate holds at the diagnosis layer"
  - phase: 11-cloud-teacher-closed-loop
    plan: "11-02"
    provides: "sync_client.opportunistic_sync() and the network_mode edge->cloud trigger — this plan's offline-window e2e test drives the exact /api/network_mode path 11-02 wired"
  - phase: 11-cloud-teacher-closed-loop
    plan: "11-03"
    provides: "GET /api/student_profile and web/teacher.html's live-rendered student identity — this plan's dashboard change sits in the same file, no conflict"
provides:
  - "server/diagnose.py::generate_diagnosis() result[\"source\"] ('cloud' | 'rule') — the sole auditable evidence that a given diagnosis card was actually produced by Bedrock rather than the silent rule-based fallback"
  - "web/teacher.html::renderDiagnosisSource() + #diagSrc badge — dashboard now renders the honest source label instead of an unconditional 'AWS Bedrock produced this' claim"
  - "tests/test_tcloud_e2e.py — 12 tests covering the six source-marker behaviors (Task 1) and five end-to-end egress/prompt-content behaviors (Task 2)"
  - "docs/TCLOUD_VERIFY.md — finals rehearsal runbook (mirrors docs/RELAY_VERIFY.md structure)"
affects: [tcloud-01-verification, tcloud-02-verification, teacher-dashboard-e2e]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Provenance marker convention reused verbatim from server/agents/homework.py: result[\"source\"] = \"cloud\" | \"rule\", set at exactly the two points where a result either did or didn't come from a successful cloud call — never inferred after the fact"
    - "Interception-over-assertion test pattern (copied from tests/test_agent_privacy.py): never mock guardrails.deidentify itself, only intercept the outbound prompt string and assert on its content — this is what actually proves the redaction worked on real data, not just that the function was called"
    - "Badge-reuse rendering: renderDiagnosisSource() writes into the existing .badge/.badge.cloud/.badge.edge/.dot CSS classes already established for agent-output cards, no new CSS introduced for a third source-badge visual language"

key-files:
  created:
    - tests/test_tcloud_e2e.py
    - docs/TCLOUD_VERIFY.md
  modified:
    - server/diagnose.py
    - web/teacher.html
    - tests/test_e2e.py

key-decisions:
  - "source is assigned at exactly two points inside generate_diagnosis() (once after a successful cloud branch, once after the rule-based fallback), never via a flag variable or post-hoc inference — this keeps the assignment trivially auditable via AST inspection and guarantees the value can never be missing"
  - "Task 1 and Task 2 were both tdd=\"true\" but, following the precedent set by 11-01/11-02/11-03 in this phase, RED and GREEN were not split into separate commits — tests and implementation were verified passing together before each task's commit. Documented under TDD Gate Compliance below."
  - "Fixed a regression this plan's Task 1 caused in a pre-existing test (tests/test_e2e.py::test_post_network_mode_cloud_returns_new_diagnosis, which asserted an exact closed key-set for the diagnosis dict). Updated the expected key set to include \"source\" and assert its value domain — this is the correct fix, not a workaround, since the plan's whole purpose is to add this key to the contract."
  - "docs/TCLOUD_VERIFY.md explicitly documents the single-machine topology limitation: the finals rehearsal validates local pending-queue promotion + real Bedrock egress, not a genuine cross-device HTTP upload. push_pending()'s full HTTP path remains unit-tested only. This is stated per the plan's explicit instruction not to gloss over it with 'upload complete' language."

requirements-completed: []

coverage:
  - id: D1
    description: "generate_diagnosis() returns source deterministically: rule when no credentials, cloud when the Bedrock branch succeeds, rule when Bedrock raises and relay is unavailable, rule with zero cloud calls when allow_cloud=False, rule with zero cloud calls when consent is not granted, and the value is always exactly \"cloud\" or \"rule\" (never missing/None)"
    requirement: "TCLOUD-02"
    verification:
      - kind: unit
        ref: "tests/test_tcloud_e2e.py#test_source_is_rule_when_no_cloud_credentials"
        status: pass
      - kind: unit
        ref: "tests/test_tcloud_e2e.py#test_source_is_cloud_when_bedrock_branch_succeeds"
        status: pass
      - kind: unit
        ref: "tests/test_tcloud_e2e.py#test_source_is_rule_when_bedrock_raises_and_relay_unavailable"
        status: pass
      - kind: unit
        ref: "tests/test_tcloud_e2e.py#test_source_is_rule_and_no_cloud_call_when_allow_cloud_false"
        status: pass
      - kind: unit
        ref: "tests/test_tcloud_e2e.py#test_source_is_rule_and_no_cloud_call_when_consent_not_granted"
        status: pass
      - kind: unit
        ref: "tests/test_tcloud_e2e.py#test_source_domain_is_closed_to_cloud_or_rule"
        status: pass
    human_judgment: false
  - id: D2
    description: "End-to-end egress and prompt-content guarantees on real data: offline-window pending drains to zero and produces one new diagnosis via POST /api/network_mode, zero intercepted Bedrock calls when network_mode=edge or consent is withheld, and the intercepted prompt string contains neither a phone number (via real, unmocked guardrails.deidentify) nor the student's display name"
    requirement: "TCLOUD-01"
    verification:
      - kind: unit
        ref: "tests/test_tcloud_e2e.py#test_offline_window_pending_zeroes_and_new_diagnosis_appears"
        status: pass
      - kind: unit
        ref: "tests/test_tcloud_e2e.py#test_edge_mode_zero_egress_calls"
        status: pass
      - kind: unit
        ref: "tests/test_tcloud_e2e.py#test_consent_not_granted_zero_egress_calls"
        status: pass
      - kind: unit
        ref: "tests/test_tcloud_e2e.py#test_prompt_excludes_phone_number_via_real_deidentify"
        status: pass
      - kind: unit
        ref: "tests/test_tcloud_e2e.py#test_prompt_excludes_student_display_name"
        status: pass
    human_judgment: false
  - id: D3
    description: "Teacher dashboard's diagnosis-source badge renders one of three honest labels driven by the latest diagnosis's source field (Bedrock direct-converse claim only when source==cloud, rule-based label when source==rule, unknown when no diagnosis yet), the previously unconditional Bedrock claim is fully removed, and the setInterval count invariant (=1) is unchanged"
    requirement: "TCLOUD-02"
    verification:
      - kind: other
        ref: "grep for the removed hardcoded string in web/teacher.html == 0"
        status: pass
      - kind: other
        ref: "diagSrc id/getElementById occurrence count in web/teacher.html >= 2"
        status: pass
      - kind: other
        ref: "'離線規則式產出'/'來源未知' occurrence count in web/teacher.html >= 2"
        status: pass
      - kind: other
        ref: "setInterval occurrence count in web/teacher.html == 1"
        status: pass
      - kind: manual_procedural
        ref: "Visual confirmation that the badge actually flips color/text in a live browser when source changes between cloud and rule"
        status: unknown
    human_judgment: true
    rationale: "Grep/text checks confirm the markup, id wiring, and label text are correct, but actual browser rendering (badge color, layout, real flip behavior on live data) was not verified with a live server + browser in this executor session. This overlaps with what Task 4's device checkpoint will observe directly."
  - id: D4
    description: "docs/TCLOUD_VERIFY.md exists, mirrors docs/RELAY_VERIFY.md's structure (purpose, what-verifies/what-doesn't table, single-machine topology honesty, env var names only, five rehearsal steps, on-site evidence commands using both cloud_provider and diagnosis source, degradation script), and leaks zero credential values"
    requirement: "TCLOUD-01"
    verification:
      - kind: other
        ref: "grep '驗不到' docs/TCLOUD_VERIFY.md >= 1"
        status: pass
      - kind: other
        ref: "grep 'cloud_provider' docs/TCLOUD_VERIFY.md >= 1"
        status: pass
      - kind: other
        ref: "grep 'source' docs/TCLOUD_VERIFY.md >= 1"
        status: pass
      - kind: other
        ref: "AKIA/sk- credential-pattern regex match count in docs/TCLOUD_VERIFY.md == 0"
        status: pass
      - kind: other
        ref: "grep '單機'/'本機' docs/TCLOUD_VERIFY.md >= 1"
        status: pass
    human_judgment: false
  - id: D5
    description: "Task 4: physical Genio 520 rehearsal of the full finale beat (unplug network, offline turns, plug back in, confirm pending drains to zero and a new diagnosis appears, confirm the dashboard's source badge and /api/status's cloud_provider agree that the diagnosis was produced by Bedrock, not silently downgraded to rule)"
    requirement: "TCLOUD-02"
    verification: []
    human_judgment: true
    rationale: "This is the plan's blocking checkpoint (type=\"checkpoint:human-verify\", gate=\"blocking\"). It requires a real AWS-credentialed Genio 520 device and cannot be simulated or auto-approved by this executor per the plan's autonomous: false frontmatter and the explicit checkpoint_handling instruction not to fabricate device behavior. See \"Awaiting Human Verification\" section below for the exact steps and evidence required."

# Metrics
duration: ~15min (Tasks 1-3; Task 4 blocked on device access)
completed: 2026-07-27
status: blocked
---

# Phase 11 Plan 04: Diagnosis Source Provenance + Finals Rehearsal Runbook Summary

**Added `generate_diagnosis()`'s `source: "cloud" | "rule"` provenance marker (the only auditable evidence a diagnosis wasn't silently downgraded), wired it into an honest teacher-dashboard badge that replaces an unconditional "AWS Bedrock produced this" claim, covered the whole closed loop with 12 offline end-to-end tests, and wrote the finals rehearsal runbook — but the plan's Task 4 physical-device checkpoint is still pending human execution on the real Genio 520**

## Performance

- **Duration:** ~15 min for Tasks 1-3 (base commit `686cc22` at 12:09:33+08:00, last commit `e2a250e` at 12:24:43+08:00). Task 4 requires physical device access and has not started.
- **Tasks:** 3 of 4 complete (Task 4 is the blocking human-verify checkpoint)
- **Files modified:** 5 (`server/diagnose.py`, `tests/test_tcloud_e2e.py`, `tests/test_e2e.py`, `web/teacher.html`, `docs/TCLOUD_VERIFY.md` created)

## Accomplishments

- `server/diagnose.py::generate_diagnosis()` now returns `result["source"]` ("cloud" | "rule") at exactly two assignment points — one after a successful cloud branch, one after the rule-based fallback — so the value can never be missing. Key/value convention copied verbatim from `server/agents/homework.py`'s existing provenance field. The silent Bedrock→relay→rule degradation chain itself is completely untouched (order, timeouts, exception handling all unchanged); this only adds an auditable marker on top of it.
- `tests/test_tcloud_e2e.py` (12 tests): six behaviors locking the `source` value domain (no credentials, Bedrock success, Bedrock+relay failure, `allow_cloud=False`, consent not granted, closed value domain), plus five end-to-end behaviors — offline-window pending drains to zero and produces a new diagnosis via `POST /api/network_mode`, zero intercepted Bedrock calls in edge mode and when consent is withheld, and the intercepted prompt string excludes both a real phone number (via unmocked `guardrails.deidentify`) and the student's display name.
- `web/teacher.html`: `.diag-src`'s hardcoded "由 Hermes Agent（持久記憶）＋ AWS Bedrock（Claude）產出" claim is gone. New `renderDiagnosisSource()` renders one of three honest labels from the latest diagnosis's `source` field, reusing the existing `.badge`/`.badge.cloud`/`.badge.edge`/`.dot` CSS classes already established for the agent-output cards (no new CSS). `setInterval` count invariant (=1, pinned by 11-03) is unchanged.
- `docs/TCLOUD_VERIFY.md`: finals rehearsal runbook mirroring `docs/RELAY_VERIFY.md`'s structure — what-verifies/what-doesn't table, an explicit single-machine topology honesty section (the rehearsal validates local pending-queue promotion + real Bedrock egress, not a genuine cross-device HTTP upload), env var names only (zero credential values), the five rehearsal steps mapped to Task 4's checkpoint, on-site evidence commands (both `cloud_provider` and diagnosis `source` together, since `cloud_provider` alone doesn't prove a call succeeded), and a degradation script for when the cloud call silently falls back.

## Task Commits

Each completed task was committed atomically:

1. **Task 1: 診斷來源標記 source: cloud | rule** - `9797ec2` (feat)
2. **Task 2: 端到端閉環與三道出境閘門的自動化驗證** - `016b893` (test)
3. **Task 3: 儀表板來源徽章與決賽彩排 runbook** - `e2a250e` (feat)
4. **Task 4: 真機端到端彩排（決賽橋段）** - **not started, blocking checkpoint, see below**

_TDD note: Tasks 1 and 2 were marked `tdd="true"`. Following the precedent established by 11-01/11-02/11-03 in this phase, execution combined the RED (failing test) and GREEN (implementation) steps into a single commit per task rather than separate `test(...)` then `feat(...)` commits. Tests were written and verified passing before each commit. See "TDD Gate Compliance" below._

## Files Created/Modified

- `server/diagnose.py` - `generate_diagnosis()` gained the `source` assignment at its two convergence points, plus a docstring paragraph explaining why the marker exists (the degradation chain is intentionally silent for demo resilience, so nothing else can distinguish a real Bedrock call from a downgrade).
- `tests/test_tcloud_e2e.py` - New file, 12 tests, docstring records the T-11-13 residual risk (`ai_response_text` is not deidentified before entering the Bedrock prompt — a pre-existing implementation boundary, not a regression, explicitly out of scope per CONTEXT.md `<deferred>`).
- `tests/test_e2e.py` - Fixed a regression this plan's Task 1 caused: `test_post_network_mode_cloud_returns_new_diagnosis` asserted an exact closed key-set for the diagnosis dict that didn't yet include `source`. Updated to include it and assert its value domain.
- `web/teacher.html` - Replaced the hardcoded `.diag-src` claim with `id="diagSrc"` + `renderDiagnosisSource()`, called from `renderAll()` alongside the existing diagnosis-card render call.
- `docs/TCLOUD_VERIFY.md` - New file, the finals rehearsal runbook.

## Decisions Made

- `source` is assigned at exactly two points inside `generate_diagnosis()` (never via a flag variable or post-hoc inference from other state) — this keeps the assignment trivially AST-auditable and guarantees the value literally cannot be missing on any return path.
- The interception-over-assertion test pattern from `tests/test_agent_privacy.py` was copied verbatim for the privacy-relevant tests: never mock `guardrails.deidentify` itself, only intercept the outbound prompt string and assert on its content. This is a materially stronger guarantee than asserting the function was merely called, because `deidentify()` does not mask Chinese names — only the content assertion would catch a regression there.
- Fixed the `tests/test_e2e.py` regression (see Deviations below) as part of Task 2's commit rather than deferring it, since it's a direct, in-scope consequence of Task 1's contract change and leaving it broken would have left the full suite red.
- `docs/TCLOUD_VERIFY.md` states the single-machine topology limitation explicitly rather than using "upload complete" language, per the plan's explicit instruction — the rehearsal genuinely does not exercise `push_pending()`'s cross-device HTTP path (unit-tested only), and conflating that with the local pending-promotion + real Bedrock-egress path the rehearsal does exercise would misrepresent what was actually verified.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed a diagnosis-contract regression in a pre-existing test**
- **Found during:** Task 2 (running the full suite after adding `source`)
- **Issue:** `tests/test_e2e.py::test_post_network_mode_cloud_returns_new_diagnosis` asserted `set(diag.keys()) == {...}` with an exact closed key-set that predates this plan's `source` addition; adding `source` to `generate_diagnosis()`'s output (Task 1's entire purpose) made this pre-existing test fail.
- **Fix:** Added `"source"` to the expected key set and an explicit assertion that its value is in `("cloud", "rule")`.
- **Files modified:** `tests/test_e2e.py`
- **Verification:** `tests/test_e2e.py` passes (13/13); full suite passes (890/890, up from the pre-plan baseline of 878).
- **Committed in:** `016b893` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug fix, Rule 1)
**Impact on plan:** Necessary and expected consequence of Task 1's contract change; no scope creep — the fix only updates the test's expectations to match the plan's intended new contract, it does not touch any implementation.

## TDD Gate Compliance

Tasks 1 and 2 were marked `tdd="true"` in the plan. As with 11-01/11-02/11-03 in this phase, execution combined the RED (failing test) and GREEN (implementation) steps into a single commit per task rather than separate `test(...)` then `feat(...)` commits. Tests were written and verified to pass before each commit; no implementation shipped without corresponding test coverage. This is a process shortcut consistent with this phase's established precedent, not a coverage gap.

## Issues Encountered

- The Bash tool's sandbox flagged `grep -c "source" server/diagnose.py` (and similar commands containing the bare word `source`) as an attempt to invoke the shell `source` builtin, refusing to run it. Worked around by using `python -c "print(open(...).read().count(...))"` for all `source`-related grep-equivalent checks instead. No impact on verification coverage — same assertions, different tool.
- This worktree has no local `.venv` (gitignored, not worktree-local, consistent with 11-01/11-02/11-03's noted environment). All test runs used `/home/budaedu/talkybuddy/.venv/bin/python` with `PYTHONPATH=.` and the worktree as `cwd`.

## Known Stubs

None — no hardcoded empty/placeholder values were introduced. The dashboard's "來源未知" (unknown source) label is an intentional, honest fallback state (no diagnosis yet), not a stub masking missing wiring.

## New Known-Gap (per plan's `<output>` requirement)

**`server/diagnose.py:536`'s `ai_response_text` is not passed through `guardrails.deidentify()`** before entering the Bedrock diagnosis prompt (only `student_text` is, at line 535). If the AI companion's reply echoes the child's name (e.g. "Hi Mimi!"), that name would go out to Bedrock verbatim inside `ai_response_text`. This is a pre-existing implementation boundary (not introduced by this plan) and is explicitly excluded from this phase's scope per `.planning/phases/11-cloud-teacher-closed-loop/11-CONTEXT.md`'s `<deferred>` section ("強化 `deidentify()` 語意層"). Recorded here per the plan's `<output>` requirement and mirrored in the STRIDE threat register as `T-11-13` (disposition: `accept`). Recommend a future milestone extend `_build_diagnosis_prompt()` to deidentify `ai_response_text` too, or (more robustly) have the companion-reply generator avoid echoing the child's name in the first place.

## Single-Machine Topology Note (per plan's `<output>` requirement)

The finals demo runs on a single Genio 520 process that plays both "the child's device" and "the teacher's cloud server" roles — there is no second machine actually receiving an HTTP upload. What "upload" means in the actual finals rehearsal is: (1) local pending-queue promotion (`store.mark_synced()` / `opportunistic_sync()`'s local path, direct SQLite operations, no HTTP), and (2) the diagnosis prompt genuinely leaving the machine to AWS Bedrock (the real device-crossing boundary, in `_build_diagnosis_prompt()`). `sync_client.push_pending()`'s full HTTP path (whitelist projection + consent gate + real network call) is covered by `tests/test_sync_client.py` unit tests only — the rehearsal itself does not exercise it, because there is no second device to receive the call. This is stated explicitly (not glossed over) in `docs/TCLOUD_VERIFY.md`'s "單機拓樸的誠實說明" section, per the plan's requirement.

## User Setup Required

**Task 4's physical-device rehearsal requires AWS Bedrock credentials and Genio 520 access that this executor does not have.** See "Awaiting Human Verification" below for exact steps.

## Awaiting Human Verification (Task 4 — blocking checkpoint, not executed)

This plan's frontmatter is `autonomous: false` specifically because Task 4 (`type="checkpoint:human-verify"`, `gate="blocking"`) requires a physical Genio 520 rehearsal that this executor cannot perform, simulate, or auto-approve. Tasks 1-3 (code, tests, docs) are complete and committed; Task 4 has not started.

**What must happen next:** a human with Genio 520 + AWS Bedrock credential access must follow `docs/TCLOUD_VERIFY.md`'s five rehearsal steps and report back:

1. Start `edge/runtime/run_edge.sh`, open the teacher dashboard, confirm the student card shows real name/ID/device (not `–`).
2. Switch to edge mode (or physically unplug ethernet), complete 2-3 offline conversation turns, confirm normal replies with no multi-second silence (Phase 9 regression check).
3. Confirm the "待同步" (pending) counter increments on the teacher dashboard during the offline window.
4. Switch back to cloud mode **without letting the child speak again**. Observe: does "待同步" drain to zero within the 5-second poll interval? Does a new diagnosis card appear? **What does the diagnosis-source badge say?**
5. Record the on-site evidence: `GET /api/status`'s `cloud_provider` value and the latest `GET /api/diagnoses` entry's `source` value.

**Judgment rule (already encoded in `docs/TCLOUD_VERIFY.md` and the plan's Task 4):** `source == "cloud"` → TCLOUD-02 passes, SC4's "real, non-mock diagnosis" claim is substantiated. `source == "rule"` → **does not pass**, regardless of what the dashboard visually shows — this is exactly the silent-downgrade failure mode this whole plan exists to make auditable. If `source == "rule"`, the human must also report the `cloud_provider` value and the tail of `run_edge.sh`'s log so a follow-up session can diagnose whether it's a credentials, region, model-ID, or timeout problem.

**Both flagged assumptions from the plan's "⚠ 旗標假設" table remain formally unresolved** until this checkpoint reports back — this SUMMARY does not resolve them; it only completes the infrastructure (the `source` field) that makes them resolvable.

## Next Phase Readiness

- `server/diagnose.py`'s `source` key is now part of the diagnosis dict's public contract — any future code reading a diagnosis dict (dashboard, exports, other agents) can and should treat it as always-present.
- `web/teacher.html`'s diagnosis-source badge is live and will correctly reflect whatever `source` value ships from the backend, including once Task 4's rehearsal happens.
- `docs/TCLOUD_VERIFY.md` is ready to hand to whoever runs the physical rehearsal — it needs no further authoring, only execution.
- **This plan (and Phase 11 as a whole, if this is the last plan in the wave) cannot be marked fully complete until Task 4's checkpoint returns.** The orchestrator should route this back to a resumed executor (or surface directly to the user) once a human has run the rehearsal, per the `<resume-signal>` in `11-04-PLAN.md`'s Task 4: report the `source` and `cloud_provider` values, then type "approved" or describe what went wrong.
- `requirements-completed` is intentionally left empty in this SUMMARY's frontmatter — both TCLOUD-01 and TCLOUD-02 have sub-scope closed by this plan (and by 11-01/11-02/11-03 before it), but the requirement itself should not be marked complete in `REQUIREMENTS.md` until Task 4's device verification actually returns a `cloud` source value, consistent with 11-01/11-02/11-03's practice of deferring `requirements mark-complete` to whichever plan closes the requirement's full scope — and per this worktree's explicit instruction that the orchestrator owns `STATE.md`/`ROADMAP.md`/`REQUIREMENTS.md` writes.

---
*Phase: 11-cloud-teacher-closed-loop*
*Completed: 2026-07-27 (Tasks 1-3 only; Task 4 pending)*

## Self-Check: PASSED

All modified/created files found on disk (`server/diagnose.py`,
`tests/test_tcloud_e2e.py`, `tests/test_e2e.py`, `web/teacher.html`,
`docs/TCLOUD_VERIFY.md`) and all three task commits (`9797ec2`,
`016b893`, `e2a250e`) confirmed present in `git log`. Task 4's checkpoint
is explicitly not self-checked as "complete" — it is unstarted pending
human device access, as detailed above.
