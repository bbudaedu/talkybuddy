# -*- coding: utf-8 -*-
"""gemini_llm.py — Google Gemini 直連 provider（第三條雲端後端）。

與 :mod:`server.anthropic_relay`（Anthropic 相容 Messages API）和
:mod:`server.bedrock_converse`（原生 boto3 Converse）平行的第三條路：直接打
Google 的 `generativelanguage.googleapis.com`。

## 為什麼要有這條

2026-07-31 實測，自架中轉（`cli-proxy-api`）接 **Claude 系列**時，我們送的
`system` 欄位會被上游 Claude Code 的 system prompt 蓋掉——玩偶會回「我是
Claude Code，Anthropic 的官方 CLI 工具」並拒絕教英文。中轉的**非 Anthropic**
上游沒有這個問題，但那條路還多依賴一台會斷線的中繼機（板子↔中繼實測
ping 100% 丟包過三次）。

直連 Gemini 兩個問題一起消失：system prompt 是我們的、也不需要中繼機。
板子對外網路本來就是通的（`api.anthropic.com` 405、0.199s 實測）。

**決賽主線仍是 Bedrock**，這條是開發與驗證期間可用的真雲端。

## 純標準函式庫

沿用 `cloud_llm` 的作風用 urllib，不引入 `google-genai` SDK：板子上多一個重
依賴就多一份啟動時間與安裝風險，而我們只需要一個 POST。

## 認證放 header 不放 query string

Google 的文件兩種都給（`?key=` 與 `x-goog-api-key:`）。這裡固定用 header——
query string 會被寫進各層 proxy／存取紀錄，而這個專案已經因為金鑰外洩付過
一次代價（見 `PIPECAT_HANDOFF.md` 第一節）。
"""

from __future__ import annotations

import json
import os
import urllib.request

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# 預設 model：最快、最便宜的穩定 flash-lite。對話路徑的預算只有
# `cloud_llm._TIMEOUT_S`（預設 1.5s），選大模型會穩定逾時而永遠降級回 edge。
# 可由 GEMINI_MODEL 覆蓋；model 清單會變，**上線前用 list_models() 對實際
# 金鑰查證**，不要相信寫死的字串。
DEFAULT_MODEL = "gemini-3.5-flash-lite"

DEFAULT_TIMEOUT_S = 12.0


class GeminiResponseError(RuntimeError):
    """Gemini 回應格式不符預期；呼叫端據此 fallback，不靜默回空字串。"""


def resolve_config() -> dict | None:
    """由環境變數解析 Gemini 設定；沒有金鑰時回 None（純函式、不觸網）。

    金鑰優先序 ``GEMINI_API_KEY`` → ``GOOGLE_API_KEY``（後者是 Google SDK 的
    通用慣例，很多人已經設好了）。model 由 ``GEMINI_MODEL`` 覆蓋，端點由
    ``GEMINI_BASE_URL`` 覆蓋（測試與自架代理用）。

    Returns:
        ``{"base_url", "model", "headers"}``，或無金鑰時 None。
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key or not api_key.strip():
        return None
    base_url = (os.environ.get("GEMINI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
    return {
        "base_url": base_url,
        "model": model,
        "headers": {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key.strip(),
        },
    }


def _extract_text(payload: dict) -> str:
    """從 generateContent 回應取出所有 text part 串接；缺 text 即拋。

    刻意不對缺 text 的情況回空字串：Gemini 在被安全機制擋下時回的是沒有
    `parts` 的 candidate（附 `finishReason`），那是**內容被擋**而不是「模型
    回了空字串」，兩者要讓呼叫端分得出來。
    """
    try:
        candidates = payload["candidates"]
        parts = candidates[0]["content"]["parts"]
    except (KeyError, IndexError, TypeError) as exc:
        reason = ""
        try:
            reason = f"（finishReason={payload['candidates'][0].get('finishReason')}）"
        except Exception:
            pass
        raise GeminiResponseError(f"Gemini 回應缺 candidates/content/parts: {exc}{reason}")
    texts = [p["text"] for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str)]
    if not texts:
        raise GeminiResponseError("Gemini 回應無任何 text part")
    return "".join(texts)


def generate_text(
    system: str,
    user: str,
    *,
    cfg: dict,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> str:
    """以 Gemini generateContent 產生文字；失敗一律拋例外，由呼叫端 fallback。

    Args:
        system: system instruction。
        user: 已組好的 user prompt。
        cfg: :func:`resolve_config` 的輸出。
        max_tokens: 產生上限。
        temperature: 隨機性。
        timeout_s: 呼叫逾時（秒）。

    Returns:
        產生的文字。

    Raises:
        GeminiResponseError: 回應格式不符預期。
    """
    url = f"{cfg['base_url']}/models/{cfg['model']}:generateContent"
    body = json.dumps(
        {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            },
        }
    ).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=cfg["headers"], method="POST")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return _extract_text(payload)


def list_models(cfg: dict | None = None) -> list[str]:
    """列出這把金鑰實際可用的 model 名稱。

    寫死的 model ID 很容易過期（Gemini 的型號改版很快），換金鑰或換環境時
    先跑這個確認 :data:`DEFAULT_MODEL` 還在不在：

        PYTHONPATH=. python -m server.gemini_llm
    """
    cfg = cfg or resolve_config()
    if cfg is None:
        return []
    req = urllib.request.Request(
        f"{cfg['base_url']}/models", headers=cfg["headers"], method="GET"
    )
    with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT_S) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    out = []
    for m in payload.get("models", []):
        name = (m.get("name") or "").removeprefix("models/")
        methods = m.get("supportedGenerationMethods") or []
        if name and (not methods or "generateContent" in methods):
            out.append(name)
    return out


if __name__ == "__main__":  # pragma: no cover - 手動探測工具
    _cfg = resolve_config()
    if _cfg is None:
        raise SystemExit("沒有 GEMINI_API_KEY / GOOGLE_API_KEY")
    print(f"base_url={_cfg['base_url']}  預設 model={_cfg['model']}")
    for _name in list_models(_cfg):
        print(" ", _name)
