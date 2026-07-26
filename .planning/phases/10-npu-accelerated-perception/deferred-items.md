# Deferred Items — Phase 10 (NPU-Accelerated Perception)

記錄執行 plan 時發現、但與該 plan 改動檔案無關、不在 scope 內修正的既有問題。

## 10-01 執行時發現（2026-07-26）

執行 `pytest`（全套）時，以下失敗與本 plan 改動的檔案（`edge/npu_spike/`、
`tests/test_npu_spike_tools.py`）完全無關，且在改動前即已存在，故不修正：

- `tests/test_audio_io.py`、`tests/test_pipeline_wav_fastpath.py` — collection error：
  `ModuleNotFoundError: No module named 'soundfile'`（dev sandbox 缺 `soundfile`）。
- `tests/test_asr_backend.py::test_sensevoice_opencc_s2twp`、
  `tests/test_asr_backend.py::test_sensevoice_transcribe_converts_to_traditional` —
  `SenseVoiceASREngine._ensure_opencc()` 回傳 `None`（dev sandbox 缺 `opencc`）。
- `tests/test_nova_sonic.py`（7 個測試）— 缺 AWS 憑證/模擬 bidi client，Bedrock/Nova Sonic
  client 測試失敗。
- `spike/a2_pipecat/tests/test_interruptible_synth.py`（3 個測試）— collection error，
  pipecat spike 依賴缺失，與本 plan 無關。

**Verified isolated：** `pytest tests/test_npu_spike_tools.py -x` — 15/15 通過。
`pytest --ignore=tests/test_audio_io.py --ignore=tests/test_pipeline_wav_fastpath.py` —
342 passed, 2 skipped，9 個既有失敗（皆列於上）、3 個既有 collection error（皆列於上），
皆非本 plan 造成。

## 10-02 執行時發現（2026-07-26）

執行 `pytest`（全套）時，以下失敗與本 plan 改動的檔案（`server/npu_placement.py`、
`tests/test_npu_placement.py`）完全無關，且在改動前即已存在，故不修正：

- `tests/test_audio_io.py`、`tests/test_pipeline_wav_fastpath.py` — collection 階段
  `ModuleNotFoundError: No module named 'soundfile'`（環境缺套件，非本 plan 引入）。
- `tests/test_asr_backend.py::test_sensevoice_opencc_s2twp`、
  `test_sensevoice_transcribe_converts_to_traditional` — OpenCC s2twp 轉換相關斷言失敗，
  疑為環境缺 OpenCC 轉換資料或版本差異，與 NPU-02 無關。
- `tests/test_nova_sonic.py`（多個測試）— 疑因 `pytest-asyncio` 外掛未安裝
  （collection 時出現 `PytestUnknownMarkWarning: Unknown pytest.mark.asyncio`），
  導致 async 測試未真正以事件迴圈執行，與 NPU-02 無關。
- `spike/a2_pipecat/tests/test_interruptible_synth.py`（多個測試）— collection error，
  疑為 spike 目錄依賴未安裝，與 NPU-02 無關。

`pytest tests/test_npu_placement.py -x`：14/14 全綠。

## 10-03 執行時確認（2026-07-26）

執行全套 `pytest` 時，以下失敗與本 plan 改動的檔案（`edge/npu_spike/raw_neuron_session.py`、
`tests/test_raw_neuron_session.py`）完全無關，且與 10-01/10-02 記錄的既有失敗集合完全相同
（同一批測試、同一批錯誤原因），故不重複修正：

- `tests/test_audio_io.py`、`tests/test_pipeline_wav_fastpath.py` — collection error：
  `ModuleNotFoundError: No module named 'soundfile'`。
- `tests/test_asr_backend.py::test_sensevoice_opencc_s2twp`、
  `test_sensevoice_transcribe_converts_to_traditional` — 缺 OpenCC 轉換資料。
- `tests/test_nova_sonic.py`（7 個測試）— 缺 `pytest-asyncio` 外掛，async 測試未真正執行。
- `spike/a2_pipecat/tests/test_interruptible_synth.py`（3 個測試）— collection error，
  spike 目錄依賴缺失。

`pytest tests/test_raw_neuron_session.py -x`：10/10 全綠（8 個 behavior 測試 + 2 個補充測試）。
`pytest --ignore=tests/test_audio_io.py --ignore=tests/test_pipeline_wav_fastpath.py`：
366 passed, 2 skipped，9 個既有失敗 + 3 個既有 collection error（皆列於上，與 10-01/10-02
記錄的清單一致），無新增回歸。
