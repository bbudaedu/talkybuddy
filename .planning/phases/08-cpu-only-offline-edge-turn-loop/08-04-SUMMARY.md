---
phase: 08-cpu-only-offline-edge-turn-loop
plan: 04
subsystem: infra
tags: [llama.cpp, cross-compile, cmake, rsync, ssh, deploy, health-check]

# Dependency graph
requires:
  - phase: 08-cpu-only-offline-edge-turn-loop
    provides: "08-01: build_llama_server_argv() + config.LLM_SERVER_HOST/PORT/THREADS"
  - phase: 08-cpu-only-offline-edge-turn-loop
    provides: "08-02: EdgeLLM as stdlib urllib HTTP client to llama-server /health + /v1/chat/completions"
provides:
  - "edge/deploy/build.sh: cross-compiles llama.cpp (D-02 flags, official repo, records commit hash) — script logic only, not yet executed on real hardware"
  - "edge/deploy/push.sh: rsync's edge/deploy/bin/ + models/*.gguf to device, chmod +x — script logic only, not yet executed on real hardware"
  - "edge/runtime/run_edge.sh: launches llama-server in background before uvicorn, health-gates on /health with 30s timeout, non-blocking degrade — script logic only, not yet executed on real hardware"
  - "docs/DEPLOY_EDGE.md: Phase 8 cross-compile + push + startup sequencing documented"
affects: [08-05]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "D-03 cross-toolchain parametrization: TALKYBUDDY_CROSS_CC/CXX env override, single code path for apt-default and Yocto-SDK-fallback"
    - "Background-launch + curl health-poll-loop + non-blocking degrade on timeout (T-08-07), mirroring edge/deploy/run.sh's existing health-check idiom"

key-files:
  created: []
  modified:
    - edge/deploy/build.sh
    - edge/deploy/push.sh
    - edge/runtime/run_edge.sh
    - docs/DEPLOY_EDGE.md
    - .gitignore

key-decisions:
  - "llama.cpp source cloned to third_party/llama.cpp/ (gitignored) rather than a temp dir, so a re-run of build.sh reuses the existing checkout instead of re-cloning every time"
  - "build.sh fails fast (exit 1) if TALKYBUDDY_CROSS_CC/CXX binaries are not found on PATH, before attempting any clone/cmake work — surfaces a clear D-03 remediation message instead of a late cmake configure-error"
  - "push.sh's new rsync blocks placed after the existing three (server/edge/runtime/web), preserving the pre-existing SSH-connectivity-check-first ordering; both new blocks error out explicitly (not silently skip) when edge/deploy/bin/llama-server or the GGUF file is missing"
  - "run_edge.sh's llama-server startup and health-poll block inserted between TALKYBUDDY_PIPELINE_PROFILE export and the final exec uvicorn line; that exec line's --host 0.0.0.0 --port 8787 was left byte-for-byte unmodified, per plan prohibition"

requirements-completed: []

# This plan's Task 1/2 deliverables are script-logic-only (verified via bash -n + grep,
# per the plan's own <verify> spec) — real hardware execution is gated on the checkpoint
# below, which could not be completed this run (board unreachable). requirements-completed
# is left empty; ELOOP-02 is not being claimed complete until the checkpoint's real-device
# verification (glibc ABI, /health 200) actually happens.

