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
    "BEDROCK_MODEL_ID_CHAT",
    "BEDROCK_MODEL_ID_DIAG",
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
# resolve_config(role=...) — 對話／診斷 model 分流
#
# 兩條路徑的延遲上界差 8 倍（對話 cloud_llm._TIMEOUT_S=1.5s vs 診斷
# _API_TIMEOUT_SEC=12s）。若兩者共用同一個大模型，對話路徑會來不及回覆而
# 永遠降級到 edge，等於雲端大腦白接。
# ---------------------------------------------------------------------------

def test_chat_role_defaults_to_a_faster_model_than_diag(_clean_env):
    """對話路徑預設必須是比診斷路徑更快的小模型，否則 1.5s 上界必然逾時。"""
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    chat = bedrock_converse.resolve_config(role="chat")["model_id"]
    diag = bedrock_converse.resolve_config(role="diag")["model_id"]
    assert chat == bedrock_converse.DEFAULT_CHAT_MODEL_ID
    assert diag == bedrock_converse.DEFAULT_MODEL_ID
    assert chat != diag


def test_chat_default_is_haiku_and_diag_default_is_sonnet(_clean_env):
    """預設 model 以 `ap-east-2`（台北）實際可用的 profile 為準。

    2026-07-26 實測：Sonnet 5 / Haiku 4.5 在台北只有 `global.` 前綴版本，
    沒有 `apac.` geo 版本（唯一的 geo 是舊的 apac.anthropic.claude-sonnet-4）。
    """
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    assert (
        bedrock_converse.resolve_config(role="chat")["model_id"]
        == "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    )
    assert (
        bedrock_converse.resolve_config(role="diag")["model_id"]
        == "global.anthropic.claude-sonnet-5"
    )


def test_default_region_follows_the_competition_rules(_clean_env):
    """預設 region 必須是規範指定的兩個之一。

    **2026-07-31 由 ap-east-2（台北）改為 us-west-2。**
    「黑客松競賽環境規範與限制_20260722.pdf」一般性規範第 6 條：

        參賽隊伍應以 us-east-1 與 us-west-2 兩個區域作為部署的指定主要區域。

    先前釘台北是依據 2026-07-26 在**團隊自有帳號**上的配額實測（只有台北
    非零）。那份實測對主辦方帳號作廢——配額是帳號獨立的，而且規範已指定區域。

    代價要記著：台北 → us-west-2 跨太平洋，RTT 通常 150–250ms，而對話路徑
    只有 1.5s 預算。8/1 拿到帳號後**必須重新量端到端延遲再決定逾時值**。
    """
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    assert bedrock_converse.DEFAULT_REGION in ("us-west-2", "us-east-1")
    assert bedrock_converse.resolve_config()["region"] in ("us-west-2", "us-east-1")


def test_both_role_defaults_are_global_profiles(_clean_env):
    """兩個 role 預設都必須是 global cross-region profile。

    這不是偏好而是唯一選項：從 `ap-east-2` 出發，這兩顆模型都沒有 geo 版本。
    若誤填 `us.` / `apac.` 前綴，在台北會直接 ValidationException。
    """
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    for role in ("chat", "diag"):
        model_id = bedrock_converse.resolve_config(role=role)["model_id"]
        assert model_id.startswith("global."), (role, model_id)


def test_role_specific_env_overrides_role_default(_clean_env):
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    _clean_env.setenv("BEDROCK_MODEL_ID_CHAT", "jp.anthropic.chat-model-v1:0")
    _clean_env.setenv("BEDROCK_MODEL_ID_DIAG", "jp.anthropic.diag-model-v1:0")
    assert (
        bedrock_converse.resolve_config(role="chat")["model_id"]
        == "jp.anthropic.chat-model-v1:0"
    )
    assert (
        bedrock_converse.resolve_config(role="diag")["model_id"]
        == "jp.anthropic.diag-model-v1:0"
    )


def test_global_model_id_still_applies_to_both_roles(_clean_env):
    """向後相容：既有部署（user-data.sh / README）只設 BEDROCK_MODEL_ID，
    未設 role 專屬變數時兩條路徑都必須沿用它，不可被 role 預設值蓋掉。"""
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    _clean_env.setenv("BEDROCK_MODEL_ID", "us.anthropic.only-one-v1:0")
    assert (
        bedrock_converse.resolve_config(role="chat")["model_id"]
        == "us.anthropic.only-one-v1:0"
    )
    assert (
        bedrock_converse.resolve_config(role="diag")["model_id"]
        == "us.anthropic.only-one-v1:0"
    )


def test_role_env_wins_over_global_model_id(_clean_env):
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    _clean_env.setenv("BEDROCK_MODEL_ID", "us.anthropic.global-v1:0")
    _clean_env.setenv("BEDROCK_MODEL_ID_CHAT", "us.anthropic.chat-v1:0")
    assert (
        bedrock_converse.resolve_config(role="chat")["model_id"]
        == "us.anthropic.chat-v1:0"
    )
    # 未設 DIAG 專屬變數的那條仍吃全域值
    assert (
        bedrock_converse.resolve_config(role="diag")["model_id"]
        == "us.anthropic.global-v1:0"
    )


def test_unknown_role_falls_back_to_generic_default(_clean_env):
    """未知 role 不得爆炸——決賽現場寧可用通用預設也不能整條雲端路徑掛掉。"""
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    cfg = bedrock_converse.resolve_config(role="不存在的角色")
    assert cfg["model_id"] == bedrock_converse.DEFAULT_MODEL_ID


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
