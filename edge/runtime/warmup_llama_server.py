# -*- coding: utf-8 -*-
"""warmup_llama_server：開機時對 llama-server 送一次假回合，焐熱 system prompt 的 KV cache。

背景（08-05 checkpoint 真機實測發現，2026-07-25）：llama-server 剛啟動、prompt cache
全空時，第一次 `/v1/chat/completions` 呼叫要把整段 system prompt（≈293 token）重新算過
一次，Genio 520 上 pp≈39 t/s 換算要 ≈7.5 秒，整輪端到端延遲衝到 10 秒，遠超 D-05 的
3–4 秒門檻（NO-GO）。後續呼叫因命中同一 slot 的 prompt cache，延遲驟降到 3 秒內
（GO）。此模組讓開機流程自己先吃下這筆冷啟動成本，讓現場觀眾聽到的第一句就在
GO 門檻內。

必須使用與 `server.llm.EdgeLLM._SYSTEM_PROMPT` 完全相同的字串——KV cache 依 prompt
前綴逐 token 比對，字串不同就命不中，白跑一次暖身。因此直接 import EdgeLLM 取用
該常數，不在此重複貼一份字串（避免兩處漂移）。

失敗（llama-server 未就緒/逾時/連線被拒）一律回 False、不拋例外：暖身只是延遲優化，
不是啟動的必要條件，run_edge.sh 呼叫本模組必須容忍失敗繼續往下走。
"""

from __future__ import annotations

import json
import logging
import sys
import urllib.request

from server.llm import EdgeLLM

_log = logging.getLogger(__name__)

# 暖身呼叫逾時（秒）：冷啟動全段 system prompt 重算實測 ≈7.5 秒，留寬裕。
_WARMUP_TIMEOUT_S = 15.0

_WARMUP_USER_MESSAGE = "（系統暖身呼叫，非真實對話，可忽略此訊息）"


def warmup(base_url: str) -> bool:
    """對 base_url 送一次暖身 /v1/chat/completions；成功回 True，任何失敗回 False。"""
    body = json.dumps(
        {
            "messages": [
                {"role": "system", "content": EdgeLLM._SYSTEM_PROMPT},
                {"role": "user", "content": _WARMUP_USER_MESSAGE},
            ],
            "max_tokens": 8,
            "temperature": 0.7,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_WARMUP_TIMEOUT_S) as resp:
            resp.read()
        return True
    except Exception:
        _log.exception("llama-server 暖身呼叫失敗，忽略並繼續啟動（僅影響冷啟動第一句延遲）")
        return False


def main() -> None:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
    ok = warmup(base_url)
    print("WARMUP_OK" if ok else "WARMUP_FAILED")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
