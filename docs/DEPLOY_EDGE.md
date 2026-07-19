# 邊緣端佈署指南（Hti Genio 520）

說說學伴的邊緣端（玩偶本地）在 **MediaTek Genio 520 開發板**（Android 14，未來
目標燒官方 Yocto BSP）上，以 **proot-distro（Debian，glibc）** 跑既有
`server.app:app`（`TALKYBUDDY_PIPELINE_PROFILE=edge`）——引用既有 `server/`、不
複製 code（D-05）。本文件對稱 `docs/DEPLOY_CLOUD.md` 結構，但描述的是「開發機
adb push 到裝置」的部署流程，而非雲端 VM 部署。

## 1. 環境變數

| 變數 | 用途 | 範例／預設 |
| --- | --- | --- |
| `TALKYBUDDY_PIPELINE_PROFILE` | 佈署 profile；玩偶（邊緣）設 `edge` | `edge` |
| `TALKYBUDDY_LLM_N_CTX` | 覆寫 LLM context 視窗（預設依 profile：edge=512）；不設則吃 profile 預設 | `512` |
| `TALKYBUDDY_EDGE_PROOT_ROOTFS` | 裝置上 proot-distro Debian rootfs 掛載路徑（`edge/deploy/push.sh`／`run.sh` 用） | `/data/data/com.termux/files/usr/var/lib/proot-distro/installed-rootfs/debian` |
| `TALKYBUDDY_EDGE_DEVICE_ROOT` | 裝置端部署根目錄（`server/`、`edge/runtime` push 目的地） | `<PROOT_ROOTFS>/root/talkybuddy` |
| `TALKYBUDDY_EDGE_HEALTH_URL` | `edge/deploy/run.sh` health-check 目標 URL | `http://127.0.0.1:8787/` |

## 2. 啟動指令（裝置端，於 proot Debian 內）

邊緣端**不直接呼叫 uvicorn**（因需先進 proot Debian、設定相對路徑與 profile），
一律透過 launcher 啟動：

```bash
cd <TARGET_ROOT>   # edge/deploy/push.sh 的 push 目標目錄
./edge/runtime/run_edge.sh
```

`run_edge.sh` 會注入 `TALKYBUDDY_PIPELINE_PROFILE=edge`，並以裝置端 venv（若存在）
或系統 `python3` 起 `uvicorn server.app:app --host 0.0.0.0 --port 8787`。細節見
`edge/runtime/README.md`。

## 3. adb 部署迴圈（開發機 → 裝置）

於開發機 repo 根目錄，依序執行 `edge/deploy/` 三腳本：

```bash
./edge/deploy/build.sh   # 確認載荷（server/、edge/runtime）齊備
./edge/deploy/push.sh    # adb push server/ + edge/runtime 到裝置 proot rootfs
./edge/deploy/run.sh     # adb shell 背景啟動 run_edge.sh + health-check
```

每次修改 `server/` 或 `edge/runtime` 後，重跑 `push.sh` → `run.sh` 即可更新裝置
上的版本（`build.sh` 只需在來源目錄結構變動時重跑）。完整腳本用法與可覆寫變數，
見 `edge/deploy/README.md`。

## 4. 驗證範圍（D-03）

本 phase 「adb 跑一次」的驗證範圍**只到**：

- server 在裝置上成功啟動（`edge/runtime/run_edge.sh` process 存活）。
- health check 通過（`edge/deploy/run.sh` 對裝置本機 `curl` 根路徑，回應成功）。

**不包含**：完整聲音迴路（喚醒 → ASR → LLM → TTS 全串）、前端瀏覽器 loopback
對話整合。這些留給 Phase 8（CPU-only 離線迴路）與後續 phase。

## 5. proot-distro provisioning（Debian）

裝置端 proot Debian 內的 Python 相依安裝，**沿用既有 `scripts/setup_env.sh` 之
M1 已審釘版套件清單**（venv + pip 安裝 fastapi/uvicorn/soundfile/sherpa-onnx 等），
本 phase **不在 repo 引入新的、未釘版的套件**。邊緣端刻意**不安裝 ffmpeg**——ALSA
直接擷取 16k mono WAV，`server/pipeline.py` 的 RIFF-sniff fast path 會直接命中、
走 `soundfile` 讀取；若擷取規格不符（非 16k mono），邊緣端會明確拋錯而非靜默降級
（因為沒有 ffmpeg 可退，見 `edge/runtime/README.md`「不裝 ffmpeg」一節）。

proot-distro 安裝步驟、Genio Tools、G520 SDK 專屬 provisioning 指令等，屬硬體
現場操作細節，請參照 `~/hackathon/` 的 Hti G520 SDK 文件（canonical ref，不在
此 repo 臆造未經驗證的燒錄/安裝指令）。
