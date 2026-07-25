---
phase: 09-network-cut-demo-hardening
plan: 03
subsystem: ui
tags: [css, vanilla-js, accessibility, copywriting, network-mode-badge]

# Dependency graph
requires:
  - phase: 09-network-cut-demo-hardening
    provides: "09-01 kill-switch JWT gate + network_mode re-sync fix; 09-02 shortened cloud timeouts — this plan only touches the front-end visual/copy layer on top of that already-correct behavior"
provides:
  - "modeBadge visual strengthening (padding 4px 12px, dot 12px, one-time badgePulse animation on active switch only)"
  - "toast copy that explicitly names the cloud disconnect during the network-cut demo"
  - "aria-live=\"polite\" on #modeBadge"
affects: [09-network-cut-demo-hardening, demo-rehearsal]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "One-time CSS keyframe animation triggered only from the user-action success callback (never from the shared applyMode()/refreshStatus() functions that also run on the 5s poll) — prevents a shared-function animation from firing on every passive refresh"

key-files:
  created: []
  modified:
    - web/index.html

key-decisions:
  - "Pulse trigger placed in airplaneSwitch click handler's fetch success callback, strictly after applyMode(target) — applyMode() overwrites modeBadge.className wholesale on line ~695, so any class added before that call would be wiped"
  - "Adopted 09-UI-SPEC.md's suggested copy verbatim (雲端已斷線 — 邊緣運算持續對話 / ☁️ 已切回雲端連線) per 09-CONTEXT.md's Claude's Discretion carve-out for toast wording"

patterns-established:
  - "badgePulse keyframes/class follow the existing compact single-line @keyframes convention (talkBeat/pop) already used in this file"

requirements-completed: [NETCUT-02]

coverage:
  - id: D1
    description: "modeBadge padding/dot size enlarged to 4px grid values (4px 12px / 12px x 12px) for stage-distance legibility"
    requirement: "NETCUT-02"
    verification:
      - kind: automated_ui
        ref: "grep -q 'padding:4px 12px' web/index.html && grep -q 'width:12px;height:12px' web/index.html"
        status: pass
    human_judgment: false
  - id: D2
    description: "One-time badgePulse animation (scale 1->1.18->1, .6s) fires only on active user-triggered mode switch, never on the 5s /api/status poll"
    requirement: "NETCUT-02"
    verification:
      - kind: automated_ui
        ref: "region-scoped grep: applyMode() and refreshStatus() function bodies contain zero 'pulse' occurrences; node --check on extracted inline <script>"
        status: pass
    human_judgment: true
    rationale: "Automated checks prove the animation code is absent from the two shared polling-adjacent functions, but confirming the pulse actually renders once on click and stays silent during 15s of idle polling requires a human to watch it in a browser (plan's own non-blocking manual verification step)."
  - id: D3
    description: "modeBadge carries aria-live=\"polite\" for accessibility"
    requirement: "NETCUT-02"
    verification:
      - kind: automated_ui
        ref: "grep -q 'aria-live=\"polite\"' web/index.html"
        status: pass
    human_judgment: false
  - id: D4
    description: "Toast copy on switching to edge explicitly states cloud is disconnected; cloud-return toast (no new diagnosis) gains matching cloud icon"
    requirement: "NETCUT-02"
    verification:
      - kind: automated_ui
        ref: "grep -q '雲端已斷線 — 邊緣運算持續對話' web/index.html && grep -q '☁️ 已切回雲端連線' web/index.html; showToast( call-count diff against pre-plan HEAD = 0"
        status: pass
    human_judgment: false

# Metrics
duration: 5min
completed: 2026-07-25
status: complete
---

# Phase 9 Plan 3: Badge Visual Strengthening + Toast Copy Summary

**modeBadge enlarged to a 4px-grid stage-legible size with a one-time click-only pulse animation, plus toast copy that explicitly names the cloud disconnect during the network-cut demo**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-07-25T20:26Z (approx, following 09-02)
- **Completed:** 2026-07-25T20:29Z
- **Tasks:** 2
- **Files modified:** 1 (`web/index.html`)

## Accomplishments
- `.badge` padding grew from `3px 10px` to `4px 12px` and `.badge .dot` from `8px` to `12px` — both now sit on the 4px spacing grid and give the badge ~20-30% more visual mass for stage-distance legibility, with zero layout/position change.
- Added `@keyframes badgePulse` / `.badge.pulse` (scale 1 → 1.18 → 1, 0.6s) triggered exclusively from the `airplaneSwitch` click handler's fetch success callback — strictly after `applyMode(target)` (which overwrites `modeBadge.className` wholesale) — and removed once via `animationend`. Verified via region-scoped grep that `applyMode()` and `refreshStatus()` (the 5s-poll path) contain zero pulse references, so passive refreshes never flash the badge.
- `#modeBadge` gained `aria-live="polite"`.
- Edge-switch toast now reads `✈️ 飛航模式開啟，雲端已斷線 — 邊緣運算持續對話` (explicitly names the cloud disconnect so judges read it as an intentional demo, not a crash); cloud-return toast (no new diagnosis) now reads `☁️ 已切回雲端連線` to match the sibling "synced N" toast's icon style.

## Task Commits

Each task was committed atomically:

1. **Task 1: modeBadge 舞台可辨識強化** - `45e3a36` (feat)
2. **Task 2: 切換 toast 文案改為明講斷網** - `1d8f494` (feat)

**Plan metadata:** (this commit)

## Files Created/Modified
- `web/index.html` - `.badge`/`.badge .dot` CSS sizing, new `@keyframes badgePulse`/`.badge.pulse`, `aria-live="polite"` on `#modeBadge`, pulse trigger in `airplaneSwitch` click success callback, two toast strings

## Decisions Made
- Pulse trigger lives strictly after `applyMode(target)` in the click handler (never inside `applyMode()`/`refreshStatus()`) because `applyMode()` overwrites `modeBadge.className` wholesale — adding the class before that line would be discarded immediately.
- Adopted 09-UI-SPEC.md's suggested toast copy verbatim per 09-CONTEXT.md's Claude's Discretion carve-out (toast wording is explicitly not a locked decision).

## Deviations from Plan

None - plan executed exactly as written. Both tasks' `<automated>` verify blocks passed unchanged, `git diff --stat` shows only `web/index.html` with 18 changed lines (within the plan's 20-line budget), and zero new hex color literals were introduced (`git diff | grep '^\+' | grep -c '#[0-9a-fA-F]{6}'` = 0).

## Issues Encountered

None. The plan required staging the two tasks as separate atomic commits despite both touching `web/index.html`; this was done by reverting to HEAD, reapplying only the Task 1 hunks (CSS sizing/keyframes/aria-live/pulse-trigger scaffolding) for the first commit, then reapplying the Task 2 toast-string hunks for the second commit — no content differs from a single combined diff, only commit boundaries.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- 09-04-PLAN.md (rehearsal script, NETCUT-03) is next; this plan's badge/toast changes are purely additive to the demo's visual clarity and don't touch any backend behavior 09-04 would depend on.
- Manual browser spot-check (plan's non-blocking verification step: click switch once, badge pulses; idle 15s / ≥3 poll cycles, badge stays silent) was not performed in this headless execution session — recommend a quick visual pass during the NETCUT-03 rehearsal.

---
*Phase: 09-network-cut-demo-hardening*
*Completed: 2026-07-25*

## Self-Check: PASSED
