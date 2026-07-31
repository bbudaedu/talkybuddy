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

# 預設 region：us-west-2。
#
# **2026-07-31 由 ap-east-2（台北）改過來，理由是規範不是效能。**
# 「黑客松競賽環境規範與限制_20260722.pdf」一般性規範第 6 條：
#
#     參賽隊伍應以 us-east-1 與 us-west-2 兩個區域作為部署的指定主要區域。
#
# 先前選台北是根據 2026-07-26 在**團隊自有帳號**上的跨 region 配額實測
# （只有台北非零，us-west-2 = 0.0，見 deploy/aws/STATUS.md）。那份實測對
# **主辦方帳號一律作廢**——不同帳號的配額是獨立的，而且規範已經指定了區域。
#
# 代價要誠實記著：台北 → us-west-2 是跨太平洋，RTT 通常 150–250ms，而對話
# 路徑只有 1.5s 預算（cloud_llm._TIMEOUT_S）。**8/1 拿到帳號後必須重新量一次
# 端到端延遲再決定逾時值**，不要沿用台北時代的數字。
#
# 換 region 時 model profile 前綴要一起重新查證（見下方 `global.` 那段註解）。
DEFAULT_REGION = os.environ.get("BEDROCK_DEFAULT_REGION", "us-west-2")

# 預設 model：cross-region inference profile ID。**上線前務必以本檔的
# list_models() 對實際帳號查證**——各帳號/region 可用的 profile 不同，
# 寫死的字串很容易過期。可由 BEDROCK_MODEL_ID 覆蓋。
# 這顆同時是「診斷路徑」與未指定 role 時的通用預設。
DEFAULT_MODEL_ID = "global.anthropic.claude-sonnet-5"

# 對話路徑（cloud_llm）專屬預設。兩條路徑的逾時上界差 8 倍——對話是
# 1.5s（cloud_llm._TIMEOUT_S，斷網橋段 D-03 的驗收上界）、診斷是 12s
# （非同步）。共用一顆大模型的話，對話路徑會穩定逾時而永遠降級回 edge，
# 等於雲端大腦白接，故預設拆成快模型。
DEFAULT_CHAT_MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

# 兩顆都是 `global.`（global cross-region）而非 geo profile，這是唯一選項
# 不是偏好：2026-07-26 實測 ap-east-2 只提供 global. 前綴，Sonnet 5 與
# Haiku 4.5 皆無 apac. geo 版本（該地唯一的 geo 是舊的
# apac.anthropic.claude-sonnet-4）。若把前綴改成 us. / apac. 而 region 仍是
# 台北，呼叫會直接失敗。換 region 時這三個常數要一起重新查證。

# role → (專屬環境變數, 預設 model)
_ROLE_MODELS = {
    "chat": ("BEDROCK_MODEL_ID_CHAT", DEFAULT_CHAT_MODEL_ID),
    "diag": ("BEDROCK_MODEL_ID_DIAG", DEFAULT_MODEL_ID),
}

# 呼叫逾時（秒）。診斷是非同步路徑，可寬鬆；若日後接到即時對話路徑，
# 呼叫端應自行傳入更短的值（參考 cloud_llm._TIMEOUT_S 的 1.5s 上界）。
DEFAULT_TIMEOUT_S = 12.0

_PROVIDER_ENV = "TALKYBUDDY_CLOUD_PROVIDER"


class BedrockResponseError(RuntimeError):
    """Bedrock 回應格式不符預期；呼叫端據此 fallback，不靜默回空字串。"""


def resolve_config(role: str | None = None) -> dict | None:
    """由環境變數解析 Bedrock 設定；未選用 bedrock provider 時回 None。

    回 ``{"region": str, "model_id": str}``。憑證由 boto3 標準鏈解析
    （env / ~/.aws / IAM role），本函式不碰憑證、不觸網。

    ``role``：``"chat"``（對話回覆，需快模型）或 ``"diag"``（教師診斷，
    可用大模型）。model 優先序為 role 專屬環境變數 → 全域 ``BEDROCK_MODEL_ID``
    → role 預設值。全域變數排在 role 預設之前是刻意的向後相容：既有部署
    （`deploy/aws/user-data.sh`）只設 ``BEDROCK_MODEL_ID``，該值必須繼續對
    兩條路徑生效，不可被 role 預設悄悄蓋掉。未知或未給 role 時退回通用預設
    ——現場寧可用通用模型也不能讓整條雲端路徑因參數打錯而掛掉。
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
    role_env, role_default = _ROLE_MODELS.get(role or "", ("", DEFAULT_MODEL_ID))
    model_id = (
        (os.environ.get(role_env) if role_env else None)
        or os.environ.get("BEDROCK_MODEL_ID")
        or role_default
    )
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


def _to_messages(messages: list[dict]) -> list[dict]:
    """把通用的 ``{"role","content"}`` 訊息串轉成 Converse 的 ``messages``。

    Converse 的 ``system`` 是獨立參數，不能混在 messages 裡——所以 system
    角色直接濾掉，由呼叫端傳。content 統一包成 ``[{"text": ...}]``。
    """
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        content = m.get("content")
        if isinstance(content, list):
            text = "".join(
                p["text"] for p in content
                if isinstance(p, dict) and isinstance(p.get("text"), str)
            )
        else:
            text = content if isinstance(content, str) else ""
        if not text:
            continue
        out.append({
            "role": "assistant" if role == "assistant" else "user",
            "content": [{"text": text}],
        })
    return out


def converse_chat(
    system: str,
    messages: list[dict],
    *,
    cfg: dict,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> str:
    """多輪版的 :func:`converse_text`；失敗一律拋例外，由呼叫端 fallback。

    Args:
        system: system prompt。
        messages: ``[{"role": "user"|"assistant"|"system", "content": str}, ...]``。
        cfg: :func:`resolve_config` 的輸出。
        max_tokens: 產生上限。
        temperature: 隨機性。
        timeout_s: 呼叫逾時（秒）。

    Returns:
        產生的文字。
    """
    # 競賽規範：Bedrock 請求須控制在每秒 1 個以下（server/bedrock_throttle.py）。
    # 節流放在這裡是因為這是全專案唯一的收斂點——cloud_llm、diagnose、
    # 三個 agent 都經過 converse()，各自加節流只會各自為政。
    from server import bedrock_throttle

    bedrock_throttle.acquire()
    client = _build_client(cfg["region"], timeout_s)
    payload = client.converse(
        modelId=cfg["model_id"],
        system=[{"text": system}],
        messages=_to_messages(messages),
        inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
    )
    return _extract_text(payload)


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
    return converse_chat(
        system,
        [{"role": "user", "content": user}],
        cfg=cfg,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_s=timeout_s,
    )


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
