# edge/ — 邊緣工作地基（Hti Genio 520）

`edge/` 是說說學伴邊緣端（MediaTek Genio 520 開發板，Android 14 → proot-distro
Debian，未來目標燒 Yocto BSP）工作的頂層骨架。三個子目錄各司其職：

| 子目錄 | 用途 |
|--------|------|
| `edge/deploy` | adb 部署管線腳本（`build.sh` → `push.sh` → `run.sh`），把既有 `server/` 與 `edge/runtime` 送上裝置並啟動、驗證。 |
| `edge/runtime` | 裝置端啟動 launcher（`run_edge.sh`），進 proot Debian 後以 `TALKYBUDDY_PIPELINE_PROFILE=edge` 起既有 `server.app:app`；**不複製 server 程式碼**（D-05）。 |
| `edge/models` | 邊緣專屬量化產物（INT8 tflite / GGUF）之 placeholder；與頂層 `models/`（PC 原型）分離、不混用。本 phase 只放 README，實際模型 Phase 8/10 才產出（D-04）。 |

完整環境變數、啟動指令、adb 部署迴圈與驗證步驟，見對稱 `docs/DEPLOY_CLOUD.md`
結構撰寫的 **`docs/DEPLOY_EDGE.md`**。
