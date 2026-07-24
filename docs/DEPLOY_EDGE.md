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

Phase 7「部署迴圈跑一次」的驗證範圍**只到**：

- server 在裝置上成功啟動（`edge/runtime/run_edge.sh` process 存活）。
- health check 通過（`edge/deploy/run.sh` 從開發端對裝置 IP 直接 `curl`，回應成功）。

**不包含**：完整聲音迴路（喚醒 → ASR → LLM → TTS 全串）、前端瀏覽器 loopback
對話整合。這些自 Phase 8（CPU-only 離線迴路）起補齊，見下節。

## 4a. Phase 8：llama-server native binary 交叉編譯 + 部署 + 啟動接線（ELOOP-02）

Phase 8 起，`edge/deploy/{build,push}.sh` 與 `edge/runtime/run_edge.sh` 額外負責把
`server/llm.py::EdgeLLM` 呼叫的 `llama-server`（llama.cpp 內建 OpenAI-compatible
HTTP server）native binary 生成、送上裝置並拉起：

1. **`build.sh` 交叉編譯**：開發機需先裝好 aarch64 交叉工具鏈
   （`sudo apt-get install -y gcc-aarch64-linux-gnu g++-aarch64-linux-gnu cmake`）。
   `build.sh` 會 clone 官方 `ggml-org/llama.cpp`（`third_party/llama.cpp/`，不使用
   任何第三方 fork/預編譯 binary），以 build flag `-march=armv8.2-a+dotprod+i8mm`
   `-DGGML_NATIVE=OFF`（D-02；`armv8.7-a`/`GGML_NATIVE=ON` 會編出 Cortex-A78 不
   支援的 ISA，runtime SIGILL）交叉編譯出 `llama-server`/`llama-bench`/
   `llama-cli`，產物置於 `edge/deploy/bin/`，並記錄編譯的 commit hash到
   `edge/deploy/bin/LLAMACPP_COMMIT.txt`。
   **D-03 交叉工具鏈 fallback**：若真機 `--version`/`ldd` 顯示 glibc ABI 不相容
   （版本落差造成動態連結失敗），立即改用 `~/hackathon/` 的 Genio Yocto BSP SDK
   官方 cross-toolchain 重編一次（`TALKYBUDDY_CROSS_CC`/`TALKYBUDDY_CROSS_CXX`
   環境變數覆寫），不要在 apt 路徑上反覆嘗試超過一次修正。
2. **`push.sh` 推送 binary + GGUF 模型**：除既有 `server/`、`edge/runtime`、
   `web/` 外，另 rsync `edge/deploy/bin/`（含 `LLAMACPP_COMMIT.txt`）到裝置
   `${TARGET_ROOT}/edge/deploy/bin/` 並 `chmod +x`，以及 GGUF 模型
   `models/qwen2.5-1.5b-instruct-q4_k_m.gguf` 到 `${TARGET_ROOT}/models/`
   （與 `server/config.py::LLM_GGUF` 裝置端解析路徑一致）。若來源缺失
   （尚未跑過 `build.sh`，或 repo `models/` 無 GGUF 檔）會明確報錯，不靜默略過。
3. **`run_edge.sh` 啟動序列**：在 `exec uvicorn` 之前，先以
   `python -m edge.runtime.run_llama_server` 背景啟動 llama-server（該模組
   `os.execv` 換成真正的 native binary，`--host` 一律預設 `127.0.0.1`／
   loopback，`--ctx-size`/`--port`/`--threads` 讀 `server/config.py` 的
   `LLM_N_CTX`/`LLM_SERVER_PORT`/`LLM_THREADS`），以 `curl` 迴圈輪詢
   `http://127.0.0.1:${TALKYBUDDY_LLM_SERVER_PORT:-8080}/health` 最多 30 秒。
   逾時仍未就緒**不會**中止 uvicorn 啟動——`EdgeLLM.available()` 的短逾時設計
   本來就容忍 llama-server 稍晚就緒，pipeline 會走 scaffold-only 降級、不 crash。
4. **綁定範圍**：llama-server 一律綁 `127.0.0.1`（不對外可路由），與 uvicorn
   既有的 `0.0.0.0:8787`（07-03 已接受風險）完全無關；對外綁定驗證由後續
   phase 從裝置外部 IP curl llama-server 埠執行（預期連線被拒）。

**新增環境變數**（皆可覆寫，預設值見腳本內註解）：

| 變數 | 用途 | 預設 |
| --- | --- | --- |
| `TALKYBUDDY_CROSS_CC` / `TALKYBUDDY_CROSS_CXX` | `build.sh` 交叉編譯器（D-03 fallback 切 Yocto SDK 時覆寫） | `aarch64-linux-gnu-gcc` / `aarch64-linux-gnu-g++` |
| `TALKYBUDDY_LLAMACPP_SRC` | llama.cpp 原始碼路徑（不存在則 clone 官方 repo） | `third_party/llama.cpp` |
| `TALKYBUDDY_LLM_SERVER_HOST` | llama-server 綁定位址（不可對外可路由） | `127.0.0.1` |
| `TALKYBUDDY_LLM_SERVER_PORT` | llama-server 埠號（與 uvicorn 8787 分開） | `8080` |
| `TALKYBUDDY_LLM_THREADS` | llama-server `--threads`（佔位，待 ELOOP-03 以 llama-bench 實測覆寫） | `4` |

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
