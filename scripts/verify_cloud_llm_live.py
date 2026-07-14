# -*- coding: utf-8 -*-
"""verify_cloud_llm_live.py — CloudLLM 雲端腦「實機」驗證（會發真請求，不 mock）。

用途：確認 anthropic_relay 解析出的端點/認證/model 在真環境（官方 API 或自架中轉）
確實連得通、回應可被 CloudLLM 正確解析並過護欄。單元測試全 mock urlopen，此腳本補上
「真的打得到嗎」這一段。

安全：金鑰只讀環境變數，絕不寫入 repo、絕不 commit。輸出時 token 一律遮蔽。

用法（於 talkybuddy/ 根目錄）：

    # 自架中轉
    ANTHROPIC_BASE_URL=http://<relay-host>:<port> \
    ANTHROPIC_AUTH_TOKEN=<token> \
    ANTHROPIC_DEFAULT_OPUS_MODEL=<model> \
    .venv/bin/python scripts/verify_cloud_llm_live.py

    # 官方 API
    ANTHROPIC_API_KEY=sk-... .venv/bin/python scripts/verify_cloud_llm_live.py

離開碼：0＝連通且回覆通過護欄；1＝設定/連線/護欄任一失敗。
"""
from __future__ import annotations

import os
import sys
import time

# 讓 `from server import ...` 可用（腳本在 scripts/ 底下，把 talkybuddy 根加入 path）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from server import anthropic_relay  # noqa: E402
from server.cloud_llm import CloudLLM  # noqa: E402


def _mask(secret: str | None) -> str:
    """遮蔽憑證：只留頭尾各 4 碼。"""
    if not secret:
        return "(空)"
    if len(secret) <= 8:
        return "****"
    return f"{secret[:4]}…{secret[-4:]}（共 {len(secret)} 碼）"


class _Scaffold:
    """假 ScaffoldResult：CloudLLM.generate 只需 target_sentence。"""
    target_sentence = "I like apples."


def main() -> int:
    print("=== CloudLLM 實機驗證 ===\n")

    # 1) 解析設定
    cfg = anthropic_relay.resolve_config()
    if cfg is None:
        print("✗ 無憑證：ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY 皆未設定。")
        print("  → cloud_llm.available() 會是 False，pipeline 會直接降級 edge。")
        return 1

    auth = cfg["headers"].get("Authorization") or cfg["headers"].get("x-api-key", "")
    print("解析設定（anthropic_relay.resolve_config）：")
    print(f"  端點 url  : {cfg['url']}")
    print(f"  model     : {cfg['model']}")
    print(f"  認證方式  : {'Bearer (AUTH_TOKEN)' if 'Authorization' in cfg['headers'] else 'x-api-key (API_KEY)'}")
    print(f"  憑證      : {_mask(auth.replace('Bearer ', ''))}")
    print(f"  版本標頭  : {cfg['headers'].get('anthropic-version')}\n")

    # 2) available()
    engine = CloudLLM()
    print(f"available(): {engine.available()}\n")

    # 3) 發真請求
    student_text = "我喜歡蘋果"
    print(f"發真請求 → 學生說：「{student_text}」／目標句：{_Scaffold.target_sentence}")
    t0 = time.monotonic()
    reply = engine.generate(student_text, _Scaffold())
    dt_ms = int((time.monotonic() - t0) * 1000)

    if reply is None:
        print(f"\n✗ generate() 回 None（耗時 {dt_ms}ms）")
        print("  可能原因：連線/逾時失敗、回應格式不符、或輸出未過護欄。")
        print("  （CloudLLM 吞例外並記 log；細節見 server log 或改用 -X 追蹤）")
        return 1

    print(f"\n✓ 連通成功（耗時 {dt_ms}ms）")
    print("─" * 48)
    print(reply)
    print("─" * 48)

    # 4) 帶讀契約自檢
    if _Scaffold.target_sentence in reply:
        print(f"\n✓ 回覆含目標英文句「{_Scaffold.target_sentence}」")
    else:
        print(f"\n⚠ 回覆未含目標句（CloudLLM 應自動補；請檢查）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
