# -*- coding: utf-8 -*-
"""test_bedrock_converse.py — 原生 AWS Bedrock Converse provider（TCLOUD-02 合規地基）。

涵蓋：預設不啟用（provider 未切換即回 None，既有 relay 行為零變更）；啟用後
region/model_id 解析與 env 覆蓋；converse() 請求形狀（system / messages /
inferenceConfig）與取字；回應格式不符時明確拋錯讓呼叫端 fallback。
全程 monkeypatch client factory、不觸網、不需 AWS 憑證。
"""
from __future__ import annotations

import pytest

from server import bedrock_converse

_ALL_ENV = [
    "TALKYBUDDY_CLOUD_PROVIDER",
    "BEDROCK_REGION",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "BEDROCK_MODEL_ID",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ALL_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class _FakeClient:
    """假 bedrock-runtime client：記下 converse() 參數並回傳預設好的 payload。"""

    def __init__(self, payload):
        self._payload = payload
        self.captured: dict = {}

    def converse(self, **kwargs):
        self.captured = kwargs
        return self._payload


def _ok_payload(text: str) -> dict:
    return {"output": {"message": {"role": "assistant", "content": [{"text": text}]}}}


# ---------------------------------------------------------------------------
# resolve_config
# ---------------------------------------------------------------------------

def test_resolve_config_none_when_provider_not_selected(_clean_env):
    """預設不啟用：未指定 provider 時回 None，既有 anthropic relay 路徑不受影響。"""
    assert bedrock_converse.resolve_config() is None


def test_resolve_config_none_when_provider_is_anthropic(_clean_env):
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "anthropic")
    assert bedrock_converse.resolve_config() is None


def test_resolve_config_defaults_when_provider_is_bedrock(_clean_env):
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    cfg = bedrock_converse.resolve_config()
    assert cfg is not None
    assert cfg["region"] == bedrock_converse.DEFAULT_REGION
    assert cfg["model_id"] == bedrock_converse.DEFAULT_MODEL_ID


def test_resolve_config_honours_env_overrides(_clean_env):
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    _clean_env.setenv("AWS_REGION", "ap-northeast-1")
    _clean_env.setenv("BEDROCK_MODEL_ID", "apac.anthropic.custom-model-v1:0")
    cfg = bedrock_converse.resolve_config()
    assert cfg["region"] == "ap-northeast-1"
    assert cfg["model_id"] == "apac.anthropic.custom-model-v1:0"


def test_resolve_config_provider_value_is_case_insensitive(_clean_env):
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "  BEDROCK ")
    assert bedrock_converse.resolve_config() is not None


def test_bedrock_region_env_wins_over_aws_region(_clean_env):
    """專案已有 BEDROCK_REGION 慣例（config.py:71，Nova Sonic 共用）。

    本模組必須以它為準，否則同一台機器上 Nova Sonic 與 Converse 會打到
    不同 region，且只有其中一個開通了模型——這種分歧在現場極難察覺。
    """
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    _clean_env.setenv("BEDROCK_REGION", "ap-northeast-1")
    _clean_env.setenv("AWS_REGION", "eu-central-1")
    assert bedrock_converse.resolve_config()["region"] == "ap-northeast-1"


def test_aws_region_used_when_bedrock_region_absent(_clean_env):
    """未設 BEDROCK_REGION 時退回 boto3 標準的 AWS_REGION。"""
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    _clean_env.setenv("AWS_REGION", "eu-central-1")
    assert bedrock_converse.resolve_config()["region"] == "eu-central-1"


# ---------------------------------------------------------------------------
# converse_text
# ---------------------------------------------------------------------------

def test_converse_text_builds_request_and_returns_text(_clean_env, monkeypatch):
    """送出 Bedrock Converse 標準形狀，並取回第一段 text。"""
    fake = _FakeClient(_ok_payload("好棒！跟我說一遍：I like apples."))
    monkeypatch.setattr(
        bedrock_converse, "_build_client", lambda region, timeout_s: fake
    )

    cfg = {"region": "us-west-2", "model_id": "model-x"}
    out = bedrock_converse.converse_text(
        system="你是台灣國小英語鷹架家教。",
        user="學生剛剛說：「蘋果」",
        cfg=cfg,
        max_tokens=160,
    )

    assert out == "好棒！跟我說一遍：I like apples."
    assert fake.captured["modelId"] == "model-x"
    assert fake.captured["system"] == [{"text": "你是台灣國小英語鷹架家教。"}]
    assert fake.captured["messages"] == [
        {"role": "user", "content": [{"text": "學生剛剛說：「蘋果」"}]}
    ]
    assert fake.captured["inferenceConfig"]["maxTokens"] == 160


def test_converse_text_concatenates_multiple_text_blocks(_clean_env, monkeypatch):
    payload = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": "前段。"}, {"text": "後段。"}],
            }
        }
    }
    monkeypatch.setattr(
        bedrock_converse, "_build_client", lambda region, timeout_s: _FakeClient(payload)
    )
    out = bedrock_converse.converse_text(
        system="s", user="u", cfg={"region": "r", "model_id": "m"}
    )
    assert out == "前段。後段。"


def test_converse_text_raises_when_no_text_block(_clean_env, monkeypatch):
    """回應缺 text：明確拋錯，由呼叫端 fallback 到 relay / 規則式，不靜默回空字串。"""
    monkeypatch.setattr(
        bedrock_converse,
        "_build_client",
        lambda region, timeout_s: _FakeClient({"output": {"message": {"content": []}}}),
    )
    with pytest.raises(bedrock_converse.BedrockResponseError):
        bedrock_converse.converse_text(
            system="s", user="u", cfg={"region": "r", "model_id": "m"}
        )


def test_module_import_does_not_load_boto3(_clean_env):
    """import 期不得載入 boto3（沿用 nova_sonic 的 lazy import 慣例，保護 edge 啟動時間）。"""
    import ast
    import pathlib

    src = pathlib.Path(bedrock_converse.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    top_level_imports = [
        n
        for n in tree.body
        if isinstance(n, (ast.Import, ast.ImportFrom))
    ]
    names = []
    for node in top_level_imports:
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        else:
            names.append(node.module or "")
    assert not any(n.startswith("boto") for n in names), names
