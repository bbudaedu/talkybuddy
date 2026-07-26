# -*- coding: utf-8 -*-
"""agentcore.py — Amazon Bedrock AgentCore Harness 客戶端封裝。

與 :mod:`server.bedrock_converse` 平行的第三條雲端後端：

    bedrock_converse  → 直接呼叫模型（bedrock-runtime.converse）
    anthropic_relay   → Anthropic 相容 Messages API（自架中轉）
    agentcore（本檔） → 呼叫託管的 agent 迴圈（bedrock-agentcore.InvokeHarness）

選 Harness 而非 Runtime 的理由：Harness 是「單次 API 呼叫的託管 agent 迴圈」，
model / systemPrompt / tools / memory 都在建立時宣告好，呼叫端只送訊息即可，
**不需要打包容器、不需要部署管線**。Runtime 適合需要自訂執行環境的情境，
本專案的三個 agent 都是純推理 + JSON 輸出，用不到那層複雜度。

**region 是 ap-southeast-1（新加坡），不是台北。** 2026-07-26 實測：
`bedrock-agentcore-control.ap-east-2.amazonaws.com` endpoint 不存在，
AgentCore 沒有在台北提供服務（console 會把你轉去雪梨）。同時具備 AgentCore
與滿額 Bedrock 配額的 region 只有新加坡、雪梨、法蘭克福；新加坡離台灣最近
（約 50ms vs 雪梨約 130ms）。詳見 docs/AGENTCORE_ARCHITECTURE.md。

**預設不啟用**：只有 `TALKYBUDDY_AGENT_BACKEND=agentcore` 時 resolve_config()
才回設定，否則回 None——既有 in-process 路徑行為完全不變。

重依賴（boto3）一律 lazy import（沿用 bedrock_converse 慣例），import 期不觸網、
不載入 SDK，保護 edge 端啟動時間——邊緣裝置斷網時根本不會用到 AgentCore。
"""

from __future__ import annotations

import hashlib
import os
import uuid

# InvokeHarness 對 runtimeSessionId 的最短長度限制（2026-07-26 實測 API 回報）。
# 呼叫端會很自然地傳「turn-12」這種短字串，故由本層負責補齊。
_SESSION_ID_MIN_LEN = 33

# AgentCore 服務所在 region。見上方模組說明：台北無此服務。
DEFAULT_REGION = "ap-southeast-1"

# 呼叫逾時（秒）。Harness 會跑多輪工具迴圈，比單次 converse 慢，
# 故用比 bedrock_converse.DEFAULT_TIMEOUT_S 更寬鬆的值。
# 對話路徑不走 AgentCore（1.5s 預算撐不住多一層代理迴圈），
# 只有非同步的診斷／派作業／週報走這裡。
DEFAULT_TIMEOUT_S = 60.0

_BACKEND_ENV = "TALKYBUDDY_AGENT_BACKEND"

# role → 該角色 harness ARN 的環境變數名稱
_ROLE_ENV = {
    "orchestrator": "AGENTCORE_HARNESS_ORCHESTRATOR",
    "homework": "AGENTCORE_HARNESS_HOMEWORK",
    "report": "AGENTCORE_HARNESS_REPORT",
}


class AgentCoreResponseError(RuntimeError):
    """Harness 回應格式不符預期；呼叫端據此降級，不靜默回空字串。"""


def resolve_config(role: str) -> dict | None:
    """由環境變數解析某個 agent 角色的 AgentCore 設定；未啟用時回 None。

    回 ``{"region": str, "harness_arn": str, "memory_arn": str | None}``。
    憑證由 boto3 標準鏈解析（env / ~/.aws / IAM role），本函式不碰憑證、不觸網。

    未設該角色的 harness ARN 時一律回 None 讓呼叫端降級——拿空字串去打 API
    只會換到一個難懂的 ValidationException，不如在這裡就講清楚沒設定。
    """
    backend = (os.environ.get(_BACKEND_ENV) or "").strip().lower()
    if backend != "agentcore":
        return None
    env_name = _ROLE_ENV.get(role)
    if not env_name:
        return None
    harness_arn = (os.environ.get(env_name) or "").strip()
    if not harness_arn:
        return None
    region = (
        os.environ.get("AGENTCORE_REGION")
        or os.environ.get("AWS_REGION")
        or DEFAULT_REGION
    )
    return {
        "region": region,
        "harness_arn": harness_arn,
        "memory_arn": (os.environ.get("AGENTCORE_MEMORY_ARN") or "").strip() or None,
    }


