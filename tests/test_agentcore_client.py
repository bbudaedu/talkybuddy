# -*- coding: utf-8 -*-
"""test_agentcore_client.py — AgentCore Harness 客戶端封裝。

三個 agent 改由 AgentCore Harness 執行後，共用這一層封裝。它負責：
harness ARN 解析、session 管理、InvokeHarness 呼叫、回應取文字。

與 bedrock_converse 平行：那層是「直接呼叫模型」，這層是「呼叫託管的 agent 迴圈」。
全程 monkeypatch client factory、不觸網、不需 AWS 憑證。
"""

from __future__ import annotations

import pytest

from server import agentcore

_ALL_ENV = [
    "TALKYBUDDY_AGENT_BACKEND",
    "AGENTCORE_REGION",
    "AGENTCORE_HARNESS_ORCHESTRATOR",
    "AGENTCORE_HARNESS_HOMEWORK",
    "AGENTCORE_HARNESS_REPORT",
    "AGENTCORE_MEMORY_ARN",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ALL_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class _FakeClient:
    """假 bedrock-agentcore client：記下 invoke_harness 參數並回傳預設 payload。"""

    def __init__(self, payload):
        self._payload = payload
        self.captured: dict = {}

    def invoke_harness(self, **kwargs):
        self.captured = kwargs
        return self._payload


def _ok(text: str) -> dict:
    return {"output": {"message": {"role": "assistant", "content": [{"text": text}]}}}


# ---------------------------------------------------------------------------
# resolve_config
# ---------------------------------------------------------------------------

def test_disabled_by_default(_clean_env):
    """未選用 agentcore 後端時回 None，既有路徑零影響。"""
    assert agentcore.resolve_config("homework") is None


def test_enabled_when_backend_selected(_clean_env):
    _clean_env.setenv("TALKYBUDDY_AGENT_BACKEND", "agentcore")
    _clean_env.setenv("AGENTCORE_HARNESS_HOMEWORK", "arn:aws:...:harness/HW-1")
    cfg = agentcore.resolve_config("homework")
    assert cfg is not None
    assert cfg["harness_arn"] == "arn:aws:...:harness/HW-1"


def test_region_defaults_to_singapore(_clean_env):
    """AgentCore 不在台北提供服務（2026-07-26 實測 endpoint 不存在），
    新加坡是同時具備 AgentCore 與滿額 Bedrock 配額、且離台灣最近的 region。"""
    _clean_env.setenv("TALKYBUDDY_AGENT_BACKEND", "agentcore")
    _clean_env.setenv("AGENTCORE_HARNESS_HOMEWORK", "arn:x")
    assert agentcore.DEFAULT_REGION == "ap-southeast-1"
    assert agentcore.resolve_config("homework")["region"] == "ap-southeast-1"


def test_missing_harness_arn_returns_none(_clean_env):
    """選了 agentcore 後端但沒設該角色的 harness ARN → 回 None 讓呼叫端降級，
    不可拿空字串去打 API 拿一個難懂的錯誤。"""
    _clean_env.setenv("TALKYBUDDY_AGENT_BACKEND", "agentcore")
    assert agentcore.resolve_config("homework") is None


def test_case_insensitive_backend_value(_clean_env):
    _clean_env.setenv("TALKYBUDDY_AGENT_BACKEND", "  AgentCore ")
    _clean_env.setenv("AGENTCORE_HARNESS_REPORT", "arn:y")
    assert agentcore.resolve_config("report") is not None


def test_unknown_role_returns_none(_clean_env):
    _clean_env.setenv("TALKYBUDDY_AGENT_BACKEND", "agentcore")
    assert agentcore.resolve_config("不存在的角色") is None


# ---------------------------------------------------------------------------
# invoke
# ---------------------------------------------------------------------------

def test_invoke_sends_message_and_returns_text(_clean_env, monkeypatch):
    fake = _FakeClient(_ok('{"focus":"文法"}'))
    monkeypatch.setattr(agentcore, "_build_client", lambda region, timeout_s: fake)

    cfg = {"region": "ap-southeast-1", "harness_arn": "arn:h", "memory_arn": None}
    out = agentcore.invoke(cfg, "學生說：我喜歡蘋果", session_id="sess-1", actor_id="s1")

    assert out == '{"focus":"文法"}'
    assert fake.captured["harnessArn"] == "arn:h"
    # session id 會被補齊到 API 最短長度，但必須看得出原始值（現場查 log 用）
    assert "sess-1" in fake.captured["runtimeSessionId"]
    assert fake.captured["messages"][0]["role"] == "user"


def test_actor_id_is_passed_for_memory_scoping(_clean_env, monkeypatch):
    """actorId 是 Memory 的分群鍵。漏傳會讓所有孩子共用同一份長期記憶。"""
    fake = _FakeClient(_ok("ok"))
    monkeypatch.setattr(agentcore, "_build_client", lambda region, timeout_s: fake)

    agentcore.invoke(
        {"region": "r", "harness_arn": "a", "memory_arn": "m"},
        "hi", session_id="s", actor_id="STUDENT-AMING-004",
    )
    # actorId 必須是雜湊而非明文：同一個值在 prompt 裡會被 deidentify 遮罩，
    # actorId 卻會被 AgentCore Memory 長期保存，明文送上去是自相矛盾的。
    sent = fake.captured["actorId"]
    assert "STUDENT-AMING-004" not in sent, sent
    assert sent == agentcore._hash_actor("STUDENT-AMING-004")


def test_short_session_id_is_padded_to_api_minimum(_clean_env, monkeypatch):
    """InvokeHarness 要求 runtimeSessionId 至少 33 字元（2026-07-26 實測）。

    呼叫端會很自然地傳「turn-12」「STUDENT-AMING-004」這種短字串，
    封裝層必須補齊，否則每個呼叫點都要自己記得這條規則。
    """
    fake = _FakeClient(_ok("ok"))
    monkeypatch.setattr(agentcore, "_build_client", lambda region, timeout_s: fake)

    agentcore.invoke(
        {"region": "r", "harness_arn": "a", "memory_arn": None},
        "hi", session_id="turn-12", actor_id="s1",
    )
    assert len(fake.captured["runtimeSessionId"]) >= 33


def test_same_short_session_id_maps_to_same_padded_id(_clean_env, monkeypatch):
    """補齊必須是決定性的：同一個教學循環的多次呼叫要落在同一個 session，
    Harness 的短期記憶才連貫。若每次補出不同值，記憶就斷了。"""
    seen = []

    def _spy(region, timeout_s):
        c = _FakeClient(_ok("ok"))
        seen.append(c)
        return c

    monkeypatch.setattr(agentcore, "_build_client", _spy)
    cfg = {"region": "r", "harness_arn": "a", "memory_arn": None}
    agentcore.invoke(cfg, "1", session_id="cycle-7", actor_id="s1")
    agentcore.invoke(cfg, "2", session_id="cycle-7", actor_id="s1")

    assert seen[0].captured["runtimeSessionId"] == seen[1].captured["runtimeSessionId"]


def test_different_short_session_ids_do_not_collide(_clean_env, monkeypatch):
    seen = []

    def _spy(region, timeout_s):
        c = _FakeClient(_ok("ok"))
        seen.append(c)
        return c

    monkeypatch.setattr(agentcore, "_build_client", _spy)
    cfg = {"region": "r", "harness_arn": "a", "memory_arn": None}
    agentcore.invoke(cfg, "1", session_id="cycle-7", actor_id="s1")
    agentcore.invoke(cfg, "2", session_id="cycle-8", actor_id="s1")

    assert seen[0].captured["runtimeSessionId"] != seen[1].captured["runtimeSessionId"]


def test_session_id_is_scoped_per_student(_clean_env, monkeypatch):
    """B2：同一個 session key、不同學生，必須落在不同 runtime session。

    呼叫端傳的是「orch-turn-4」「hw-2026-07-20」這種不含學生維度的字串——
    任何孩子跑到第 4 回合都會共用同一個 session，短期記憶跨童串接。
    這不是機率性碰撞，是決定性的、每次都發生。
    """
    seen = []

    def _spy(region, timeout_s):
        c = _FakeClient(_ok("ok"))
        seen.append(c)
        return c

    monkeypatch.setattr(agentcore, "_build_client", _spy)
    cfg = {"region": "r", "harness_arn": "a", "memory_arn": "m"}
    agentcore.invoke(cfg, "1", session_id="orch-turn-4", actor_id="STUDENT-A")
    agentcore.invoke(cfg, "2", session_id="orch-turn-4", actor_id="STUDENT-B")

    assert seen[0].captured["runtimeSessionId"] != seen[1].captured["runtimeSessionId"], \
        "不同學生的同名 session 必須分開"


def test_session_id_stays_stable_for_same_student(_clean_env, monkeypatch):
    """加上學生維度後，同一個孩子的同一個教學循環仍要落在同一 session。"""
    seen = []

    def _spy(region, timeout_s):
        c = _FakeClient(_ok("ok"))
        seen.append(c)
        return c

    monkeypatch.setattr(agentcore, "_build_client", _spy)
    cfg = {"region": "r", "harness_arn": "a", "memory_arn": "m"}
    agentcore.invoke(cfg, "1", session_id="orch-turn-4", actor_id="STUDENT-A")
    agentcore.invoke(cfg, "2", session_id="orch-turn-4", actor_id="STUDENT-A")

    assert seen[0].captured["runtimeSessionId"] == seen[1].captured["runtimeSessionId"]


def test_session_id_does_not_leak_student_id(_clean_env, monkeypatch):
    """學生維度用雜湊帶入：runtimeSessionId 同樣會被雲端保存，不得含明文 id。"""
    fake = _FakeClient(_ok("ok"))
    monkeypatch.setattr(agentcore, "_build_client", lambda region, timeout_s: fake)

    agentcore.invoke(
        {"region": "r", "harness_arn": "a", "memory_arn": "m"},
        "hi", session_id="hw-2026-07-20", actor_id="STUDENT-AMING-004",
    )
    sid = fake.captured["runtimeSessionId"]
    assert "STUDENT-AMING-004" not in sid, sid
    assert "AMING" not in sid, sid
    # 長度仍須符合 API 下限
    assert 33 <= len(sid) <= 64, len(sid)


def test_concatenates_multiple_text_blocks(_clean_env, monkeypatch):
    payload = {"output": {"message": {"content": [{"text": "前"}, {"text": "後"}]}}}
    monkeypatch.setattr(
        agentcore, "_build_client", lambda region, timeout_s: _FakeClient(payload)
    )
    out = agentcore.invoke(
        {"region": "r", "harness_arn": "a", "memory_arn": None}, "u",
        session_id="s", actor_id="s1",
    )
    assert out == "前後"


def test_raises_when_no_text_block(_clean_env, monkeypatch):
    """回應缺 text：明確拋錯讓呼叫端降級，不靜默回空字串。"""
    monkeypatch.setattr(
        agentcore, "_build_client",
        lambda region, timeout_s: _FakeClient({"output": {"message": {"content": []}}}),
    )
    with pytest.raises(agentcore.AgentCoreResponseError):
        agentcore.invoke(
            {"region": "r", "harness_arn": "a", "memory_arn": None}, "u",
            session_id="s", actor_id="s1",
        )


def test_missing_actor_id_raises_instead_of_silently_dropping(_clean_env, monkeypatch):
    """actor_id 為空時必須拋錯，不可靜默略過欄位。

    少了 actorId，InvokeHarness 照樣成功，但所有孩子的長期記憶會混在同一個
    actor 底下——API 不會抱怨、日誌不會留痕，是最難察覺的隱私事故。
    拋例外讓呼叫端降級回規則式，寧可這次不用雲端。
    """
    monkeypatch.setattr(
        agentcore, "_build_client",
        lambda region, timeout_s: pytest.fail("缺 actor_id 時不該送出請求"),
    )
    for bad in (None, ""):
        with pytest.raises(ValueError):
            agentcore.invoke(
                {"region": "r", "harness_arn": "a", "memory_arn": None}, "u",
                session_id="s", actor_id=bad,
            )


def test_module_import_does_not_load_boto3(_clean_env):
    """import 期不得載入 boto3（沿用 bedrock_converse / nova_sonic 慣例，
    保護 edge 端啟動時間——邊緣裝置根本不會用到 AgentCore）。"""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(agentcore.__file__).read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    assert not any(n.startswith("boto") for n in names), names
