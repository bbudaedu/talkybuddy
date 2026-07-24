# edge/deploy — SSH/rsync 部署管線（build → push → run）

三個腳本依序執行，把既有 `server/`、`edge/runtime`、`web/` 送上裝置並啟動、驗證。

> 07-03 board bring-up 實測確認：Hti G520 燒 Yocto 成功後是原生 glibc Linux，沒有
> adb 介面；部署管線改用 **SSH + rsync**，取代原規劃的 adb/proot-distro 路徑。
> 詳見 `edge/BOARD_BRINGUP_DECISION.md`。

| 順序 | 腳本 | 用途 |
|------|------|------|
| 1 | `build.sh` | 確認要 push 的來源目錄存在（`server/`、`edge/runtime`），列出清單。本 phase 不需編譯 native binary（llama.cpp native build 屬 Phase 8）。 |
| 2 | `push.sh` | `rsync` 既有 `server/`、`edge/runtime`、`web/` 到裝置（SSH）的目標路徑。 |
| 3 | `run.sh` | SSH 於裝置背景啟動 `edge/runtime/run_edge.sh`，並從開發端直接對裝置 IP 做 health-check（只驗 server 起來 + 回應，D-03 範圍）。 |

## 執行順序

於 repo 根目錄依序執行（需先設定 `TALKYBUDDY_EDGE_SSH_HOST`）：

```bash
export TALKYBUDDY_EDGE_SSH_HOST=192.168.31.78   # 裝置區網 IP，DHCP 配發，依現場實際值
./edge/deploy/build.sh
./edge/deploy/push.sh
./edge/deploy/run.sh
```

## SSH 前置

- 開發機需能直連裝置區網 IP（同區網，或已核准的 Tailscale subnet route）。
- 目前板卡 sshd **無驗證機制**（`root` 免密碼、免 key 即可登入）——這是實測發現的
  已知風險，非本 phase 刻意設計，見 `edge/BOARD_BRINGUP_DECISION.md` §4。決賽前應
  補 SSH key-only 登入或限制網段。

## 目標路徑可覆寫

`push.sh` / `run.sh` 內的裝置端目標與連線資訊，皆可用環境變數覆寫（預設值見腳本
內註解）：

- `TALKYBUDDY_EDGE_SSH_HOST`：裝置區網 IP（**必填**，無預設值，因 DHCP 配發會變動）。
- `TALKYBUDDY_EDGE_SSH_USER`：SSH 登入帳號（預設 `root`）。
- `TALKYBUDDY_EDGE_DEVICE_ROOT`：裝置上部署根目錄（`server/`、`edge/runtime`、`web/`
  的推送目的地，`edge/runtime/run_edge.sh` 以相對定位找到此目錄，見
  `edge/runtime/README.md`；預設 `/root/talkybuddy`）。
- `TALKYBUDDY_EDGE_HEALTH_URL` / `TALKYBUDDY_EDGE_HEALTH_PORT` /
  `TALKYBUDDY_EDGE_HEALTH_TIMEOUT_S`：`run.sh` health-check 目標 URL、port 與逾時秒數。

## 裝置端 Python 環境 provisioning（不引入新套件）

裝置端的 Python 相依安裝由 `edge/runtime/provision_device.sh` 執行（隨 `push.sh`
一併推送到裝置）。SSH 進裝置後於部署目標目錄手動跑一次：

```bash
cd "${TARGET_ROOT}"   # push.sh 的 push 目標目錄，預設 /root/talkybuddy
./edge/runtime/provision_device.sh
```

套件版本**沿用既有 `scripts/setup_env.sh` 之 M1 已審釘版清單**的子集，本 phase
不在 repo 引入新的、未釘版的套件（per D-06 assumptions）。刻意排除 Path 1 全雙工
串流（torch/pipecat）、piper-tts（GPL 殘留）、faster-whisper（非邊緣主力 ASR）
與 `llama-cpp-python`（ELOOP-02 邊緣 LLM 改走交叉編譯 native binary，非 Python
wheel）——理由詳見腳本內註解。實測確認裝置無 gcc/cmake，但上述套件皆有現成
manylinux aarch64 cp312 wheel，免編譯即可安裝成功。

完整環境變數表、啟動指令與驗證範圍，見 `docs/DEPLOY_EDGE.md`。
