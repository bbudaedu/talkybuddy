# edge/runtime — 邊緣端啟動 launcher

`run_edge.sh` 是裝置端（Hti G520，Android 14 → proot-distro Debian）啟動 TalkyBuddy
server 的唯一入口。它**引用既有 `server/`、不複製任何 server 程式碼**——避免兩份
`server/` 各自演進、行為分裂（D-05）。裝置上跑的仍是同一套 `server.app:app`，只是
換一個環境變數（`TALKYBUDDY_PIPELINE_PROFILE=edge`）與啟動路徑。

## 為何是 proot-distro（Debian），而不是 Termux 原生

裝置端 Python runtime 刻意選 **proot-distro 內的 Debian（glibc）**，不用 Termux
原生環境（D-01）：

- **glibc + apt**：llama.cpp / sherpa-onnx / SenseVoice 這些原生擴充套件的
  build/wheel 生態系以 glibc 為主流假設；Termux 的 bionic libc 會破壞
  manylinux wheel 相容性。
- **與最終 Yocto Linux 環境一致**：決賽正式目標是燒 Yocto BSP；proot Debian
  跟 Yocto 一樣是 glibc 環境，先在這條路徑上把 stack 跑順，之後遷到 Yocto
  的移植成本最低（不用兩次踩雷）。
- 不用 chroot：chroot 需要 root，12 天衝刺時間內風險太高；proot-distro 免 root
  即可跑。

`edge/runtime` 目前**只針對 Android 14（proot）這一條啟動路徑**，不預先抽象成
dual-host（Android/Yocto 通用）launcher（YAGNI，D-02）。若 board bring-up spike
（見 07-03）判定改燒 Yocto，屆時再補 native（非 proot）啟動路徑。

## 不裝 ffmpeg

邊緣端音訊輸入固定走 ALSA 直接擷取 16k mono WAV，`server/pipeline.py` 的
RIFF-sniff fast path 會直接命中、走 `soundfile` 讀取，完全不需要呼叫 ffmpeg
子行程。因此 **proot Debian provisioning 刻意不安裝 ffmpeg**——這也是邊緣端刻意
不支援非 16k mono WAV 輸入（規格不符時明確報錯，不靜默降級）的前提，見
`docs/DEPLOY_EDGE.md` §4。

## 用法

`run_edge.sh` 以自身檔案位置相對定位部署根目錄（`<TARGET_ROOT>` = 本檔案所在
目錄的上兩層），因此裝置上的部署佈局必須是：

```
<TARGET_ROOT>/
├── server/            # 既有 server/（由 edge/deploy/push.sh 推送）
└── edge/
    └── runtime/
        └── run_edge.sh
```

於 proot Debian shell 內執行：

```bash
cd <TARGET_ROOT>
./edge/runtime/run_edge.sh
```

腳本會注入 `TALKYBUDDY_PIPELINE_PROFILE=edge`，並以 `<TARGET_ROOT>/.venv/bin/python`
（若存在）或系統 `python3` 起 `uvicorn server.app:app --host 0.0.0.0 --port 8787`。

完整 adb 部署迴圈見 `edge/deploy/README.md` 與 `docs/DEPLOY_EDGE.md`。
