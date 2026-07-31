#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""provision_agentcore.py — 在「任何」AWS 帳號重建 TalkyBuddy 的 AgentCore 資源。

為什麼需要這支腳本
------------------
現有的 Memory 與三個 Harness 是 2026-07-26 用 CLI 手動建的，ARN 長這樣：

    arn:aws:bedrock-agentcore:ap-southeast-1:**<AWS_ACCOUNT_ID>**:harness/...

**帳號綁定**。決賽當天若用主辦方提供的 AWS 資源，這些 ARN 全部失效，
必須在現場重建 IAM role + Memory + 3 個 Harness——而那條路徑從來沒有人
走過第二遍。手動重建要記得的細節包括：confused-deputy 的兩個條件、
summarization 策略的 namespace 必須含 ``{sessionId}``、三個執行上限必須
顯式設定。現場壓力下漏掉任何一項都是無聲的失敗。

這支腳本把那條路徑變成一行指令，而且是**冪等**的：已存在的資源會被
沿用（並回讀比對關鍵欄位），不會重複建立。

可以先驗證再上場
----------------
``--dry-run``（預設）**不需要 AWS 憑證**：它會組出完整的請求，用 botocore
自己的 service model 驗證每一個欄位名稱與型別，然後把請求印出來。
API 形狀若有漂移，會在本機當場失敗，而不是在決賽現場。

    python3 deploy/aws/provision_agentcore.py                 # 只驗形狀、不連 AWS
    python3 deploy/aws/provision_agentcore.py --apply         # 真的建立
    python3 deploy/aws/provision_agentcore.py --apply --region ap-southeast-2

