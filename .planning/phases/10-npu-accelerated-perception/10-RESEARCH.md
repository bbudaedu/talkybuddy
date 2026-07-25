# Phase 10: NPU-Accelerated Perception - Research

**Researched:** 2026-07-25
**Domain:** MediaTek Genio 520 NPU (MDLA 5.3) acceleration for ONNX-based ASR (SenseVoice via sherpa-onnx), ONNX Runtime `NeuronExecutionProvider` vs TFLite+Neuron Stable Delegate, per-op placement verification
**Confidence:** MEDIUM — official MediaTek Genio Community + docs cross-checked; the single most important finding (sherpa-onnx's Python API does **not** expose `NeuronExecutionProvider`) is derived from reading the sherpa-onnx source directly, not from a hands-on device test. Re-verify against the actual on-device `sherpa_onnx` package version on Day 1 before committing further.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01（鎖定）：** 使用者已持有 NeuroPilot Public 帳號（可下載 NP8 Converter），不受帳號註冊阻擋。可直接開始 ORT-NeuronEP vs TFLite 真機比較 spike，不需先做「不需帳號的部分」的迂迴安排。
- **D-02（鎖定）：** 照 ROADMAP 原訂 1–2 天硬性 time-box，非壓縮版半天快篩。中途設檢查點（建議 Day 1 結束）：若尚未看到「至少一個算子/子圖真的跑在 NPU 上」的可運作證據（per-op placement log 顯示 NPU 執行比例 > 0），立即收斂回 Phase 8 CPU-only 基線，記為 not-attempted 或部分達成，把剩餘時間讓給 Phase 11/12，不得為了硬湊 NPU 加速故事而超支到決賽剩餘天數的風險區。

### Claude's Discretion

- ORT-NeuronEP 與 TFLite 兩路徑的實際試驗順序、每條路徑分配的時數切分（在 1–2 天總預算內），由 planner/executor 依 `PITFALLS.md` Pitfall 3 建議（先試 ORT+NeuronEP，因轉換風險較低）決定，但需在 Day 1 結束前有明確可行/不可行的證據。
- per-op 放置 logging 的具體形式（console log / debug HUD / 檔案輸出）由 executor 依現有程式碼慣例（`server/app.py` 既有 status 端點模式）決定，不視為新決策。
- 中文 INT8 品質閘的具體驗收方式（母語聽測人選、腳本音檔來源）由 executor 依現場可取得資源決定；若 1–2 天 time-box 內來不及做完整 NPU-03 品質閘，優先完成 NPU-01 定案 + NPU-02 可運作 spike，NPU-03 可視情況部分達成或延後（但不得跳過誠實記錄）。

### Deferred Ideas (OUT OF SCOPE)

- **TFLite + Neuron Stable Delegate 完整轉換路徑**：僅在 ORT+NeuronEP 被證實算子覆蓋不足時才啟動；若 ORT+NeuronEP 可行，本輪不投入 onnx2tf/NP8 Converter 轉檔工作。
- **NPU TTS 加速**：REQUIREMENTS 既定 Out of Scope（P2），ASR 加速優先，時間有餘才考慮。
- **Neuron SDK All-in-One Bundle / ncc-tflite DLA offline 路徑**：NDA-gated 且無 Python API（僅 C/C++），明確排除。
- **GAI Toolkit（NPU 加速 LLM 生成）**：NDA-gated 且 Android-only，明確排除；CPU-only llama.cpp（Phase 8 已交付）維持為生成引擎。

> **Research addendum on D-02 interpretation:** Given the finding below (sherpa-onnx's Python API has no escape hatch to `NeuronExecutionProvider`, and both the ORT and TFLite paths require an *identical* amount of "bypass sherpa-onnx's convenience wrapper" engineering effort), the practical reading of D-02's Day-1 checkpoint should be: "did a **standalone raw ONNX Runtime session** (not sherpa-onnx's wrapper) show any node placed on `NeuronExecutionProvider`?" — not "is SenseVoice transcribing end-to-end via NPU?" See Common Pitfalls and Summary below for why this distinction matters for realistic scoping.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| NPU-01 | Spike 定案 NPU 路徑：ORT-NeuronEP vs TFLite 轉檔；1–2 天內決策，排除 NDA-gated 路徑 | See "Standard Stack", "Common Pitfalls" Pitfall N1/N2, "Code Examples" — concrete decision tree + smallest-falsifiable-test definition for the Day-1 checkpoint |
| NPU-02 | ASR（SenseVoice）經 NPU delegate 加速，含 per-op 放置 logging；算子不支援時退 CPU，不得靜默偽成功 | See "Code Examples" — verified, concrete `VerifyEachNodeIsAssignedToAnEp` verbose-log mechanism + injection point in `server/asr_sensevoice.py` |
| NPU-03 | 中文 INT8 品質閘：真實繁中決賽腳本音訊 + 母語聽測 FP32 vs INT8 A/B | See "Common Pitfalls" Pitfall N3 and milestone `PITFALLS.md` Pitfall 2 (already covers the quantization-quality mechanics); this document adds the concrete A/B harness shape |
</phase_requirements>

## Summary

The milestone-level research (`STACK.md`, `PITFALLS.md`) correctly identified that Genio 520/720 are the only Genio SoCs with ONNX Runtime NPU acceleration (`NeuronExecutionProvider`) available by default on Yocto, and correctly flagged "try ORT+NeuronEP before TFLite conversion" as the lower-risk path. This phase-level research digs one level deeper — into the actual Python call path used by this project's existing SenseVoice ASR engine (`server/asr_sensevoice.py`) — and finds a critical constraint the milestone research could not have caught without reading source: **sherpa-onnx's own Python API hard-limits the `provider` parameter to `"cpu" | "cuda" | "coreml"`** [CITED: github.com/k2-fsa/sherpa-onnx offline_recognizer.py docstring]. There is no parameter, env var, or documented escape hatch to make sherpa-onnx's `OfflineRecognizer.from_sense_voice(...)` hand `NeuronExecutionProvider` to the underlying ONNX Runtime session. This means the "just inject NeuronExecutionProvider into the existing ONNX Runtime session" framing in the milestone docs is **not literally true** for this codebase's actual call site — the existing session is created deep inside sherpa-onnx's compiled C++ core, not in Python.

The practically achievable version of NPU-01/02 within the time-box is: bypass sherpa-onnx's convenience wrapper entirely for the NPU spike and load the **same `model.int8.onnx` file** (already present on-device from Phase 8) directly with a raw `onnxruntime.InferenceSession(..., providers=[("NeuronExecutionProvider", {...}), "CPUExecutionProvider"])` call. This is a well-documented, MediaTek-supported pattern [CITED: genio-community.mediatek.com "Accelerating AI on Genio with the ONNX Runtime NeuronExecutionProvider"], and MediaTek's own IoT AI Hub docs confirm ORT 1.20.2 with `NeuronExecutionProvider` ships prebuilt in the Yocto image since PR4 (which this project's already-flashed `rity-scarthgap-v25.1` should include) [CITED: genio.mediatek.com/doc/iot-aihub]. Two hard technical constraints apply to this raw-session approach and must shape the Day-1 plan: (1) **the NPU requires fully static input shapes** — SenseVoice's ONNX graph almost certainly has a dynamic time-axis for variable-length audio, and must be fixed via `onnxruntime.tools.make_dynamic_shape_fixed` before Neuron EP will accept it [CITED: genio.mediatek.com onnx_dev.html]; (2) sherpa-onnx computes fbank features **outside** the ONNX graph via `kaldi-native-fbank` [CITED: sherpa-onnx docs — feature extraction], so a raw session bypassing sherpa-onnx needs its own fbank front-end — fortunately `kaldi-native-fbank` is a separately pip-installable package (`pip install kaldi-native-fbank`, latest 1.22.3) built by the same author, so this is reimplementable, not a research dead-end, but it is real, non-trivial engineering work, not a one-line change.

Given this, the honest assessment for TFLite-as-fallback (per the additional-context ask) is: **if ORT+NeuronEP's raw-session smoke test fails by end of Day 1, TFLite is NOT realistically attemptable in the remaining budget.** Both paths share the identical "reimplement fbank frontend + CTC/ITN postprocessing outside sherpa-onnx" burden; TFLite adds a *strictly additional* onnx2tf conversion hop with its own op-coverage failure modes on top of that shared burden. There is no scenario in a 1-2 day box where TFLite is a cheaper fallback after ORT+NeuronEP fails — if Day 1 shows no NPU op placement, the correct action per D-02 is to trigger the stop-loss directly, not pivot to TFLite.

**Primary recommendation:** Spend Day 1 morning on the smallest possible falsifiable test — load the existing on-device `model.int8.onnx` (same file `server/asr_sensevoice.py` already uses) via a **standalone Python script** (not through the FastAPI app) with `onnxruntime.InferenceSession` + `NeuronExecutionProvider`, fixing shape and feeding synthetic/zero input if needed just to see whether *any* node gets assigned to `NeuronExecutionProvider` (verified via `ort.set_default_logger_severity(0)` verbose logs, which print an explicit `VerifyEachNodeIsAssignedToAnEp` line per provider). Treat "at least 1 node on NeuronExecutionProvider" as the Day-1 checkpoint pass condition. Only if that passes, invest Day 2 in building the real fbank frontend + decode postprocessing to get an actual working, loggable NPU-accelerated transcription path wired behind a new `TALKYBUDDY_ASR_NPU` flag alongside the existing sherpa-onnx CPU path (not replacing it).

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Audio capture (16kHz mono WAV) | Edge device (ALSA, Phase 8 delivered) | — | Already built; out of scope for Phase 10 |
| ASR feature extraction (fbank) | Edge device — Python process | — | Must run wherever the ONNX session runs; on-device only, no cloud dependency (offline requirement) |
| ASR inference (SenseVoice) | Edge device — NPU (MDLA 5.3) via ORT `NeuronExecutionProvider`, CPU fallback | Edge device — CPU (ONNX Runtime, Phase 8 baseline) | This phase's core deliverable; CPU tier remains the always-available fallback per stop-loss requirement |
| Per-op placement logging / NPU status | Edge device — Python process (in-process logging) | Server `/api/status`-style HTTP surface | Follows existing `server/app.py` REST status pattern; must be observable without SSH-ing into raw logs during a live demo |
| INT8 quality gate (A/B listening test) | Human process (native-speaker listening), not a system tier | — | Not a runtime component; a validation gate that blocks phase completion, not a deployed capability |
| LLM generation (CPU, llama.cpp) | Edge device — CPU | — | Explicitly unchanged by this phase (out of scope, NDA-gated NPU-LLM excluded) |

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| **onnxruntime** (Yocto-bundled) | 1.20.2 [CITED: genio.mediatek.com/doc/iot-aihub — "We currently support ORT version 1.20.2"] | Runs `.onnx` graphs; MediaTek's Yocto build bundles `NeuronExecutionProvider` for Genio 520/720 by default since PR4 | This is the only ORT build known to include `NeuronExecutionProvider` for Genio; a `pip install onnxruntime` from PyPI would NOT include this provider (PyPI's generic wheel has no MediaTek NPU backend). Must confirm the exact package is already present on the flashed Yocto image, not re-installed from PyPI. |
| **sherpa-onnx** (existing, already pinned in this project) | version already used in Phase 8 (re-verify via `pip show sherpa_onnx` on-device) | Existing CPU-path ASR wrapper; SenseVoice-Small INT8 model already deployed | Kept **unmodified** as the CPU fallback — its `provider` param is limited to `cpu/cuda/coreml` [CITED: github.com/k2-fsa/sherpa-onnx offline_recognizer.py], so it cannot itself drive `NeuronExecutionProvider`. It remains load-bearing for the always-on CPU baseline. |
| **kaldi-native-fbank** | 1.22.3 (PyPI, latest as of 2026-07-25) [VERIFIED: PyPI `pip index versions kaldi-native-fbank`] | Standalone Python fbank feature extractor, Kaldi-compatible; same library sherpa-onnx uses internally in C++ | Needed ONLY if the raw-session NPU path is pursued past the Day-1 smoke test — sherpa-onnx computes fbank features outside the ONNX graph, so a raw `onnxruntime.InferenceSession` bypassing sherpa-onnx needs its own equivalent front-end. Author is the same person (`csukuangfj`) who maintains sherpa-onnx, so feature-computation parity is the best available option, not a from-scratch reimplementation. |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `onnxruntime.tools.make_dynamic_shape_fixed` (part of onnxruntime, not separate install) | matches onnxruntime 1.20.2 | Converts a dynamic-axis ONNX graph to fixed input shape, required by MDLA/NeuronExecutionProvider | Run once, offline, on the dev machine (not on-device) against `model.int8.onnx` before attempting the raw NPU session. Command: `python3 -m onnxruntime.tools.make_dynamic_shape_fixed --input_name <name> --input_shape <fixed_shape> model.int8.onnx model.int8.fixed.onnx` [CITED: genio.mediatek.com onnx_dev.html] |
| `onnx` (for inspecting model I/O signature) | current PyPI release | Inspect `model.int8.onnx`'s actual input names/shapes/dtypes before attempting shape-fixing or writing a raw session | First diagnostic step — run `python3 -c "import onnx; m=onnx.load('model.int8.onnx'); print(m.graph.input)"` to discover exact input tensor name(s) and whether the time axis is symbolic (e.g. named `"T"` or `"?"`) |
| `onnx2tf` | 2.6.7 (PyPI, latest as of 2026-07-25) [VERIFIED: PyPI `pip index versions onnx2tf`] | ONNX→TFLite conversion, first hop of the TFLite path | Only if Day-1 ORT+NeuronEP checkpoint fails to show `NeuronExecutionProvider` placement AND the team decides (against this research's recommendation) to still attempt TFLite — see Summary for why this is discouraged within the time-box |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Raw `onnxruntime.InferenceSession` + `NeuronExecutionProvider` bypassing sherpa-onnx | Patch/recompile sherpa-onnx's C++ core to add a `"neuron"` provider string | Requires a Yocto/aarch64 cross-compile toolchain for sherpa-onnx's C++ layer (feasible per Phase 7/8's already-proven cross-compile pattern for llama.cpp) but is materially higher effort than a raw Python session for a 1-2 day box — not recommended |
| TFLite + Neuron Stable Delegate | Neuron SDK All-in-One Bundle (`ncc-tflite` → `.dla` + `neuronrt`) | NDA-gated + no Python API (C/C++ only) — already excluded per locked decisions and REQUIREMENTS.md Out of Scope |

**Installation:**
```bash
# Dev machine (offline conversion/inspection tooling — NOT on-device):
pip install onnx onnxruntime kaldi-native-fbank

# On-device (Yocto, via SSH) — confirm bundled onnxruntime + NeuronExecutionProvider first:
ssh root@<device-ip> "python3 -c \"import onnxruntime as ort; print(ort.__version__); print(ort.get_available_providers())\""
# Expected: 1.20.2 and a list that includes 'NeuronExecutionProvider'.
# If onnxruntime is NOT importable on-device at all, this phase's NPU-01 spike
# stops immediately (missing dependency with no fallback — see Environment Availability).
```

**Version verification:** `onnx2tf` (2.6.7) and `kaldi-native-fbank` (1.22.3) versions above were confirmed live against the PyPI index on 2026-07-25 via `pip index versions <pkg>` [VERIFIED: PyPI]. The `onnxruntime` 1.20.2 version is what MediaTek's docs state ships in the Genio Yocto image — this is NOT the same artifact as PyPI's `onnxruntime` package (which lacks `NeuronExecutionProvider`); do not `pip install onnxruntime` on-device expecting NPU support — verify the bundled version first.

## Package Legitimacy Audit

| Package | Registry | Age | Downloads | Source Repo | Verdict | Disposition |
|---------|----------|-----|-----------|-------------|---------|-------------|
| `onnx` | PyPI | established (ONNX Foundation project) | unknown (seam could not fetch PyPI download stats) | https://onnx.ai/ | SUS (reason: `unknown-downloads` — a PyPI download-stats gap in the tooling, not a legitimacy signal) | Approved — well-known ONNX Foundation project, cross-checked against official onnx.ai; `checkpoint:human-verify` not required but executor should confirm `pip show onnx` after install matches expected maintainer metadata |
| `onnxruntime` (PyPI variant, dev-machine tooling only) | PyPI | established (Microsoft/ONNX Runtime project) | unknown (same tooling gap) | https://onnxruntime.ai | SUS (reason: `too-new` + `unknown-downloads` — flagged because the seam's registry lookup returned a very recent `publishedAt`, likely a routine point-release, not evidence of a new/hallucinated package) | Approved for dev-machine use only (shape-fixing tool); **on-device, use the Yocto-bundled onnxruntime, do not pip-install** |
| `onnx2tf` | PyPI | established (PINTO0309, active since 2022) | unknown (same tooling gap) | https://github.com/PINTO0309/onnx2tf | SUS (reason: `too-new` + `unknown-downloads`) | Approved if TFLite path is pursued — this is the de-facto standard ONNX→TFLite tool already documented in milestone `STACK.md`; `checkpoint:human-verify` recommended before install given the SUS flag, but reasons are tooling-data gaps, not authenticity concerns |
| `kaldi-native-fbank` | PyPI | established (csukuangfj, sherpa-onnx author) | unknown (same tooling gap) | https://github.com/csukuangfj/kaldi-native-fbank | SUS (reason: `unknown-downloads`) | Approved — same maintainer as the project's existing `sherpa_onnx` dependency; low risk despite SUS flag |

**Packages removed due to [SLOP] verdict:** none.
**Packages flagged as suspicious [SUS]:** `onnx`, `onnxruntime` (dev-tooling), `onnx2tf`, `kaldi-native-fbank` — all four flagged solely due to the legitimacy-check seam's inability to retrieve PyPI weekly-download statistics (a known tooling gap for the PyPI registry, not evidence of typosquatting/hallucination). All four are well-established, widely-documented projects with verifiable official source repos (onnx.ai, onnxruntime.ai, PINTO0309/onnx2tf, csukuangfj/kaldi-native-fbank) cross-checked against MediaTek's own official docs and the existing project's already-working sherpa-onnx dependency chain. The planner should still add a lightweight `checkpoint:human-verify` before each new install (confirm `pip show <pkg>` maintainer/homepage matches the repo URLs above) but should not treat these as `[ASSUMED]`-tier risk on par with an unverified/novel package name.

## Architecture Patterns

### System Architecture Diagram

```
                 ┌─────────────────────────────────────────────────────────┐
                 │  Genio 520 device (Yocto, existing Phase 7/8 deploy)     │
                 │                                                         │
  16kHz mono WAV │   ┌──────────────┐        ┌───────────────────────┐     │
  (ALSA capture, │   │ fbank         │        │  ONNX Runtime Session  │     │
  Phase 8 done)──┼──▶│ frontend      │───────▶│  providers=[           │     │
                 │   │ (kaldi-native-│  fbank │   ("NeuronExecution     │     │
                 │   │  fbank OR     │features│    Provider", {...}),  │     │
                 │   │  sherpa-onnx  │        │   "CPUExecutionProvider"│     │
                 │   │  CPU path)    │        │  ]                     │     │
                 │   └──────────────┘        └───────────┬───────────┘     │
                 │                                       │                 │
                 │              ┌────────────────────────┴────────────┐    │
                 │              │  Per-op placement check              │    │
                 │              │  (ort.set_default_logger_severity(0) │    │
                 │              │   -> VerifyEachNodeIsAssignedToAnEp   │    │
                 │              │   log lines, parsed to count nodes    │    │
                 │              │   per provider)                       │    │
                 │              └────────────────────────┬────────────┘    │
                 │                                       │                 │
                 │                          ┌────────────▼────────────┐    │
                 │                          │ decode (CTC/greedy +      │    │
                 │                          │ ITN + OpenCC s2twp,       │    │
                 │                          │ existing logic reused     │    │
                 │                          │ from asr_sensevoice.py)   │    │
                 │                          └────────────┬────────────┘    │
                 │                                       │                 │
                 │                          text + NPU-op-ratio metric      │
                 │                                       │                 │
                 │                          ┌────────────▼────────────┐    │
                 │                          │ /api/status (existing     │    │
                 │                          │ pattern) or new field:    │    │
                 │                          │ "npu": {"on": true,       │    │
                 │                          │  "ops_accelerated": N,    │    │
                 │                          │  "ops_total": M}          │    │
                 │                          └───────────────────────────┘    │
                 │                                                         │
                 │  Fallback path (unchanged, Phase 8 baseline):           │
                 │  server/asr_sensevoice.py -> sherpa_onnx.OfflineRecognizer│
                 │  (provider="cpu", the only reachable provider from       │
                 │   sherpa-onnx's Python API)                              │
                 └─────────────────────────────────────────────────────────┘
```

### Recommended Project Structure

```
server/
├── asr_sensevoice.py         # UNCHANGED — existing CPU-path engine, remains the fallback
├── asr_npu.py                # NEW — raw ONNX Runtime session + NeuronExecutionProvider
│                              #   spike/engine; standalone, does not touch asr_sensevoice.py
├── config.py                 # ADD: TALKYBUDDY_ASR_NPU env flag (pattern-matches ASR_BACKEND)
└── app.py                    # ADD: /api/status "npu" field, following existing status pattern

edge/
├── npu_spike/                 # NEW — Day-1/Day-2 spike scripts, run standalone via SSH,
│   ├── inspect_model.py       #   NOT wired into the FastAPI app until proven working
│   ├── fix_shape.py           #   (dev-machine, offline: make_dynamic_shape_fixed wrapper)
│   ├── raw_neuron_session.py  #   on-device: minimal InferenceSession + verbose log parse
│   └── ADR-npu-path.md        #   NPU-01 written decision record (required by success criteria #1)
```

### Pattern 1: Verbose per-op placement verification (satisfies NPU-02)

**What:** Enable ONNX Runtime's built-in verbose session-initialization logging, which prints one line per execution provider listing exactly which graph nodes were assigned to it, BEFORE running any inference.

**When to use:** Immediately after constructing any `InferenceSession` that includes `NeuronExecutionProvider` — this is the Day-1 smoke test's core verification step, and later the always-on production logging for NPU-02's "not silently fake success" requirement.

**Example:**
```python
# Source: pattern confirmed via ONNX Runtime session_state.cc verbose log format
# (community-documented; re-verify exact line format against on-device ORT 1.20.2 build)
import onnxruntime as ort

ort.set_default_logger_severity(0)  # 0 = VERBOSE; must be set BEFORE session creation

session = ort.InferenceSession(
    "model.int8.fixed.onnx",
    providers=[
        ("NeuronExecutionProvider", {
            # provider_options are MediaTek-specific; confirm exact keys against
            # the on-device ORT build's docs/samples before relying on them —
            # this exact pair was reported in a MediaTek Genio Community post:
            "NEURON_FLAG_USE_FP16": "1",
            "NEURON_FLAG_MIN_GROUP_SIZE": "1",
        }),
        "CPUExecutionProvider",
    ],
)

# Verbose stderr/stdout output will include lines of the form:
#   [V:onnxruntime:, session_state.cc:NNNN VerifyEachNodeIsAssignedToAnEp]
#     Provider: [NeuronExecutionProvider]: [Conv (Conv_12), MatMul (MatMul_45), ...]
#   [V:onnxruntime:, session_state.cc:NNNN VerifyEachNodeIsAssignedToAnEp]
#     Provider: [CPUExecutionProvider]: [Gather (Gather_3), Shape (Shape_9), ...]
#
# Parse these lines (grep for "VerifyEachNodeIsAssignedToAnEp"), count nodes
# listed per provider, and derive "NPU: X/Y ops accelerated" — this is the
# concrete, non-silent-fallback evidence NPU-02 requires.
```

### Pattern 2: Fixing dynamic input shapes before Neuron EP (a hard prerequisite, not optional)

**What:** MediaTek's own docs state plainly that Genio NPU accelerators require static input shapes; any symbolic/dynamic axis (very likely present on SenseVoice's time/sequence-length axis) must be resolved to a concrete integer before the graph is NPU-schedulable.

**When to use:** Before EVERY attempt to load `model.int8.onnx` (or any ONNX model) into a session using `NeuronExecutionProvider`.

**Example:**
```bash
# Source: genio.mediatek.com/doc/iot-aihub/ai_hub/supported_os/yocto/onnxruntime/onnx_dev.html
# First inspect the model to find the actual dynamic dim name/position:
python3 -c "import onnx; m = onnx.load('model.int8.onnx'); print(m.graph.input)"

# If the dynamic dim has a symbolic name (e.g. "T" or "sequence_length"):
python3 -m onnxruntime.tools.make_dynamic_shape_fixed \
  --dim_param T --dim_value 200 model.int8.onnx model.int8.fixed.onnx

# If it is an unnamed dynamic dim (shown as "?"), specify full fixed shape instead:
python3 -m onnxruntime.tools.make_dynamic_shape_fixed \
  --input_name speech --input_shape 1,200,80 model.int8.onnx model.int8.fixed.onnx
```
**Caveat:** fixing the shape to a fixed max length means the on-device audio pipeline must pad/truncate every utterance to that exact length before feeding it to the NPU session — this is a real behavior change from sherpa-onnx's variable-length streaming-friendly CPU path, and must be scoped explicitly into NPU-02's tasks (not assumed away).

### Anti-Patterns to Avoid

- **Assuming `available()==True` on the existing `SenseVoiceASREngine` means anything about NPU usage:** it only checks that `sherpa_onnx` imports and the model file exists (see `server/asr_sensevoice.py:36-50`) — this is entirely orthogonal to NPU acceleration and must not be conflated with NPU-02's evidence requirement.
- **Passing `provider="neuron"` (or any string other than `cpu`/`cuda`/`coreml`) to `sherpa_onnx.OfflineRecognizer.from_sense_voice(...)` expecting it to silently work or silently fail into CPU:** the sherpa-onnx Python binding's docstring and C++ core do not recognize this value; behavior is undefined/unsupported, not a documented fallback. Don't attempt this as a "quick win."
- **Treating a successful `onnx2tf` or NP8 Converter conversion log as evidence of NPU execution:** per milestone `PITFALLS.md` Pitfall 1, conversion success proves nothing about runtime op placement — only the verbose-log/profiling evidence in Pattern 1 above counts.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Kaldi-compatible fbank feature extraction (if raw-session path is pursued) | A custom FFT/mel-filterbank pipeline from scratch | `kaldi-native-fbank` (pip, same author as sherpa-onnx) | Feature-computation mismatches (window size, mel bin count, CMVN) between what SenseVoice was trained/exported with and a hand-rolled reimplementation are a classic silent-quality-regression source; use the maintained library that's numerically matched to sherpa-onnx's own C++ frontend |
| Dynamic-to-static ONNX shape conversion | Manually editing the ONNX graph protobuf | `onnxruntime.tools.make_dynamic_shape_fixed` | Official ORT tool, handles graph-level shape propagation correctly; hand-editing risks producing an invalid graph that fails silently at a different stage |
| Per-op device placement detection | A custom ONNX graph walker cross-referencing NPU op-support tables | ONNX Runtime's built-in verbose `VerifyEachNodeIsAssignedToAnEp` logging (Pattern 1) | This is the runtime's own ground-truth record of what actually got scheduled where — no static analysis substitute is as trustworthy for the "not silently fake success" requirement |

**Key insight:** every hand-rolled piece in this domain (custom fbank, custom shape editing, custom op-placement heuristics) risks reintroducing exactly the "looks done but isn't" failure mode this phase exists to prevent. Prefer official/maintained tools at each step even when writing a small spike script.

## Common Pitfalls

### Pitfall N1: Assuming sherpa-onnx's existing session is the "existing ONNX Runtime session" to inject NeuronExecutionProvider into

**What goes wrong:** The milestone-level research and this phase's own CONTEXT.md code-context notes both frame the task as "specify `NeuronExecutionProvider` when the existing ONNX Runtime session is created" — implying a one-line change inside `server/asr_sensevoice.py`'s `_ensure_model()`. In reality, that call goes through `sherpa_onnx.OfflineRecognizer.from_sense_voice(provider=...)`, and the sherpa-onnx Python binding's own docstring limits `provider` to `cpu`, `cuda`, `coreml` [CITED: github.com/k2-fsa/sherpa-onnx offline_recognizer.py]. There is no session object directly reachable from Python to attach an execution provider list to.

**Why it happens:** sherpa-onnx's Python bindings are a thin pybind11 wrapper over a C++ core that constructs and owns the `Ort::Session` object internally; the provider whitelist is hardcoded in that C++ layer at compile time, not exposed as an open-ended string.

**How to avoid:** Treat "get SenseVoice on the NPU" as "build a parallel raw-session engine" (see `asr_npu.py` in Recommended Project Structure), not "patch a parameter in the existing engine." Confirm this by reading the actual on-device `sherpa_onnx` package's Python source (`python3 -c "import sherpa_onnx, inspect; print(inspect.getsourcefile(sherpa_onnx))"` then grep for `provider`) as the very first Day-1 action, since sherpa-onnx version drift could theoretically have added more providers since this research was written.

**Warning signs:** Time spent trying different string values for `provider=` on the existing `SenseVoiceASREngine` without reading the library's own source/docstring first.

### Pitfall N2: NPU requiring static shapes silently breaking variable-length audio input

**What goes wrong:** MediaTek's docs state "NPU does not support dynamic op shapes, so it is imperative to ensure dynamic shapes have been made static" [CITED: genio.mediatek.com onnx_dev.html]. If a fixed shape (e.g. 200 frames) is picked without matching it to real demo-script utterance lengths, either short utterances waste NPU compute on padding, or long utterances get silently truncated — producing plausible-looking but wrong transcripts, which is a second, distinct route to the "淪為音箱" failure mode beyond Pitfall 1 in the milestone `PITFALLS.md`.

**Why it happens:** Fixing a dynamic shape is a one-time offline step (Pattern 2 above) disconnected from the actual runtime audio-padding/truncation logic that must be added to whatever calls the raw session — it's easy to fix the shape once and forget the caller-side implication.

**How to avoid:** Pick the fixed frame count based on the actual demo script's longest expected utterance (with margin), and explicitly implement pad-or-truncate logic in the new `asr_npu.py`, logging when truncation occurs so it's visible during rehearsal, not just discovered live.

### Pitfall N3: Treating the INT8 quality gate (NPU-03) as separate from the shape-fixing/frontend-reimplementation work

**What goes wrong:** Because building the raw-session NPU path already requires touching feature extraction and shape handling (Pitfalls N1/N2), there's a temptation to treat NPU-03's "母語聽測 FP32 vs INT8 A/B" as a downstream, independent task. In practice, any bugs introduced by the reimplemented fbank frontend or fixed-shape padding will be indistinguishable from genuine INT8 quantization quality loss unless the FP32 comparison baseline is run through the exact same new code path (same frontend, same shape-fixing), not through the original sherpa-onnx CPU path.

**How to avoid:** For the FP32 vs INT8 A/B (NPU-03), use two variants of the SAME raw-session pipeline (one loading an FP32 `.onnx` export, one loading `model.int8.onnx`) — both going through the new frontend/shape-fixing code — rather than comparing the new INT8-NPU path against the old sherpa-onnx CPU path (which would conflate "reimplementation bugs" with "quantization quality loss").

**Warning signs:** An A/B test that compares two structurally different code paths (old sherpa-onnx wrapper vs new raw session) rather than two model-precision variants of the same new path.

## Code Examples

### Diagnostic: confirm on-device ORT + NeuronExecutionProvider availability (run FIRST, before anything else)

```python
# Source: pattern derived from MediaTek Genio Community + onnxruntime official docs
import onnxruntime as ort
print("ORT version:", ort.__version__)
print("Available providers:", ort.get_available_providers())
# Expect: version 1.20.2-ish and 'NeuronExecutionProvider' present in the list.
# If 'NeuronExecutionProvider' is ABSENT here, NPU-01/02 cannot proceed at all
# via this path — this is the fastest possible falsification, do it before
# touching the model file.
```

### Diagnostic: inspect SenseVoice's actual ONNX input signature

```python
# Source: standard onnx package usage
import onnx
m = onnx.load("model.int8.onnx")  # same file path server/config.py:SENSEVOICE_DIR points to
for inp in m.graph.input:
    print(inp.name, [d.dim_param or d.dim_value for d in inp.type.tensor_type.shape.dim])
# Confirms: input tensor name(s), and whether any dimension is a symbolic dim_param
# (dynamic) vs a concrete dim_value (static) — needed for Pattern 2's shape-fixing step.
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| Assume TFLite + Neuron Delegate is the only NDA-free NPU path (milestone-level framing) | ONNX Runtime + `NeuronExecutionProvider`, prebuilt into Yocto since PR4, is equally NDA-free and lower-conversion-risk for models already on ONNX Runtime | Confirmed by MediaTek Genio Community official posts, already noted in milestone `PITFALLS.md` Pitfall 3 | Validates D-02's "try ORT+NeuronEP first" ordering — but this phase-level research further shows the *effort* is not "specify a provider string," it's "build a parallel raw-session engine with its own frontend" |

**Deprecated/outdated:** none identified specific to this phase beyond the milestone-level `STACK.md` framing already superseded by this research (see Summary).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | The on-device Yocto image (`rity-scarthgap-v25.1`) actually includes the PR4+ onnxruntime build with `NeuronExecutionProvider`, matching MediaTek's general Genio 520/720 documentation | Summary, Standard Stack, Environment Availability | If the specific flashed image predates PR4 or omits this component, the entire ORT+NeuronEP path is unavailable and NPU-01 must go straight to stop-loss on Day 1 — this is exactly why the diagnostic script above must be the very first action |
| A2 | `NEURON_FLAG_USE_FP16` / `NEURON_FLAG_MIN_GROUP_SIZE` are valid `provider_options` keys for the on-device ORT build | Code Examples Pattern 1 | These exact keys were reported in a single MediaTek Genio Community forum post, not MediaTek's canonical API reference; wrong/renamed keys would likely be silently ignored rather than erroring, so provider_options should be treated as a starting guess, verified against the actual installed ORT's `NeuronExecutionProvider` source/docs on Day 1 |
| A3 | SenseVoice's exported `model.int8.onnx` graph has a dynamic (not already-fixed) time axis, requiring `make_dynamic_shape_fixed` | Summary, Pattern 2 | If the graph is already export-time-fixed to a static shape (some sherpa-onnx exports pad to fixed windows), this step is unnecessary — the diagnostic script above resolves this in under a minute on Day 1, so the risk is wasted assumption-driven planning, not wasted execution time |
| A4 | The `VerifyEachNodeIsAssignedToAnEp` verbose log line format is stable across ORT versions and will appear in the same recognizable form in ORT 1.20.2 | Code Examples Pattern 1 | If the exact log format differs, the parsing logic for "NPU: X/Y ops accelerated" needs adjustment — but the underlying mechanism (verbose logging showing per-provider node lists) is a long-standing ORT feature, so the risk is cosmetic (regex tweak), not architectural |
| A5 | TFLite conversion (onnx2tf → NP8 Converter → Neuron Stable Delegate) shares the identical fbank-frontend/postprocessing reimplementation burden as the raw ORT path, making it strictly not-cheaper as a fallback | Summary | This is a reasoning inference from A1-A4, not independently verified against a live TFLite conversion attempt; if wrong (e.g., the NP8 Converter toolchain somehow re-embeds a compatible frontend), TFLite could be more viable than stated here — but no evidence for this was found, and the milestone's own `STACK.md` doesn't claim otherwise either |

**If this table is empty:** N/A — see entries above; all should be spot-checked on Day 1 before committing further engineering time, per D-02's stop-loss spirit.

## Open Questions

1. **Is `NeuronExecutionProvider` actually present in the specific onnxruntime build on THIS flashed Yocto image?**
   - What we know: MediaTek's general Genio 520/720 IoT AI Hub docs state it ships by default since PR4.
   - What's unclear: whether the exact `rity-scarthgap-v25.1` image (already confirmed flashed per `edge/BOARD_BRINGUP_DECISION.md`) includes this component, and whether `onnxruntime` is importable in the existing Python 3.12.11 environment at all (it isn't currently installed per Phase 8's boot-minimal dependency scope).
   - Recommendation: run the diagnostic script (Code Examples) via SSH as the literal first action of Day 1, before any other NPU-01 work.

2. **What is the real op-coverage of `NeuronExecutionProvider` for SenseVoice's specific ops (CTC output layer, any custom normalization/attention variants)?**
   - What we know: MediaTek states unsupported ops fall back to CPU automatically; no explicit supported/unsupported op list was found in the docs fetched during this research.
   - What's unclear: whether the *majority* of SenseVoice's compute-heavy ops (matmuls, convs) get NPU-placed, or whether only a handful of trivial ops (adds, reshapes) do — which would make the "NPU acceleration" technically true but performance-irrelevant.
   - Recommendation: this is exactly what the Day-1 verbose-log smoke test measures; do not assume coverage level before that evidence exists.

3. **Does the demo-script's actual longest utterance fit comfortably within a reasonable fixed frame count, or does SenseVoice's typical utterance length vary widely enough to make a single fixed shape wasteful/lossy?**
   - What we know: nothing yet — this depends on the specific decathlon/demo script content, not researched here.
   - What's unclear: the actual distribution of utterance lengths in the real demo script.
   - Recommendation: measure this from existing Phase 8/9 rehearsal recordings (if any were captured) or from the demo script text directly (estimate ~150-250ms/character for Mandarin speech) before picking a fixed shape in Pattern 2.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `onnxruntime` with `NeuronExecutionProvider` on-device | NPU-01, NPU-02 | ✗ (not confirmed — not currently installed on-device per Phase 8's boot-minimal scope; must be checked Day 1) | unknown until checked | None if MDLA-enabled build is absent from the image — this directly triggers the D-02 stop-loss |
| `onnx` (Python) | Model inspection, shape-fixing (dev machine) | ✗ on this dev sandbox (not installed); installable via `pip install onnx` | latest PyPI | pip install is low-risk, no fallback needed |
| `sherpa_onnx` (existing) | NPU-02 CPU fallback (already delivered in Phase 8) | ✗ on this dev sandbox (expected — dev machine is not the target device); confirmed present/working on Genio 520 per Phase 8 completion | already pinned in Phase 8 | Already the fallback itself — no further fallback needed |
| `kaldi-native-fbank` | Raw-session frontend (only if pursuing past Day-1 smoke test) | not yet installed anywhere | 1.22.3 (PyPI) | If unavailable/incompatible, fbank extraction would need to be sourced from sherpa-onnx's C++ internals directly (much higher effort) — treat `kaldi-native-fbank`'s absence as a strong signal to reconsider scope, not a hard blocker requiring workaround |
| SSH access to the Genio 520 device | All on-device verification steps | ✓ (per `edge/BOARD_BRINGUP_DECISION.md`, already working via Tailscale subnet route) | N/A | — |

**Missing dependencies with no fallback:**
- On-device `onnxruntime` with `NeuronExecutionProvider` — if absent, NPU-01/02 cannot proceed via the ORT path at all; per this research's Summary, TFLite is not a realistic fallback within the time-box either, so absence here should trigger D-02's stop-loss directly.

**Missing dependencies with fallback:**
- `onnx`, `kaldi-native-fbank` on the dev machine — trivial `pip install`, no blocking risk.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (existing project-wide test command, `.planning/config.json: test_command: "pytest"`) |
| Config file | existing `pytest.ini`/`pyproject.toml` if present in repo root (not modified by this phase) |
| Quick run command | `pytest tests/test_asr_npu.py -x` (new file, see Wave 0 Gaps) |
| Full suite command | `pytest` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|--------------------|-------------|
| NPU-01 | Written ADR exists comparing ORT-NeuronEP vs TFLite with concrete spike evidence | manual-only (a document, not code) — justification: a decision record cannot be asserted by an automated test | — | N/A |
| NPU-02 | Per-op placement log/metric is parseable and non-zero when NPU path is active; falls back to CPU-only reporting when NeuronExecutionProvider is unavailable, without crashing | unit (log-parsing logic) + manual (real on-device verbose-log capture, since this cannot be simulated off-device) | `pytest tests/test_asr_npu.py::test_parse_ep_placement_log -x` | ❌ Wave 0 — new test + new parsing function needed |
| NPU-02 | CPU fallback still works when NPU path errors (no silent hang/crash) | unit | `pytest tests/test_asr_npu.py::test_npu_engine_falls_back_to_cpu_on_error -x` | ❌ Wave 0 |
| NPU-03 | FP32 vs INT8 A/B comparison harness produces two transcripts from the same input for human comparison | manual-only — justification: perceptual audio quality judged by a native speaker cannot be automated | — | N/A (harness script, not a pytest test) |

### Sampling Rate
- **Per task commit:** `pytest tests/test_asr_npu.py -x` (fast, no hardware dependency for the unit-testable parsing/fallback logic)
- **Per wave merge:** `pytest` (full suite, ensures no regression to existing `asr_sensevoice.py`/`asr.py` CPU path)
- **Phase gate:** Full suite green before `/gsd-verify-work`; additionally, the Day-1 checkpoint itself (verbose-log evidence of NPU placement) is a manual, on-hardware gate that cannot be replaced by pytest — it is the actual stop-loss trigger per D-02.

### Wave 0 Gaps
- [ ] `tests/test_asr_npu.py` — new file; needs at minimum: (a) a pure-function test for parsing `VerifyEachNodeIsAssignedToAnEp`-style log lines into a `{provider: [node_names]}` dict (feed it fixture log text, no real ORT/hardware needed), and (b) a test that the new NPU engine class degrades to reporting "cpu-only" state (not crashing) when constructed in an environment without `NeuronExecutionProvider` available (mockable via monkeypatching `onnxruntime.get_available_providers`)
- [ ] No shared fixtures beyond what already exists in `server/asr_sensevoice.py`'s test patterns (if any) — check `server/streaming/tests/` for existing ASR test conventions to match style

*(Gaps limited to the log-parsing/fallback logic, which is the only part of this phase's deliverable that is realistically unit-testable off-hardware; the actual NPU acceleration claim and the INT8 quality gate are inherently hardware/human-verification items per the phase's own success criteria.)*

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | This phase touches no auth surface |
| V3 Session Management | no | No session/user-facing change |
| V4 Access Control | no | No new access-controlled endpoint (the `/api/status` field addition is read-only telemetry, same trust boundary as existing status fields) |
| V5 Input Validation | yes | Fixed-shape audio input to the raw NPU session must be explicitly padded/truncated (Pitfall N2) — an unvalidated overlong input fed into a fixed-shape session could crash the process or silently truncate; validate length before feeding the session, matching the existing project's "raise on spec mismatch, don't silently succeed" pattern already used for the ALSA WAV ingestion path (`WavSpecMismatchError`, per STATE.md decisions) |
| V6 Cryptography | no | No new cryptographic surface |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Oversized/malformed audio silently truncated by fixed-shape NPU input, producing plausible-but-wrong transcripts | Tampering (data integrity, not security-adversarial in this context but same mitigation shape) | Explicit length check + logged truncation event before feeding the raw ORT session (see Pitfall N2); never let truncation happen silently |
| New model file paths (converted `.onnx`/`.tflite` variants) introduced without validating provenance | Tampering | Keep all converted model artifacts under the same `edge/models/` tree already used by Phase 7/8, sourced only from the existing verified SenseVoice model already deployed — do not download new model weights from unverified sources as part of this phase |

## Sources

### Primary (HIGH confidence)
- `.planning/research/STACK.md`, `.planning/research/PITFALLS.md` — milestone-level research, direct project source — HIGH
- `server/asr_sensevoice.py`, `server/asr_base.py`, `server/asr.py`, `server/config.py`, `server/app.py` — direct codebase reads confirming exact injection point and existing patterns — HIGH
- `edge/BOARD_BRINGUP_DECISION.md` — direct project source confirming actual on-device OS/environment state — HIGH
- [github.com/k2-fsa/sherpa-onnx offline_recognizer.py](https://github.com/k2-fsa/sherpa-onnx/blob/master/sherpa-onnx/python/sherpa_onnx/offline_recognizer.py) — direct source read confirming `provider` parameter whitelist (`cpu, cuda, coreml`) — MEDIUM-HIGH (fetched via tool, official repo)
- PyPI registry (`pip index versions kaldi-native-fbank`, `pip index versions onnx2tf`) — VERIFIED live package existence and current versions — HIGH

### Secondary (MEDIUM confidence)
- [Accelerating AI on Genio with the ONNX Runtime NeuronExecutionProvider — MediaTek Genio Community](https://genio-community.mediatek.com/t/accelerating-ai-on-genio-with-the-onnx-runtime-neuronexecutionprovider/1347) — official MediaTek forum, code example + ORT 1.20.2 version + provider_options
- [ONNX Development — IoT AI Hub documentation](https://genio.mediatek.com/doc/iot-aihub/ai_hub/supported_os/yocto/onnxruntime/onnx_dev.html) — official MediaTek docs, static-shape requirement + `make_dynamic_shape_fixed` usage
- [Make dynamic input shape fixed — onnxruntime official docs](https://onnxruntime.ai/docs/tutorials/mobile/helpers/make-dynamic-shape-fixed.html) — official ORT docs
- [sherpa-onnx SenseVoice docs — k2-fsa](https://k2-fsa.github.io/sherpa/onnx/sense-voice/index.html) — confirms kaldi-native-fbank feature extraction approach
- [kaldi-native-fbank — csukuangfj/GitHub](https://github.com/csukuangfj/kaldi-native-fbank) — official repo, pip installable, same author as sherpa-onnx

### Tertiary (LOW confidence)
- WebSearch-synthesized claims about ONNX Runtime's `VerifyEachNodeIsAssignedToAnEp` verbose log format — pattern corroborated across multiple community sources (GitHub issues, forum threads) but not independently confirmed against the specific on-device ORT 1.20.2 build in this project; must be spot-checked on Day 1 (see Assumption A4)
- `NEURON_FLAG_USE_FP16` / `NEURON_FLAG_MIN_GROUP_SIZE` provider_options keys — sourced from a single community forum code sample, not MediaTek's canonical API reference (see Assumption A2)

## Metadata

**Confidence breakdown:**
- Standard stack: MEDIUM — package versions verified live against PyPI; on-device ORT/NeuronEP presence is unconfirmed until Day 1's diagnostic script runs
- Architecture: MEDIUM-HIGH — the critical "sherpa-onnx provider whitelist" finding is a direct source read (high-confidence), but exact provider_options and log format are community-sourced (lower confidence), flagged accordingly
- Pitfalls: HIGH — the static-shape requirement and feature-extraction-outside-graph facts are both stated plainly in MediaTek's own official docs and sherpa-onnx's own docs respectively

**Research date:** 2026-07-25
**Valid until:** 7 days (fast-moving/hardware-dependent domain; also this phase is itself only a 1-2 day time-boxed spike, so validity window should track the phase's own execution window, not a generic 30-day default)
