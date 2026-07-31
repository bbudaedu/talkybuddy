# -*- coding: utf-8 -*-
"""test_agent_backends.py — 降級鏈的單一真相來源。

三個 agent 各自抄了同一段「解析兩個雲端後端」的程式碼。抄三份的直接後果
已經發生過一次：`cfg = None if ac_cfg else ...` 這個錯在三個檔案裡一模一樣地
存在，改的時候也得改三次。

更麻煩的是 `scripts/aws_preflight.py` 要回報「現在的降級鏈長什麼樣」。
如果它自己再重寫一遍判斷，那就是第四份——**一個會漂移的 preflight
比沒有 preflight 更糟**，因為它會言之鑿鑿地報告一條實際上不成立的鏈。

所以把「哪些後端現在可用、順序是什麼」收斂到這裡，agent 與 preflight 共用。
"""

from __future__ import annotations

import pytest

from server import agent_backends

_ENV = [
    "TALKYBUDDY_AGENT_BACKEND", "TALKYBUDDY_CLOUD_PROVIDER",
    "AGENTCORE_HARNESS_ORCHESTRATOR", "AGENTCORE_HARNESS_HOMEWORK",
    "AGENTCORE_HARNESS_REPORT", "AGENTCORE_HARNESS_MATERIAL", "AGENTCORE_REGION",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for n in _ENV:
        monkeypatch.delenv(n, raising=False)
    return monkeypatch


# ---------------------------------------------------------------------------
# chain：現在實際可用的降級順序
# ---------------------------------------------------------------------------

def test_chain_is_rule_only_when_nothing_is_configured(_clean_env):
    """什麼都沒設 → 只剩規則式。它永遠在鏈尾，是保底。"""
    assert agent_backends.chain("homework") == ["rule"]


def test_chain_has_bedrock_when_only_bedrock_is_configured(_clean_env):
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    assert agent_backends.chain("homework") == ["bedrock", "rule"]


def test_chain_has_all_three_when_both_cloud_backends_are_configured(_clean_env):
    _clean_env.setenv("TALKYBUDDY_AGENT_BACKEND", "agentcore")
    _clean_env.setenv("AGENTCORE_HARNESS_HOMEWORK", "arn:hw")
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    assert agent_backends.chain("homework") == ["agentcore", "bedrock", "rule"]


def test_agentcore_flag_without_the_role_arn_does_not_appear_in_the_chain(_clean_env):
    """撥了 AgentCore 開關卻漏設某個角色的 harness ARN——這條鏈就沒有 agentcore。

    這是現場最容易犯、也最難察覺的設定錯誤：開關看起來撥了，實際上
    那個角色根本沒走 AgentCore。preflight 印出這條鏈就能一眼看出來。
    """
    _clean_env.setenv("TALKYBUDDY_AGENT_BACKEND", "agentcore")
    _clean_env.setenv("AGENTCORE_HARNESS_HOMEWORK", "arn:hw")
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")

    assert agent_backends.chain("homework") == ["agentcore", "bedrock", "rule"]
    assert agent_backends.chain("report") == ["bedrock", "rule"], \
        "report 沒設 harness ARN，不該出現在鏈上"


def test_chain_always_ends_with_rule(_clean_env):
    """保底不可消失——任何設定組合下規則式都必須在鏈尾。"""
    for env in ({}, {"TALKYBUDDY_CLOUD_PROVIDER": "bedrock"},
                {"TALKYBUDDY_AGENT_BACKEND": "agentcore",
                 "AGENTCORE_HARNESS_REPORT": "arn:rp"}):
        for k, v in env.items():
            _clean_env.setenv(k, v)
        assert agent_backends.chain("report")[-1] == "rule"


# ---------------------------------------------------------------------------
# resolve：agent 真正拿來用的兩個設定
# ---------------------------------------------------------------------------

def test_resolve_returns_both_configs_independently(_clean_env):
    """兩個後端**各自**解析。AgentCore 有設定不得把 Bedrock 變成 None——
    那正是先前那個「撥開關反而更糟」缺陷的根因。"""
    _clean_env.setenv("TALKYBUDDY_AGENT_BACKEND", "agentcore")
    _clean_env.setenv("AGENTCORE_HARNESS_HOMEWORK", "arn:hw")
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")

    ac_cfg, cfg = agent_backends.resolve("homework")
    assert ac_cfg is not None
    assert cfg is not None, "AgentCore 有設定時 Bedrock 仍必須解析出來"


def test_resolve_returns_none_none_when_nothing_is_configured(_clean_env):
    assert agent_backends.resolve("homework") == (None, None)


def test_chain_and_resolve_never_disagree(_clean_env):
    """兩個函式必須看同一份事實——不然 preflight 印的鏈跟 agent 走的路不一樣。"""
    _clean_env.setenv("TALKYBUDDY_AGENT_BACKEND", "agentcore")
    _clean_env.setenv("AGENTCORE_HARNESS_ORCHESTRATOR", "arn:or")
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")

    for role in ("orchestrator", "homework", "report"):
        ac_cfg, cfg = agent_backends.resolve(role)
        chain = agent_backends.chain(role)
        assert ("agentcore" in chain) is (ac_cfg is not None), role
        assert ("bedrock" in chain) is (cfg is not None), role


def test_agents_use_this_module_rather_than_resolving_it_themselves():
    """三個 agent 必須真的用這個模組，否則它就只是第四份會漂移的複本。

    這條測試存在的理由：抽共用模組最常見的失敗是「抽了但沒人用」。
    """
    import ast
    import pathlib

    from server.agents import homework, material, orchestrator, report

    for mod in (homework, orchestrator, report, material):
        src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        # 比對**程式碼**而非原始文字：註解裡會提到舊寫法（那是刻意留的說明），
        # 直接 grep 原始碼會咬到自己的註解。ast.unparse 會把註解拿掉。
        code = ast.unparse(ast.parse(src))

        assert "agent_backends.resolve(" in code, f"{mod.__name__} 沒有改用共用解析"
        # 舊寫法不得復活
        assert "None if ac_cfg else" not in code, \
            f"{mod.__name__} 又出現「AgentCore 一設定就把 Bedrock 設成 None」的寫法"
        assert "bedrock_converse.resolve_config(" not in code, \
            f"{mod.__name__} 又自己解析 Bedrock 設定，共用模組就白抽了"


def test_chain_supports_material_role(_clean_env):
    """material 角色比照既有三個 agent，能出現在降級鏈最前面。"""
    _clean_env.setenv("TALKYBUDDY_AGENT_BACKEND", "agentcore")
    _clean_env.setenv("AGENTCORE_HARNESS_MATERIAL", "arn:material")
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    assert agent_backends.chain("material") == ["agentcore", "bedrock", "rule"]
