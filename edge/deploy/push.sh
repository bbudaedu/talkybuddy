#!/usr/bin/env bash
# 說說學伴 — 邊緣部署 [2/3] push：adb push server/ + edge/runtime 到裝置 proot rootfs
#
# 目標路徑（proot Debian rootfs 內的部署根目錄）以下方變數宣告，可用環境變數
# 覆寫。假設於 repo 根目錄執行，且裝置已透過 `adb devices` 確認連線並授權。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

# proot-distro Debian 的 rootfs 路徑慣例（Termux proot-distro 預設 install 位置）；
# 實際路徑需依裝置上 proot-distro 版本/安裝參數確認，可用
# TALKYBUDDY_EDGE_DEVICE_ROOT 覆寫。
DEVICE_PROOT_ROOTFS="${TALKYBUDDY_EDGE_PROOT_ROOTFS:-/data/data/com.termux/files/usr/var/lib/proot-distro/installed-rootfs/debian}"
TARGET_ROOT="${TALKYBUDDY_EDGE_DEVICE_ROOT:-${DEVICE_PROOT_ROOTFS}/root/talkybuddy}"

echo "=== [2/3] push：推送 server/ 與 edge/runtime 到裝置 ==="
echo "  目標路徑：${TARGET_ROOT}"

echo "  - 確認裝置連線"
adb get-state >/dev/null

echo "  - 建立裝置端目標目錄"
adb shell "mkdir -p '${TARGET_ROOT}'"

echo "  - adb push server/ -> ${TARGET_ROOT}/server"
adb push server "${TARGET_ROOT}/"

echo "  - adb push edge/runtime -> ${TARGET_ROOT}/edge/runtime"
adb shell "mkdir -p '${TARGET_ROOT}/edge'"
adb push edge/runtime "${TARGET_ROOT}/edge/"

# TODO：proot-distro Debian 內 Python 相依（venv + pip 套件）provisioning，
# 沿用既有 scripts/setup_env.sh 之 M1 已審釘版清單，於裝置 proot shell 內
# 手動或另包腳本執行（本 phase 不新增未釘版套件，見 edge/deploy/README.md）。
# proot-distro 安裝、G520 SDK 專屬 USB/adb 授權步驟，請參照
# ~/hackathon/ 的 Hti G520 SDK 文件。

echo "=== push 完成：server/ 與 edge/runtime 已送達裝置 ${TARGET_ROOT} ==="
