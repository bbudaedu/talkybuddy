---
phase: 8
slug: cpu-only-offline-edge-turn-loop
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-07-25
---

# Phase 8 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest（既有 `tests/` 目錄，無 `pytest.ini`/`pyproject.toml` 專屬設定，走預設探索規則） |
| **Config file** | none — 沿用既有慣例（`tests/conftest.py` 提供 `tmp_db` autouse fixture + `anyio_backend` fixture） |
| **Quick run command** | `.venv/bin/python -m pytest tests/test_llm.py tests/test_llm_n_ctx_profile.py tests/test_audio_io.py -x -q` |
| **Full suite command** | `.venv/bin/python -m pytest tests/ -x -q` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `.venv/bin/python -m pytest tests/test_llm.py tests/test_llm_n_ctx_profile.py tests/test_audio_io.py -x -q`
- **After every plan wave:** Run `.venv/bin/python -m pytest tests/ -x -q`
- **Before `/gsd-verify-work`:** Full suite must be green + 真機 `llama-bench`/RSS 量測記錄（manual-only 項目）皆完成並附上實際輸出
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 08-01-01 | 01 | TBD | ELOOP-02 | T-08-01 | `EdgeLLM.available()` 對 `/health` 逾時/連線失敗回 False，不拋例外 | unit | `pytest tests/test_llm.py::test_available_false_on_connection_error -x` | ❌ W0（新案例） | ⬜ pending |
| 08-01-02 | 01 | TBD | ELOOP-02 | T-08-01 | `EdgeLLM.generate()` 逾時/HTTP 錯誤/safety_check 未過一律回 None | unit | `pytest tests/test_llm.py -x` | 🟡 需改寫 | ⬜ pending |
| 08-01-03 | 01 | TBD | ELOOP-02 | — | `config.LLM_N_CTX` 正確反映到 llama-server `--ctx-size` | unit | `pytest tests/test_llm_n_ctx_profile.py -x` | 🟡 需改寫 | ⬜ pending |
| 08-02-01 | 02 | TBD | ELOOP-01 | — | 音訊 I/O 模組擷取的 WAV bytes 符合 16k mono，命中既有 RIFF-sniff fast path | unit | `pytest tests/test_audio_io.py -x` | ❌ W0（新模組） | ⬜ pending |
| 08-0X-XX | TBD | TBD | ELOOP-04 | T-08-02 | llama-server 綁定 `127.0.0.1`，從裝置區網 IP 連線被拒絕 | integration（需真機） | 手動 `curl` 對裝置區網 IP + llama-server 埠號，預期連線失敗 | — manual-only | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

*Exact task IDs finalized once gsd-planner writes PLAN.md wave/task numbering — this table is the requirement→test contract the planner must satisfy, not a pre-assigned task list.*

---

## Wave 0 Requirements

- [ ] `tests/test_audio_io.py` — 覆蓋新增的裝置端音訊 I/O 模組（ELOOP-01），可用假 bytes/mock subprocess 測試，不需真硬體
- [ ] `tests/test_llm.py` 改寫 — 覆蓋 `EdgeLLM` 改為 llama-server HTTP client 後的 `available()`/`generate()` 契約（ELOOP-02）
- [ ] `tests/test_llm_n_ctx_profile.py` 改寫 — 覆蓋 `config.LLM_N_CTX` 對應到 llama-server `--ctx-size` 啟動參數組裝邏輯（ELOOP-02）

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| on-device 首字延遲 / 每回合延遲 go/no-go（門檻：首字 <800ms、單回合總延遲 <3–4 秒，見 08-CONTEXT.md D-05） | ELOOP-03 | 硬體效能量測，非程式行為，需在真機 Genio 520 上以 `llama-bench` + 端到端計時實測，不可由 CI/單元測試模擬 | 於裝置上跑 `llama-bench`（1/2/4 執行緒），並跑一次完整回合計時收音結束→開始播放回覆；記錄實際數字並對照門檻判定 go/no-go |
| 三引擎（ASR+LLM+TTS）同時載入峰值 RSS < 4GB | ELOOP-04 | 硬體記憶體量測，需同時對 uvicorn 與 llama-server 兩個獨立 process 讀 `VmHWM` 加總，非單一 PID 可測 | 於裝置上跑滿三引擎後，`grep VmHWM /proc/<uvicorn_pid>/status` + `grep VmHWM /proc/<llama_server_pid>/status`，兩者加總並記錄 |
| llama-server 綁定範圍驗證（不可外洩到區網/tailnet） | ELOOP-02（安全，見 RESEARCH.md Security Domain） | 需要從裝置外部（開發機）對裝置區網 IP 實際發起連線才能驗證綁定範圍是否正確；本機 loopback 測不出這件事 | 從開發機 `curl http://<裝置區網IP>:<llama-server port>/health`，預期連線被拒絕（非 127.0.0.1 本機） |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
