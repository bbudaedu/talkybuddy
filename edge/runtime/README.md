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
