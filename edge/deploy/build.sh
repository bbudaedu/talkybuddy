#!/usr/bin/env bash
# 說說學伴 — 邊緣部署 [1/3] build：準備要 push 的載荷清單
#
# 本 phase 不需編譯 native binary（llama.cpp native build 屬 Phase 8）；
# 這裡的「build」= 確認要 push 的來源目錄存在、列出清單，供 push.sh 使用。
# 假設於 repo 根目錄執行。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

echo "=== [1/3] build：確認邊緣部署載荷 ==="

SRC_PATHS=(
  "server"
  "edge/runtime"
)

for p in "${SRC_PATHS[@]}"; do
  if [ ! -e "${REPO_ROOT}/${p}" ]; then
    echo "ERROR: 找不到預期載荷路徑：${p}" >&2
    exit 1
  fi
  echo "  - ${p} (OK)"
done

# TODO：llama.cpp / sherpa-onnx native binary 交叉編譯屬 Phase 8（CPU-only
# 離線迴路）與 Phase 10（NPU 加速），本 phase 不涉及。若需要 G520 SDK 專屬
# 交叉編譯 toolchain 指令，請參照 ~/hackathon/ 的 Hti G520 SDK 文件（見
# docs/DEPLOY_EDGE.md §5）。

echo "=== build 完成，載荷就緒（server/、edge/runtime）==="
