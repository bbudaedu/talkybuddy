# Stack Research

**Domain:** On-device edge AI (ASR/TTS on NPU, LLM generation on CPU) for MediaTek Genio 520 — hackathon final MVP
**Researched:** 2026-07-18
**Confidence:** MEDIUM (official MediaTek/Google docs cross-checked with community forum reports; several claims only verifiable once the physical board + NeuroPilot Public account are in hand — flagged below)

## Hardware Ground Truth (corrects milestone doc drift)

The Hti hub G520 SDK PDFs (`~/hackathon/申請MediaTek Genio 520/HUBG520_doc_sdk/`) and MediaTek's public Genio docs give a **more precise** picture than the older `專案技術棧與開發規格書.md` (which still says MT8365/1.2 TOPS — wrong, already corrected in `說說學伴_技術SPEC_v2.md`):

| Item | Value | Source |
|---|---|---|
| SoC | Genio 520 = **MT8371** + MT6365 (PMIC) + MT6319 + RT6375 + MT6631X (WiFi/BT) | Hti `G520 Mediatek AIoT Module Spec_V1.1.pdf` |
| CPU | 8-core: 2×Cortex-A78 + 6×Cortex-A55 | Hti spec PDF, SPEC v2 |
| NPU | 8th-gen MediaTek APU, **MDLA 5.3**, ~9 TOPS (10 TOPS system total) | genio.mediatek.com IoT AI Hub "related_resource" page (verified) |
| RAM | 4GB LPDDR4x (discrete, not expandable) | Hti spec PDF |
| Storage | 16GB eMMC (discrete) | Hti spec PDF |
| Default OS (as shipped) | **Android 14** (support to Android 15), root access supported | Hti spec PDF |
| Vendor SDK | HTI Service SDK (`htiapi_v0.0.2.jar`) — Java API for I2C/UART/SPI/CAN/GPIO from an Android app. **Not related to NPU/TFLite** — it's for peripheral I/O (e.g. LEDs, sensors), not model inference. | Hti `HTIService API Programming User Guide_v0.0.2.pdf` |