單一真相來源
------------
三個 harness 的 system prompt **從 server/agents/*.py 匯入**，不在這裡重抄。
抄一份的話，改了程式碼卻忘了更新 harness，雲端與離線兩條路徑就會給出
不同的東西，而且測試抓不到（測試跑的是離線那條）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from server.agents import homework, orchestrator, report  # noqa: E402

# ---------------------------------------------------------------------------
# 常數（與 AGENTCORE_RESOURCES.md 記錄的現行值一致）
# ---------------------------------------------------------------------------

DEFAULT_REGION = "ap-southeast-1"   # AgentCore + Bedrock 配額都有、離台灣最近

ROLE_NAME = "TalkyBuddyAgentCoreExecution"
INLINE_POLICY_NAME = "TalkyBuddyAgentCoreRuntime"
MEMORY_NAME = "TalkyBuddyStudentMemory"

# 事件保留天數（API 限制 3–365）。demo 用不到長期保存，取下限以縮小個資暴露窗口。
MEMORY_EXPIRY_DAYS = 30

# 帳號確實有的模型。放行後要換成 Sonnet 5 只需改這裡再跑一次（冪等會走 update）。
MODEL_ID = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"

# 執行上限——**必須顯式設定**。官方明列這三個是成本與濫用護欄；
# 微 VM 每次呼叫都帶 shell 存取，不設上限等於開放資源耗盡。
MAX_ITERATIONS = 3
TIMEOUT_SECONDS = 60

HARNESSES = [
    ("TalkyBuddyOrchestrator", "AGENTCORE_HARNESS_ORCHESTRATOR",
     orchestrator._SYSTEM_PROMPT, 512),
    ("TalkyBuddyHomework", "AGENTCORE_HARNESS_HOMEWORK",
     homework._SYSTEM_PROMPT, 1024),
    ("TalkyBuddyReport", "AGENTCORE_HARNESS_REPORT",
     report._SYSTEM_PROMPT, 2048),
]


# ---------------------------------------------------------------------------
# 請求組裝（純函式，好測）
# ---------------------------------------------------------------------------

def trust_policy(account_id: str, region: str) -> dict:
    """執行角色的信任政策，**含 confused-deputy 兩個條件**。

    少了 Condition 的話，*任何* AWS 帳號的 harness 都能假冒服務主體 assume
    這個角色，用我們的權限打 Bedrock 與讀 Memory。這是 2026-07-26 官方安全
    稽核抓到的第一個缺陷，不可省略。
    """
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {
                "StringEquals": {"aws:SourceAccount": account_id},
                "ArnLike": {
                    "aws:SourceArn":
                        f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"
                },
            },
        }],
    }


def inline_policy(account_id: str, region: str) -> dict:
    """執行角色的權限：Bedrock 模型呼叫 + AgentCore Memory 存取。

    模型權限收斂到 ``anthropic.claude-*``（稽核缺陷 2）。
    Memory 那組是必要的——``AmazonBedrockFullAccess`` **不涵蓋**
    ``bedrock-agentcore:*``，這是實測踩到的坑。
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeAnthropicModels",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": [
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
                    f"arn:aws:bedrock:{region}:{account_id}:inference-profile/*anthropic.claude-*",
                ],
            },
            {
                "Sid": "AgentCoreMemoryAccess",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:RetrieveMemoryRecords",
                    "bedrock-agentcore:ListMemoryRecords",
                ],
                "Resource": f"arn:aws:bedrock-agentcore:{region}:{account_id}:memory/*",
            },
        ],
    }


def memory_request() -> dict:
    """CreateMemory 請求。

    ⚠️ summarization 策略的 namespace **必須**含 ``{sessionId}``，
    否則 CreateMemory 回 ValidationException。這是實測得知，文件未強調。
    """
    return {
        "name": MEMORY_NAME,
        "description": "TalkyBuddy 學生長期記憶：興趣、已掌握詞彙、重複錯誤、每場摘要",
        "eventExpiryDuration": MEMORY_EXPIRY_DAYS,
        "memoryStrategies": [
            {"semanticMemoryStrategy": {
                "name": "StudentSemantic",
                "description": "孩子的興趣、已掌握詞彙、重複出現的錯誤",
                "namespaces": ["/student/{actorId}/semantic"],
            }},
            {"summaryMemoryStrategy": {
                "name": "SessionSummary",
                "description": "每次練習的摘要",
                "namespaces": ["/student/{actorId}/session/{sessionId}/summary"],
            }},
        ],
    }


def harness_request(name: str, role_arn: str, system_prompt: str,
                    max_tokens: int, memory_arn: str | None,
                    skill_s3_uri: str | None = None) -> dict:
    """CreateHarness / UpdateHarness 的共用請求體。

    ⚠️ ``update_harness`` **不是 patch 語意**：只傳部分欄位會讓其他欄位掉回
    預設（本專案曾被它把 maxTokens 靜默重置成 None）。所以建立與更新共用
    同一個完整請求體，不做「只傳有變的欄位」那種最佳化。
    """
    req: dict = {
        "harnessName": name,
        "executionRoleArn": role_arn,
        "systemPrompt": [{"text": system_prompt}],
        "model": {"bedrockModelConfig": {
            "modelId": MODEL_ID,
            "maxTokens": max_tokens,
            "apiFormat": "converse_stream",
        }},
        "maxIterations": MAX_ITERATIONS,
        "maxTokens": max_tokens,
        "timeoutSeconds": TIMEOUT_SECONDS,
    }
    if memory_arn:
        req["memory"] = {"agentCoreMemoryConfiguration": {"arn": memory_arn}}
    if skill_s3_uri:
        # skill 內容（含它帶的腳本）會被當成**可信輸入**注入 agent context，
        # 而且沒有 IAM condition key 能限制 per-invocation 的 skills 欄位。
        # bucket 要當程式碼管：限制寫入權限、開版本控制。
        req["skills"] = [{"s3": {"uri": skill_s3_uri}}]
    return req


# ---------------------------------------------------------------------------
# 形狀驗證（不需憑證，用 botocore 自己的 service model）
# ---------------------------------------------------------------------------

def validate_shape(service: str, operation: str, request: dict) -> list[str]:
    """用 botocore 的 service model 驗請求；回傳問題清單（空 = 通過）。

    這一步是這支腳本最重要的部分：它讓「API 形狀漂移」在**本機**當場失敗，
    而不是在決賽現場拿到一句 ValidationException。
    """
    import botocore.session

    model = botocore.session.get_session().get_service_model(service)
    shape = model.operation_model(operation).input_shape
    problems: list[str] = []

    def walk(sh, value, path: str) -> None:
        if sh.type_name == "structure":
            if not isinstance(value, dict):
                problems.append(f"{path}: 應為 dict，實得 {type(value).__name__}")
                return
            for key in value:
                if key not in sh.members:
                    problems.append(f"{path}.{key}: API 沒有這個欄位")
                    continue
                walk(sh.members[key], value[key], f"{path}.{key}")
            for req in sh.required_members:
                if req not in value:
                    problems.append(f"{path}.{req}: 必填欄位缺漏")
        elif sh.type_name == "list":
            if not isinstance(value, list):
                problems.append(f"{path}: 應為 list，實得 {type(value).__name__}")
                return
            for i, item in enumerate(value):
                walk(sh.member, item, f"{path}[{i}]")
        elif sh.type_name == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                problems.append(f"{path}: 應為 integer")
                return
            meta = getattr(sh, "metadata", {}) or {}
            lo, hi = meta.get("min"), meta.get("max")
            if lo is not None and value < lo:
                problems.append(f"{path}: {value} 小於下限 {lo}")
            if hi is not None and value > hi:
                problems.append(f"{path}: {value} 超過上限 {hi}")
        elif sh.type_name == "string":
            if not isinstance(value, str):
                problems.append(f"{path}: 應為 string，實得 {type(value).__name__}")
        # float / map / blob 交給 botocore 送出時驗，這裡不重複實作

    walk(shape, request, operation)
    return problems


# ---------------------------------------------------------------------------
# 實際佈建（冪等）
# ---------------------------------------------------------------------------

def _ensure_role(iam, account_id: str, region: str, apply: bool) -> str:
    role_arn = f"arn:aws:iam::{account_id}:role/{ROLE_NAME}"
    trust = trust_policy(account_id, region)
    policy = inline_policy(account_id, region)

    if not apply:
        print(f"  [dry-run] IAM role {ROLE_NAME}")
        print(f"            trust: {json.dumps(trust, ensure_ascii=False)}")
        return role_arn

    try:
        iam.get_role(RoleName=ROLE_NAME)
        print(f"  已存在 → 更新信任政策：{ROLE_NAME}")
        iam.update_assume_role_policy(
            RoleName=ROLE_NAME, PolicyDocument=json.dumps(trust))
    except iam.exceptions.NoSuchEntityException:
        print(f"  建立 IAM role：{ROLE_NAME}")
        iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="TalkyBuddy AgentCore Harness 執行角色",
        )
    iam.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName=INLINE_POLICY_NAME,
        PolicyDocument=json.dumps(policy),
    )
    print(f"  inline policy 已套用：{INLINE_POLICY_NAME}")
    return role_arn


def _ensure_memory(control, apply: bool) -> str | None:
    req = memory_request()
    problems = validate_shape("bedrock-agentcore-control", "CreateMemory", req)
    if problems:
        raise SystemExit("CreateMemory 形狀不符：\n  " + "\n  ".join(problems))

    if not apply:
        print(f"  [dry-run] Memory {MEMORY_NAME}（形狀通過驗證）")
        return None

    for page in control.get_paginator("list_memories").paginate():
        for item in page.get("memories", []):
            if item.get("id", "").startswith(MEMORY_NAME):
                arn = item.get("arn") or item["id"]
                print(f"  已存在 → 沿用 Memory：{arn}")
                return arn
    resp = control.create_memory(**req)
    arn = resp["memory"]["arn"]
    print(f"  建立 Memory：{arn}")
    return arn


def _ensure_harness(control, name: str, role_arn: str, system_prompt: str,
                    max_tokens: int, memory_arn: str | None,
                    skill_s3_uri: str | None, apply: bool) -> str | None:
    req = harness_request(name, role_arn, system_prompt, max_tokens,
                          memory_arn, skill_s3_uri)
    problems = validate_shape("bedrock-agentcore-control", "CreateHarness", req)
    if problems:
        raise SystemExit(f"CreateHarness({name}) 形狀不符：\n  " + "\n  ".join(problems))

    if not apply:
        print(f"  [dry-run] Harness {name}（maxTokens={max_tokens}，形狀通過驗證）")
        return None

    existing = None
    for page in control.get_paginator("list_harnesses").paginate():
        for item in page.get("harnesses", []):
            if item.get("harnessName") == name or item.get("harnessId", "").startswith(name):
                existing = item
                break
    if existing:
        hid = existing.get("harnessId") or existing["harnessName"]
        print(f"  已存在 → 更新 Harness：{hid}（完整重傳所有欄位）")
        update = dict(req)
        update.pop("harnessName", None)
        control.update_harness(harnessId=hid, **update)
        arn = existing.get("harnessArn")
    else:
        resp = control.create_harness(**req)
        arn = resp["harness"]["harnessArn"]
        hid = resp["harness"]["harnessId"]
        print(f"  建立 Harness：{arn}")

    # 回讀驗證：update_harness 不是 patch 語意，必須確認上限沒被重置
    got = control.get_harness(harnessId=hid)["harness"]
    for field, expect in (("maxTokens", max_tokens),
                          ("maxIterations", MAX_ITERATIONS),
                          ("timeoutSeconds", TIMEOUT_SECONDS)):
        if got.get(field) != expect:
            raise SystemExit(
                f"{name} 的 {field} 回讀為 {got.get(field)}，預期 {expect}"
                "——update_harness 又把欄位重置了，不要繼續。"
            )
    print(f"    回讀確認：maxTokens={got.get('maxTokens')} "
          f"maxIterations={got.get('maxIterations')} "
          f"timeoutSeconds={got.get('timeoutSeconds')} status={got.get('status')}")
    return arn


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="真的建立資源（預設只做形狀驗證與 dry-run）")
    ap.add_argument("--region", default=DEFAULT_REGION,
                    help=f"AgentCore region（預設 {DEFAULT_REGION}）")
    ap.add_argument("--skill-s3-uri", default=None,
                    help="掛上共用 skill 的 S3 位置，例如 s3://bucket/skills/taiwan-elementary-english/")
    args = ap.parse_args()

    print("=== TalkyBuddy AgentCore 佈建 ===")
    print(f"region: {args.region}　模式: {'APPLY（會真的建立）' if args.apply else 'dry-run（不連 AWS）'}\n")

    if args.apply:
        import boto3
        sts = boto3.client("sts", region_name=args.region)
        account_id = sts.get_caller_identity()["Account"]
        iam = boto3.client("iam", region_name=args.region)
        control = boto3.client("bedrock-agentcore-control", region_name=args.region)
        print(f"帳號: {account_id}\n")
    else:
        account_id = "000000000000"
        iam = control = None

    print("[1/3] IAM 執行角色")
    role_arn = _ensure_role(iam, account_id, args.region, args.apply)

    print("\n[2/3] Memory")
    memory_arn = _ensure_memory(control, args.apply)

    print("\n[3/3] Harness ×3")
    arns: dict[str, str | None] = {}
    for name, env_var, prompt, max_tokens in HARNESSES:
        arns[env_var] = _ensure_harness(
            control, name, role_arn, prompt, max_tokens,
            memory_arn, args.skill_s3_uri, args.apply,
        )

    print("\n=== 環境變數 ===")
    if not args.apply:
        print("（dry-run：形狀全部通過驗證。加 --apply 才會真的建立並印出 ARN）")
        return 0
    print(f"export TALKYBUDDY_AGENT_BACKEND=agentcore")
    print(f"export AGENTCORE_REGION={args.region}")
    print(f"export AGENTCORE_MEMORY_ARN={memory_arn}")
    for env_var, arn in arns.items():
        print(f"export {env_var}={arn}")
    print("\n下一步：撥開關後端到端實跑，確認產出的 source 從 rule 變 cloud。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
