# -*- coding: utf-8 -*-
"""pipecat_adapters — 把本板已驗證的音訊路徑包成 pipecat 元件。

## 為什麼只有一個 adapter

原本預期要自寫四個（transport / VAD / STT / TTS）。**2026-07-31 在板子上實裝後，
只剩 transport 真的需要自寫。**

實測方法很重要，因為第一次判斷是錯的：先用
`pip download --platform manylinux2014_aarch64 --python-version 312
--only-binary=:all:` 在開發機模擬，結論是「onnxruntime 無 aarch64 wheel，
`pipecat-ai[silero]` 不可行」。**那是假陰性**——`manylinux2014_aarch64` 這個
platform tag 訂得太舊，onnxruntime 的 aarch64 wheel 用的是更新的 manylinux tag。
真的在 Genio 520 上 `pip install` 就裝起來了。

**教訓：platform tag 模擬只能證明「可行」，不能證明「不可行」。**
下不可行的結論一律要在真板子上跑過。

| 官方元件 | 需要 | 板子實測（2026-07-31） | 結論 |
|---|---|---|---|
| `pipecat-ai` 核心 | — | ✅ 裝好，`import pipecat` 0.27s | 直接用 |
| `pipecat-ai[silero]` 的 `SileroVADAnalyzer` | onnxruntime | ✅ ort 1.24.4 CPUExecutionProvider 可用 | 直接用，不自寫 |
| `pipecat-ai[local]` 的 `LocalAudioTransport` | pyaudio | ❌ `Failed to build installable wheels` | **必須自寫** → `alsa_transport` |

板子無 gcc/cmake（見 `provision_device.sh`），pyaudio 是 source-only 發佈，
所以那個 ❌ 是結構性的，不會因為換 tag 而改變。

## Silero VAD 在本板的實測數字

`SileroVADAnalyzer` 模型是 pipecat 內建的，不必另外下載：

- 實例化 0.10s、行程 RSS 143MB
- 端到端 `analyze_audio()` **1.90ms/窗**（每窗 512 samples = 32ms 音訊）→ 即時率約 6%
- 靜音 confidence 0.024、類語音 0.61，`VADState.QUIET` 判斷正確

**一個會咬人的行為**：`SileroVADAnalyzer()` 建構後 `sample_rate` 是 0，
`num_frames_required()` 回 256（8kHz 的窗）；要等 pipeline 送 `StartFrame`
觸發 `set_sample_rate(16000)` 之後才會變成正確的 512。任何在 `__init__`
裡就依賴 `self.sample_rate` 的子類別都會拿到 0。

另外 `voice_confidence()` 型別註記寫 `-> float`，實際回傳的是 shape `(1,)`
的 `ndarray`（官方 `return new_confidence` 直接回 model 輸出）。pipecat 內部
拿它跟門檻比較沒問題，但外部程式碼對它呼叫 `float()` 在 numpy 2.x 會拋
`TypeError`——要先 `np.asarray(...).ravel()[0]`。

## 不重新發明 ALSA 參數

`alsa_transport` 的 argv 一律 import `live_client` 的
`build_arecord_argv` / `build_aplay_argv`，避免出現第二份會漂移的 ALSA 參數
（尤其是 `--buffer-time 2000000` 那個 2026-07-30 的實機教訓）。
"""
