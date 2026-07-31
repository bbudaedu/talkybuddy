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


# ---------------------------------------------------------------------------
# _ensure_* 的 --apply 路徑（先前零測試覆蓋）
# ---------------------------------------------------------------------------
#
# 這三個函式是 `--apply` 唯一會走的路，卻一條測試都沒有。它們讀的回應欄位
# 名稱只要有一個錯，決賽現場第一次 `--apply` 就 KeyError——而那是全場最貴
# 的時段。以下用假 client 把它們的回應契約釘住，並用本機 botocore service
# model 交叉驗證欄位名稱確實存在。


class _FakeControl:
    """假 bedrock-agentcore-control client，回應欄位照真實 service model 命名。"""

    def __init__(self, *, harnesses=None, memories=None):
        self._harnesses = harnesses or []
        self._memories = memories or []
        self.calls: list[tuple[str, dict]] = []

    # --- paginator ---
    def get_paginator(self, name):
        data = {"list_harnesses": {"harnesses": self._harnesses},
                "list_memories": {"memories": self._memories}}[name]

        class _P:
            def paginate(self_inner, **kw):
                return [data]
        return _P()

    def create_harness(self, **kw):
        self.calls.append(("create_harness", kw))
        return {"harness": {
            "harnessId": "HARNESS-NEW",
            "harnessName": kw["harnessName"],
            # 真實欄位名稱是 arn，不是 harnessArn
            "arn": "arn:aws:bedrock-agentcore:us-west-2:1:harness/HARNESS-NEW",
            "status": "CREATING",
        }}

    def update_harness(self, **kw):
        self.calls.append(("update_harness", kw))
        return {"harness": {"harnessId": kw["harnessId"]}}

    def get_harness(self, **kw):
        self.calls.append(("get_harness", kw))
        return {"harness": {
            "harnessId": kw["harnessId"],
            "maxTokens": self._expect_max_tokens,
            "maxIterations": prov.MAX_ITERATIONS,
            "timeoutSeconds": prov.TIMEOUT_SECONDS,
            "status": "READY",
        }}

    def create_memory(self, **kw):
        self.calls.append(("create_memory", kw))
        return {"memory": {"arn": "arn:aws:bedrock-agentcore:us-west-2:1:memory/M-1",
                           "id": "M-1", "status": "CREATING"}}


def _fake_control(**kw):
    c = _FakeControl(**kw)
    c._expect_max_tokens = 1024
    return c


def test_create_harness_returns_the_arn_field_not_harness_arn():
    """CreateHarness 回的是 harness.arn。讀成 harnessArn 會 KeyError。

    用本機 botocore service model 可離線證明：CreateHarness 的輸出結構
    `harness` 底下有 `arn`，**沒有** `harnessArn`。這條路第一次 --apply
    就會炸，而且是在決賽現場最貴的時段炸。
    """
    control = _fake_control()
    arn = prov._ensure_harness(
        control, "TalkyBuddyHomework", "arn:aws:iam::1:role/R", "sys", 1024,
        None, None, apply=True,
    )
    assert arn == "arn:aws:bedrock-agentcore:us-west-2:1:harness/HARNESS-NEW"


def test_existing_harness_returns_the_arn_field_not_harness_arn():
    """ListHarnesses 的項目同樣是 arn，不是 harnessArn（冪等重跑會走這條）。"""
    control = _fake_control(harnesses=[{
        "harnessId": "HARNESS-OLD",
        "harnessName": "TalkyBuddyHomework",
        "arn": "arn:aws:bedrock-agentcore:us-west-2:1:harness/HARNESS-OLD",
        "status": "READY",
    }])
    arn = prov._ensure_harness(
        control, "TalkyBuddyHomework", "arn:aws:iam::1:role/R", "sys", 1024,
        None, None, apply=True,
    )
    assert arn == "arn:aws:bedrock-agentcore:us-west-2:1:harness/HARNESS-OLD"
    assert [c[0] for c in control.calls] == ["update_harness", "get_harness"]


def test_harness_response_field_names_exist_in_the_service_model():
    """把上面兩條假 client 的欄位名稱釘到真實 service model 上。

    假 client 寫錯就等於沒測——這條測試防的正是那個。
    """
    botocore_session = pytest.importorskip("botocore.session")
    model = botocore_session.get_session().get_service_model(
        "bedrock-agentcore-control")

    created = model.operation_model("CreateHarness").output_shape.members["harness"]
    assert "arn" in created.members
    assert "harnessArn" not in created.members, \
        "CreateHarness 回的是 arn；harnessArn 是想像出來的欄位"

    listed = model.operation_model("ListHarnesses").output_shape.members["harnesses"]
    assert "arn" in listed.member.members
    assert "harnessArn" not in listed.member.members

    created_mem = model.operation_model("CreateMemory").output_shape.members["memory"]
    assert "arn" in created_mem.members


def test_ensure_memory_returns_the_arn(monkeypatch):
    control = _fake_control()
    arn = prov._ensure_memory(control, apply=True)
    assert arn == "arn:aws:bedrock-agentcore:us-west-2:1:memory/M-1"


def test_ensure_memory_reuses_an_existing_one():
    """冪等：已存在就沿用，不重複建立（重跑不該長出第二份學生記憶）。"""
    control = _fake_control(memories=[
        {"id": f"{prov.MEMORY_NAME}-abc123",
         "arn": "arn:aws:bedrock-agentcore:us-west-2:1:memory/EXISTING",
         "status": "ACTIVE"},
    ])
    arn = prov._ensure_memory(control, apply=True)
    assert arn == "arn:aws:bedrock-agentcore:us-west-2:1:memory/EXISTING"
    assert control.calls == [], "已存在時不得呼叫 create_memory"


