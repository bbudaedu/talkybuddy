# -*- coding: utf-8 -*-
"""test_cloud_llm_chat.py — `CloudLLM.generate_chat`：多輪 + 可換 system + 可關帶讀強制。

## 為什麼要這個進入點

`generate_from_prompt` 是**單輪**的，而且寫死 `cloud_llm._SYSTEM_PROMPT`
（60 字、每輪硬帶讀）。那是回合式鷹架的契約，給 512 ctx 的小模型用的。

2026-07-31 真人實測，玩偶四輪回覆幾乎一模一樣——孩子問「可以跟我練習說英文
嗎？」，它還是回「跟我說一遍：I want an apple.」。三個原因疊在一起：
看不到對話歷史、system prompt 強制格式、`ensure_readalong` 事後再補一次。

專案裡**早就有**另一套契約：`scaffold.build_live_system_prompt`（教練企鵝，
明寫「孩子如果問你別的，一定要先回應他…絕對不可以假裝沒聽到孩子的話」），
`server/app.py` 的 `/ws/live` 在用，而且那條路**不套** `ensure_readalong`。

所以這裡不是發明新契約，是讓雲端路徑**接得上既有的那一套**。
"""
from __future__ import annotations

import json

import pytest

from server import cloud_llm as cloud_llm_mod
from server.cloud_llm import CloudLLM

_ALL_ENV = [
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL", "TALKYBUDDY_CLOUD_PROVIDER",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_MODEL", "GEMINI_BASE_URL",
]

HISTORY = [
    {"role": "system", "content": "會被丟掉的舊 system"},
    {"role": "user", "content": "我想要蘋果"},
    {"role": "assistant", "content": "很好！跟我說一遍：I want an apple."},
    {"role": "user", "content": "可以跟我練習說英文嗎？"},
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ALL_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _capture_gemini(monkeypatch, text: str) -> dict:
    from server import gemini_llm

    seen: dict = {}

    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(
                {"candidates": [{"content": {"parts": [{"text": text}]},
                                 "finishReason": "STOP"}]}
            ).encode("utf-8")

    def _fake(req, timeout=None):
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _R()

    monkeypatch.setattr(gemini_llm.urllib.request, "urlopen", _fake)
    return seen


def test_history_reaches_the_model(_clean_env, monkeypatch):
    """玩偶要記得上一輪——歷史必須真的送出去。"""
    _clean_env.setenv("GEMINI_API_KEY", "k")
    seen = _capture_gemini(monkeypatch, "當然好呀！我們一起練。")

    CloudLLM().generate_chat(HISTORY, system="你是教練企鵝", target=None)

    contents = seen["body"]["contents"]
    assert len(contents) == 3, "system 要被抽走，另外三則都要送出去"
    assert contents[0]["parts"][0]["text"] == "我想要蘋果"
    assert contents[2]["parts"][0]["text"] == "可以跟我練習說英文嗎？"


def test_assistant_role_is_mapped_for_gemini(_clean_env, monkeypatch):
    """Gemini 的助理角色叫 model 不叫 assistant，送錯會被拒。"""
    _clean_env.setenv("GEMINI_API_KEY", "k")
    seen = _capture_gemini(monkeypatch, "好")

    CloudLLM().generate_chat(HISTORY, system="s", target=None)

    roles = [c["role"] for c in seen["body"]["contents"]]
    assert roles == ["user", "model", "user"]


def test_system_override_is_used(_clean_env, monkeypatch):
    """要能換成 build_live_system_prompt，不可以還是那個 60 字硬帶讀的。"""
    _clean_env.setenv("GEMINI_API_KEY", "k")
    seen = _capture_gemini(monkeypatch, "好")

    CloudLLM().generate_chat(HISTORY, system="你是陪台灣國小學生的教練企鵝", target=None)

    sent = seen["body"]["systemInstruction"]["parts"][0]["text"]
    assert sent == "你是陪台灣國小學生的教練企鵝"
    assert "不超過60個字" not in sent


def test_readalong_not_forced_when_disabled(_clean_env, monkeypatch):
    """即時陪聊契約不強制帶讀——孩子問問題時玩偶要能只回答。"""
    _clean_env.setenv("GEMINI_API_KEY", "k")
    _capture_gemini(monkeypatch, "當然好呀！你想先練哪一句呢？")

    out = CloudLLM().generate_chat(
        HISTORY, system="s", target="I want an apple.", enforce_readalong=False
    )

    assert out == "當然好呀！你想先練哪一句呢？"
    assert "跟我說一遍" not in out


def test_readalong_still_forced_by_default(_clean_env, monkeypatch):
    """預設維持回合式契約——既有呼叫端行為不可以被這次改動動到。"""
    _clean_env.setenv("GEMINI_API_KEY", "k")
    _capture_gemini(monkeypatch, "你好棒！")

    out = CloudLLM().generate_chat(HISTORY, system="s", target="I want an apple.")

    assert "跟我說一遍：I want an apple." in out


def test_guardrails_still_apply_when_readalong_disabled(_clean_env, monkeypatch):
    """放寬帶讀格式**不等於**放寬安全護欄與繁化。"""
    _clean_env.setenv("GEMINI_API_KEY", "k")
    _capture_gemini(monkeypatch, "你说得真好，我们一起练习。")

    out = CloudLLM().generate_chat(
        HISTORY, system="s", target=None, enforce_readalong=False
    )

    assert out is not None
    assert "说" not in out and "說" in out, "簡轉繁不可以被關掉"


def test_empty_history_returns_none(_clean_env, monkeypatch):
    """沒有任何 user 訊息就沒有這一輪，別送空的上雲燒配額。"""
    _clean_env.setenv("GEMINI_API_KEY", "k")
    c = CloudLLM()
    assert c.generate_chat([{"role": "system", "content": "s"}], system="s",
                           target=None) is None


def test_generate_from_prompt_still_single_turn(_clean_env, monkeypatch):
    """既有進入點行為不變：一則 user 訊息、用內建 system。"""
    _clean_env.setenv("GEMINI_API_KEY", "k")
    seen = _capture_gemini(monkeypatch, "很好！跟我說一遍：I want an apple.")

    CloudLLM().generate_from_prompt("單輪 prompt", target="I want an apple.")

    assert len(seen["body"]["contents"]) == 1
    sent_system = seen["body"]["systemInstruction"]["parts"][0]["text"]
    assert sent_system == cloud_llm_mod._SYSTEM_PROMPT
