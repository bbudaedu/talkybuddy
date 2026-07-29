# 評估：要不要把 Genio 520 刷回 Android 14？（2026-07-29）

## 結論

**決賽前：不要。** 沒有任何一項要展示的能力需要 Android，而刷過去會讓**現在能跑的每一樣東西都失效**。

**決賽後：可以考慮，但先別為了 WiFi/BT 刷** —— 更便宜的路是跟供應商要 Yocto 版驅動。

---

## 一、想換過去的動機是什麼

唯一的動機是 **WiFi / Bluetooth**：Yocto 這套映像沒有啟用無線，而供應商表示同型板跑
Android 是通的（`docs/OPERATIONS_MODEL.md` §1.3 已據此更正歸因）。

**但這個動機撐不起代價，因為 demo 不需要無線**：

- 乙太網路已實測 **UP / 1Gbps**
- 手機 USB 網路共享是已驗證的備援（`rndis_host`/`cdc_ether`/`cdc_ncm` 都在 kernel 內）
- **整套論述是「離線優先、斷網不中斷」** —— 現場沒網路反而是加分題

## 二、刷過去會失效什麼（實查，非推測）

| 現有元件 | 現況查證 | 到 Android 後 |
|---|---|---|
| `llama-server` | `file` 顯示 `interpreter /lib/ld-linux-aarch64.so.1`、`for GNU/Linux 3.7.0`，連結 `libc.so.6`／`libstdc++.so.6` | ❌ **glibc 執行檔，Android 用 bionic（`/system/bin/linker64`），不能直接跑** |
| Python 3.12.11 venv | `sherpa_onnx 1.13.4`、`onnx 1.22.0`、`numpy 2.5.1`、`OpenCC 1.4.1` —— 全是 aarch64 **glibc** wheel | ❌ 全部要重建或改走 proot glibc rootfs |
| **音訊鏈路** | `arecord`/`aplay`/`amixer`，`plughw:1,0`（USB 麥克風）、`plughw:0,0`（3.5mm 喇叭），mixer 已 `alsactl store` | ❌ **Android 音訊由 AudioFlinger/HAL 掌控**，userspace 拿不到 ALSA 裝置。這條鏈路是花最多時間才驗起來的，等於從零開始 |
| 服務管理 | `systemd`（`talkybuddy-edge.service`） | ❌ Android 無 systemd |
| 遠端操作 | `sshd` + rsync 部署流程 | ❌ 要改 adb 或自行架 |

> **關鍵一句**：失效的不是「幾個套件」，而是**整個執行環境的 ABI 與音訊架構**。
> 這不是移植，是重做。

## 三、時間與風險

- **剩不到 2 天**（決賽 2026-08-01）。刷機 + 重建 stack + 重驗音訊 + 重驗延遲，
  任何一項卡住就沒有 demo
- **刷機不可逆的風險**：若刷完發現回不去 Yocto，**手上就沒有可展示的裝置**
- 目前狀態是：語音開局 8/8 真機通過、斷網型態 B 首次量到、延遲有實測數字。
  **這些全部建立在現在這套映像上**

## 四、如果之後真的想要無線

**優先順序**（成本由低到高）：

1. **跟慧通智聯要 Yocto/BSP 版驅動**：kernel config fragment + `.ko` + firmware +
   DTS 節點。他們既然有 Android 跑通，晶片型號和 firmware 一定在手上。這可能是幾小時的事
2. 確認 SoM 是否有 `-W`／非 `-W` 變體，以及 `/proc/device-tree` 有沒有 wifi/bt 節點
3. 真的要移植 Android 驅動 → 需要對得上 `6.6.92-mtk+` 的 kernel headers 與 build 目錄
   （**裝置上沒有**，也沒有 gcc）、交叉工具鏈、DTS 節點、firmware blob。天到週的工作量
4. 刷 Android —— **只有在「無線是產品必要條件」且「願意重做整套 stack」時才成立**

## 五、如果還是要試

**不要在這台示範機上試。** 要試就準備第二片板子，或至少先確認：

- 手上有完整的 Yocto 映像與刷機工具，且**驗證過能刷回去**
- 決賽用的 stack 已完整備份（`models/`、`edge/deploy/bin/`、`.venv`、`.env`）

> 兩天前把 rootfs 弄壞，就沒有 demo 了。
