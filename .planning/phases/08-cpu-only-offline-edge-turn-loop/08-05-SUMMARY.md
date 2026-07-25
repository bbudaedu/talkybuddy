---
phase: 08-cpu-only-offline-edge-turn-loop
plan: 05
subsystem: infra
tags: [genio-520, llama-server, sherpa-onnx, edge, tcpdump, vmhwm]

requires:
  - phase: 08-04
    provides: llama-server + uvicorn deployed and running on real Genio 520 board, /health reachable
provides:
  - edge/runtime/measure_peak_rss.py — cross-process VmHWM peak-memory summation tool (ELOOP-04), unit-tested
  - edge/runtime/warmup_llama_server.py — boot-time prompt-cache warmup for llama-server, wired into run_edge.sh
  - edge/EDGE_TURN_LOOP_VALIDATION.md — real-hardware A/B/C/D validation record (latency, memory, zero-cloud, binding)
affects: [phase-09, edge-runtime, demo-day-runbook]

tech-stack:
  added: []
  patterns:
    - "llama-server prompt-cache warmup on boot (throwaway completion before exec uvicorn) to move fixed system-prompt recompute cost off the first real turn"
    - "cross-process peak-RSS accounting via /proc/<pid>/status VmHWM summed across all engine processes, never a single PID"

key-files:
  created:
    - edge/runtime/measure_peak_rss.py
    - tests/test_measure_peak_rss.py
    - edge/runtime/warmup_llama_server.py
    - tests/test_warmup_llama_server.py
    - edge/EDGE_TURN_LOOP_VALIDATION.md
  modified:
    - edge/runtime/run_edge.sh

key-decisions:
  - "Accepted steady-state GO / cold-start NO-GO as the final checkpoint verdict rather than chasing the threshold further — root cause is understood (fixed reply-format suffix lives in the user message, not the system prompt, so warmup can't pre-heat it) and the residual gap (5.85s vs 3-4s target) is small enough to cover with an operational fallback (host speaks one warm-up turn before the audience's first line) rather than a code change under finals-week time pressure."
  - "Chose TALKYBUDDY_LLM_THREADS=6 as the production thread count based on real on-device llama-bench (pp 39.06 t/s / tg 12.35 t/s, both best at threads=6; threads=8 regresses since only 2 of 8 cores are big Cortex-A78)"
  - "Warmup call is a graceful-degrade no-op on failure (run_edge.sh's `|| true`) — a stalled/crashed warmup must never block uvicorn from starting"

patterns-established:
  - "Pattern: boot-time engine warmup as a separate, independently testable module (warmup_llama_server.py) invoked from the shell launcher rather than inlined bash — keeps the HTTP call unit-testable"

requirements-completed: [ELOOP-01, ELOOP-03, ELOOP-04]

coverage:
  - id: D1
    description: "Cross-process VmHWM peak-memory summation tool with unit tests (ELOOP-04 measurement method)"
    requirement: "ELOOP-04"
    verification:
      - kind: unit
        ref: "tests/test_measure_peak_rss.py"
        status: pass
    human_judgment: false
  - id: D2
    description: "Real Genio 520 measurement: llama-bench thread scan + real-turn latency go/no-go against D-05 threshold (ELOOP-03)"
    requirement: "ELOOP-03"
    verification:
      - kind: manual_procedural
        ref: "edge/EDGE_TURN_LOOP_VALIDATION.md section A — llama-bench table, warm-turn samples (2.96-2.99s x3), cold-start samples (10.03s pre-warmup, 5.85s post-warmup)"
        status: pass
    human_judgment: true
    rationale: "Hardware latency measurement on physical board; steady-state passes but cold-start remains NO-GO — requires a human go/no-go call on whether the documented operational fallback is acceptable for demo day"
  - id: D3
    description: "Real Genio 520 measurement: uvicorn + llama-server VmHWM cross-process sum vs 4GB threshold (ELOOP-04)"
    requirement: "ELOOP-04"
    verification:
      - kind: manual_procedural
        ref: "edge/EDGE_TURN_LOOP_VALIDATION.md section B — uvicorn 673456 kB + llama-server 2114524 kB = ~2723 MB, 33.5% headroom under 4096 MB"
        status: pass
    human_judgment: false
  - id: D4
    description: "Real Genio 520 zero-cloud audit: 25s tcpdump during a live turn shows no outbound packets from the board, /api/status confirms network_mode=edge (ELOOP-01, ROADMAP success criterion 1)"
    requirement: "ELOOP-01"
    verification:
      - kind: manual_procedural
        ref: "edge/EDGE_TURN_LOOP_VALIDATION.md section C — tcpdump capture (only a neighboring device's mDNS broadcast, zero packets from 192.168.31.78) + /api/status network_mode:edge"
        status: pass
    human_judgment: false
  - id: D5
    description: "llama-server external-IP binding verification: refused from outside, 200 from loopback (Open Question 2)"
    verification:
      - kind: manual_procedural
        ref: "edge/EDGE_TURN_LOOP_VALIDATION.md section D — external curl exit 7 (connection refused) vs loopback curl 200"
        status: pass
    human_judgment: false
  - id: D6
    description: "Boot-time llama-server prompt-cache warmup wired into run_edge.sh, cuts cold-start first-turn latency from 10.03s to 5.85s"
    verification:
      - kind: unit
        ref: "tests/test_warmup_llama_server.py"
        status: pass
      - kind: manual_procedural
        ref: "edge/EDGE_TURN_LOOP_VALIDATION.md section A (warmup mitigation) — real-device boot log showing WARMUP_OK, then real turn showing LCP similarity 0.771 and round_total 5852ms"
        status: pass
    human_judgment: false

