#!/usr/bin/env python3
"""取得 sherpa-onnx WASM KWS 預建資產放進 web/vendor/sherpa-kws/。
用法：  python scripts/fetch_sherpa_wasm.py
需在有 outbound 網路的機器上跑一次；資產不進版控（見 .gitignore）。
"""
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.normpath(os.path.join(HERE, "..", "web", "vendor", "sherpa-kws"))

# k2-fsa 官方 WASM KWS demo 產物（含 wenetspeech-3.3M 模型的 .data）。
# 若下列 URL 失效，改用官方 HF Space「k2-fsa/web-assembly-kws-sherpa-onnx」
# 的 Files 頁確認同名檔的 resolve 連結後替換 BASE。
BASE = "https://huggingface.co/spaces/k2-fsa/web-assembly-kws-sherpa-onnx/resolve/main/"
FILES = [
    "sherpa-onnx-kws.js",
    "sherpa-onnx-wasm-kws-main.js",
    "sherpa-onnx-wasm-kws-main.wasm",
    "sherpa-onnx-wasm-kws-main.data",
]


def main():
    os.makedirs(DEST, exist_ok=True)
    for name in FILES:
        url = BASE + name
        out = os.path.join(DEST, name)
        print(f"下載 {name} ...")
        try:
            urllib.request.urlretrieve(url, out)
        except Exception as e:
            sys.exit(f"下載失敗 {url}\n  {e}\n"
                     f"請確認網路，或到 k2-fsa HF Space Files 頁核對檔名/連結後改 BASE。")
    print(f"\n✅ 完成，資產在 {DEST}")
    print("   啟動 server 後開學生端頁，講「說說學伴」測試喚醒。")


if __name__ == "__main__":
    main()
