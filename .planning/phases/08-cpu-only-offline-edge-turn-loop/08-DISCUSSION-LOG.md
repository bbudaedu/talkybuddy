# Phase 8: CPU-Only Offline Edge Turn Loop - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-25
**Phase:** 8-cpu-only-offline-edge-turn-loop
**Mode:** `--auto`（single pass；使用者要求「1. 2. 都要 繼續 auto 完成」，選推薦選項不中斷）
**Areas discussed:** 交叉編譯工具鏈取得方式、裝置端音訊 I/O、on-device 延遲 go/no-go 門檻

---

## 交叉編譯工具鏈取得方式

| Option | Description | Selected |
|--------|-------------|----------|
| 開發機 apt 泛用 aarch64-linux-gnu cross-toolchain | 免額外 SDK 依賴，可立即動手；風險是 glibc ABI 版本可能與 Yocto 目標不符 | ✓ |
| ~/hackathon/ 的 Genio Yocto BSP SDK 官方 cross-toolchain | ABI 保證一致，但需使用者提供/確認路徑，時間成本較高 | fallback |

**Selected (auto):** apt 泛用工具鏈優先，跑起來 ABI 不相容才切 Yocto SDK。
**Notes:** 07-03 board bring-up 實測確認裝置（Yocto）無 gcc/cmake，此為本輪新發現的缺口，先前規劃文件未預期需要處理「工具鏈完全不在裝置上」的情境。

---

## 裝置端音訊 I/O

| Option | Description | Selected |
|--------|-------------|----------|
| ALSA 直接擷取/播放（Python `sounddevice` 或 `arecord`/`aplay` 子行程） | 沿用既有 RIFF-sniff fast path、不經瀏覽器，符合 ARCHITECTURE.md 既有 `audio_io.py`/`local_client.py` 規劃 | ✓ |
| 瀏覽器 WebSocket loopback（裝置本機瀏覽器打 `/ws/talk`） | 重用既有前端，但依賴瀏覽器程序常駐裝置、非本 phase 驗收必要 | deferred → Phase 9 / stretch |

**Selected (auto):** ALSA 路徑；`sounddevice` 或 `arecord`/`aplay` 子行程二擇一留給 executor 依裝置實測決定。
**Notes:** 呼應 `.planning/research/ARCHITECTURE.md` 既有規劃，且與「邊緣不裝 ffmpeg、只吃 16k mono WAV」的既有決策一致。

---

## On-device 延遲 go/no-go 門檻（ELOOP-03）

| Option | Description | Selected |
|--------|-------------|----------|
| 首字 <800ms／單回合總延遲 <3–4 秒 | 直接沿用 `PITFALLS.md` Cross-Cutting Risk Register 既有建議數字 | ✓ |
| 由 executor 實測後現場再訂數字 | 更貼近實測現況，但拖到執行期才有門檻，規劃期無法評估風險 | not selected |

**Selected (auto):** 採用 PITFALLS.md 既有建議數字作為門檻；真正 go/no-go 判定仍需真機 `llama-bench` + 端到端計時後才能下結論，本次只鎖定「用哪個數字當門檻」。
**Notes:** 若真機實測超過門檻，fallback 順序為：縮短 prompt/scaffold → 降 n_ctx → 該回合改用 scaffold-only 回覆。

---

## Claude's Discretion

- llama-server 啟動方式（`run_edge.sh` 內同步拉起 vs 獨立常駐行程）
- 執行緒數（`-t`）具體值——需 `llama-bench` 實測 1/2/4，不可預設 `nproc`
- `sounddevice` vs `arecord`/`aplay` 子行程之取捨

## Deferred Ideas

- NPU 加速路徑（ASR/TTS 經 Neuron Delegate）→ Phase 10
- 前端瀏覽器 loopback 對話整合 → Phase 9 或 stretch goal
- 斷網橋段話劇化彩排 → Phase 9
