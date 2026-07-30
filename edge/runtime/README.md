# edge/runtime — 邊緣端啟動 launcher

`run_edge.sh` 是裝置端（Hti G520，官方 IoT Yocto，07-03 board bring-up 實測燒錄
成功並確認 GO）啟動 TalkyBuddy server 的唯一入口。它**引用既有 `server/`、不複製
任何 server 程式碼**——避免兩份 `server/` 各自演進、行為分裂（D-05）。裝置上跑的
仍是同一套 `server.app:app`，只是換一個環境變數（`TALKYBUDDY_PIPELINE_PROFILE=edge`）
與啟動路徑。完整決策與實測證據見 `edge/BOARD_BRINGUP_DECISION.md`。

## 為何不需要 proot / Termux

Yocto 板卡（`Rity Demo Layer 25.1.1-release scarthgap`）本身就是**原生 glibc
Linux**（Python 3.12.11 內建、`pip3`/`opkg`/`systemd` 齊全），07-03 之前規劃過的
proot-distro Debian 中介層完全不需要——原本考慮 proot 是為了給 Android 14 fallback
路徑一個 glibc 環境（llama.cpp/sherpa-onnx 這類原生擴充套件的 wheel 生態系以 glibc
為主流假設，Termux 的 bionic libc 會破壞相容性），但既然 Yocto 直接就是 glibc，這層
中介完全省略。`edge/runtime` 現在只有一條啟動路徑（Yocto 原生），不需要
dual-host（Android/Yocto 通用）抽象（YAGNI，D-02）。

## 不裝 ffmpeg

邊緣端音訊輸入固定走 ALSA 直接擷取 16k mono WAV，`server/pipeline.py` 的
RIFF-sniff fast path 會直接命中、走 `soundfile` 讀取，完全不需要呼叫 ffmpeg
子行程。因此 `edge/runtime/provision_device.sh` 刻意不安裝 ffmpeg——這也是邊緣端
刻意不支援非 16k mono WAV 輸入（規格不符時明確報錯，不靜默降級）的前提，見
`docs/DEPLOY_EDGE.md` §4。

## 原生對話迴路的 ALSA 裝置（`local_client`）

`run_edge.sh` 只起 server（uvicorn + llama-server）。**無螢幕的原生對話迴路
（按實體鍵 → USB 麥克風 → ASR → TTS → 3.5mm 喇叭）是另一個行程 `local_client`，
必須另外啟動。**

`audio_io.py` 的錄音與播放裝置都預設走 ALSA 的 `default`，而 `default` 由裝置上的
`/etc/asound.conf` 決定、**不保證是實測可用的那兩顆**。Genio 520 上兩個都必須明示，
否則的典型症狀是「玩偶回答了，但聽不到」——TTS 有產出、`aplay` 也回 0，只是送到
了沒有接喇叭的裝置上。

| 環境變數 | 預設 | Genio 520 實測值 |
|---|---|---|
| `TALKYBUDDY_EDGE_ALSA_DEVICE`（錄音） | `default` | `plughw:1,0`（USB 麥克風） |
| `TALKYBUDDY_EDGE_ALSA_PLAYBACK`（播放） | 空＝`aplay` 不帶 `-D` | `plughw:0,0`（3.5mm Lineout） |

錄音與播放是**兩張不同的音效卡**：USB 麥克風沒有播放能力，喇叭走板上的 3.5mm
Lineout。用 `plughw` 而非 `hw` 的理由（ALSA 在擷取當下重採樣，否則取樣率不符只會
印一行 warning 就繼續、送進 pipeline 才炸）見 `edge/NATIVE_KWS_PLAN.md`。

```bash
cd <TARGET_ROOT>
TALKYBUDDY_EDGE_ALSA_DEVICE=plughw:1,0 \
TALKYBUDDY_EDGE_ALSA_PLAYBACK=plughw:0,0 \
  ./.venv/bin/python -m edge.runtime.local_client
```

> ⚠️ 開機後 **USB 麥克風的實體靜音鍵會回到靜音，且軟體偵測不到也控制不了**。
> 每次演練前先按一次並實際錄音驗證，見 `edge/NETCUT_REHEARSAL_CHECKLIST.md` 步驟 0。

### 實體按鍵觸發：用 power 鍵短按，不是「自訂鍵」

錄音觸發鍵是 **`KEY_POWER`（碼 116）短按**。

**板上那顆「自訂鍵」不能用。** 2026-07-30 真機實測：`KEY_HOME`(102) 按數十次、
跨重開機、繞過 Python 直接 `dd` 讀 evdev，一律 **0 bytes**；同一時間以耳機孔
插拔事件作對照組（`event0` 收到 48 bytes）證明觀測方法本身有效。kernel 的位元圖
（`B: KEY=10004000000000 0`，高位在前 → bit 102 與 116）確實註冊了 102，
但**註冊不等於那顆實體鍵接得上**。power 鍵短按則穩定產出
`EV_KEY code=116 value=1`。

