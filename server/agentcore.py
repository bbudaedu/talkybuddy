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

**region 是 us-west-2（Oregon）。** 競賽環境規範第 6 條指定該 region，
官方 region 表（devguide/agentcore-regions）列 AgentCore harness 與 Memory
在 US West (Oregon) 皆可用，規範與可用性沒有衝突。

（歷史：2026-07-26 實測 `bedrock-agentcore-control.ap-east-2.amazonaws.com`
endpoint 不存在，AgentCore 沒有在台北提供服務，當時選了離台灣最近的新加坡。
規範指定 region 之後那個理由就不成立了。詳見 docs/AGENTCORE_ARCHITECTURE.md。）

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

# 上限沿用先前實作的保守值（文件未載明，取 64 不會踩到 API 限制）。
_SESSION_ID_MAX_LEN = 64

# AgentCore 服務所在 region。競賽環境規範第 6 條指定 us-west-2，
# 官方 region 表列 AgentCore harness 與 Memory 在 US West (Oregon) 皆可用。
# `AGENTCORE_REGION` 仍可覆蓋，現場出狀況時能立刻改。
DEFAULT_REGION = "us-west-2"

# 呼叫逾時（秒）。Harness 會跑多輪工具迴圈，比單次 converse 慢，
# 故用比 bedrock_converse.DEFAULT_TIMEOUT_S 更寬鬆的值。
# 對話路徑不走 AgentCore（1.5s 預算撐不住多一層代理迴圈），
# 只有非同步的診斷／派作業／週報走這裡。
DEFAULT_TIMEOUT_S = 60.0

_BACKEND_ENV = "TALKYBUDDY_AGENT_BACKEND"

# InvokeHarness 把錯誤當成**串流事件**送回來，不是拋 boto3 例外。
# 名稱取自本機 botocore service model 的 InvokeHarnessStreamOutput 成員。
_STREAM_ERROR_EVENTS = (
    "validationException",
    "internalServerException",
    "runtimeClientError",
)

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


def _normalize_session_id(session_id: str | None, actor_id: str | None = None) -> str:
    """把呼叫端給的 session id 正規化成 API 可接受的長度，並綁上學生維度。

    補齊必須是**決定性**的：同一個教學循環（診斷→決策→派作業→週報）的多次
    呼叫要落在同一個 session，Harness 的短期記憶才連貫。若每次補出不同值，
    記憶就斷了，等於白接 Memory。

    學生維度必須在這一層加，不能靠呼叫端自律：呼叫端傳的是「orch-turn-4」
    「hw-2026-07-20」這種只描述「第幾回合／哪一天」的字串，**任何孩子**跑到
    第 4 回合都會落在同一個 runtime session，短期記憶跨童串接。那是決定性的
    碰撞，不是機率問題。actor 前綴用雜湊：runtimeSessionId 同樣會被雲端保存，
    明文 student_id 進去等於繞過 ``_hash_actor`` 的努力。
    """
    scope = _hash_actor(actor_id)[:18] if actor_id else "anon"
    base = (session_id or "").strip() or uuid.uuid4().hex
    sid = f"tb-{scope}-{base}"
    if len(sid) > _SESSION_ID_MAX_LEN:
        # 過長時只壓縮 base（保留 actor 前綴），仍是決定性的
        digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]
        sid = f"tb-{scope}-{digest}"
    if len(sid) < _SESSION_ID_MIN_LEN:
        # 補齊到 API 下限；用自身雜湊填充，同一輸入永遠補出同一個值
        sid = (sid + "-" + hashlib.sha256(sid.encode("utf-8")).hexdigest())[:_SESSION_ID_MAX_LEN]
    return sid


def _hash_actor(student_id: str) -> str:
    """把 student_id 雜湊成 AgentCore Memory 的分群鍵。

    直接把 student_id 當 actorId 送上雲是矛盾的：同一個值在 prompt 裡會經
    guardrails.deidentify 遮罩，actorId 卻是明文，而且被 Memory **長期保存**。
    現場的 id 真的帶可識別資訊（例如 STUDENT-AMING-004 含小名）。

    用穩定雜湊：分群語意不變（同一個孩子永遠對到同一個 actor），但不可逆。
    """
    return "s-" + hashlib.sha256(student_id.encode("utf-8")).hexdigest()[:32]


def _extract_text(payload: dict) -> str:
    """消費 InvokeHarness 的 EventStream，串接所有文字 delta；缺文字即拋錯。

    **回應是 EventStream，不是 dict payload。** 本函式先前解的是
    ``payload["output"]["message"]["content"]``——那是 bedrock-runtime.Converse
    的形狀。用本機 botocore service model 可離線驗證：``InvokeHarness`` 的
    output shape 只有一個成員 ``stream``，且標記 ``{"eventstream": True}``。
    形狀對不上，所以這條路徑對真實 API 一次都沒成功過。

    只取 ``delta.text``：``delta`` 底下 ``text`` / ``toolUse`` / ``toolResult`` /
    ``reasoningContent`` 是平行的鍵，把思考過程串進答案會直接毀掉 JSON。

    錯誤是**串流裡的事件**（``validationException`` 等），不是 boto3 例外。
    不主動檢查就會拿到半截 JSON、schema 驗證失敗、靜默降級回規則式——
    現場看起來像「雲端品質不好」，而不是「雲端根本失敗了」。
    """
    try:
        stream = payload["stream"]
    except (KeyError, TypeError) as exc:
        raise AgentCoreResponseError(f"Harness 回應缺 stream: {exc}")

    parts: list[str] = []
    try:
        for event in stream:
            if not isinstance(event, dict):
                continue
            for name in _STREAM_ERROR_EVENTS:
                if name in event:
                    msg = (event.get(name) or {}).get("message") or ""
                    raise AgentCoreResponseError(f"Harness 串流回報 {name}: {msg}")
            delta = (event.get("contentBlockDelta") or {}).get("delta") or {}
            text = delta.get("text")
            if isinstance(text, str):
                parts.append(text)
    except AgentCoreResponseError:
        raise
    except Exception as exc:  # 串流中斷、解碼失敗…一律轉成本層的例外型別
        raise AgentCoreResponseError(f"Harness 串流讀取失敗: {exc}")

    if not parts:
        raise AgentCoreResponseError("Harness 串流無任何文字 delta")
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

    # 競賽規範：Bedrock 請求須控制在每秒 1 個以下（server/bedrock_throttle.py）。
    # Harness 在雲端跑的就是模型推理迴圈，它是第五個 Bedrock 呼叫端，走的是
    # 另一支 API——不主動接上這道閘就會整個繞過去。lazy import 沿用本檔慣例。
    # 排在 _build_client 之前：不放行就不必做事，ThrottleTimeout 往外拋，
    # 呼叫端的 except 會把它當成雲端不可用，降級到 Bedrock Converse。
    from server import bedrock_throttle

    bedrock_throttle.acquire()

    client = _build_client(cfg["region"], timeout_s)
    params: dict = {
        "harnessArn": cfg["harness_arn"],
        "runtimeSessionId": _normalize_session_id(session_id, actor_id),
        "messages": [{"role": "user", "content": [{"text": user_message}]}],
    }
    params["actorId"] = _hash_actor(actor_id)
    return _extract_text(client.invoke_harness(**params))