duration: ~4h (across multiple background-agent + main-session turns)
completed: 2026-07-25
status: complete
---

# Phase 08 Plan 05: Edge Turn-Loop Real-Hardware Validation Summary

**Genio 520 real-hardware A/B/C/D validation closes ELOOP-01/03/04: steady-state turn latency GO (2.96-2.99s), memory PASS (~2723MB/4096MB), zero-cloud PASS, llama-server binding PASS — plus a boot-time prompt-cache warmup that cuts cold-start first-turn latency 42% (10.03s → 5.85s), documented as still NO-GO with a named operational fallback for demo day.**

## Performance

- **Duration:** ~4h (spanning background-agent Task 1 work, a blocked-then-resumed checkpoint requiring real device access, and a follow-up warmup implementation round)
- **Completed:** 2026-07-25
- **Tasks:** 3 (Task 1 auto, Checkpoint human-verify, Task 3 auto) + 1 approved follow-up (warmup mitigation)
- **Files modified:** 6

## Accomplishments
- Cross-process VmHWM peak-memory summation tool (`edge/runtime/measure_peak_rss.py`) built and unit-tested (9 tests), used on real device to get the ELOOP-04 number instead of hand-adding `/proc` output
- Full real-machine A/B/C/D validation run on Genio 520: llama-bench thread scan (threads=6 selected), real-turn end-to-end latency (steady-state and cold-start), cross-process VmHWM, 25s tcpdump zero-cloud audit, and external-vs-loopback binding check
- Root-caused the cold-start latency gap to llama-server's per-slot KV-cache reuse: a fresh process must recompute the full ~293-token system prompt at ~39 t/s (~7.5s), while a warmed slot only recomputes new tokens
- Implemented and verified on-device a boot-time llama-server prompt-cache warmup (`edge/runtime/warmup_llama_server.py`, wired into `run_edge.sh`), cutting the audience-facing first turn from 10.03s to 5.85s — real device log evidence captured, not simulated
- Wrote `edge/EDGE_TURN_LOOP_VALIDATION.md` as an honest, dated, auditable record of all four checkpoint results plus the warmup follow-up, explicitly not overstating the still-NO-GO cold-start number

## Task Commits

