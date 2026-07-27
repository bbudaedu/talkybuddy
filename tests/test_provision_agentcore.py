# -*- coding: utf-8 -*-
"""test_provision_agentcore.py — 佈建腳本的請求組裝與形狀驗證。

決賽當天若用主辦方的 AWS 資源，現有的 ARN（帳號綁定）全部失效，
必須在現場重建。這支腳本把那條路徑變成一行指令——所以它自己不能出錯。

這些測試驗兩件事：
1. **請求形狀對得上真實 API**（用 botocore 自己的 service model，不是我記得的欄位）
2. **安全條件沒有被誰順手拿掉**——confused-deputy 的兩個 Condition、
   收斂過的模型資源、三個執行上限
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "provision_agentcore", _ROOT / "deploy" / "aws" / "provision_agentcore.py"
)
prov = importlib.util.module_from_spec(_spec)
sys.modules["provision_agentcore"] = prov
_spec.loader.exec_module(prov)

_ACCOUNT = "123456789012"
_REGION = "ap-southeast-1"


# ---------------------------------------------------------------------------
# 形狀：對得上真實 API
# ---------------------------------------------------------------------------

def test_memory_request_matches_the_real_api_shape():
    problems = prov.validate_shape(
        "bedrock-agentcore-control", "CreateMemory", prov.memory_request())
    assert problems == [], problems


@pytest.mark.parametrize("name,_env,prompt,max_tokens", prov.HARNESSES)
def test_harness_request_matches_the_real_api_shape(name, _env, prompt, max_tokens):
    req = prov.harness_request(
        name, f"arn:aws:iam::{_ACCOUNT}:role/r", prompt, max_tokens,
        memory_arn=f"arn:aws:bedrock-agentcore:{_REGION}:{_ACCOUNT}:memory/m",
        skill_s3_uri="s3://bucket/skills/taiwan-elementary-english/",
    )
    problems = prov.validate_shape("bedrock-agentcore-control", "CreateHarness", req)
    assert problems == [], problems


def test_validate_shape_actually_catches_a_bad_field():
    """驗證器本身要有牙——不然上面兩條測試等於沒驗。"""
    bad = dict(prov.memory_request(), notARealField="x")
    assert prov.validate_shape("bedrock-agentcore-control", "CreateMemory", bad)

    bad2 = dict(prov.memory_request(), eventExpiryDuration=99999)  # API 上限 365
    assert prov.validate_shape("bedrock-agentcore-control", "CreateMemory", bad2)

    bad3 = dict(prov.memory_request())
    del bad3["name"]  # 必填
    assert prov.validate_shape("bedrock-agentcore-control", "CreateMemory", bad3)


# ---------------------------------------------------------------------------
# 安全：稽核抓到的三個缺陷不得復發
# ---------------------------------------------------------------------------

def test_trust_policy_has_both_confused_deputy_conditions():
    """少了 Condition，**任何** AWS 帳號的 harness 都能假冒服務主體 assume
    這個角色，用我們的權限打 Bedrock 與讀 Memory。"""
    stmt = prov.trust_policy(_ACCOUNT, _REGION)["Statement"][0]
    cond = stmt["Condition"]
    assert cond["StringEquals"]["aws:SourceAccount"] == _ACCOUNT
    assert cond["ArnLike"]["aws:SourceArn"].startswith(
        f"arn:aws:bedrock-agentcore:{_REGION}:{_ACCOUNT}:")
    assert stmt["Principal"]["Service"] == "bedrock-agentcore.amazonaws.com"


def test_model_permission_is_not_a_wildcard():
    """稽核缺陷 2：模型權限不得是 Resource: "*"。"""
    doc = prov.inline_policy(_ACCOUNT, _REGION)
    invoke = [s for s in doc["Statement"] if "bedrock:InvokeModel" in s["Action"]][0]
    assert "*" not in invoke["Resource"], invoke["Resource"]
    assert all("anthropic.claude-" in r for r in invoke["Resource"]), invoke["Resource"]


def test_memory_permissions_are_present():
    """AmazonBedrockFullAccess **不涵蓋** bedrock-agentcore:*，必須自己補。"""
    doc = prov.inline_policy(_ACCOUNT, _REGION)
    actions = {a for s in doc["Statement"] for a in s["Action"]}
    for needed in ("bedrock-agentcore:CreateEvent",
                   "bedrock-agentcore:ListEvents",
                   "bedrock-agentcore:RetrieveMemoryRecords"):
        assert needed in actions, f"缺 {needed}"


@pytest.mark.parametrize("name,_env,prompt,max_tokens", prov.HARNESSES)
def test_execution_limits_are_always_explicit(name, _env, prompt, max_tokens):
    """三個執行上限必須每次都顯式傳。

    update_harness 不是 patch 語意——只傳部分欄位會讓其他欄位掉回預設，
    本專案曾被它把 maxTokens 靜默重置成 None。
    """
    req = prov.harness_request(name, "arn:aws:iam::1:role/r", prompt, max_tokens, None)
    assert req["maxTokens"] == max_tokens
    assert req["maxIterations"] == prov.MAX_ITERATIONS
    assert req["timeoutSeconds"] == prov.TIMEOUT_SECONDS
    assert req["model"]["bedrockModelConfig"]["maxTokens"] == max_tokens


def test_summary_namespace_contains_session_id():
    """summarization 策略的 namespace **必須**含 {sessionId}，
    否則 CreateMemory 回 ValidationException（實測得知，文件未強調）。"""
    strategies = prov.memory_request()["memoryStrategies"]
    summary = [s["summaryMemoryStrategy"] for s in strategies if "summaryMemoryStrategy" in s][0]
    assert any("{sessionId}" in ns for ns in summary["namespaces"]), summary


def test_semantic_namespace_is_scoped_per_student():
    """語意記憶必須依 actorId 分群，否則所有孩子共用同一份長期記憶。"""
    strategies = prov.memory_request()["memoryStrategies"]
    semantic = [s["semanticMemoryStrategy"] for s in strategies
                if "semanticMemoryStrategy" in s][0]
    assert all("{actorId}" in ns for ns in semantic["namespaces"]), semantic


# ---------------------------------------------------------------------------
# 單一真相來源：system prompt 不得在腳本裡重抄
# ---------------------------------------------------------------------------

def test_system_prompts_come_from_the_agent_modules():
    """抄一份的話，改了程式碼卻忘了更新 harness，雲端與離線會給出不同的
    東西，而且測試抓不到（測試跑的是離線那條）。"""
    from server.agents import homework, orchestrator, report

    by_name = {n: p for n, _e, p, _m in prov.HARNESSES}
    assert by_name["TalkyBuddyOrchestrator"] is orchestrator._SYSTEM_PROMPT
    assert by_name["TalkyBuddyHomework"] is homework._SYSTEM_PROMPT
    assert by_name["TalkyBuddyReport"] is report._SYSTEM_PROMPT


def test_skill_is_optional_and_uses_the_s3_variant():
    without = prov.harness_request("H", "arn:aws:iam::1:role/r", "p", 512, None)
    assert "skills" not in without

    with_skill = prov.harness_request(
        "H", "arn:aws:iam::1:role/r", "p", 512, None,
        skill_s3_uri="s3://b/skills/taiwan-elementary-english/")
    assert with_skill["skills"] == [{"s3": {"uri": "s3://b/skills/taiwan-elementary-english/"}}]


def test_dry_run_needs_no_credentials(monkeypatch, capsys):
    """dry-run 必須在沒有 AWS 憑證的機器上跑得完——這是它的全部價值。"""
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_PROFILE",
                "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sys, "argv", ["provision_agentcore.py"])

    assert prov.main() == 0
    out = capsys.readouterr().out
    assert "形狀全部通過驗證" in out
    assert "dry-run" in out