class _FakeIam:
    class exceptions:
        class NoSuchEntityException(Exception):
            pass

    def __init__(self, exists: bool):
        self._exists = exists
        self.calls: list[tuple[str, dict]] = []

    def get_role(self, **kw):
        self.calls.append(("get_role", kw))
        if not self._exists:
            raise self.exceptions.NoSuchEntityException()
        return {"Role": {"Arn": "arn:aws:iam::1:role/R"}}

    def update_assume_role_policy(self, **kw):
        self.calls.append(("update_assume_role_policy", kw))

    def create_role(self, **kw):
        self.calls.append(("create_role", kw))

    def put_role_policy(self, **kw):
        self.calls.append(("put_role_policy", kw))


@pytest.mark.parametrize("exists", [False, True])
def test_ensure_role_always_puts_the_inline_policy(exists):
    """新建或既有都必須套 inline policy——漏掉的症狀是
    「CreateHarness 成功、狀態 READY、Invoke 起不來」，最難查的那種。"""
    iam = _FakeIam(exists)
    arn = prov._ensure_role(iam, _ACCOUNT, _REGION, apply=True)
    assert arn == f"arn:aws:iam::{_ACCOUNT}:role/{prov.ROLE_NAME}"
    names = [c[0] for c in iam.calls]
    assert "put_role_policy" in names
    assert ("update_assume_role_policy" in names) is exists
    assert ("create_role" in names) is not exists


# ---------------------------------------------------------------------------
# 執行角色權限：官方 harness-security 範例的必要項
# ---------------------------------------------------------------------------
#
# 來源：https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-security.html
# 「Sample execution role policy」。少了這些，CreateHarness 會成功、狀態會
# 變 READY，但 InvokeHarness 起不來——因為 harness 是在自己的 microVM 裡
# 開機的，它要先從 ECR Public 拉管理容器、要能寫 log。
# 症狀與「模型沒開通」「region 不對」長得一模一樣，現場分不出來。


def _actions(policy: dict) -> set[str]:
    out: set[str] = set()
    for st in policy["Statement"]:
        act = st["Action"]
        out.update([act] if isinstance(act, str) else act)
    return out


def test_inline_policy_allows_pulling_the_managed_container_image():
    """harness 每個 session 都要從 ECR Public 拉管理容器映像。"""
    acts = _actions(prov.inline_policy(_ACCOUNT, _REGION))
    assert "ecr-public:GetAuthorizationToken" in acts
    assert "sts:GetServiceBearerToken" in acts, "ECR Public 拉取要靠這個換 token"


def test_inline_policy_allows_writing_its_own_logs():
    """沒有 logs 權限時，失敗現場連 log 都沒有，等於瞎著除錯。"""
    acts = _actions(prov.inline_policy(_ACCOUNT, _REGION))
    for a in ("logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents",
              "logs:DescribeLogGroups", "logs:DescribeLogStreams"):
        assert a in acts, a


def test_inline_policy_allows_tracing_and_metrics():
    acts = _actions(prov.inline_policy(_ACCOUNT, _REGION))
    for a in ("xray:PutTraceSegments", "xray:PutTelemetryRecords",
              "xray:GetSamplingRules", "xray:GetSamplingTargets",
              "cloudwatch:PutMetricData"):
        assert a in acts, a


def test_inline_policy_allows_workload_identity():
    """GetWorkloadAccessToken 是 harness 取得自身工作負載身分用的。"""
    acts = _actions(prov.inline_policy(_ACCOUNT, _REGION))
    assert "bedrock-agentcore:GetWorkloadAccessToken" in acts


def test_metric_publishing_is_scoped_to_the_agentcore_namespace():
    """cloudwatch:PutMetricData 是 Resource "*"，官方範例用 namespace 條件收斂。"""
    for st in prov.inline_policy(_ACCOUNT, _REGION)["Statement"]:
        act = st["Action"]
        acts = [act] if isinstance(act, str) else act
        if "cloudwatch:PutMetricData" in acts:
            ns = st.get("Condition", {}).get("StringEquals", {})
            assert ns.get("cloudwatch:namespace") == "bedrock-agentcore"
            return
    pytest.fail("找不到 cloudwatch:PutMetricData 的 statement")


def test_model_permission_is_still_not_a_wildcard():
    """補權限不得順手把模型資源放寬成 *（既有稽核缺陷 2 的回歸防線）。"""
    for st in prov.inline_policy(_ACCOUNT, _REGION)["Statement"]:
        act = st["Action"]
        acts = [act] if isinstance(act, str) else act
        if any(a.startswith("bedrock:InvokeModel") for a in acts):
            res = st["Resource"]
            res = [res] if isinstance(res, str) else res
            assert "*" not in res
            assert all("anthropic.claude-" in r for r in res), res


# ---------------------------------------------------------------------------
# region：競賽環境規範第 6 條指定 us-west-2
# ---------------------------------------------------------------------------

def test_default_region_is_us_west_2():
    """規範第 6 條指定 us-west-2，且官方 region 表列 AgentCore harness 在
    US West (Oregon) 可用。預設值留在新加坡會讓現場的人照預設跑下去。"""
    assert prov.DEFAULT_REGION == "us-west-2"
