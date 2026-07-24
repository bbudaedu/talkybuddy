"""邊緣端 llama-server launcher（Phase 8 CPU-only 離線迴路）。

用途：把 llama-server 啟動參數（--model/--ctx-size/--host/--port/--threads）的
組裝邏輯抽成獨立、純粹、可單元測試的函式 build_llama_server_argv()，避免藏進
shell 字串拼接、失去自動化保護（見 08-PATTERNS.md Pitfall 2）。

llama-server 為獨立 OS 行程（非 in-process Llama 物件），n_ctx 是啟動時 CLI
flag（--ctx-size），不再是 llama_cpp.Llama(n_ctx=...) 建構參數。

T-08-01（威脅模型，見 08-01-PLAN.md）：host 參數預設寫死 "127.0.0.1"（loopback），
絕不可預設對外可路由位址——這是 llama-server 不對外暴露無驗證端點的結構性第一道防線。
"""

from __future__ import annotations

import os
from pathlib import Path


def build_llama_server_argv(
    model_path,
    ctx_size: int,
    host: str = "127.0.0.1",
    port: int = 8080,
    threads: int = 4,
    binary_path: str = "llama-server",
) -> list[str]:
    """組出 llama-server 啟動 argv（純函式，無 I/O、不啟動子行程、無副作用）。

    Args:
        model_path: GGUF 模型檔路徑（str 或 Path，僅取 str()）。
        ctx_size: --ctx-size 值（承接 config.LLM_N_CTX）。
        host: --host 值，預設 "127.0.0.1"（loopback，見 T-08-01）。
        port: --port 值，預設 8080。
        threads: --threads 值，預設 4（佔位，待 ELOOP-03 以 llama-bench 實測覆寫）。
        binary_path: llama-server 執行檔路徑，預設相對名稱 "llama-server"
            （main() 呼叫時會傳入實際解析出的絕對路徑）。

    Returns:
        list[str]：可直接交給 os.execv / subprocess 的 argv 串列。
    """
    return [
        str(binary_path),
        "--model",
        str(model_path),
        "--ctx-size",
        str(ctx_size),
        "--host",
        host,
        "--port",
        str(port),
        "--threads",
        str(threads),
    ]


def main() -> None:
    """launcher 進入點：解析路徑與設定，組出 argv，以 os.execv 取代行程啟動 llama-server。

    - config 一律 lazy import（import 本模組不可因 config 缺失而炸）。
    - 以等價於 run_edge.sh 的 BASH_SOURCE 相對定位（Path(__file__).resolve() 上溯）
      求得 repo/deploy 根目錄，不硬編個人 home 絕對路徑（比照 D-02）。
    - binary 路徑經 env TALKYBUDDY_LLAMA_SERVER_BIN 覆寫，預設指向 push.sh 部署的
      edge/deploy/bin/llama-server。
    """
    from server import config  # lazy import：避免 import 本模組時就需要 server 套件齊全

    script_dir = Path(__file__).resolve().parent
    target_root = script_dir.parent.parent
    default_bin = target_root / "edge" / "deploy" / "bin" / "llama-server"
    binary_path = os.environ.get("TALKYBUDDY_LLAMA_SERVER_BIN", str(default_bin))

    argv = build_llama_server_argv(
        model_path=config.LLM_GGUF,
        ctx_size=config.LLM_N_CTX,
        host=config.LLM_SERVER_HOST,
        port=config.LLM_SERVER_PORT,
        threads=config.LLM_THREADS,
        binary_path=binary_path,
    )

    os.execv(binary_path, argv)


if __name__ == "__main__":
    main()