1. **Task 1: measure_peak_rss.py + unit tests (ELOOP-04)** - `60ceae8` (feat)
2. **Checkpoint: real Genio 520 A/B/C/D validation** - human-verify, executed live via SSH over this session (no code commit; results captured in Task 3's file)
3. **Follow-up: llama-server prompt-cache warmup wired into run_edge.sh** - `387242c` (fix)
4. **Task 3: edge/EDGE_TURN_LOOP_VALIDATION.md** - `6b6bcf5` (docs)

## Files Created/Modified
- `edge/runtime/measure_peak_rss.py` - Pure functions `read_peak_rss_kb`/`sum_peak_rss`/`kb_to_mb`/`within_threshold` + `main()` pgrep-based on-device entrypoint (fixed argv, no `shell=True`)
- `tests/test_measure_peak_rss.py` - 9 unit tests against `tmp_path`-faked `/proc/<pid>/status` files
- `edge/runtime/warmup_llama_server.py` - Sends one throwaway `/v1/chat/completions` warmup call to llama-server using the same system prompt as `server/llm.py::EdgeLLM`, swallows failures
- `tests/test_warmup_llama_server.py` - Unit tests covering warmup success/failure paths (11 tests)
- `edge/runtime/run_edge.sh` - Invokes the warmup module after llama-server's `/health` check passes and before `exec`'ing uvicorn
- `edge/EDGE_TURN_LOOP_VALIDATION.md` - Dated real-hardware record of all four checkpoint results (A latency, B memory, C zero-cloud, D binding) plus the warmup mitigation follow-up

## Decisions Made
- **Accepted the checkpoint as passed with a documented residual gap** rather than pursuing a second structural prompt-restructuring pass (moving the fixed reply-format suffix from the user message into the system prompt, estimated to save another 1-1.5s but requiring another real-device verification round). With 5 days to finals, the user chose to lock in the verified 42% improvement and cover the remainder operationally (host speaks a warm-up line before the audience's first turn) rather than risk more prompt surgery against the clock.
- **Selected `--threads 6`** for the production llama-server invocation based on real on-device `llama-bench` numbers (best pp and tg both at threads=6), overriding the previous placeholder default of 4 via `TALKYBUDDY_LLM_THREADS=6`.
- **Warmup failure is non-blocking** — `run_edge.sh` calls the warmup module with `|| true` so a warmup timeout/crash never prevents uvicorn from starting (same graceful-degradation posture as the existing llama-server `/health` timeout handling it sits next to).

## Deviations from Plan

### Auto-fixed Issues

**1. [Checkpoint finding, not in original plan scope] Boot-time llama-server prompt-cache warmup**
- **Found during:** Checkpoint (real-device A latency measurement)
- **Issue:** Cold-start first turn measured at 10.03s, well past the D-05 3-4s threshold; plan's checkpoint only asked to measure and record go/no-go, not to fix it
- **Fix:** Added `edge/runtime/warmup_llama_server.py` + wired it into `run_edge.sh` to warm the llama-server prompt cache during boot, moving the fixed system-prompt recompute cost off the audience-visible first turn
- **Files modified:** `edge/runtime/warmup_llama_server.py` (new), `tests/test_warmup_llama_server.py` (new), `edge/runtime/run_edge.sh`
- **Verification:** 11 unit tests pass; real-device boot log shows `WARMUP_OK`; real post-boot turn shows `LCP similarity 0.771` and `round_total: 5852ms` (down from 10.03s)
- **Committed in:** `387242c`

---

**Total deviations:** 1 auto-fixed (scope addition, user-approved mid-session — user explicitly chose "implement the warmup mitigation" over "accept the NO-GO as-is" when presented with the choice)
**Impact on plan:** Net improvement to the phase's core latency deliverable; no scope creep beyond what the checkpoint's own findings required. Did not pursue the further structural prompt fix (system-prompt reordering) — logged below as a Phase 9 candidate instead.

## Issues Encountered
- Real-device provisioning for this checkpoint required pushing large ASR/TTS model assets (SenseVoice `model.int8.onnx`, ~229MB) over an intermittently-dropping Tailscale-routed SSH connection; resolved with a retrying rsync script (`--partial-dir` + 8 retries) run via `nohup ... & disown` rather than the harness's own background-job tracking, which had silently died mid-transfer on prior attempts. Not part of this plan's deliverables but was the blocking precondition for the checkpoint to run at all.
- `TALKYBUDDY_LLM_THREADS` env override required a full stack restart to take effect; two prior restart attempts failed silently (exit 255, no `/api/status` response) before the process ordering was corrected — resolved, final restart confirmed `asr:true, llm:true, tts:true` with `--threads 6` in the process list.

## User Setup Required
None - no external service configuration required. (Real-device SSH access to `192.168.31.78` was already provisioned from Phase 07/08-04.)

## Next Phase Readiness
- Phase 8's four hardware validation gates (ELOOP-01/03/04, external-binding) are all closed with honest, dated, real-machine evidence in `edge/EDGE_TURN_LOOP_VALIDATION.md` — no silent-fake-success debt carried forward.
- **Carried-forward item for Phase 9 (non-blocking):** move the fixed "跟我說一遍：<英文句>" reply-format instruction out of the user-message suffix in `server/llm.py::EdgeLLM.generate()` and into the system prompt, so the boot-time warmup can pre-heat it too. Estimated to close most or all of the remaining cold-start gap (5.85s → ~4.5s estimate, unverified). Requires a real-device round to confirm.
- **Demo-day operational note:** before letting the audience speak the first turn, the host should trigger one throwaway warm-up turn against the already-booted stack (same effect as the boot-time warmup, but covering the KV-cache-invalidating user-message suffix) so the audience-visible first turn lands in the verified 2.96-2.99s steady-state range instead of the 5.85s cold-start number.
- No blockers to closing out Phase 8.

---
*Phase: 08-cpu-only-offline-edge-turn-loop*
*Completed: 2026-07-25*
