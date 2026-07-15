# -*- coding: utf-8 -*-
"""實機驗證：真打 Amazon Bedrock，驗陪聊 / 導師 / 降級三條路徑。

用法（憑證由呼叫端 env 提供，本腳本不碰金鑰明文）：
    cd talkybuddy
    LLM_CLOUD_PROVIDER=bedrock BEDROCK_REGION=us-east-1 \
      AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
      .venv/bin/python scripts/verify_bedrock_live.py

或先把憑證寫進 ~/.aws/credentials（boto3 credential chain 自動抓），再：
    LLM_CLOUD_PROVIDER=bedrock BEDROCK_REGION=us-east-1 \
      .venv/bin/python scripts/verify_bedrock_live.py

離線降級驗證（不需憑證）：
    LLM_CLOUD_PROVIDER=off .venv/bin/python scripts/verify_bedrock_live.py --downgrade-only
"""
import os
import sys
import time

# 讓 `from server import ...` 不論從哪個 cwd 執行都可用（talkybuddy root 進 path）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import cloud_llm, config


class _Scaffold:
    def __init__(self, target):
        self.target_sentence = target


def _hr(title):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def _show_config():
    _hr("設定（config / 環境變數）")
    print(f"LLM_CLOUD_PROVIDER = {config.LLM_CLOUD_PROVIDER}")
    print(f"BEDROCK_REGION     = {config.BEDROCK_REGION}")
    print(f"COMPANION_MODEL_ID = {config.COMPANION_MODEL_ID}")
    print(f"TUTOR_MODEL_ID     = {config.TUTOR_MODEL_ID}")


def verify_companion():
    _hr("① 陪聊 CloudLLM.generate（真打 Bedrock）")
    llm = cloud_llm.CloudLLM()
    avail = llm.available()
    print(f"available() = {avail}")
    if not avail:
        print("❌ available() 為 False：provider 非 bedrock 或無法建立 boto3 client。")
        print("   → 請確認 LLM_CLOUD_PROVIDER=bedrock 且 AWS 憑證已設好。")
        return False
    target = "I see a cat"
    t0 = time.monotonic()
    out = llm.generate("我看到一隻貓", _Scaffold(target))
    dt = time.monotonic() - t0
    print(f"耗時 = {dt:.2f}s")
    print(f"回覆 = {out!r}")
    if out is None:
        print("❌ 回 None：逾時 / 例外 / 空輸出 / 命中護欄。（見上方 log）")
        return False
    ok_read = f"跟我說一遍：{target}" in out or target in out
    print(f"帶讀目標句包含檢查 = {ok_read}")
    if not ok_read:
        print("⚠️  回覆未含目標英文句（帶讀補句應保證含），請人工判讀。")
    print("✅ 陪聊真打 Bedrock 成功，回覆為雲端生成且過護欄。")
    return True


def verify_tutor():
    _hr("② 導師 diagnose_via_bedrock（真打 Bedrock, toolChoice 強制 JSON）")
    interactions = [
        {"student_text": "I see a cat", "ai_response_text": "很好！跟我說一遍：I see a cat",
         "asr_confidence": 0.82, "scores": {"pronunciation": 60, "fluency": 55}},
        {"student_text": "The dog is big", "ai_response_text": "很棒！跟我說一遍：The dog is big",
         "asr_confidence": 0.75, "scores": {"pronunciation": 58, "fluency": 60}},
    ]
    t0 = time.monotonic()
    out = cloud_llm.diagnose_via_bedrock(interactions, None)
    dt = time.monotonic() - t0
    print(f"耗時 = {dt:.2f}s")
    if out is None:
        print("❌ 回 None：Bedrock 失敗 / schema 不符 / 例外。（見上方 log）")
        return False
    import json
    print("回傳診斷 JSON：")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    ok = (
        isinstance(out.get("scores"), dict)
        and all(k in out["scores"] for k in ("pronunciation", "fluency", "vocabulary", "grammar"))
        and isinstance(out.get("instructions"), dict)
        and all(k in out["instructions"] for k in ("classroom", "device", "peer"))
    )
    print(f"schema 必填欄位檢查 = {ok}")
    if not ok:
        print("❌ 結構缺必填欄位。")
        return False
    print("✅ 導師真打 Bedrock 成功，結構化 JSON 合法。")
    return True


def verify_downgrade():
    _hr("③ 降級路徑（available() 應為 False → pipeline 走本地）")
    llm = cloud_llm.CloudLLM()
    avail = llm.available()
    print(f"provider={config.LLM_CLOUD_PROVIDER} → available() = {avail}")
    if avail:
        print("⚠️  provider 開著且 client 可建，非降級情境。此檢查請在 provider=off 或無憑證時跑。")
        return None
    out = llm.generate("我看到一隻貓", _Scaffold("I see a cat"))
    print(f"generate() = {out!r}（應為 None → pipeline 降級回本地 EdgeLLM/scaffold）")
    ok = out is None
    print("✅ 降級正確：available()=False 且 generate 回 None。" if ok else "❌ 降級異常。")
    return ok


def main():
    downgrade_only = "--downgrade-only" in sys.argv
    _show_config()
    if downgrade_only:
        r = verify_downgrade()
        return 0 if r else 1
    results = {
        "陪聊 generate": verify_companion(),
        "導師 diagnose": verify_tutor(),
    }
    _hr("結果總結")
    all_ok = True
    for name, r in results.items():
        print(f"  {'✅' if r else '❌'} {name}")
        all_ok = all_ok and bool(r)
    print("\n" + ("🎉 實機驗證全數通過。" if all_ok else "⚠️  有項目未通過，見上方細節。"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
