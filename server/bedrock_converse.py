# -*- coding: utf-8 -*-
"""bedrock_converse.py — 原生 AWS Bedrock Converse API provider（TCLOUD-02）。

與 :mod:`server.anthropic_relay` 平行的第二條雲端後端：前者走 Anthropic 相容
Messages API（自架中轉），本模組走 `boto3 bedrock-runtime.converse()`，滿足
「大腦 100% 在 Bedrock」的合規條件，並收斂 ROADMAP Phase 3 已登錄的
「無原生 Bedrock Converse 後端，僅 relay」缺口。

**預設不啟用**：只有 `TALKYBUDDY_CLOUD_PROVIDER=bedrock` 時 resolve_config()
才回設定，否則回 None——既有 relay 路徑行為完全不變（零迴歸風險）。

重依賴（boto3）一律 lazy import（沿用 nova_sonic 慣例），import 期不觸網、
不載入 SDK，保護 edge 端啟動時間。
"""

from __future__ import annotations

import os

# 預設 region：us-west-2 的 Anthropic 模型可用性最廣。台灣現場若要壓低延遲，
# 設 AWS_REGION=ap-northeast-1（東京，RTT 約 40ms vs Oregon 約 130ms），但需
# 先確認該 region 已開通對應模型（見本檔 __main__ 的 list_models 探測）。
DEFAULT_REGION = "us-west-2"

# 預設 model：cross-region inference profile ID。**上線前務必以本檔的
# list_models() 對實際帳號查證**——各帳號/region 可用的 profile 不同，
# 寫死的字串很容易過期。可由 BEDROCK_MODEL_ID 覆蓋。
DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

# 呼叫逾時（秒）。診斷是非同步路徑，可寬鬆；若日後接到即時對話路徑，
# 呼叫端應自行傳入更短的值（參考 cloud_llm._TIMEOUT_S 的 1.5s 上界）。
DEFAULT_TIMEOUT_S = 12.0

_PROVIDER_ENV = "TALKYBUDDY_CLOUD_PROVIDER"


class BedrockResponseError(RuntimeError):
    """Bedrock 回應格式不符預期；呼叫端據此 fallback，不靜默回空字串。"""


def resolve_config() -> dict | None:
    """由環境變數解析 Bedrock 設定；未選用 bedrock provider 時回 None。

    回 ``{"region": str, "model_id": str}``。憑證由 boto3 標準鏈解析
    （env / ~/.aws / IAM role），本函式不碰憑證、不觸網。
    """
    provider = (os.environ.get(_PROVIDER_ENV) or "").strip().lower()
    if provider != "bedrock":
        return None
    # region 優先序：BEDROCK_REGION（專案慣例，見 config.py:71，Nova Sonic
    # 共用同一個變數）→ boto3 標準的 AWS_REGION / AWS_DEFAULT_REGION →
    # 本模組預設。以 BEDROCK_REGION 為首是刻意的：同一台機器上若 Nova Sonic
    # 與 Converse 打到不同 region，而只有其中一個開通了模型，現場極難察覺。
    region = (
        os.environ.get("BEDROCK_REGION")
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or DEFAULT_REGION
    )
    model_id = os.environ.get("BEDROCK_MODEL_ID") or DEFAULT_MODEL_ID
    return {"region": region, "model_id": model_id}


def available() -> bool:
    """provider 已切到 bedrock 且 boto3 可載入即 True；任何失敗回 False。"""
    if resolve_config() is None:
        return False
    try:
        import boto3  # noqa: F401
    except Exception:
        return False
    return True


def _build_client(region: str, timeout_s: float):
    """建立 bedrock-runtime client（lazy import；測試以 monkeypatch 取代）。"""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(
            read_timeout=timeout_s,
            connect_timeout=min(timeout_s, 5.0),
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


def _extract_text(payload: dict) -> str:
    """從 Converse 回應取出所有 text block 串接；缺 text 即拋 BedrockResponseError。"""
    try:
        blocks = payload["output"]["message"]["content"]
    except (KeyError, TypeError) as exc:
        raise BedrockResponseError(f"Converse 回應缺 output.message.content: {exc}")
    parts = [b["text"] for b in blocks if isinstance(b, dict) and "text" in b]
    if not parts:
        raise BedrockResponseError("Converse 回應無任何 text block")
    return "".join(parts)


def converse_text(
    system: str,
    user: str,
    *,
    cfg: dict,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> str:
    """以 Bedrock Converse 產生文字；失敗一律拋例外，由呼叫端 fallback。"""
    client = _build_client(cfg["region"], timeout_s)
    payload = client.converse(
        modelId=cfg["model_id"],
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": user}]}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
    )
    return _extract_text(payload)


def list_models(region: str | None = None) -> list[str]:
    """列出該帳號/region 實際可用的 Anthropic inference profile 與 model ID。

    憑證到位後先跑這個確認 DEFAULT_MODEL_ID 是否正確：
        python -m server.bedrock_converse
    """
    import boto3

    region = region or os.environ.get("AWS_REGION") or DEFAULT_REGION
    bedrock = boto3.client("bedrock", region_name=region)
    out: list[str] = []
    try:
        for p in bedrock.list_inference_profiles().get("inferenceProfileSummaries", []):
            pid = p.get("inferenceProfileId", "")
            if "anthropic" in pid:
                out.append(pid)
    except Exception as exc:  # 部分 region 無此 API
        out.append(f"(list_inference_profiles 不可用：{exc})")
    try:
        for m in bedrock.list_foundation_models(byProvider="anthropic").get(
            "modelSummaries", []
        ):
            out.append(m.get("modelId", ""))
    except Exception as exc:
        out.append(f"(list_foundation_models 不可用：{exc})")
    return [x for x in out if x]


if __name__ == "__main__":  # pragma: no cover - 手動探測工具
    _region = os.environ.get("AWS_REGION") or DEFAULT_REGION
    print(f"region={_region}")
    for _mid in list_models(_region):
        print(" ", _mid)
