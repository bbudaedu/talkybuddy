# 邊緣端佈署指南（Hti Genio 520）

說說學伴的邊緣端（玩偶本地）在 **MediaTek Genio 520 開發板**（Yocto，`Rity Demo Layer
25.1.1-release (scarthgap)`，07-03 board bring-up 實測燒錄成功並已確認 GO）上，以
**原生 glibc Linux**（無 proot/Termux 層）跑既有 `server.app:app`
（`TALKYBUDDY_PIPELINE_PROFILE=edge`）——引用既有 `server/`、不複製 code（D-05）。
本文件對稱 `docs/DEPLOY_CLOUD.md` 結構，但描述的是「開發機經 SSH/rsync 推到裝置」
的部署流程，而非雲端 VM 部署。完整 go/no-go 決策紀錄與實測結果見
`edge/BOARD_BRINGUP_DECISION.md`。

## 1. 環境變數

| 變數 | 用途 | 範例／預設 |
| --- | --- | --- |
| `TALKYBUDDY_PIPELINE_PROFILE` | 佈署 profile；玩偶（邊緣）設 `edge` | `edge` |
| `TALKYBUDDY_LLM_N_CTX` | 覆寫 LLM context 視窗（預設依 profile：edge=512）；不設則吃 profile 預設 | `512` |
| `TALKYBUDDY_EDGE_SSH_HOST` | 裝置區網 IP（**必填**，DHCP 配發、無固定預設值） | `192.168.31.78` |
| `TALKYBUDDY_EDGE_SSH_USER` | SSH 登入帳號 | `root`（預設） |
| `TALKYBUDDY_EDGE_DEVICE_ROOT` | 裝置端部署根目錄（`server/`、`edge/runtime`、`web/` push 目的地） | `/root/talkybuddy`（預設） |
| `TALKYBUDDY_EDGE_HEALTH_URL` / `TALKYBUDDY_EDGE_HEALTH_PORT` | `edge/deploy/run.sh` health-check 目標 URL／port | `http://<SSH_HOST>:8787/` |

## 2. 啟動指令（裝置端）

邊緣端**不直接呼叫 uvicorn**（因需設定相對路徑與 profile），一律透過 launcher 啟動：

```bash
cd <TARGET_ROOT>   # edge/deploy/push.sh 的 push 目標目錄，預設 /root/talkybuddy
./edge/runtime/run_edge.sh
```

`run_edge.sh` 會注入 `TALKYBUDDY_PIPELINE_PROFILE=edge`，並以裝置端 venv（若存在）
或系統 `python3` 起 `uvicorn server.app:app --host 0.0.0.0 --port 8787`。細節見
`edge/runtime/README.md`。此腳本無 proot/Android 特定邏輯，Yocto 原生環境下無需
修改即可直接使用。

## 3. SSH/rsync 部署迴圈（開發機 → 裝置）

於開發機 repo 根目錄，先設定裝置 IP，再依序執行 `edge/deploy/` 三腳本：

```bash
export TALKYBUDDY_EDGE_SSH_HOST=192.168.31.78   # 裝置區網 IP，依現場實際值
./edge/deploy/build.sh   # 確認載荷（server/、edge/runtime）齊備
./edge/deploy/push.sh    # rsync push server/ + edge/runtime + web/ 到裝置
./edge/deploy/run.sh     # SSH 背景啟動 run_edge.sh + 從開發端直接 health-check
```

每次修改 `server/` 或 `edge/runtime` 後，重跑 `push.sh` → `run.sh` 即可更新裝置
上的版本（`build.sh` 只需在來源目錄結構變動時重跑）。完整腳本用法與可覆寫變數，
見 `edge/deploy/README.md`。

**網路前置**：開發機需能直連裝置區網 IP。若不同網段（如開發機在雲端 sandbox、
裝置在使用者家用區網），07-03 實測驗證的可行做法是：裝置所在區網內一台機器
（如筆電）開啟 Tailscale subnet router（`--advertise-routes=<裝置網段>`），並在
Tailscale admin console 核准該 route；開發機同樣加入該 tailnet 後即可直連裝置 IP，
不需在板卡本身安裝任何額外軟體。

**已知安全風險**：實測發現裝置目前 sshd 無驗證機制（`root` 免密碼/免 key）。
決賽現場若接上非受控網路，應優先補 SSH key-only 登入或限制網段，見
`edge/BOARD_BRINGUP_DECISION.md` §4。

## 4. 驗證範圍（D-03）

本 phase 「部署迴圈跑一次」的驗證範圍**只到**：

- server 在裝置上成功啟動（`edge/runtime/run_edge.sh` process 存活）。
- health check 通過（`edge/deploy/run.sh` 從開發端對裝置 IP 直接 `curl`，回應成功）。

**不包含**：完整聲音迴路（喚醒 → ASR → LLM → TTS 全串）、前端瀏覽器 loopback
對話整合。這些留給 Phase 8（CPU-only 離線迴路）與後續 phase。

## 5. 裝置端 Python 環境 provisioning

裝置端 Python 相依安裝由 `edge/runtime/provision_device.sh` 執行（隨 `push.sh`
一併推送到裝置），SSH 進裝置後於部署目標目錄手動跑一次：

```bash
cd <TARGET_ROOT>
./edge/runtime/provision_device.sh
```

套件版本**沿用既有 `scripts/setup_env.sh` 之 M1 已審釘版清單**的子集（venv +
pip 安裝 fastapi/uvicorn/websockets/pydantic/numpy/soundfile/huggingface_hub/
sherpa-onnx/onnx/opencc），本 phase **不在 repo 引入新的、未釘版的套件**。刻意
排除 Path 1 全雙工串流相依（torch/pipecat-ai）、piper-tts（GPL 殘留）、
faster-whisper（非邊緣主力 ASR）與 `llama-cpp-python`（ELOOP-02：邊緣 LLM 改走
交叉編譯 native binary over localhost，非 Python wheel，屬 Phase 8 範疇）。
邊緣端刻意**不安裝 ffmpeg**——ALSA 直接擷取 16k mono WAV，`server/pipeline.py`
的 RIFF-sniff fast path 會直接命中、走 `soundfile` 讀取；若擷取規格不符（非
16k mono），邊緣端會明確拋錯而非靜默降級（因為沒有 ffmpeg 可退，見
`edge/runtime/README.md`「不裝 ffmpeg」一節）。

**實測確認**：裝置（Yocto）無 `gcc`/`cmake`，但上述套件在 PyPI 皆有現成
manylinux aarch64 cp312 預編譯 wheel，`pip install` 全數免編譯成功——不構成
provisioning 阻塞。若後續 Phase 8/10 需要裝置端編譯原生元件，須改成「開發機
交叉編譯 → rsync push 執行檔」，這與既有 llama-server native binary 規劃一致。