> 2026-07-29 記錄的「按自訂鍵三次都收到 KEY_HOME」是錯的（口頭回報、無留存輸出），
> 依此宣稱的 commit `75cb5b2` 在真機上站不住。

#### ⚠️ 前提：logind 必須放開 power 鍵，否則按下去是關機

logind 內建預設 `HandlePowerKey=poweroff`。`provision_device.sh` 步驟 [3/3] 會寫入
撐得過重開機的 drop-in；若換機或重刷機沒跑過 provisioning，**按玩偶就會斷電**。
`audio_io` 啟動時會檢查並印出明顯警告（`_power_key_guard_ok()`），但不阻止啟動。

手動設定與驗證：

```bash
mkdir -p /etc/systemd/logind.conf.d
printf '[Login]\nHandlePowerKey=ignore\nHandlePowerKeyLongPress=ignore\n' \
  > /etc/systemd/logind.conf.d/10-talkybuddy-powerkey.conf
systemctl restart systemd-logind
# 必須回 s "ignore"
busctl get-property org.freedesktop.login1 /org/freedesktop/login1 \
  org.freedesktop.login1.Manager HandlePowerKey
```

#### ⚠️ 這是 demo 場景的權宜方案，不是能出貨的設計

`HandlePowerKeyLongPress=ignore` 只擋得住 logind 那一層。**PMIC 的長按強制斷電是
硬體行為、不經 kernel，軟體攔不住**（約按住 8–10 秒）。操作者自己短按沒問題；
但交給小孩「按住不放」就會斷電。要出貨得換 GPIO 外接按鈕或改非按鍵觸發（VAD）。

| 環境變數 | 預設 | 用途 |
|---|---|---|
| `TALKYBUDDY_EDGE_KEY_CODE` | `116`（KEY_POWER） | 觸發鍵碼。`102` 在本板實測不可用 |
| `TALKYBUDDY_EDGE_KEY_DEVICE` | 空＝依名稱自動偵測 | 明示節點，覆寫自動偵測 |
| `TALKYBUDDY_EDGE_KEY_NAME` | `pmic` | 自動偵測比對的裝置名稱片段 |

節點編號不寫死：USB 音效裝置（麥克風）插上去也會註冊 `/dev/input/eventN`，
按鍵那顆的編號可能位移，因此預設從 `/proc/bus/input/devices` 依名稱解析。

#### 「按了完全沒反應」時跑這支

會印出名稱↔節點對照、`local_client` 實際讀哪一個與鍵碼、監聽所有節點，
並在事件來自別處時直接給出該設的環境變數：

```bash
cd <TARGET_ROOT>
./.venv/bin/python -m edge.runtime.key_probe             # 必須用 -m
./.venv/bin/python -m edge.runtime.key_probe --seconds 60  # SSH 會斷時搭配 nohup
```

> 診斷時**先排除觀測管道再懷疑硬體**：若經 SSH 前景執行，連線一中斷，輸出就卡在
> 斷掉的 TCP 裡永遠傳不回來，看起來與「按了沒反應」一模一樣。`--seconds` 搭配
> `nohup` 可讓結果留在裝置上、不依賴連線存活。

按鍵讀取**不會**再無限阻塞：`_block_until_key_press()` 同時監看 stdin（僅當它是
TTY），所以按鍵若因硬體或節點問題永遠不送事件，互動情境下按 Enter 仍能脫身。
先前只讀按鍵，一旦節點「存在且可讀」但不送事件就會死鎖，連寫好的 Enter 降級
都走不到——這正是 2026-07-30「卡在自訂按鍵」的根因。

## 用法

`run_edge.sh` 以自身檔案位置相對定位部署根目錄（`<TARGET_ROOT>` = 本檔案所在
目錄的上兩層），因此裝置上的部署佈局必須是：

```
<TARGET_ROOT>/
├── server/            # 既有 server/（由 edge/deploy/push.sh rsync 推送）
└── edge/
    └── runtime/
        └── run_edge.sh
```

於裝置 SSH shell 內執行：

```bash
cd <TARGET_ROOT>
./edge/runtime/run_edge.sh
```

腳本會注入 `TALKYBUDDY_PIPELINE_PROFILE=edge`，並以 `<TARGET_ROOT>/.venv/bin/python`
（若存在）或系統 `python3` 起 `uvicorn server.app:app --host 0.0.0.0 --port 8787`。

完整 SSH/rsync 部署迴圈見 `edge/deploy/README.md` 與 `docs/DEPLOY_EDGE.md`。
