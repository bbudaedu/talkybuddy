# Board Bring-Up Decision — Hti Genio 520 (Phase 07-03)

**決策日期：2026-07-25**
**狀態：GO — Yocto，checkpoint 1 與 checkpoint 2 皆已實測通過**

---

## 1. OS 路徑決策：Yocto GO（推翻 2026-07-20 的 Android 14 暫定路線）

2026-07-20 因板卡尚未燒錄成功、決賽時間緊迫，checkpoint 1 曾暫定「Android 14 為主線、
Yocto out-of-band」。2026-07-25 使用者實體燒錄 **官方 IoT Yocto 成功**，情勢反轉，本文件
以此為準記錄最終決策：**Yocto GO，不需要 Android 14 fallback**。

### 燒錄與開機驗證（checkpoint 1）

透過 SSH 對板卡實測確認：

```
ID=rity-demo
NAME="Rity Demo Layer"
VERSION="25.1.1-release (scarthgap)"
PRETTY_NAME="Rity Demo Layer 25.1.1-release (scarthgap)"

Linux genio-520-evk 6.6.92-mtk+g7a9a94d39e1d-g0f82689e1ac2 aarch64 GNU/Linux
```

- 開機成功、SSH 可連、systemd/NetworkManager 正常運作。
- CPU：6× Cortex-A55（`0xd05`）+ 2× Cortex-A78（`0xd41`）big.LITTLE，符合
  `.planning/research/STACK.md` 對 Genio 520 的假設。
- 記憶體：`3.7Gi` total（符合硬體規格 4GB，含系統佔用後可用 headroom）。
- 根檔案系統：14GB，可用 9.1GB。
- Python **3.12.11 原生**（glibc，非 proot/Termux），`pip3`、`opkg`、`systemd` 皆內建。
- 無 `gcc`/`g++`/`cmake`（image 不含編譯工具鏈）；`make`/`curl`/`wget`/`rsync` 皆有。
- 對外網路可達（`ping 8.8.8.8`、`curl pypi.org` 皆成功）。

**因此 Yocto fallback 成本記錄為：無新增成本。** 原本規劃給 Android 14 fallback 的
`proot-distro Debian provisioning`、Java/NDK shim 完全不需要——Yocto 本身就是原生 glibc
Linux 環境，比 Android 14 proot 路徑更單純、更貼近正式決賽目標（BSP 本來就是 Yocto）。
`edge/runtime/provision_device.sh` 當初為 Android 14 proot Debian 撰寫，內容（venv +
pip 套件子集）在 Yocto 原生環境下依然適用，只是不再需要「先進 proot」這一層；已同步
更新其文件用語（見第 4 節）。

## 2. 部署管線變更：adb+proot → SSH+rsync（checkpoint 2 前置變更）

07-02 規劃的 `edge/deploy/*.sh` 假設 **Android 14 + adb + proot-distro**。Yocto 板卡沒有
adb 介面（`android-tools-adbd.service` 雖存在於 image 但非本次採用路徑），改用
**SSH（免密碼登入，見下方安全提醒）+ rsync** 對裝置推送與啟動，機制上等價（推送
`server/` 與 `edge/runtime/` → 裝置端建 venv → 啟動 `run_edge.sh` → health check），
只是傳輸層從 USB adb 換成區網 SSH。`edge/runtime/run_edge.sh`（07-02 產物）**不需要修改**
即可直接在 Yocto 上運作——原本就沒有寫死 proot 路徑，只依自身檔案位置相對定位
`TARGET_ROOT`。`edge/deploy/{push,run}.sh` 已同步更新為 SSH/rsync 版本（見 git diff）。

### 網路連線方式

裝置（`192.168.31.78`）與開發端（雲端 sandbox）不同網段；透過使用者 NB 開啟
**Tailscale subnet router**（`--advertise-routes=192.168.31.0/24`）並在 admin console
核准後，開發端加入同一 tailnet 即可直連裝置區網 IP，不需在板卡上額外安裝 Tailscale。

## 3. Checkpoint 2 — 部署迴圈 + health check 實測結果（真實輸出，非模擬）

**部署步驟（實際執行，2026-07-25）：**

```
$ ssh root@192.168.31.78 "mkdir -p /root/talkybuddy"
$ rsync -az server/ root@192.168.31.78:/root/talkybuddy/server/
$ rsync -az edge/runtime/ root@192.168.31.78:/root/talkybuddy/edge/runtime/
$ rsync -az web/ root@192.168.31.78:/root/talkybuddy/web/
$ ssh root@192.168.31.78 "cd /root/talkybuddy && python3 -m venv .venv"
$ ssh root@192.168.31.78 "cd /root/talkybuddy && .venv/bin/pip install fastapi 'uvicorn[standard]' websockets pydantic"
```

