# -*- coding: utf-8 -*-
"""test_gemini_llm.py — Gemini 直連 provider 的契約。

全程 monkeypatch urlopen、不觸網。重點釘住三件事：

1. 送出去的 body 形狀對（`systemInstruction` / `contents` / `generationConfig`）
2. 金鑰走 header 不走 query string——query string 會被各層 proxy 記下來
3. 被安全機制擋下（candidate 沒有 parts）時**拋例外**而不是回空字串，
   呼叫端才分得出「被擋」與「模型回空」
"""
from __future__ import annotations

import json

import pytest

from server import gemini_llm

_ENV = ["GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_MODEL", "GEMINI_BASE_URL"]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _capture(monkeypatch, payload: dict) -> dict:
    seen: dict = {}

    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    def _fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = dict(req.headers)
        seen["body"] = json.loads(req.data.decode("utf-8")) if req.data else None
        return _R()

    monkeypatch.setattr(gemini_llm.urllib.request, "urlopen", _fake_urlopen)
    return seen


def _ok(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def test_resolve_config_none_without_key(_clean_env):
    assert gemini_llm.resolve_config() is None


def test_resolve_config_accepts_either_env_name(_clean_env):
    _clean_env.setenv("GOOGLE_API_KEY", "k1")
    assert gemini_llm.resolve_config() is not None
    _clean_env.setenv("GEMINI_API_KEY", "k2")
    # GEMINI_API_KEY 優先
    assert gemini_llm.resolve_config()["headers"]["x-goog-api-key"] == "k2"


def test_blank_key_is_not_a_key(_clean_env):
    _clean_env.setenv("GEMINI_API_KEY", "   ")
    assert gemini_llm.resolve_config() is None


def test_key_goes_in_header_not_query_string(_clean_env, monkeypatch):
    """金鑰不可出現在 URL —— 會被各層存取紀錄留下來。"""
    _clean_env.setenv("GEMINI_API_KEY", "super-secret")
    seen = _capture(monkeypatch, _ok("好"))

    gemini_llm.generate_text("s", "u", cfg=gemini_llm.resolve_config())

    assert "super-secret" not in seen["url"]
    assert seen["headers"]["X-goog-api-key"] == "super-secret"


def test_request_body_shape(_clean_env, monkeypatch):
    _clean_env.setenv("GEMINI_API_KEY", "k")
    _clean_env.setenv("GEMINI_MODEL", "gemini-test")
    seen = _capture(monkeypatch, _ok("好"))

    gemini_llm.generate_text(
        "你是玩偶", "學生說：我想要蘋果", cfg=gemini_llm.resolve_config(),
        max_tokens=160, temperature=0.5,
    )

    assert seen["url"].endswith("/models/gemini-test:generateContent")
    body = seen["body"]
    assert body["systemInstruction"]["parts"][0]["text"] == "你是玩偶"
    assert body["contents"][0]["parts"][0]["text"] == "學生說：我想要蘋果"
    assert body["generationConfig"] == {"maxOutputTokens": 160, "temperature": 0.5}


def test_extracts_and_joins_all_text_parts(_clean_env, monkeypatch):
    _clean_env.setenv("GEMINI_API_KEY", "k")
    _capture(monkeypatch, {"candidates": [{"content": {"parts": [
        {"text": "很好！"}, {"text": "跟我說一遍：I want an apple."},
    ]}}]})

    out = gemini_llm.generate_text("s", "u", cfg=gemini_llm.resolve_config())

    assert out == "很好！跟我說一遍：I want an apple."


def test_blocked_response_raises_not_returns_empty(_clean_env, monkeypatch):
    """安全機制擋下時 candidate 沒有 parts —— 那是「被擋」不是「回了空字串」。"""
    _clean_env.setenv("GEMINI_API_KEY", "k")
    _capture(monkeypatch, {"candidates": [{"finishReason": "SAFETY"}]})

    with pytest.raises(gemini_llm.GeminiResponseError) as exc:
        gemini_llm.generate_text("s", "u", cfg=gemini_llm.resolve_config())
    assert "SAFETY" in str(exc.value)


def test_empty_candidates_raises(_clean_env, monkeypatch):
    _clean_env.setenv("GEMINI_API_KEY", "k")
    _capture(monkeypatch, {"candidates": []})

    with pytest.raises(gemini_llm.GeminiResponseError):
        gemini_llm.generate_text("s", "u", cfg=gemini_llm.resolve_config())


def test_base_url_is_overridable(_clean_env, monkeypatch):
    """自架代理／測試要能改端點。"""
    _clean_env.setenv("GEMINI_API_KEY", "k")
    _clean_env.setenv("GEMINI_BASE_URL", "http://127.0.0.1:9999/v1beta/")
    seen = _capture(monkeypatch, _ok("好"))

    gemini_llm.generate_text("s", "u", cfg=gemini_llm.resolve_config())

    assert seen["url"].startswith("http://127.0.0.1:9999/v1beta/models/")


# --- 截斷不是合格回覆 -------------------------------------------------------


def test_max_tokens_truncation_raises_not_returns_partial(_clean_env, monkeypatch):
    """finishReason=MAX_TOKENS 要拋，不可以把半截的稱讚當成回覆送出去。

    2026-07-31 實測：Gemini 3.x 的非 lite 模型會做內部 thinking，而 thinking
    token **算在 maxOutputTokens 裡**。`_MAX_TOKENS=160` 被 thinking 吃掉 153，
    只剩 3 個 token 給回覆，於是 gemini-3.5-flash 回的是「你太棒」。

    最危險的不是截斷本身，是**它會被下游蓋掉**：`ensure_readalong` 補上帶讀句
    之後，「你太棒 跟我說一遍：I want an apple.」看起來完全合格，測不出來，
    而孩子聽到的是玩偶講話講到一半。所以要在這裡就攔下來，讓它變成一次誠實的
    失敗、正常降級回 edge。
    """
    _clean_env.setenv("GEMINI_API_KEY", "k")
    _capture(monkeypatch, {
        "candidates": [{
            "content": {"parts": [{"text": "你太棒"}]},
            "finishReason": "MAX_TOKENS",
        }]
    })

    with pytest.raises(gemini_llm.GeminiResponseError) as exc:
        gemini_llm.generate_text("s", "u", cfg=gemini_llm.resolve_config())
    msg = str(exc.value)
    assert "MAX_TOKENS" in msg
    assert "flash-lite" in msg, "錯誤訊息要講得出怎麼修，否則現場只會看到一直降級"


def test_normal_stop_is_not_treated_as_truncation(_clean_env, monkeypatch):
    _clean_env.setenv("GEMINI_API_KEY", "k")
    _capture(monkeypatch, {
        "candidates": [{
            "content": {"parts": [{"text": "很好！跟我說一遍：I want an apple."}]},
            "finishReason": "STOP",
        }]
    })

    out = gemini_llm.generate_text("s", "u", cfg=gemini_llm.resolve_config())
    assert out == "很好！跟我說一遍：I want an apple."


def test_default_model_is_one_measured_to_fit_the_budget(_clean_env):
    """預設 model 必須是實測不會 thinking 的 lite 系列。

    會 thinking 的模型在 160 token 預算下**必定**截斷 → 每輪都降級回 edge，
    雲端等於白接。把這條寫成測試是因為「換個看起來更新的 model」是很自然的
    改動，而它會靜靜地把雲端關掉。
    """
    assert "flash-lite" in gemini_llm.DEFAULT_MODEL
