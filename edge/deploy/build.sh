#!/usr/bin/env bash
# 說說學伴 — 邊緣部署 [1/3] build：確認載荷清單 + 交叉編譯 llama.cpp native binary
#
# 本腳本除確認要 push 的來源目錄存在（server/、edge/runtime）外，Phase 8
# 起也負責交叉編譯 llama.cpp（llama-server/llama-bench/llama-cli），供
# push.sh 推送到 Genio 520（aarch64 glibc Linux，裝置本身無 gcc/cmake，
# 見 edge/BOARD_BRINGUP_DECISION.md §5「無 C 編譯器」）。
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

echo "=== [1b/3] build：交叉編譯 llama.cpp native binary（ELOOP-02，D-02 build flag）==="

# D-03：交叉工具鏈以環境變數參數化。預設走開發機 apt 泛用 aarch64-linux-gnu
# 工具鏈（gcc-aarch64-linux-gnu / g++-aarch64-linux-gnu）。若裝置上 glibc ABI
# 不相容（--version / ldd 失敗一次），呼叫端應立即改用 ~/hackathon/ 的 Genio
# Yocto BSP SDK 官方 cross-toolchain——只需以下列 env 覆寫指向 SDK 的
# gcc/g++，不需要新增第二條編譯碼路：
#   TALKYBUDDY_CROSS_CC=<yocto-sdk>/bin/aarch64-...-gcc \
#   TALKYBUDDY_CROSS_CXX=<yocto-sdk>/bin/aarch64-...-g++ \
#   edge/deploy/build.sh
# 不得在 apt 路徑上反覆嘗試超過一次修正（D-03 止損規則）。
CROSS_CC="${TALKYBUDDY_CROSS_CC:-aarch64-linux-gnu-gcc}"
CROSS_CXX="${TALKYBUDDY_CROSS_CXX:-aarch64-linux-gnu-g++}"

if ! command -v "${CROSS_CC}" >/dev/null 2>&1; then
  echo "ERROR: 找不到交叉編譯器 ${CROSS_CC}（開發機 apt 路徑預設；D-03 fallback 可用 TALKYBUDDY_CROSS_CC 覆寫指向 Yocto SDK）" >&2
  echo "       apt 安裝：sudo apt-get install -y gcc-aarch64-linux-gnu g++-aarch64-linux-gnu cmake" >&2
  exit 1
fi
if ! command -v "${CROSS_CXX}" >/dev/null 2>&1; then
  echo "ERROR: 找不到交叉編譯器 ${CROSS_CXX}（開發機 apt 路徑預設；D-03 fallback 可用 TALKYBUDDY_CROSS_CXX 覆寫指向 Yocto SDK）" >&2
  exit 1
fi
echo "  - 交叉編譯器：CC=${CROSS_CC} CXX=${CROSS_CXX} (OK)"

# 取得 llama.cpp 原始碼：只從官方 ggml-org/llama.cpp clone（供應鏈完整性，T-08-02）。
LLAMACPP_SRC="${TALKYBUDDY_LLAMACPP_SRC:-${REPO_ROOT}/third_party/llama.cpp}"
if [ ! -d "${LLAMACPP_SRC}" ]; then
  echo "  - ${LLAMACPP_SRC} 不存在，clone 官方 llama.cpp（https://github.com/ggml-org/llama.cpp.git）"
  mkdir -p "$(dirname "${LLAMACPP_SRC}")"
  git clone https://github.com/ggml-org/llama.cpp.git "${LLAMACPP_SRC}"
else
  echo "  - 使用既有原始碼：${LLAMACPP_SRC} (OK)"
fi

LLAMACPP_COMMIT="$(git -C "${LLAMACPP_SRC}" rev-parse HEAD)"
echo "  - llama.cpp commit：${LLAMACPP_COMMIT}"

BUILD_DIR="${LLAMACPP_SRC}/build-aarch64"
BIN_DIR="${REPO_ROOT}/edge/deploy/bin"

echo "  - cmake configure（-march=armv8.2-a+dotprod+i8mm，GGML_NATIVE=OFF；D-02，絕不用 armv8.7-a）"
cmake -B "${BUILD_DIR}" -S "${LLAMACPP_SRC}" \
  -DCMAKE_SYSTEM_NAME=Linux \
  -DCMAKE_SYSTEM_PROCESSOR=aarch64 \
  -DCMAKE_C_COMPILER="${CROSS_CC}" \
  -DCMAKE_CXX_COMPILER="${CROSS_CXX}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_NATIVE=OFF \
  -DCMAKE_C_FLAGS="-march=armv8.2-a+dotprod+i8mm" \
  -DCMAKE_CXX_FLAGS="-march=armv8.2-a+dotprod+i8mm" \
  -DGGML_OPENMP=OFF

echo "  - cmake build（--target llama-server llama-bench llama-cli）"
cmake --build "${BUILD_DIR}" --config Release -j"$(nproc)" --target llama-server llama-bench llama-cli

mkdir -p "${BIN_DIR}"
for bin in llama-server llama-bench llama-cli; do
  SRC_BIN="${BUILD_DIR}/bin/${bin}"
  if [ ! -e "${SRC_BIN}" ]; then
    echo "ERROR: 交叉編譯後找不到預期產物：${SRC_BIN}" >&2
    exit 1
  fi
  cp -f "${SRC_BIN}" "${BIN_DIR}/${bin}"
  echo "  - ${BIN_DIR}/${bin} (OK)"
done

echo "  - file 快篩：確認產物為 aarch64 ELF（非開發機 x86-64）"
FILE_OUT="$(file "${BIN_DIR}/llama-server")"
echo "    ${FILE_OUT}"
case "${FILE_OUT}" in
  *aarch64*) echo "    aarch64 ELF 確認 (OK)" ;;
  *)
    echo "ERROR: ${BIN_DIR}/llama-server 非 aarch64 ELF，交叉編譯設定有誤（file 輸出：${FILE_OUT}）" >&2
    exit 1
    ;;
esac

# 記錄所編譯 llama.cpp 的 git commit hash，供 Day-N 可重現與供應鏈追溯。
echo "${LLAMACPP_COMMIT}" > "${BIN_DIR}/LLAMACPP_COMMIT.txt"
echo "  - ${BIN_DIR}/LLAMACPP_COMMIT.txt (OK, commit=${LLAMACPP_COMMIT})"

echo "=== build 完成，載荷就緒（server/、edge/runtime、edge/deploy/bin/{llama-server,llama-bench,llama-cli}）==="
echo ""
echo "注意（D-03 止損規則）：若 push.sh 推送後在真機上 llama-server --version / ldd 失敗"
echo "（glibc ABI 不相容），請立即改用 Yocto SDK 工具鏈重編一次，不要在 apt 路徑上反覆嘗試："
echo "  TALKYBUDDY_CROSS_CC=<yocto-sdk-gcc> TALKYBUDDY_CROSS_CXX=<yocto-sdk-g++> edge/deploy/build.sh"
