# -*- coding: utf-8 -*-
"""aws_preflight.py — 決賽現場「上台前 60 秒」AWS 就緒檢查。

一條指令把整條雲端鏈路從憑證查到真的產出一次診斷，任何一步失敗都明確說出
「壞在哪、怎麼修」，避免上台才發現模型沒開通 / region 打錯 / 權限缺一項。

用法：
    .venv/bin/python scripts/aws_preflight.py
    BEDROCK_REGION=ap-northeast-1 .venv/bin/python scripts/aws_preflight.py

本檔刻意只做編排與輸出，不含新的商業邏輯——實際判斷都委派給
server.bedrock_converse（已有 tests/test_bedrock_converse.py 覆蓋）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OK, BAD, WARN = "\033[32m✔\033[0m", "\033[31m✘\033[0m", "\033[33m!\033[0m"


def _hr(title: str) -> None:
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


def main() -> int:  # noqa: C901 - 線性檢查流程，拆開反而難讀
    from server import bedrock_converse

    failures: list[str] = []

    _hr("① provider 開關")
    os.environ.setdefault("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    cfg = bedrock_converse.resolve_config()
    if cfg is None:
        print(f"{BAD} TALKYBUDDY_CLOUD_PROVIDER 未設為 bedrock")
        print("   修：export TALKYBUDDY_CLOUD_PROVIDER=bedrock")
        return 1
    print(f"{OK} provider=bedrock  region={cfg['region']}  model={cfg['model_id']}")

    _hr("② AWS 憑證（boto3 標準鏈：env → ~/.aws → EC2 IAM Role）")
    try:
        import boto3

        ident = boto3.client("sts", region_name=cfg["region"]).get_caller_identity()
        print(f"{OK} Account={ident['Account']}")
        print(f"   Arn={ident['Arn']}")
        if ":assumed-role/" in ident["Arn"]:
            print(f"   {OK} 走 IAM Role（金鑰不落地，這是正式部署該有的樣子）")
        else:
            print(f"   {WARN} 走長期 access key —— 決賽後記得刪掉這組金鑰")
    except Exception as exc:
        print(f"{BAD} 拿不到憑證：{type(exc).__name__}: {exc}")
        print("   修：aws configure（本機）／確認 EC2 已附掛 IAM Instance Profile")
        return 1

    _hr("③ Bedrock 模型開通狀態")
    try:
        available = bedrock_converse.list_models(cfg["region"])
        anthropic_ids = [m for m in available if "anthropic" in m and "(" not in m]
        if not anthropic_ids:
            print(f"{BAD} 此 region 查不到任何 Anthropic 模型")
            print("   修：Bedrock console → Model access → Modify model access")
            failures.append("model-access")
        else:
            print(f"{OK} 可用 Anthropic model / profile 共 {len(anthropic_ids)} 個：")
            for m in anthropic_ids[:12]:
                mark = " ← 目前設定" if m == cfg["model_id"] else ""
                print(f"     {m}{mark}")
            if cfg["model_id"] not in anthropic_ids:
                print(f"   {WARN} 目前設定的 model_id 不在清單中！")
                print(f"   修：export BEDROCK_MODEL_ID=<上面挑一個>")
                failures.append("model-id")
    except Exception as exc:
        print(f"{WARN} 列模型失敗（不一定是致命問題）：{type(exc).__name__}: {exc}")

    _hr("④ 真打一次 Converse")
    try:
        text = bedrock_converse.converse_text(
            "你是一個測試回應器。只回覆四個字，不要標點。",
            "請回覆：連線正常",
            cfg=cfg,
            max_tokens=32,
            timeout_s=20.0,
        )
        print(f"{OK} Bedrock 回應：{text.strip()!r}")
    except Exception as exc:
        print(f"{BAD} Converse 失敗：{type(exc).__name__}: {exc}")
        print("   常見原因：model_id 錯（見③）／IAM 缺 bedrock:Converse／region 沒開通")
        failures.append("converse")

    _hr("⑤ 端到端：產出一次教師診斷")
    try:
        from server import diagnose

        result = diagnose.generate_diagnosis([], None)
        src = "Bedrock" if not failures else "規則式 fallback（雲端失敗，降級鏈生效）"
        print(f"{OK} 診斷產出成功，來源＝{src}")
        print(f"   scores={result['scores']}")
    except Exception as exc:
        print(f"{BAD} 診斷產出失敗：{type(exc).__name__}: {exc}")
        failures.append("diagnose")

    _hr("結論")
    if failures:
        print(f"{BAD} 有 {len(failures)} 項未過：{', '.join(failures)}")
        print("   注意：降級鏈仍會讓 demo 跑得動（退規則式），但 Bedrock 合規未成立。")
        return 1
    print(f"{OK} 全數通過 —— 雲端主線可上台。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
