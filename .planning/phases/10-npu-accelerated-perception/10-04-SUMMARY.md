NPU_PATH_DECISION: STOP-LOSS-CPU-BASELINE

---
id: 10-04
parent: 10-npu-accelerated-perception
milestone: 10-npu-accelerated-perception
provides:
  - 真機 Day-1 NeuronEP 原始證據與可執行的 NPU stop-loss ADR
requires:
  - slice: 10-03
    provides: raw NeuronExecutionProvider probe
affects:
  - 10-05
  - 10-06
  - 10-07
key_files:
  - edge/npu_spike/DAY1-EVIDENCE.md
  - edge/npu_spike/DAY1-RAW-OUTPUT.txt
  - edge/npu_spike/ADR-npu-path.md
key_decisions:
  - Day-1 無任何 NPU placement，停止 NPU 路徑並維持 Phase 8 CPU-only baseline
patterns_established:
  - provider 存在與 session 建立不構成 NPU 加速；僅 per-op placement 大於零可通過
observability_surfaces:
  - DAY1_NPU_PROBE marker、完整 ORT verbose raw output、NPU_PATH_DECISION marker
duration: 真機部署、續傳與 probe 約 30 分鐘
verification_result: passed
completed_at: 2026-07-26
---

# 10-04: Day-1 NPU 路徑決策與停損

**真機 raw NeuronEP probe 在兩種 provider options 下都無法完成 SenseVoice fixed-shape session，留下完整 verbose 原文並以 `STOP-LOSS-CPU-BASELINE` 阻止後續 NPU runtime 工作。**

## What Happened

Genio 520 重新可達後，先確認先前中斷傳輸的模型不完整（193,228,800 bytes），再以 `rsync --partial --append-verify` 續傳。真機端最終校驗固定模型為 239,233,683 bytes，SHA-256 為 `d9c5d2cef743268156768786bae155a5da777cc1791dac2e08cb896765948049`；raw probe 與 placement helper 檔案亦逐一 checksum 相符。

真機 ORT 1.20.2 列出 `NeuronExecutionProvider`，Neuron runtime 列舉 `mtk-mdla`，而且兩次均可建立 provider；但帶 provider options 的第一次與空 options 的第二次均在 ORT session 初始化以 `unordered_map::at` 失敗。未生成任何 per-op placement table，最後 marker 是 `DAY1_NPU_PROBE: FAIL 0/0 ops on NeuronExecutionProvider`，exit code 為 1。

依 D-02，這是硬性停損而不是重試 TFLite 的邀請：ADR 設定唯一標記 `NPU_PATH_DECISION: STOP-LOSS-CPU-BASELINE`，使 10-05／10-06 不得執行。NPU-03 同樣記為 not-attempted，因沒有真實 NPU 路徑可做品質比較。

## Verification

- 真機 SSH：`genio-520-evk` 成功執行 raw probe。
- 固定模型真機 checksum：大小 239,233,683 bytes，SHA-256 與開發機相符。
- 真機 raw output：355,823 bytes，SHA-256 `94dc698082498a2e1d62eaa22e941bcd67586b1cafb15df799336ef1f8034d64`，保存於 `DAY1-RAW-OUTPUT.txt`。
- 真機 verdict：`DAY1_NPU_PROBE: FAIL 0/0 ops on NeuronExecutionProvider`；exit code 1。
- 本機 NPU probe 相關測試：`39 passed in 0.71s`（`test_npu_spike_tools.py`、`test_npu_placement.py`、`test_raw_neuron_session.py`）。
- `git diff --name-only HEAD -- server` 只顯示既有其他 session 的 `server/diagnose.py`；本 NPU 工作沒有修改既有 CPU baseline server 檔案。

## Requirements Advanced

- NPU-01 — 已以真機 Day-1 證據完成 ORT-NeuronEP vs TFLite 的書面決策，並明確排除 NDA-gated 路徑。

## Requirements Validated

- NPU-01 — 停損決策有完整 ORT verbose 原文、模型/探針 checksum 及唯一 machine-readable marker 支撐。

## Requirements Invalidated or Re-scoped

- NPU-02 — Not attempted；raw NeuronEP session 無法初始化，沒有任何 NPU placement。
- NPU-03 — Not attempted；依賴 NPU-02 的可運作 ASR 路徑，故不做 FP32/INT8 A/B 聽測。

## Operational Readiness

- **Health signal：** `DAY1_NPU_PROBE:` 與 `NPU_PATH_DECISION:` marker 可由 grep 直接判讀。
- **Failure signal：** 真機 ORT `Exception during initialization: unordered_map::at`，並有 exit code 1。
- **Recovery：** 維持 Phase 8 CPU-only ASR/LLM/TTS；不需要 rollback。
- **Monitoring gaps：** 尚未知不同模型或不同 MediaTek runtime 是否可產生 placement；那必須是未來獨立 time-boxed spike。

## Deviations

無程式碼範圍偏差。網路中斷使模型初次傳輸截斷；裝置恢復後改用可續傳 rsync 並完成 checksum，符合真機驗證要求。

## Known Limitations

SenseVoice fixed-shape model 在目前 Genio 520 Yocto ORT 1.20.2 NeuronEP 環境中無法完成 session 初始化，因此沒有可宣稱的 NPU ASR 能力。

## Follow-ups

- 後續工作轉向雲端 LLM 串接與斷網降級驗證。
- 若未來有新 MediaTek runtime 或較小／不同模型，必須先重跑 raw per-op placement probe；不得引用本次 provider presence 當成加速證據。

## Files Created/Modified

- `edge/npu_spike/DAY1-EVIDENCE.md` — 真機 checksum、原始關鍵輸出與 D-02 判定。
- `edge/npu_spike/DAY1-RAW-OUTPUT.txt` — 355,823-byte 完整 ORT verbose stdout/stderr 原文。
- `edge/npu_spike/ADR-npu-path.md` — NPU-01 路徑比較、NDA 排除與 stop-loss gate。
- `.planning/phases/10-npu-accelerated-perception/10-04-SUMMARY.md` — 10-04 交付與下游範圍指示。

## Forward Intelligence

### What the next slice should know
- `NeuronExecutionProvider` 出現在 provider list、成功建立 provider、甚至列出 `mtk-mdla` 都不足以通過 NPU claim；必須有成功 session 的 per-op placement 且 NPU ops > 0。

### What's fragile
- Genio 網路連線曾在大型模型傳輸期間中斷；部署大檔應使用 `rsync --partial --append-verify`，並在真機再算 checksum。

### Authoritative diagnostics
- `edge/npu_spike/DAY1-RAW-OUTPUT.txt` — 真機完整 verbose ORT 原文與 exit code，較摘要更具可稽核性。

### What assumptions changed
- 「固定 dynamic shape 後可取得 NPU op placement」在此 SenseVoice 模型與此 Yocto ORT build 上已被 Day-1 結果否定；維持 CPU baseline，不轉入 TFLite。
