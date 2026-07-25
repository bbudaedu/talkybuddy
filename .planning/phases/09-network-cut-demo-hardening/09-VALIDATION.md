---
phase: 9
slug: network-cut-demo-hardening
# status lifecycle: draft (seeded by plan-phase) → validated (set by validate-phase §6)
# audit-milestone §5.5 distinguishes NOT-VALIDATED (draft) from PARTIAL (validated + nyquist_compliant: false) (#2117)
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-07-25
---

# Phase 9 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | server/streaming/tests/pytest.ini (project has no root pytest.ini; existing `tests/` dir runs via `.venv/bin/python -m pytest`) |
| **Quick run command** | `.venv/bin/python -m pytest tests/test_pipeline.py tests/test_app_authz.py -x -q` (narrow to touched modules) |
| **Full suite command** | `.venv/bin/python -m pytest tests/ -x -q` |
| **Estimated runtime** | ~30-60 seconds |

---

## Sampling Rate

- **After every task commit:** Run quick run command
- **After every plan wave:** Run full suite command
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 60 seconds

---

## Per-Task Verification Map

*Populated by gsd-planner during task breakdown — each task's `<verify><automated>` command maps to a row here.*

---

## Wave 0 Requirements

Existing pytest infrastructure (`tests/`, `.venv`) covers Phase 9 requirements — no new framework/scaffolding needed.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| ≥3 repeated physical/toggle-triggered network-cut rehearsals with recovery timing on real Genio 520 hardware | NETCUT-03 | Requires real device, SSH access, and human-operated timing measurement — cannot be simulated or fabricated | See phase RESEARCH.md "operational definition of recovery time"; checkpoint task must capture per-run timestamps and pass/fail against <1-2s threshold |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 60s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
