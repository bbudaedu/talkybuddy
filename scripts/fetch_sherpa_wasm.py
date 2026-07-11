#!/usr/bin/env python3
"""取得 sherpa-onnx WASM KWS 預建資產放進 web/vendor/sherpa-kws/。

用法：
    python scripts/fetch_sherpa_wasm.py              # 依序試 huggingface → modelscope
    python scripts/fetch_sherpa_wasm.py --source modelscope
    python scripts/fetch_sherpa_wasm.py --base https://your.mirror/path/

需在有 outbound 網路的機器上跑一次；資產不進版控（見 .gitignore）。

設計重點（見交接記憶 talkybuddy-openwakeword-spike 項目1）：
- 多來源：HF Space（有 HF 存取時）優先，官方 ModelScope 鏡像次之（CN／無 HF）。
- 自我驗證：每個檔下載後做完整性檢查，把「HTTP 200 但其實是登入/WAF 挑戰頁」
  這種 stub 擋下（例如 HF 的 `Invalid username or password.`、阿里雲 WAF 的
  HTML），避免非資產內容被當成 .js/.wasm/.data 靜默寫入 vendor。
- 全有或全無：同一來源四個檔全驗過才寫檔；任一失敗換下一來源。
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.normpath(os.path.join(HERE, "..", "web", "vendor", "sherpa-kws"))

# 檔名以 sherpa-onnx repo `wasm/kws/index.html` 載入的 <script> 為準（權威來源）。
FILES = [
    "sherpa-onnx-kws.js",              # glue：定義全域 createKws() factory
    "sherpa-onnx-wasm-kws-main.js",    # emscripten 產出的載入器
    "sherpa-onnx-wasm-kws-main.wasm",  # 編譯後的 WASM
    "sherpa-onnx-wasm-kws-main.data",  # file-packager 打包的模型（encoder/decoder/joiner/tokens）
]

# 來源（依序嘗試）。ModelScope 是官方文件替「無法存取 Huggingface」者提供的鏡像
# （docs/source/onnx/wasm/hf-spaces.rst）。base 末端斜線可有可無，build_url 會處理。
SOURCES = [
    {
        "name": "huggingface",
        "base": "https://huggingface.co/spaces/k2-fsa/web-assembly-kws-sherpa-onnx/resolve/main/",
    },
    {
        "name": "modelscope",
        "base": "https://www.modelscope.cn/studios/k2-fsa/web-assembly-kws-sherpa-onnx/resolve/master/",
    },
]

# 每種副檔名的最小合理大小（bytes）；小於此值幾乎必是錯誤／挑戰頁 stub。
_MIN_SIZE = {".js": 2000, ".wasm": 100_000, ".data": 500_000}


def build_url(base: str, name: str) -> str:
    """組 base + name，容忍 base 末端有無斜線。"""
    return base.rstrip("/") + "/" + name


def _looks_like_html(data: bytes) -> bool:
    head = data.lstrip()[:512].lower()
    return head.startswith(b"<") or b"<!doctype" in head or b"<html" in head


def validate_asset(name: str, data: bytes) -> None:
    """檢查 data 確實是預期的資產，否則 raise ValueError（訊息可讀）。

    擋掉的常見假成功：HF 401 內文 `Invalid username or password.`、
    ModelScope 阿里雲 WAF 的 HTML 挑戰頁、任何過小的檔。
    """
    ext = os.path.splitext(name)[1].lower()
    min_size = _MIN_SIZE.get(ext, 1)
    if len(data) < min_size:
        raise ValueError(
            f"{name} 只有 {len(data)} bytes（< 最小 {min_size}），"
            f"多半是登入/WAF 挑戰頁或空檔，不是真資產"
        )
    if ext in (".js", ".data", ".wasm") and _looks_like_html(data):
        raise ValueError(f"{name} 內容像 HTML 頁面（登入/WAF 挑戰？），不是真資產")

    if name == "sherpa-onnx-kws.js" and b"createKws" not in data:
        raise ValueError(
            "sherpa-onnx-kws.js 內找不到 createKws factory；"
            "檔名或來源可能不對（前端 available() 靠全域 createKws）"
        )
    if ext == ".wasm" and not data.startswith(b"\x00asm"):
        raise ValueError("*.wasm 缺少 WASM magic bytes（\\0asm），不是有效 WASM")


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 fetch_sherpa_wasm"})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310（固定官方來源）
        if getattr(resp, "status", 200) != 200:
            raise ValueError(f"HTTP {resp.status}")
        return resp.read()


def _try_source(source: dict) -> tuple[dict[str, bytes] | None, str]:
    """試單一來源抓齊四檔並逐一驗證。全過回 (blobs, "")；否則回 (None, 原因)。"""
    blobs: dict[str, bytes] = {}
    for name in FILES:
        url = build_url(source["base"], name)
        print(f"  下載 {name} ...", flush=True)
        try:
            data = _fetch(url)
            validate_asset(name, data)
        except Exception as e:  # noqa: BLE001（想涵蓋網路＋驗證各種錯）
            return None, f"{name}: {e}"
        blobs[name] = data
    return blobs, ""


def _write(blobs: dict[str, bytes]) -> None:
    os.makedirs(DEST, exist_ok=True)
    for name, data in blobs.items():
        with open(os.path.join(DEST, name), "wb") as f:
            f.write(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="取得 sherpa-onnx WASM KWS 資產")
    parser.add_argument("--source", choices=[s["name"] for s in SOURCES],
                        help="只用指定來源（預設全部依序試）")
    parser.add_argument("--base", help="自訂 base URL（覆寫來源清單，供鏡像）")
    args = parser.parse_args(argv)

    if args.base:
        sources = [{"name": "custom", "base": args.base}]
    elif args.source:
        sources = [s for s in SOURCES if s["name"] == args.source]
    else:
        sources = SOURCES

    reasons = []
    for source in sources:
        print(f"來源：{source['name']}（{source['base']}）")
        blobs, reason = _try_source(source)
        if blobs is not None:
            _write(blobs)
            print(f"\n✅ 完成，資產在 {DEST}")
            print("   啟動 server 後開學生端頁，講「說說學伴」測試喚醒。")
            return 0
        print(f"  ✗ 略過此來源：{reason}")
        reasons.append(f"{source['name']}: {reason}")

    sys.stderr.write(
        "\n❌ 所有來源都失敗：\n  - " + "\n  - ".join(reasons) + "\n\n"
        "排查：\n"
        "  1. 該機是否能連 huggingface.co / modelscope.cn？\n"
        "  2. 到 k2-fsa 的 KWS wasm space/studio 的 Files 頁確認同名檔的 resolve\n"
        "     連結，再用 --base 指定；例如：\n"
        "     python scripts/fetch_sherpa_wasm.py --base <你的鏡像/…/resolve/main/>\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
