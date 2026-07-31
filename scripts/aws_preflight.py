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


_AGENT_ROLES = ("orchestrator", "homework", "report", "material")

# 角色 → 該角色 harness ARN 的環境變數名稱（訊息要講得出「去設哪一個」）
_ROLE_ENV = {
    "orchestrator": "AGENTCORE_HARNESS_ORCHESTRATOR",
    "homework": "AGENTCORE_HARNESS_HOMEWORK",
    "report": "AGENTCORE_HARNESS_REPORT",
    "material": "AGENTCORE_HARNESS_MATERIAL",
}


def agentcore_checks(chains: dict[str, list[str]],
                     flag_on: bool) -> list[tuple[str, str]]:
    """把四個 agent 的降級鏈判成 ``(等級, 訊息)``；等級 ∈ ok / warn / bad。

    純函式、不觸網，測試涵蓋在 tests/test_aws_preflight_agentcore.py。

    判定原則：

    - **AgentCore 沒撥開關 → warn，不是 bad。** 它是加分項；降級鏈本來就設計
      成「沒有它也完整」。把加分項判成失敗會讓現場的人去救一個不需要救的東西。
    - **撥了開關卻漏設某個角色的 harness ARN → bad。** 這是現場最容易犯、
      也最難察覺的錯：開關看起來撥了，那個角色其實整個沒走 AgentCore，
      而且沒有任何東西會報錯。
    - **鏈上沒有 bedrock → bad。** 第二層不見了等於 AgentCore 一失敗就直接
      摔到規則式，品質下界比什麼都不開還低。這正是先前那個缺陷造成的形狀。
    """
    out: list[tuple[str, str]] = []
    for role in _AGENT_ROLES:
        chain = list(chains.get(role) or [])
        arrow = " → ".join(chain) if chain else "（空）"
        has_ac = "agentcore" in chain
        has_bedrock = "bedrock" in chain

        if flag_on and not has_ac:
            out.append(("bad", f"{role}：{arrow} —— 撥了 AgentCore 開關卻沒走到它。"
                               f"修：export {_ROLE_ENV[role]}=<harness arn>"))
        elif flag_on and not has_bedrock:
            out.append(("bad", f"{role}：{arrow} —— 少了 Bedrock 這一層，"
                               f"AgentCore 一失敗就直接掉規則式。"
                               f"修：export TALKYBUDDY_CLOUD_PROVIDER=bedrock"))
        elif has_ac:
            out.append(("ok", f"{role}：{arrow}"))
        else:
            out.append(("warn", f"{role}：{arrow} —— AgentCore 未啟用"
                                f"（加分項，不影響降級鏈完整性）"))
    return out