coverage:
  - id: D1
    description: "edge/deploy/build.sh cross-compiles llama.cpp with D-02 flags (-march=armv8.2-a+dotprod+i8mm, GGML_NATIVE=OFF), from the official ggml-org/llama.cpp repo only, records commit hash to LLAMACPP_COMMIT.txt, cross-toolchain parametrized via TALKYBUDDY_CROSS_CC/CXX for D-03 fallback"
    verification:
      - kind: unit
        ref: "bash -n edge/deploy/build.sh && grep -q 'armv8.2-a+dotprod+i8mm' ... && grep -q 'GGML_NATIVE=OFF' ... && grep -q 'llama-server' ... && grep -q 'rev-parse HEAD' edge/deploy/build.sh"
        status: pass
    human_judgment: true
    rationale: "Automated verification is syntax/grep-level only (bash -n + grep, exactly as the plan's own <verify> spec requires — the actual cross-compile was deliberately not run this pass, per explicit orchestrator instruction, since it needs real hardware to validate its output anyway). Real execution correctness (does cmake actually succeed, does the binary actually run) is unverified until the checkpoint."
  - id: D2
    description: "edge/deploy/push.sh rsyncs edge/deploy/bin/ (binaries + LLAMACPP_COMMIT.txt) and models/*.gguf to device with chmod +x, erroring explicitly if either source is missing; edge/runtime/run_edge.sh backgrounds llama-server via python -m edge.runtime.run_llama_server before uvicorn, polls /health for up to 30s, does not block uvicorn startup on timeout; the existing uvicorn exec line (--host 0.0.0.0 --port 8787) is unmodified"
    verification:
      - kind: unit
        ref: "bash -n edge/deploy/push.sh edge/runtime/run_edge.sh && grep -q 'edge/deploy/bin' push.sh && grep -Eq 'models/.*gguf' push.sh && grep -q 'run_llama_server' run_edge.sh && grep -q '/health' run_edge.sh && grep -q 'uvicorn server.app:app' run_edge.sh"
        status: pass
    human_judgment: true
    rationale: "Same as D1 — syntax/grep-level automated verification only, per plan spec. Real device rsync/startup/health behavior is unverified until the checkpoint."
  - id: D3
    description: "Real-machine glibc ABI validation (llama-server --version / ldd succeeds on Genio 520) and full-stack bring-up (run_edge.sh starts llama-server + uvicorn, both /health endpoints return 200, /api/status shows llm=true)"
    verification: []
    human_judgment: true
    rationale: "Requires physical access to the Genio 520 board over SSH — the board is confirmed unreachable during this execution run (Tailscale subnet route down, orchestrator-verified: ping 100% loss, ssh connection timed out). This is the plan's checkpoint:human-verify task; it could not be completed and is NOT being marked as passed. No fabricated output was produced for this deliverable."

# Metrics
duration: ~4min (Tasks 1-2 only; checkpoint not attempted — board unreachable)
completed: 2026-07-25
status: blocked
---

# Phase 8 Plan 04: llama-server cross-compile + deploy wiring Summary

**edge/deploy/build.sh cross-compiles llama.cpp (D-02 flags, official repo, commit-hash tracked); push.sh rsyncs the binaries + GGUF model; run_edge.sh backgrounds llama-server and health-gates uvicorn startup on it — all three verified at the script-syntax/grep level only; the plan's checkpoint (real Genio 520 glibc ABI + full-stack bring-up) is BLOCKED, not completed, because the board is currently unreachable**

## Performance

- **Duration:** ~4 min (Tasks 1-2; checkpoint task not attempted)
- **Completed:** 2026-07-25T07:08:05+08:00 (Task 2 commit; checkpoint still pending)
- **Tasks:** 2 of 3 (Task 1, Task 2 done; Checkpoint task reached and reported, not resolved)
- **Files modified:** 5 (4 modified scripts/docs + .gitignore)