def available(role: str) -> bool:
    """該角色已設定且 boto3 可載入即 True；任何失敗回 False。"""
    if resolve_config(role) is None:
        return False
    try:
        import boto3  # noqa: F401
    except Exception:
        return False
    return True


def _build_client(region: str, timeout_s: float):
    """建立 bedrock-agentcore client（lazy import；測試以 monkeypatch 取代）。"""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(
            read_timeout=timeout_s,
            connect_timeout=min(timeout_s, 10.0),
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


def _normalize_session_id(session_id: str | None) -> str:
    """把呼叫端給的 session id 正規化成 API 可接受的長度。

    補齊必須是**決定性**的：同一個教學循環（診斷→決策→派作業→週報）的多次
    呼叫要落在同一個 session，Harness 的短期記憶才連貫。若每次補出不同值，
    記憶就斷了，等於白接 Memory。
    """
    if not session_id:
        return f"tb-{uuid.uuid4()}"
    if len(session_id) >= _SESSION_ID_MIN_LEN:
        return session_id
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return f"tb-{session_id}-{digest}"[:64]


def _hash_actor(student_id: str) -> str:
    """把 student_id 雜湊成 AgentCore Memory 的分群鍵。

    直接把 student_id 當 actorId 送上雲是矛盾的：同一個值在 prompt 裡會經
    guardrails.deidentify 遮罩，actorId 卻是明文，而且被 Memory **長期保存**。
    現場的 id 真的帶可識別資訊（例如 STUDENT-AMING-004 含小名）。

    用穩定雜湊：分群語意不變（同一個孩子永遠對到同一個 actor），但不可逆。
    """
    return "s-" + hashlib.sha256(student_id.encode("utf-8")).hexdigest()[:32]


def _extract_text(payload: dict) -> str:
    """從 InvokeHarness 回應取出所有 text block 串接；缺 text 即拋錯。"""
    try:
        blocks = payload["output"]["message"]["content"]
    except (KeyError, TypeError) as exc:
        raise AgentCoreResponseError(f"Harness 回應缺 output.message.content: {exc}")
    parts = [b["text"] for b in blocks if isinstance(b, dict) and "text" in b]
    if not parts:
        raise AgentCoreResponseError("Harness 回應無任何 text block")
    return "".join(parts)


def try_invoke(
    role: str,
    user_message: str,
    *,
    actor_id: str | None = None,
    session_id: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> str | None:
    """若該角色已啟用 AgentCore 就呼叫並回傳文字；**未啟用回 None**。

    三個 agent 的雲端分支共用這一層，避免把後端優先序邏輯抄三份。

    回傳值的兩種 None 語意要分清楚：
    - 回 `None` = 「沒啟用」，呼叫端應往下試 bedrock_converse。
    - 拋例外 = 「啟用了但失敗」，呼叫端的外層 except 會降級回規則式。

    刻意不在這裡吞例外：靜默失敗會讓「AgentCore 其實沒在跑」這件事
    永遠查不出來，而那正是決賽現場最不能發生的誤會。
    """
    cfg = resolve_config(role)
    if cfg is None:
        return None
    return invoke(
        cfg, user_message,
        session_id=session_id, actor_id=actor_id, timeout_s=timeout_s,
    )


def invoke(
    cfg: dict,
    user_message: str,
    *,
    session_id: str | None = None,
    actor_id: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> str:
    """呼叫 Harness 產生文字；失敗一律拋例外，由呼叫端降級。

    ``actor_id`` 是 AgentCore Memory 的分群鍵。**漏傳會讓所有孩子共用同一份
    長期記憶**——這是隱私事故，不只是功能瑕疵，故獨立成參數而非塞在 message 裡。

    ``session_id`` 省略時自動產生。同一次教學循環（診斷→決策→派作業→週報）
    應傳同一個值，Harness 的短期記憶才會連貫。
    """
    if not actor_id:
        # 先驗參數再建 client：不可靜默略過 actorId，少了它 API 照樣成功，
        # 但所有孩子的長期記憶會混在同一個 actor 底下。拋例外讓呼叫端的
        # except 降級回規則式——寧可這次不用雲端，也不要製造無聲的隱私事故。
        raise ValueError("AgentCore invoke 缺 actor_id：Memory 會跨學生混用")
    client = _build_client(cfg["region"], timeout_s)
    params: dict = {
        "harnessArn": cfg["harness_arn"],
        "runtimeSessionId": _normalize_session_id(session_id),
        "messages": [{"role": "user", "content": [{"text": user_message}]}],
    }
    params["actorId"] = _hash_actor(actor_id)
    return _extract_text(client.invoke_harness(**params))
