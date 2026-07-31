# -*- coding: utf-8 -*-
"""test_aws_preflight_agentcore.py — preflight 的 AgentCore 判定邏輯。

`scripts/aws_preflight.py` 是「上台前 60 秒」跑的那一支，先前 grep agentcore
**零命中**——也就是說「08:05 跑 preflight」驗不到主線上的 AgentCore。

本檔只測判定邏輯（純函式，不觸網、不需憑證）。實際的 API 呼叫由 8/1 現場
帶憑證跑，這裡確保的是「同樣的鏈況一定得到同樣的結論」。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "aws_preflight", _ROOT / "scripts" / "aws_preflight.py"
)
pf = importlib.util.module_from_spec(_spec)
sys.modules["aws_preflight"] = pf
_spec.loader.exec_module(pf)


def _levels(checks) -> list[str]:
    return [level for level, _ in checks]


def test_full_chain_on_all_four_roles_is_ok():
    chains = {r: ["agentcore", "bedrock", "rule"]
              for r in ("orchestrator", "homework", "report", "material")}
    checks = pf.agentcore_checks(chains, flag_on=True)
    assert _levels(checks) == ["ok", "ok", "ok", "ok"]


def test_flag_off_is_a_warning_not_a_failure():
    """AgentCore 是加分項。沒撥開關不該讓 preflight 判定失敗——
    降級鏈本來就設計成「沒有它也完整」。"""
    chains = {r: ["bedrock", "rule"]
              for r in ("orchestrator", "homework", "report", "material")}
    checks = pf.agentcore_checks(chains, flag_on=False)
    assert _levels(checks) == ["warn", "warn", "warn", "warn"]
    assert all("加分" in msg or "未啟用" in msg for _, msg in checks)


def test_flag_on_but_a_role_missing_its_harness_arn_is_a_failure():
    """撥了開關卻漏設某個角色的 harness ARN——現場最容易犯、最難察覺的錯。

    開關看起來撥了，那個角色其實整個沒走 AgentCore，而且沒有任何東西會報錯。
    preflight 必須把它判成失敗，不是警告。
    """
    chains = {
        "orchestrator": ["agentcore", "bedrock", "rule"],
        "homework": ["agentcore", "bedrock", "rule"],
        "report": ["bedrock", "rule"],          # 漏設 AGENTCORE_HARNESS_REPORT
        "material": ["agentcore", "bedrock", "rule"],
    }
    checks = pf.agentcore_checks(chains, flag_on=True)
    assert _levels(checks) == ["ok", "ok", "bad", "ok"]
    assert "AGENTCORE_HARNESS_REPORT" in checks[2][1], checks[2][1]


def test_missing_bedrock_layer_is_a_failure_even_when_agentcore_is_on():
    """第二層不見了 = AgentCore 一失敗就直接摔到規則式。

    這正是先前那個缺陷造成的形狀。開了 AgentCore 卻沒設 Bedrock，
    品質下界比什麼都不開還低——preflight 要在現場就把它擋下來。
    """
    chains = {r: ["agentcore", "rule"]
              for r in ("orchestrator", "homework", "report", "material")}
    checks = pf.agentcore_checks(chains, flag_on=True)
    assert _levels(checks) == ["bad", "bad", "bad", "bad"]
    assert all("Bedrock" in msg for _, msg in checks)


def test_rule_only_chain_is_reported_when_flag_off():
    """完全沒有雲端：誠實回報，但不是 AgentCore 段該判失敗的事
    （Bedrock 本身有第①③④段在管）。"""
    chains = {r: ["rule"] for r in ("orchestrator", "homework", "report", "material")}
    checks = pf.agentcore_checks(chains, flag_on=False)
    assert _levels(checks) == ["warn", "warn", "warn", "warn"]


def test_every_role_gets_exactly_one_check():
    """不漏報也不重複——四個 agent 各一行，現場一眼掃完。"""
    chains = {r: ["bedrock", "rule"]
              for r in ("orchestrator", "homework", "report", "material")}
    checks = pf.agentcore_checks(chains, flag_on=False)
    assert len(checks) == 4


def test_messages_name_the_role_so_you_know_which_one_to_fix():
    chains = {
        "orchestrator": ["agentcore", "bedrock", "rule"],
        "homework": ["bedrock", "rule"],
        "report": ["agentcore", "bedrock", "rule"],
        "material": ["agentcore", "bedrock", "rule"],
    }
    checks = pf.agentcore_checks(chains, flag_on=True)
    bad = [msg for lvl, msg in checks if lvl == "bad"]
    assert len(bad) == 1
    assert "homework" in bad[0]


def test_checks_use_the_same_chain_source_as_the_agents():
    """preflight 必須用 server.agent_backends.chain()，不得自己重寫一遍判斷。

    一個會漂移的 preflight 比沒有 preflight 更糟：它會言之鑿鑿地報告
    一條實際上不成立的鏈，而現場的人會相信它。
    """
    import ast

    code = ast.unparse(ast.parse(
        (_ROOT / "scripts" / "aws_preflight.py").read_text(encoding="utf-8")))
    assert "agent_backends.chain(" in code
    assert "agentcore.resolve_config(" not in code, \
        "preflight 不得自己解析 AgentCore 設定，要走 agent_backends"


@pytest.mark.parametrize("flag_on", [True, False])
def test_never_raises_on_unexpected_chain_values(flag_on):
    """鏈的內容若因日後改動變了樣，preflight 也不該炸——
    上台前 60 秒跑的東西，最不能做的事就是自己拋例外。"""
    # 刻意漏掉 "material"：agentcore_checks 對缺鍵的角色要當空鏈處理，不炸。
    chains = {"orchestrator": [], "homework": ["???"], "report": ["rule"]}
    checks = pf.agentcore_checks(chains, flag_on=flag_on)
    assert len(checks) == 4
    assert all(lvl in ("ok", "warn", "bad") for lvl, _ in checks)