## Accomplishments
- `edge/deploy/build.sh` extended in place: parametrizes the aarch64 cross-toolchain via `TALKYBUDDY_CROSS_CC`/`TALKYBUDDY_CROSS_CXX` (D-03, apt default with Yocto-SDK-fallback override, single code path); clones only the official `ggml-org/llama.cpp` (`third_party/llama.cpp/`, gitignored); cross-compiles with `-march=armv8.2-a+dotprod+i8mm` + `-DGGML_NATIVE=OFF` (D-02, never `armv8.7-a`/`GGML_NATIVE=ON`); builds `llama-server`/`llama-bench`/`llama-cli`, copies to `edge/deploy/bin/`, `file`-checks for aarch64 ELF, and records the compiled commit hash to `edge/deploy/bin/LLAMACPP_COMMIT.txt`
- `edge/deploy/push.sh` extended: two new `rsync -az` blocks push `edge/deploy/bin/` (binaries + commit-hash file) and `models/qwen2.5-1.5b-instruct-q4_k_m.gguf` to the device, `chmod +x` on the binaries, explicit `exit 1` errors (not silent skips) when either source is missing
- `edge/runtime/run_edge.sh` extended: backgrounds `python -m edge.runtime.run_llama_server` (which `os.execv`s into the real native binary, host defaulting to `127.0.0.1` per `run_llama_server.py`) before `exec uvicorn`, polls `http://127.0.0.1:${TALKYBUDDY_LLM_SERVER_PORT:-8080}/health` for up to 30s; timeout logs a warning but does NOT block uvicorn startup (T-08-07 — `EdgeLLM.available()`'s short-timeout degrade chain tolerates a not-yet-ready llama-server, pipeline falls back to scaffold-only). The pre-existing `exec ... uvicorn ... --host 0.0.0.0 --port 8787` line was left byte-for-byte unmodified
- `docs/DEPLOY_EDGE.md` updated with a new §4a documenting the Phase 8 cross-compile → push → health-gated-startup sequence and the five new `TALKYBUDDY_*` env vars
- `.gitignore` updated to exclude `edge/deploy/bin/` (compiled binaries) and `third_party/llama.cpp/` (cloned upstream source) — never committed, regenerated by `build.sh`

## Task Commits

Each task was committed atomically:

1. **Task 1: edge/deploy/build.sh — cross-compile llama.cpp (D-02 flags) + file/ldd sanity check** - `831b395` (feat)
2. **Task 2: push.sh binary+GGUF push + run_edge.sh llama-server startup/health-gating (Blocker 4)** - `ed51b7f` (feat)

**Checkpoint task:** reached, NOT resolved — see "Checkpoint Status" below. No commit for the checkpoint itself (nothing to commit; it is a real-hardware verification step).

**Plan metadata:** committed by orchestrator after wave completion (worktree convention — this executor does not write STATE.md/ROADMAP.md)

## Files Created/Modified
- `edge/deploy/build.sh` - Cross-compile llama.cpp (D-02 flags, official repo, commit-hash tracking, D-03-parametrized toolchain)
- `edge/deploy/push.sh` - Two new rsync blocks (binaries + GGUF), chmod +x, explicit-error-on-missing-source
- `edge/runtime/run_edge.sh` - Background llama-server launch + `/health` poll-loop before `exec uvicorn`; uvicorn line unchanged
- `docs/DEPLOY_EDGE.md` - New §4a documenting the Phase 8 deploy/startup sequencing and env vars
- `.gitignore` - Excludes `edge/deploy/bin/` and `third_party/llama.cpp/`

## Decisions Made
- llama.cpp source cloned to `third_party/llama.cpp/` (gitignored, not a temp dir) so re-runs of `build.sh` reuse the existing checkout rather than re-cloning every time
- `build.sh` fails fast with a clear D-03-remediation error message if `TALKYBUDDY_CROSS_CC`/`TALKYBUDDY_CROSS_CXX` are not found on `PATH`, before attempting any clone/cmake work
- `push.sh`'s two new rsync blocks are placed after the existing three, preserving the pre-existing "confirm SSH connectivity first" ordering; both explicitly error out (not silently skip) when the binary or GGUF source is missing
- `run_edge.sh`'s new block sits between the `TALKYBUDDY_PIPELINE_PROFILE` export and the final `exec uvicorn` line; the uvicorn line's `--host 0.0.0.0 --port 8787` was left completely untouched per the plan's explicit prohibition

## Deviations from Plan

None — Tasks 1 and 2 executed exactly as written; both automated `<verify>` commands (`bash -n` + `grep` checks) pass as specified in the plan.

## Checkpoint Status: BLOCKED (board unreachable — not completed, not fabricated)

The plan's `checkpoint:human-verify` task ("真機交叉編譯 ABI 驗證 + 全 stack bring-up") requires:
1. Running the real cross-compile (`edge/deploy/build.sh`) on a machine with the aarch64 toolchain.
2. Pushing to the real Genio 520 board (`edge/deploy/push.sh`).
3. SSH'ing into the board to check `llama-server --version`/`ldd` (glibc ABI, Pitfall 1, D-03 fallback if it fails).
4. Running `run_edge.sh` on the board and confirming both `/health` endpoints return 200 and `/api/status` shows `llm: true`.

**This could not be attempted in this run.** The orchestrator confirmed immediately before dispatching this executor that the board (`192.168.31.78`) is unreachable: `ping -c 3 192.168.31.78` → 100% packet loss, `ssh root@192.168.31.78` → connection timed out. The board is normally reachable via a Tailscale subnet route (advertised by the user's laptop/PVE host per `edge/BOARD_BRINGUP_DECISION.md`), and that route is currently down — outside this executor's control, and not something installable/fixable from this dev machine (a prior attempt to install Tailscale locally was already tried and explicitly rejected by the user).

Per the orchestrator's explicit instruction for this dispatch, the actual cross-compile (Task 1's real llama.cpp clone + cmake build, which takes multiple minutes and cannot itself be validated without the board) was also deliberately **not** run in this pass — it is left for the checkpoint resumption, to be run once by the orchestrator when the board is confirmed back online, so the compile and the real-hardware validation happen together rather than compiling blind now and re-validating later.

**No fabricated hardware output was produced.** No `--version` string, `/health` response, or `/api/status` payload is claimed anywhere in this SUMMARY. `coverage[].D3` above is explicitly `verification: []` / `human_judgment: true` to reflect this honestly.

**What IS verified:** the script logic itself (syntax-correct, contains the required build flags/commands per the plan's own automated `<verify>` spec) — see `coverage[].D1`/`D2` above.

**Resume path:** once the Tailscale subnet route is restored and the board is reachable again, resume at the checkpoint task by running (in order): `edge/deploy/build.sh` → `edge/deploy/push.sh` → SSH `llama-server --version`/`ldd` (D-03 fallback if ABI fails) → `edge/deploy/run.sh` (or `nohup ./edge/runtime/run_edge.sh &` on-device) → verify both `/health` endpoints and `/api/status`'s `llm=true`, per the plan's `<how-to-verify>` steps.

## Issues Encountered
Board/network unreachability (Tailscale subnet route down) — not a code or plan defect; see "Checkpoint Status" above.

## User Setup Required
Real-hardware checkpoint verification (see "Checkpoint Status") — requires the Genio 520 board to be reachable over SSH again. No other external service configuration required.

## Next Phase Readiness
- Task 1/2 script logic is ready and merge-safe (verified via `bash -n` + `grep`, matches D-02/D-03 requirements)
- **ELOOP-02 is NOT complete** — the checkpoint's real-device glibc ABI validation and full-stack `/health`/`/api/status` bring-up are outstanding and block this plan's `success_criteria`
- 08-05 (and any resumption of this plan's checkpoint) needs the board reachable to proceed; no code changes are needed to unblock it, only network/hardware access

## Self-Check: PASSED

- FOUND: edge/deploy/build.sh
- FOUND: edge/deploy/push.sh
- FOUND: edge/runtime/run_edge.sh
- FOUND: docs/DEPLOY_EDGE.md
- FOUND: .gitignore
- FOUND commit: 831b395
- FOUND commit: ed51b7f

---
*Phase: 08-cpu-only-offline-edge-turn-loop*
*Completed: 2026-07-25 (Tasks 1-2 only; checkpoint blocked, see above)*
