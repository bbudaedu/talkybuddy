#!/usr/bin/env bash
# 說說學伴 — 邊緣部署 [2/3] push：rsync 推送 server/ + edge/runtime + web/ 到裝置（SSH）
#
# 07-03 board bring-up 實測確認：Hti G520 燒 Yocto 成功後是原生 glibc Linux + SSH，
# 沒有 adb 介面；改用 SSH/rsync 取代 adb push（見 edge/BOARD_BRINGUP_DECISION.md）。
# 假設於 repo 根目錄執行，且開發機已可直連裝置 SSH（同區網或已核准的 Tailscale
# subnet route）。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

SSH_HOST="${TALKYBUDDY_EDGE_SSH_HOST:?請設定 TALKYBUDDY_EDGE_SSH_HOST（裝置區網 IP；DHCP 配發，無固定預設值）}"
SSH_USER="${TALKYBUDDY_EDGE_SSH_USER:-root}"
TARGET_ROOT="${TALKYBUDDY_EDGE_DEVICE_ROOT:-/root/talkybuddy}"
SSH_TARGET="${SSH_USER}@${SSH_HOST}"

echo "=== [2/3] push：rsync 推送 server/、edge/runtime、web 到裝置 ==="
echo "  目標：${SSH_TARGET}:${TARGET_ROOT}"

echo "  - 確認裝置 SSH 連線"
ssh -o ConnectTimeout=5 "${SSH_TARGET}" "echo ok" >/dev/null

echo "  - 建立裝置端目標目錄"
ssh "${SSH_TARGET}" "mkdir -p '${TARGET_ROOT}/edge/runtime'"

echo "  - rsync server/ -> ${TARGET_ROOT}/server"
rsync -az --exclude='__pycache__' --exclude='*.pyc' server/ "${SSH_TARGET}:${TARGET_ROOT}/server/"

echo "  - rsync edge/runtime/ -> ${TARGET_ROOT}/edge/runtime"
rsync -az --exclude='__pycache__' --exclude='*.pyc' edge/runtime/ "${SSH_TARGET}:${TARGET_ROOT}/edge/runtime/"

echo "  - rsync web/ -> ${TARGET_ROOT}/web"
rsync -az --exclude='__pycache__' --exclude='*.pyc' web/ "${SSH_TARGET}:${TARGET_ROOT}/web/"

# systemd unit 模板與安裝/切換腳本。
# 2026-07-30 補上：先前只推 edge/deploy/bin/，所以改了 unit 檔（例如兩個 client
# 的 Conflicts=、S2S 的 NEAR_FIELD_PEAK=0）之後 push.sh 跑完裝置仍是舊設定，
# 得手動 rsync——而「以為推過去了但其實沒有」是最難察覺的一種錯。
# 注意：推檔不等於生效，unit 要重裝才會套用（見本腳本結尾提示）。
echo "  - rsync edge/deploy 的 unit 模板與腳本 -> ${TARGET_ROOT}/edge/deploy"
ssh "${SSH_TARGET}" "mkdir -p '${TARGET_ROOT}/edge/deploy'"
rsync -az edge/deploy/*.service edge/deploy/install_services.sh \
  edge/deploy/switch_mode.sh "${SSH_TARGET}:${TARGET_ROOT}/edge/deploy/"

echo "  - rsync edge/probes/ -> ${TARGET_ROOT}/edge/probes（現場量測工具）"
rsync -az --exclude='__pycache__' --exclude='*.pyc' edge/probes/ "${SSH_TARGET}:${TARGET_ROOT}/edge/probes/"

echo "  - 建立裝置端 llama-server binary / GGUF 模型目標目錄"
ssh "${SSH_TARGET}" "mkdir -p '${TARGET_ROOT}/edge/deploy/bin' '${TARGET_ROOT}/models'"

echo "  - rsync edge/deploy/bin/ -> ${TARGET_ROOT}/edge/deploy/bin（交叉編譯產物 + LLAMACPP_COMMIT.txt）"
if [ ! -e "edge/deploy/bin/llama-server" ]; then
  echo "ERROR: 找不到 edge/deploy/bin/llama-server，請先執行 edge/deploy/build.sh 交叉編譯" >&2
  exit 1
fi
rsync -az edge/deploy/bin/ "${SSH_TARGET}:${TARGET_ROOT}/edge/deploy/bin/"

echo "  - chmod +x llama-server/llama-bench（裝置端可執行位元）"
ssh "${SSH_TARGET}" "chmod +x '${TARGET_ROOT}/edge/deploy/bin/llama-server' '${TARGET_ROOT}/edge/deploy/bin/llama-bench'"

GGUF_MODEL="models/qwen2.5-1.5b-instruct-q4_k_m.gguf"
echo "  - rsync ${GGUF_MODEL} -> ${TARGET_ROOT}/models/（GGUF 模型，與 config.LLM_GGUF 裝置端路徑一致）"
if [ ! -e "${GGUF_MODEL}" ]; then
  echo "ERROR: 找不到 ${GGUF_MODEL}，請先準備 GGUF 模型檔於 repo models/ 目錄再重跑 push.sh" >&2
  exit 1
fi
rsync -az "${GGUF_MODEL}" "${SSH_TARGET}:${TARGET_ROOT}/models/"

# 裝置端 Python 相依（venv + pip 套件）provisioning：edge/runtime/provision_device.sh
# 已隨 edge/runtime 推送到裝置；SSH 進裝置後於 ${TARGET_ROOT} 手動執行一次：
#   cd "${TARGET_ROOT}" && ./edge/runtime/provision_device.sh
# （沿用 scripts/setup_env.sh 之 M1 已審釘版清單子集，見該腳本註解；本 phase
# 不新增未釘版套件，見 edge/deploy/README.md）。

echo "=== push 完成：server/、edge/runtime、web、edge/deploy/、edge/probes/、models/*.gguf 已送達 ${SSH_TARGET}:${TARGET_ROOT} ==="
echo
echo "⚠️ 檔案送到了不等於生效："
echo "  - server / client 的程式碼改動：systemctl restart talkybuddy-server"
echo "  - systemd unit 檔（Environment=、Conflicts= 等）改動："
echo "        ssh ${SSH_TARGET} 'cd ${TARGET_ROOT} && ./edge/deploy/install_services.sh'"
echo "    只 rsync 不重裝的話 /etc/systemd/system 下還是舊的，而且不會有任何錯誤訊息。"
echo
echo "確認現在是哪個模式（並保證恰好有一個 client 在跑）："
echo "        ssh ${SSH_TARGET} '${TARGET_ROOT}/edge/deploy/switch_mode.sh status'"