`pip install` 全部命中 **manylinux2014/2_17 aarch64 cp312 預編譯 wheel**
（`pydantic_core`、`uvloop`、`httptools`、`watchfiles` 皆有現成 wheel），
**沒有觸發任何原始碼編譯**——與 image 缺 gcc/cmake 的事實一致，不構成阻塞。

```
$ ssh root@192.168.31.78 "cd /root/talkybuddy && nohup ./edge/runtime/run_edge.sh > /tmp/talkybuddy_edge.log 2>&1 & disown"
$ ssh root@192.168.31.78 "cat /tmp/talkybuddy_edge.log"
INFO:     Started server process [963]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8787 (Press CTRL+C to quit)
```

**Health check（從開發端直接對裝置區網 IP 發出，非裝置本機 curl）：**

```
$ curl -sS -o /dev/null -w 'HTTP %{http_code}  connect=%{time_connect}s  total=%{time_total}s\n' http://192.168.31.78:8787/
HTTP 200  connect=0.057042s  total=0.277042s

$ curl -sS http://192.168.31.78:8787/api/status
{"asr":false,"llm":false,"tts":false,"cloud_tts":false,"cloud_llm":false,"network_mode":"edge","pending":3,"live_s2s":false}
```

**結果：PASS。** Server 在裝置上成功啟動並回應 HTTP 200，`network_mode=edge` 確認
`TALKYBUDDY_PIPELINE_PROFILE=edge` 生效。`asr/llm/tts` 皆為 `false` 是預期行為——本次
只安裝了 boot 最小相依（`server/app.py` 模組層級 import 全為標準庫 + fastapi/uvicorn/
pydantic，ASR/TTS/LLM 皆 lazy import，未載入模型），符合 D-03 驗證範圍（僅驗 server
起來 + health check，不含完整聲音迴路）。完整引擎（sherpa-onnx/llama.cpp）安裝與模型
下載留給 Phase 8。

## 4. 安全發現（需記錄，非本 phase 阻塞項，但決賽前應處理）

實測過程中發現：板卡 sshd **完全沒有驗證機制**——`ssh root@<device-ip>` 免密碼、免
key，`ssh -v` 明確顯示 `Authenticated to ... using "none"`，`/root/.ssh/authorized_keys`
不存在，`/etc/ssh/sshd_config` 亦不存在（推測為精簡版 sshd/dropbear）。目前板卡透過
Tailscale subnet router 被使用者整個 tailnet 上已核准的裝置間接連通。

**風險**：同區網（或同 tailnet）任何裝置皆可免驗證取得板卡 root shell。決賽現場若接上
非受控網路（會場公用 Wi-Fi 等），此風險會擴大。

**本 phase 不處理**（不在 D-03 範圍內），但列入後續 known-gaps：決賽前應至少設定
SSH key-only 登入並關閉 `none`/密碼驗證，或於現場保持板卡在隔離網段（不接公用 Wi-Fi）。

## 5. 對後續 Phase 8 / 10 的影響

- **地基已確認**：Yocto（Rity Demo Layer 25.1.1, scarthgap, kernel 6.6.92-mtk aarch64）+
  原生 glibc Python 3.12.11，**不是** proot/Termux/Android 環境。Phase 8 的 llama.cpp
  native binary 交叉編譯（`-march=armv8.2-a+dotprod+i8mm`，見 `research/STACK.md`）目標
  環境不變（仍是 aarch64 glibc Linux），研究結論持續有效。
- **無 C 編譯器**：裝置端沒有 gcc/cmake，若 Phase 8/10 需要在裝置上編譯任何原生元件
  （非 pip wheel），必須改成「開發機交叉編譯 → rsync push 執行檔」，這與 Phase 8 研究
  已定案的 llama-server native binary 方案（本來就打算交叉編譯後 push，非裝置端建置）
  一致，不構成新問題。
- **部署管線**：`edge/deploy/{build,push,run}.sh` 已從 adb/proot 改為 SSH/rsync 並保留
  可執行、`bash -n` 通過；`docs/DEPLOY_EDGE.md` 同步更新反映真實路徑。

## 6. Phase 7 收尾

- checkpoint 1（OS go/no-go）：**PASS**（Yocto GO，取代 07-20 暫定的 Android 14）。
- checkpoint 2（adb/SSH 部署迴圈 + health check 實測）：**PASS**（見第 3 節真實輸出）。
- ROADMAP §Phase 7 success criteria #4、#5 達成。
- 「不靜默偽成功」prohibition 落實：以上皆為本次實際執行的真實指令與輸出，無模擬/假設。
