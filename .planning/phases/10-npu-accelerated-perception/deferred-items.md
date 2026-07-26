# Deferred Items — Phase 10 (NPU-Accelerated Perception)

記錄執行 plan 時發現、但與該 plan 改動檔案無關、不在 scope 內修正的既有問題。

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