def cloud_timeout() -> float:
    """對話路徑的逾時上界（讀 cloud_llm 的實際值，避免文件與程式漂移）。"""
    from server import cloud_llm

    return cloud_llm._TIMEOUT_S


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
    # 對話／診斷兩條路徑的 model 分流（逾時上界 1.5s vs 12s）。preflight 必須
    # 兩顆都驗，否則對話那顆打錯會拖到現場才炸——而它的症狀是「安靜地降級回
    # edge」，最難當場察覺。
    chat_cfg = bedrock_converse.resolve_config(role="chat")
    diag_cfg = bedrock_converse.resolve_config(role="diag")
    print(f"{OK} provider=bedrock  region={cfg['region']}")
    print(f"   對話 model（上界 {cloud_timeout()}s）＝{chat_cfg['model_id']}")
    print(f"   診斷 model（上界 12s）＝{diag_cfg['model_id']}")
    if chat_cfg["model_id"] == diag_cfg["model_id"]:
        print(f"   {WARN} 兩條路徑共用同一顆 model。若它不是快模型，對話回覆會")
        print(f"       穩定逾時而永遠降級回 edge，等於雲端大腦白接。")
        print(f"       修：export BEDROCK_MODEL_ID_CHAT=<haiku> "
              f"BEDROCK_MODEL_ID_DIAG=<sonnet>")

    _hr("② 四個 agent 的降級鏈（AgentCore → Bedrock → 規則式）")
    try:
        from server import agent_backends

        flag_on = (os.environ.get("TALKYBUDDY_AGENT_BACKEND") or ""
                   ).strip().lower() == "agentcore"
        # 鏈的判定一律走 agent_backends.chain()——agent 用的是同一份。
        # preflight 若自己重寫一遍，就會有一條會漂移的假鏈，而現場的人會信它。
        chains = {role: agent_backends.chain(role) for role in _AGENT_ROLES}
        for level, msg in agentcore_checks(chains, flag_on):
            print(f"{ {'ok': OK, 'warn': WARN, 'bad': BAD}[level] } {msg}")
            if level == "bad":
                failures.append("agentcore-chain")
        if not flag_on:
            print("   （這是設定讀數，不是證據。要證明 AgentCore 真的產出過，"
                  "看教師端卡片的 source 徽章。）")
    except Exception as exc:
        print(f"{WARN} 降級鏈檢查本身失敗：{type(exc).__name__}: {exc}")

    _hr("③ AWS 憑證（boto3 標準鏈：env → ~/.aws → EC2 IAM Role）")
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

    _hr("④ Bedrock 模型開通狀態")
    try:
        available = bedrock_converse.list_models(cfg["region"])
        anthropic_ids = [m for m in available if "anthropic" in m and "(" not in m]
        if not anthropic_ids:
            print(f"{BAD} 此 region 查不到任何 Anthropic 模型")
            print("   修：Bedrock console → Model access → Modify model access")
            failures.append("model-access")
        else:
            print(f"{OK} 可用 Anthropic model / profile 共 {len(anthropic_ids)} 個：")
            marks = {chat_cfg["model_id"]: "對話", diag_cfg["model_id"]: "診斷"}
            # 設定中的兩顆一定要看得見；其餘補到 12 筆當作參考清單，否則
            # 目前設定的 model 若排在第 13 名之後，標記等於不存在。
            shown = [m for m in anthropic_ids if m in marks]
            shown += [m for m in anthropic_ids if m not in marks][: 12 - len(shown)]
            for m in shown:
                mark = f" ← 目前設定（{marks[m]}）" if m in marks else ""
                print(f"     {m}{mark}")
            for role_name, role_cfg, env_name in (
                ("對話", chat_cfg, "BEDROCK_MODEL_ID_CHAT"),
                ("診斷", diag_cfg, "BEDROCK_MODEL_ID_DIAG"),
            ):
                if role_cfg["model_id"] not in anthropic_ids:
                    print(f"   {WARN} {role_name}路徑的 model_id "
                          f"{role_cfg['model_id']} 不在清單中！")
                    print(f"   修：export {env_name}=<上面挑一個>")
                    failures.append("model-id")
    except Exception as exc:
        print(f"{WARN} 列模型失敗（不一定是致命問題）：{type(exc).__name__}: {exc}")

    _hr("⑤ 真打一次 Converse（用對話路徑那顆 model）")
    try:
        import time

        budget = cloud_timeout()
        t0 = time.monotonic()
        text = bedrock_converse.converse_text(
            "你是一個測試回應器。只回覆四個字，不要標點。",
            "請回覆：連線正常",
            cfg=chat_cfg,
            max_tokens=32,
            timeout_s=20.0,
        )
        elapsed = time.monotonic() - t0
        print(f"{OK} Bedrock 回應：{text.strip()!r}（{elapsed:.2f}s）")
        # 這裡刻意給 20s 寬限才發請求：目的是先確認「打得通」，再單獨用實測
        # 秒數對照 1.5s 預算。若合併成一次 1.5s 呼叫，連不通與太慢會混在一起。
        if elapsed > budget:
            print(f"   {WARN} 實測 {elapsed:.2f}s 超過對話路徑預算 {budget}s ——")
            print(f"       正式跑會逾時並降級回 edge。修：換更快的 "
                  f"BEDROCK_MODEL_ID_CHAT，或放寬 CLOUD_LLM_TIMEOUT_S。")
            failures.append("chat-latency")
        else:
            print(f"   {OK} 在對話路徑 {budget}s 預算內")
    except Exception as exc:
        print(f"{BAD} Converse 失敗：{type(exc).__name__}: {exc}")
        print("   常見原因：model_id 錯（見③）／IAM 缺 bedrock:Converse／region 沒開通")
        failures.append("converse")

    _hr("⑥ 端到端：產出一次教師診斷")
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
