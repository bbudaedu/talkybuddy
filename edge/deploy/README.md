# edge/deploy — adb 部署管線（build → push → run）

三個腳本依序執行，把既有 `server/` 與 `edge/runtime` 送上裝置並啟動、驗證：

| 順序 | 腳本 | 用途 |
|------|------|------|
| 1 | `build.sh` | 確認要 push 的來源目錄存在（`server/`、`edge/runtime`），列出清單。本 phase 不需編譯 native binary（llama.cpp native build 屬 Phase 8）。 |
| 2 | `push.sh` | `adb push` 既有 `server/` 與 `edge/runtime` 到裝置 proot Debian rootfs 的目標路徑。 |
| 3 | `run.sh` | `adb shell` 於裝置背景啟動 `edge/runtime/run_edge.sh`，並做 health-check（只驗 server 起來 + 回應，D-03 範圍）。 |

## 執行順序

於 repo 根目錄依序執行：

```bash
./edge/deploy/build.sh
./edge/deploy/push.sh
./edge/deploy/run.sh
```

## adb 前置

- 裝置需開啟**開發者選項 → USB 偵錯**，並以 USB 或無線 adb 連上開發機。
- 首次連線時裝置會跳出 USB 偵錯授權提示，需在裝置上點「允許」。
- 以 `adb devices` 確認裝置已列出且狀態為 `device`（非 `unauthorized`）。

## 目標路徑與 proot 細節可覆寫

`push.sh` / `run.sh` 內的裝置端 proot rootfs 路徑與部署目標目錄，皆可用環境變數
覆寫（預設值見腳本內註解）：

- `TALKYBUDDY_EDGE_PROOT_ROOTFS`：proot-distro Debian rootfs 掛載路徑。
- `TALKYBUDDY_EDGE_DEVICE_ROOT`：裝置上部署根目錄（`server/`、`edge/runtime` 的
  push 目的地，`edge/runtime/run_edge.sh` 以相對定位找到此目錄，見
  `edge/runtime/README.md`）。
- `TALKYBUDDY_EDGE_HEALTH_URL` / `TALKYBUDDY_EDGE_HEALTH_TIMEOUT_S`：`run.sh`
  health-check 目標 URL 與逾時秒數。

## proot-distro provisioning（不引入新套件）

proot Debian 內的 Python 相依安裝，**沿用既有 `scripts/setup_env.sh` 之 M1 已審
釘版套件清單**，本 phase 不在 repo 引入新的、未釘版的套件（per D-06 assumptions）。
腳本內以 `TODO` 註解標示需要具體 proot-distro 安裝指令或 G520 SDK 專屬
provisioning 細節之處，這些指令請參照 `~/hackathon/` 的 Hti G520 SDK 文件
（canonical ref，不在此 repo 臆造）。

完整環境變數表、啟動指令與驗證範圍，見 `docs/DEPLOY_EDGE.md`。