**Important nuance the milestone doc doesn't state explicitly:** the Hti "hub G520" is a **third-party carrier board** (HuiTong/hti, 慧通智聯) around the MediaTek Genio 520 SoM, not MediaTek's own reference EVK. MediaTek's official Yocto BSP (see below) targets MediaTek's **Genio 520/720-EVK** reference board. Flashing that BSP onto the Hti carrier may require device-tree/driver adaptation (audio codec routing, mic array, buttons) that the official BSP doesn't provide out of the box — this is the single biggest schedule risk in the whole edge stack and should get a Day-1 spike, not be assumed away.

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| **MediaTek IoT Yocto BSP** | **v25.1** ("rity-scarthgap-v25.1", released 2025-12-30) | Official Linux (Yocto Scarthgap) image with **Genio 520/720-EVK support** | First IoT Yocto release with native Genio 520 support (520/720 share one BSP — pin-to-pin compatible, same MACHINE config in `meta-mediatek-bsp`). This is the "official Yocto BSP" the milestone already locked; confirms it exists and is current. Built via `repo init -u https://gitlab.com/mediatek/aiot/bsp/manifest.git -b refs/tags/rity-scarthgap-v25.1`. Flash/manage with **Genio Tools v1.7+** (required — older Genio Tools don't recognize Genio 520/720). |
| **TFLite (LiteRT) + Neuron Stable Delegate** | NeuroPilot 8 (NP8) line, MDLA 5.3 target | Runs `.tflite` INT8 models on the NPU without NDA | Confirmed NDA-free path (MediaTek staff response, community thread "How to use NPU on G720 without the NDA access"): convert with the **NP8 Converter (public)**, run with **LiteRT + Neuron Stable Delegate** (`libneuron_stable_delegate.so`), which auto-partitions ops onto MDLA and falls back to CPU for unsupported ops. This is exactly the milestone's "TFLite + Neuron Delegate (NeuroPilot Public)" plan — it exists and is reachable without an NDA. |
| **llama.cpp** | latest `master` (build via CMake, GGUF-only workflow already in use) | CPU LLM inference for Qwen2.5-1.5B-Instruct GGUF Q4 | Already the project's chosen engine (`llama-cpp-python` on PC). For the board, build the **native llama.cpp CLI/server binary** directly with the Android NDK or Yocto's aarch64 cross toolchain rather than relying on `llama-cpp-python` wheels (no prebuilt aarch64-Android/Yocto wheels exist; C++ compile is required either way — see "Dependencies at risk" in codebase CONCERNS.md, already known). |
| **onnx2tf** (PINTO0309) | ≥1.26.x | ONNX → TensorFlow SavedModel → TFLite (+ INT8 quantization) converter | The de-facto standard OSS tool for ONNX→TFLite in 2025/2026 (actively maintained, supports per-channel INT8, INT8+INT16 activation, and direct `output_integer_quantized_tflite`). sherpa-onnx ships `.onnx` models — this is the first hop in the conversion pipeline before handing off to MediaTek's NP8 Converter for MDLA-aware quantization/compilation. Free, no NDA. |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| **NP8 Converter (public)** | NeuroPilot 8, "public" tier | Re-quantizes/calibrates TFLite graphs against MDLA op constraints before Neuron Delegate/`ncc-tflite` | Run *after* onnx2tf produces a plain TFLite INT8 graph. This is the MediaTek-specific pass that makes the model MDLA-schedulable; confirmed downloadable without NDA (contrast with `ncc-tflite`/Neuron SDK "All-in-One Bundle" and the GAI Toolkit, which the MediaTek IoT AI Hub docs list as **NDA-gated**, requiring a MediaTek Online (MOL) account). |
| **sherpa-onnx** (existing) | pinned version already in `setup_env.sh` | Reference ONNX graphs for SenseVoice-Small ASR + TTS, and CPU/ONNX Runtime fallback path | Keep as the CPU/ONNX-Runtime fallback engine when the Neuron Delegate falls back an op, or as the PC dev/demo path. Also the source of the `.onnx` graphs to feed into onnx2tf. |
| **tflite-runtime / ai-edge-litert (Python)** | matches Yocto v25.1's bundled Python ABI | Loads `.tflite` model + Neuron Stable Delegate from **Python** | Confirmed working pattern on Yocto (community-verified): `from tflite_runtime.interpreter import Interpreter, load_delegate; delegate = load_delegate("/usr/lib/libneuron_stable_delegate.so")`. This is what keeps the **existing Python/FastAPI backend architecture intact** on-device — no rewrite to Java/Kotlin needed if you run on Yocto. Note: TensorFlow's own docs still mark Stable Delegate "experimental" as of Yocto v24.0 (demo-only in that release); re-verify status against the v25.1 release notes on Day 1. |
| **onnxruntime + Genio ONNX Runtime NPU EP** | matches Genio 520 (NP8/MDLA5.3) listing | Alternative to TFLite path — Genio AI Hub table explicitly lists "ONNX Runtime: Supported" for Genio 520 | Worth a same-day comparison spike: if the ORT execution provider for MDLA is easier to wire into the existing sherpa-onnx (which already uses ONNX Runtime) than the TFLite conversion detour, it could cut a conversion hop. Treat as a **B-plan**, not primary, since the milestone already locked TFLite/Neuron Delegate and public docs on the ORT NPU EP are thinner than on TFLite. |
| **oboe** or plain **AAudio** (Android) / **ALSA (`arecord`/`aplay`) + `python-sounddevice`** (Yocto) | oboe 1.9.x; ALSA is kernel-native on Yocto | Native low-latency 16kHz mono audio capture, replacing ffmpeg/WebM entirely | This directly resolves the CONCERNS.md-flagged tech debt: "capture audio directly via ALSA as 16kHz mono WAV (bypassing browser MediaRecorder), eliminating ffmpeg and WebM conversion." On Yocto, ALSA is already present in the kernel/userspace (`alsa-utils`, `libasound2`) — no browser, no WebM, no ffmpeg subprocess; Python can talk to ALSA directly via `sounddevice` (PortAudio binding) or `pyalsaaudio`. On Android 14, the equivalent is **AAudio** (or the **Oboe** C++ wrapper for lower jitter) via a small JNI/native capture module, since Android apps cannot open `/dev/snd/*` directly. |
| **Genio Tools** | v1.7+ | Board flashing/imaging utility (fastboot-like) for Genio 520/720 EVK images (UFS/eMMC/serial-NOR boot variants) | Required specifically because 520/720 support only landed in Genio Tools v1.7 — older versions silently fail or don't recognize the board. Use for the Yocto flash path; separate from `adb`. |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| **adb** (Android Debug Bridge) | Deploy/test on the stock Android 14 image (Phase 1 quick validation before committing to Yocto) | `adb push` binaries/models to `/data/local/tmp/`, `adb shell` to run, `LD_LIBRARY_PATH=lib` for llama.cpp's shared libs (Android doesn't auto-discover lib dirs the way Linux does). Root is supported per Hti spec, which helps for perf profiling but Android app sandboxing still applies for anything not run as a root shell binary. |
| **Termux** (fallback dev shell on Android) | Linux-like userland on Android 14 for building/running llama.cpp / Python quickly without a full Android Studio APK | Useful only for the **Android-14-first quick test** the milestone calls for; not the production deploy path once you commit to Yocto. |
| **Android NDK** (r26+) + CMake | Cross-compile llama.cpp for `arm64-v8a` if staying on Android 14 | Standard llama.cpp Android doc (`docs/android.md`) recommends `-DANDROID_PLATFORM=android-28 -DCMAKE_C_FLAGS="-march=armv8.7a"` — **do not copy that march flag verbatim for Genio 520's Cortex-A78**. `armv8.7a` implies ISA features (e.g. BF16, MTE) that A78 does not have and can trigger `SIGILL` at runtime (this exact failure mode is a known llama.cpp GitHub issue on Android). Use **`-march=armv8.2-a+dotprod+i8mm`** instead — Cortex-A78 supports both dotprod and i8mm, which is what actually accelerates GGUF Q4 matmuls; nothing higher is safe to assume. |
| **Yocto aarch64 cross toolchain** (from the BSP SDK) | Native compile of llama.cpp, sherpa-onnx CPU fallback, and the Python interpreter environment for Yocto target | Preferred over Android NDK once Yocto is the deploy target — gives a normal glibc/musl Linux userland, so the existing venv-based Python server (`server/*.py`) ports with far fewer changes than porting to an Android app process model. |
| **repo** (Google's `repo` tool) + **git-lfs** | Fetch the IoT Yocto BSP manifest and build from source | `repo init -u https://gitlab.com/mediatek/aiot/bsp/manifest.git -b refs/tags/rity-scarthgap-v25.1`; expect a multi-hour first build on a beefy x86 host — do this on Day 1, not Day 8. |

## Installation

```bash
# --- Host-side conversion toolchain (x86 dev machine, inside existing venv) ---
pip install onnx2tf tensorflow  # ONNX -> TFLite (+INT8) first hop
# NP8 Converter: obtained from neuropilot.mediatek.com under the "NeuroPilot Public" tier
# (public developer account, NOT the NDA-gated "All-in-One Bundle" / GAI Toolkit)
# -> distributed as a MediaTek-hosted wheel/tarball; register + download manually,
#    cannot be pip-installed from PyPI.

# --- On-device (Yocto target, via cross-compiled Python or opkg/dnf recipe) ---
pip install tflite-runtime          # or ai-edge-litert, matching Yocto's Python ABI
pip install sounddevice numpy       # ALSA-backed 16kHz mono capture, replaces ffmpeg path

# --- llama.cpp: native build for the target, NOT llama-cpp-python wheels ---
# Yocto (native aarch64 toolchain from BSP SDK):
cmake -B build -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_FLAGS="-march=armv8.2-a+dotprod+i8mm" \
  -DCMAKE_CXX_FLAGS="-march=armv8.2-a+dotprod+i8mm" \
  -DGGML_OPENMP=OFF
cmake --build build --config Release -j$(nproc)

# Android 14 (NDK cross-compile, quick-test path):
cmake -B build-android \
  -DCMAKE_TOOLCHAIN_FILE=$ANDROID_NDK/build/cmake/android.toolchain.cmake \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-28 \
  -DCMAKE_C_FLAGS="-march=armv8.2-a+dotprod+i8mm" \
  -DCMAKE_CXX_FLAGS="-march=armv8.2-a+dotprod+i8mm" \
  -DGGML_OPENMP=OFF -DGGML_LLAMAFILE=OFF
cmake --build build-android --config Release -j$(nproc)
adb push build-android/bin /data/local/tmp/llama.cpp/
adb push qwen2.5-1.5b-instruct-q4_k_m.gguf /data/local/tmp/llama.cpp/
```

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|--------------------------|
| Official Yocto BSP v25.1 (Genio 520/720-EVK) | Stay on stock Android 14 image | If Yocto flashing/porting to the Hti carrier board proves infeasible within ~2 days — Android 14 is what ships and works today. Cost: Python-on-Android is awkward (no native ALSA access, no first-class `tflite_runtime` + Neuron Stable Delegate path documented for Android app processes the way there is for Yocto), so you'd likely have to move audio + inference glue into a small Java/Kotlin or NDK layer talking to the Python brain over a local socket — real added complexity. Only take this path if Yocto genuinely blocks. |
| TFLite + Neuron Stable Delegate (NP8, NDA-free) | MediaTek Neuron SDK offline path (`ncc-tflite` → `.dla` + `neuronrt`/Neuron Runtime API) | Only if you can get NDA access (unlikely in 12 days) or if the Stable Delegate proves too flaky. The offline `.dla` path is reportedly more mature/performant but (a) requires the NDA-gated "All-in-One Bundle," and (b) a community-confirmed report shows **no Python API for DLA inference** — it's C/C++-only via `neuronrt`/Neuron Runtime API. That would force a C++ inference shim even on Yocto. Avoid unless Stable Delegate is a dead end. |
| onnx2tf for ONNX→TFLite | Google's `ai-edge-torch` (PyTorch→TFLite direct) | If a model's *original* weights are PyTorch (not the ONNX sherpa-onnx already ships), `ai-edge-torch` skips the ONNX hop. Not needed here since SenseVoice/TTS are already `.onnx` via sherpa-onnx. |
| ONNX-first path for SenseVoice-Small (non-autoregressive) | Whisper-style encoder-decoder ASR on NPU | A community report (Genio 510 forum thread) shows a Whisper encoder converts to TFLite/DLA fine, but the **decoder does not** ("could not convert both encoder + decoder into a single TFLite" — decoder isn't TFLite-convertible because it's autoregressive). **SenseVoice-Small is a non-autoregressive, single-pass (CTC/paraformer-style) encoder** — structurally far more NPU-friendly than Whisper. This is a strong argument for keeping SenseVoice-Small (already validated in this project) rather than introducing Whisper/Breeze-ASR for the NPU path. |
| llama.cpp native binary (CLI/server) built for target | `llama-cpp-python` (current PC approach) | Keep `llama-cpp-python` for local x86 dev/tests only. On-device, a native `llama-server` binary (llama.cpp's built-in OpenAI-compatible HTTP server) called from the existing Python FastAPI backend over localhost is simpler than fighting cross-compiled Python C-extension wheels with no prebuilt aarch64-Android/Yocto binaries. |
| Python-side Neuron Stable Delegate invocation on Yocto | Kotlin/Java NDK app hosting the whole pipeline | Only necessary if you end up staying on Android 14 as the final target — see row 1. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|--------------|
| **MediaTek GAI-Deployment-Toolkit** (`compile_generative.sh`, NPU-accelerated LLM path — the thing that would put Qwen2.5 itself on the NPU) | Community forum evidence (Genio 520 thread, "Qwen2.5-0.5B compile_generative.sh...") shows this toolkit exists and a user got it partway working, but MediaTek's own IoT AI Hub resource page classifies **"GAI Toolkit" as NDA-gated, Android-only for NP8 platforms**, and the thread itself shows nontrivial version-coupling bugs (`rotEmbNumInputs`/`llm-sdk` version mismatches) even for people with access. This matches the milestone's own already-locked rationale for avoiding it — don't revisit this only because a public forum thread exists; the tool's *access tier* is still gated, and its maturity is unproven in the timeframe. | llama.cpp GGUF Q4 on CPU (2×Cortex-A78), as already locked in the milestone. |
| **Neuron SDK "All-in-One Bundle" / `ncc-tflite` DLA offline path**, treated as primary | NDA-gated (confirmed: genio.mediatek.com AI Hub resource page lists NP Converter *and* Neuron SDK downloads as requiring NDA + MOL account — only the "NeuroPilot Public"/NP8-public-converter + Stable Delegate combo is NDA-free) and **no Python API** for `.dla` execution (C/C++ only via `neuronrt`) | TFLite + Neuron **Stable** Delegate (online path), which is public and has a confirmed Python binding pattern. |
| **`-march=armv8.7a`** blindly copied from llama.cpp's generic Android doc | Targets ISA features beyond Cortex-A78 (e.g. BF16, MTE); mismatched `-march` has caused `SIGILL` crashes on real Android/ARM devices in llama.cpp's own issue tracker | `-march=armv8.2-a+dotprod+i8mm` — correct feature set for Cortex-A78, still gets the INT8 dot-product / matmul speedups GGUF Q4 benefits from. |
| **ffmpeg subprocess for audio conversion**, carried over unchanged from the PC prototype | Already flagged in `.planning/codebase/CONCERNS.md` as blocking Genio 520 porting; adds latency, external binary dependency, brittle timeout handling | ALSA (`arecord`/`sounddevice`) direct 16kHz mono capture on Yocto; AAudio/Oboe on Android 14. No browser MediaRecorder, no WebM, no ffmpeg. |
| **Self-building a custom Yocto image / custom kernel from scratch** | Milestone already locked "do NOT build custom OS" — high risk, low payoff in 12 days; MediaTek's v25.1 BSP already targets this exact chip family | Flash/build the **official** `rity-scarthgap-v25.1` manifest via `repo`, customizing only layers/recipes needed for the app (Python, model files, audio), not the kernel/BSP core. |
| **torch/torchaudio (full PyTorch) on-device** | Already a "dependency at risk" per CONCERNS.md (large footprint, currently a pipecat dependency on PC); at 4GB budget this is disqualifying on-device — pipecat's torch dependency should NOT be dragged onto the board | Do not run Pipecat's torch-backed components on-device at all; the edge pipeline should be a hand-rolled thin async loop (VAD → sherpa-onnx/TFLite ASR → llama.cpp → TTS) as the 28-day MVP doc itself already concluded ("自寫 async 迴圈" as the fallback to Pipecat). |
| **Breeze-ASR-25 / Llama-Breeze2-3B / BreezyVoice as the *edge* models** | All three are explicitly deferred by the locked milestone decisions (they're the "quality upgrade path... 記憶體允許再換," not MVP-critical) and Breeze-ASR-25 (~1.5B, Whisper-large-v2-based) plus Breeze2-3B Q4 (~2GB) blow past the 4GB budget once stacked with OS + runtime + KV cache (see SPEC v2 §4 budget table, already ~2.6–3.1GB with the *smaller* 1.5B Qwen) | Qwen2.5-1.5B-Instruct GGUF Q4 (LLM) + SenseVoice-Small (ASR) + Piper or sherpa-onnx TTS, all already validated at PC scale in this project — keep as-is for edge, treat Breeze family purely as a post-hackathon cloud/quality upgrade path. |

## Stack Patterns by Variant

**If the Hti G520 carrier board flashes cleanly with the official Genio 520/720-EVK Yocto image (best case):**
- Use Yocto v25.1 as final target; run the existing Python/FastAPI server natively (aarch64 cross-compiled Python + venv-equivalent).
- Invoke NPU inference directly from Python via `tflite_runtime` + `load_delegate("libneuron_stable_delegate.so")`.
- Capture audio via ALSA/`sounddevice` — closest architecture to the existing PC codebase, least rewrite.

**If Yocto porting to the Hti carrier stalls (device tree / audio codec mismatch) and Android 14 must remain the demo target:**
- Keep ASR/TTS TFLite+Neuron Delegate invocation inside a small Android app process (Kotlin or NDK C++), since the Python `tflite_runtime` + Stable Delegate combo is Yocto-proven, not Android-app-proven in the sources found.
- Run the FastAPI/Python "brain" (LLM, pipeline orchestration, SQLite, teacher-loop upload) either (a) inside Termux as a background process talking to the Android app over localhost, or (b) fully inside the Android app via Chaquopy/JNI — (a) is far less risky in the remaining days.
- Treat this as visibly a fallback, not the primary plan — flag it to the roadmap as a phase-level risk needing an explicit go/no-go checkpoint around Day 3–4 (mirrors the 28-day MVP doc's own Day 6 "test op compatibility immediately" risk item, just pulled forward for the OS choice too).

**If NPU op-fallback rate for SenseVoice-Small/TTS turns out too high (Neuron Stable Delegate silently falls back most ops to CPU):**
- The NPU acceleration becomes a "nice to have, real but modest" story rather than a load-bearing latency win — still legitimate for the 國產晶片 scoring angle (the milestone's own SPEC v2 already separates "chip credit" from "must accelerate everything"), but don't let it block the demo.
- Fall back entirely to CPU ONNX Runtime (sherpa-onnx as already validated) for ASR/TTS, and spend saved engineering time on the llama.cpp CPU path + teacher loop instead.

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|------------------|-------|
| IoT Yocto v25.1 (`rity-scarthgap-v25.1`) | Genio 520 **and** Genio 720 EVK (shared BSP/MACHINE config) | Confirmed pin-to-pin compatible per MediaTek release notes; do not mix with older Genio Tools. |
| Genio Tools | v1.7+ required | Older Genio Tools versions predate 520/720 support and will not correctly flash/recognize the board. |
| NeuroPilot Converter/Delegate | NP8 line, targeting **MDLA 5.3** | NP6 and NP8 `ncc-tflite` binaries are **not interchangeable** — always match the compiler generation to the SoC's NPU generation (Genio 520 = NP8/MDLA5.3). |
| llama.cpp `-march` flags | Cortex-A78 (armv8.2-A + dotprod + i8mm) | Do not use flags implying armv8.6a/8.7a-only features (BF16, MTE) — not present on A78, causes SIGILL at runtime, not at compile time (silent trap until you actually hit the code path). |
| `tflite_runtime`/`ai-edge-litert` (Python) | Must match the Yocto image's bundled Python interpreter ABI (cross-compiled, not pip-from-PyPI for aarch64 target) | Community-confirmed working combo is version-paired to the Yocto release; re-verify exact pairing once BSP is flashed (Stable Delegate was "demo-only/experimental" as of Yocto v24.0 — re-check v25.1 release notes for current maturity). |
| onnx2tf output | TFLite INT8 graphs it produces must still pass through the **NP8 Converter** before `ncc-tflite`/Neuron Delegate can schedule ops on MDLA | onnx2tf alone does not guarantee MDLA-schedulable ops — it's a generic TFLite converter, not MediaTek-aware; the NP8 Converter pass is what does MDLA-specific calibration/op-mapping. |

## Sources

- `~/hackathon/申請MediaTek Genio 520/HUBG520_doc_sdk/G520 Mediatek AIoT Module Spec_V1.1.pdf` — Hti carrier board spec (MT8371, 4GB LPDDR4x, Android 14, HTI Service SDK) — HIGH (primary vendor doc, read directly)
- `~/hackathon/申請MediaTek Genio 520/HUBG520_doc_sdk/SDK_API (HUB520_0005)/Hub G520 HTIService API Programming User Guide_v0.0.2.pdf` — confirms HTI SDK scope is peripheral I/O, not NPU — HIGH (primary vendor doc)
- `~/hackathon/說說學伴_技術SPEC_v2.md`, `~/hackathon/說說學伴_28天決賽MVP規劃書.md` — project's own corrected hardware specs + locked strategy rationale — HIGH (project source of truth)
- [MediaTek-NeuroPilot/tflite-neuron-delegate (GitHub)](https://github.com/MediaTek-NeuroPilot/tflite-neuron-delegate) — MIT license, last release v2.8.0 (2022-06-24), explicitly redirects newer SoCs to Google LiteRT MediaTek docs — MEDIUM (official repo, but stale; superseded by LiteRT path below)
- [MediaTek NPU and LiteRT (Google Developers Blog)](https://developers.googleblog.com/mediatek-npu-and-litert-powering-the-next-generation-of-on-device-ai/) and [LiteRT MediaTek NeuroPilot docs](https://developers.google.com/edge/litert/next/mediatek) — MEDIUM (official Google doc; explicitly lists only Dimensity phone SoCs, NOT Genio — confirms Genio uses the separate IoT AI Hub / Neuron Stable Delegate path, not this newer LiteRT-next flow)
- [Neuron Compiler and Runtime (NeuronSDK) — IoT AI Hub docs](https://genio.mediatek.com/doc/iot-aihub/ai_hub/ai-workflow/neuron-sdk.html) — MEDIUM (official MediaTek doc; ncc-tflite/DLA offline path, mdla3.0 example — note actual Genio 520 target is mdla5.3)
- [AI Development Resources — IoT AI Hub docs](https://genio.mediatek.com/doc/iot-aihub/ai_hub/related_resource.html) — MEDIUM (official; confirms Genio 520 = NP8/MDLA5.3, and the NDA-gating matrix for NP Converter/Neuron SDK/GAI Toolkit vs public Yocto guides)
- [Build Genio 720/520 EVK Images from Latest IoT Yocto v25.1-dev (MediaTek Genio Community, official announcement)](https://genio-community.mediatek.com/t/build-genio-720-520-evk-images-from-latest-iot-yocto-v25-1-dev/939) — MEDIUM (official announcement thread; confirms v25.1 BSP + Genio Tools v1.7 requirement)
- [How to use NPU on G720 without the NDA access (MediaTek Genio Community, staff-answered)](https://genio-community.mediatek.com/t/how-to-use-npu-on-g720-without-the-nda-access/1622) — MEDIUM (community thread, but answer is from MediaTek staff account `joying.kuo`; confirms NP8 Converter public + Stable Delegate is the sanctioned NDA-free path)
- [NPU Deployment Issue — Whisper Model (Genio 510)](https://genio-community.mediatek.com/t/npu-deployment-issue-whisper-model-genio-510/1430) — LOW-MEDIUM (single community report, but internally consistent and technically specific — encoder-only TFLite conversion, no Python API for DLA, C/C++-only via Neuron Runtime API)
- [[Qwen2.5-0.5B] compile_generative.sh Genio 520 (MediaTek Genio Community)](https://genio-community.mediatek.com/t/qwen2-5-0-5b-compile-generative-sh-with-num-mdla-1-skips-generative-dla-output-no-dla-file-on-genio-520/1731) — LOW (single unresolved community report; used only to corroborate why GAI Toolkit is out of scope, not as a positive recommendation)
- [Genio Neuron Stable Delegate Python usage (search-aggregated, incl. Genio community "Unable to Load Model on Genio 510 NPU with Python")](https://genio-community.mediatek.com/t/unable-to-load-model-on-genio-510-npu-with-python/1174) — LOW-MEDIUM (community-confirmed `tflite_runtime.interpreter.load_delegate` pattern; re-verify once board is in hand)
- [onnx2tf (PINTO0309, GitHub)](https://github.com/PINTO0309/onnx2tf) — MEDIUM (widely used, actively maintained OSS tool; version numbers from PyPI/GitHub cross-checked)
- [llama.cpp docs/android.md (ggml-org, GitHub)](https://github.com/ggml-org/llama.cpp/blob/master/docs/android.md) — MEDIUM (official llama.cpp doc; `-march` flag corrected against known Cortex-A78 SIGILL issue reports in llama.cpp's own tracker)

---
*Stack research for: MediaTek Genio 520 edge ASR/TTS (NPU) + LLM (CPU) integration*
*Researched: 2026-07-18*
