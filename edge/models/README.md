# edge/models — 邊緣專屬量化產物 placeholder

此目錄用來放**邊緣（Genio 520）專屬的量化模型產物**：

- **INT8 tflite**：ASR / TTS 模型經 TFLite 轉換 + INT8 量化後，透過
  TFLite / Neuron Delegate（NeuroPilot Public，免 NDA）上 Genio 520 NPU 執行；
  算子不支援時 fallback 回 CPU。
- **GGUF**：CPU 端生成引擎（llama.cpp，Qwen2.5-1.5B Q4）於 Cortex-A78 上跑的
  量化模型檔。

## 與頂層 `models/` 的分離

既有頂層 `models/`（PC 原型用的 onnx 等資產）**保持不動、不搬動**。`edge/models`
與其**完全分離、不混用**——`edge/models` 只放「邊緣專屬、為 Genio 520 硬體特化」
的量化產物，兩者用途與產出流程不同，混放會讓兩條 profile（PC/cloud vs edge）的
模型資產彼此干擾、難以追蹤來源。

## 本 phase 範圍

本 phase（Day-0 config hardening & board bring-up spike）**只建立此空目錄與本
README placeholder**，不放置任何實際模型檔：

- INT8 tflite（NPU 加速路徑）與 GGUF（CPU 生成路徑）的實際量化產出，排在
  **Phase 8（CPU-only 離線迴路）與 Phase 10（NPU 加速）**。
- 本 phase 的重點是先把 `edge/deploy` 與 `edge/runtime` 的最小可跑部署管線立起
  （見 `docs/DEPLOY_EDGE.md`），讓後續 phase 有地方放模型、有腳本可以把模型連同
  server 一起送上裝置。
