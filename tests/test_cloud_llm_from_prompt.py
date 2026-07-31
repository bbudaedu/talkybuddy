# -*- coding: utf-8 -*-
"""test_cloud_llm_from_prompt.py — `CloudLLM.generate_from_prompt` 的契約。

這個進入點是為 pipecat 開的：pipeline 上游的 `LessonPromptInjector` 已經用
`build_user_prompt` 把 prompt 組好了，雲端這一層**不可以再組一次**（會雙重包裝）。

同時鎖住兩件容易在重構中漂掉的事：

1. `generate()` 的行為必須與加這個進入點之前**一模一樣**——去識別化仍然只
   套在學生文字上、模板仍然是 `build_user_prompt` 那一份。
2. `generate_from_prompt()` **不做**去識別化。呼叫端已經對學生文字做過了，
   在這裡對整段 prompt 再做一次會遮掉目標句裡的專名——
   `My name is Tom.` 會變成 `My name is [名字]`，玩偶就帶讀錯了。
   這條測試就是釘住這個坑。
"""
from __future__ import annotations

import json

import pytest

from server import cloud_llm as cloud_llm_mod
from server.cloud_llm import CloudLLM
from server.llm import build_user_prompt

_ALL_ENV = [
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_MODEL",
    "TALKYBUDDY_CLOUD_PROVIDER",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ALL_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class _Sc:
    target_sentence = "I want an apple."


def _capture(monkeypatch, reply: str) -> dict:
    """攔下 urlopen，把送出去的 body 錄下來供斷言；回傳可變的錄音 dict。"""
    seen: dict = {}

    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"content": [{"type": "text", "text": reply}]}).encode("utf-8")

    def _fake_urlopen(req, timeout=None):
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _R()

    monkeypatch.setattr(cloud_llm_mod.urllib.request, "urlopen", _fake_urlopen)
    return seen


def test_generate_still_builds_prompt_with_shared_template(_clean_env, monkeypatch):
    """generate() 組出來的 user prompt 必須等於 build_user_prompt 的輸出。

    模板重複一份在 cloud_llm 裡是這次重構要消掉的東西；這條測試確保消掉之後
    送出去的內容沒有變。
    """
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "sk-relay")
    seen = _capture(monkeypatch, "很好！跟我說一遍：I want an apple.")

    CloudLLM().generate("我想要蘋果", _Sc(), "本輪策略：多鼓勵")

    sent = seen["body"]["messages"][0]["content"]
    assert sent == build_user_prompt("我想要蘋果", "I want an apple.", "本輪策略：多鼓勵")


def test_generate_still_deidentifies_student_text_only(_clean_env, monkeypatch):
    """學生講的專名要被遮，但目標句裡的專名不可以被遮。"""
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "sk-relay")
    seen = _capture(monkeypatch, "很好！跟我說一遍：I want an apple.")

    class _ScTom:
        target_sentence = "My name is Tom."

    CloudLLM().generate("我是 Tom 我家住 0912345678", _ScTom())

    sent = seen["body"]["messages"][0]["content"]
    assert "[名字]" in sent, "學生文字裡的專名沒被遮"
    assert "[數字]" in sent, "學生文字裡的電話沒被遮"
    assert "My name is Tom." in sent, "目標句被連帶遮掉了——帶讀會念錯"


def test_generate_from_prompt_sends_prompt_verbatim(_clean_env, monkeypatch):
    """已組好的 prompt 要原封不動送出：不重組、不去識別化。"""
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "sk-relay")
    seen = _capture(monkeypatch, "很好！跟我說一遍：I want an apple.")

    prompt = build_user_prompt("我是 Tom", "My name is Tom.", None)
    CloudLLM().generate_from_prompt(prompt, target="My name is Tom.")

    sent = seen["body"]["messages"][0]["content"]
    assert sent == prompt
    assert "[名字]" not in sent, "generate_from_prompt 不該再去識別化一次"


def test_generate_from_prompt_still_applies_readalong_guard(_clean_env, monkeypatch):
    """雲端漏掉帶讀句時仍要補上——這是 generate() 既有的護欄，不可以掉。"""
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "sk-relay")
    _capture(monkeypatch, "你好棒喔！")  # 只稱讚、沒帶讀

    out = CloudLLM().generate_from_prompt("隨便什麼 prompt", target="I want an apple.")

    assert out is not None
    assert "跟我說一遍：I want an apple." in out


def test_generate_from_prompt_converts_to_traditional(_clean_env, monkeypatch):
    """簡體回覆要繁化（與 generate() 同序：先繁化再跑帶讀護欄）。"""
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "sk-relay")
    _capture(monkeypatch, "你说得真好！跟我说一遍：I want an apple.")

    out = CloudLLM().generate_from_prompt("prompt", target="I want an apple.")

    assert out is not None
    assert "说" not in out and "說" in out


def test_generate_from_prompt_failure_returns_none_and_records(_clean_env, monkeypatch):
    """任何例外都回 None，而且要留下證據讓 status_detail 講得出原因。"""
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "sk-relay")

    def _boom(req, timeout=None):
        raise TimeoutError("連線逾時")

    monkeypatch.setattr(cloud_llm_mod.urllib.request, "urlopen", _boom)

    c = CloudLLM()
    assert c.generate_from_prompt("prompt", target="I want an apple.") is None
    assert c.verified() is False
    assert "TimeoutError" in c.status_detail()


def test_generate_from_prompt_without_backend_returns_none(_clean_env):
    """沒設定任何後端就直接回 None，不該拋。"""
    c = CloudLLM()
    assert c.generate_from_prompt("prompt", target="x") is None
    assert c.verified_backend() == "none"


# --- 第三條後端：Gemini 直連 -------------------------------------------------


def _gemini_ok(monkeypatch, text: str) -> dict:
    """攔下 gemini_llm 的 urlopen，錄下送出的 body。"""
    from server import gemini_llm

    seen: dict = {}

    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(
                {"candidates": [{"content": {"parts": [{"text": text}]}}]}
            ).encode("utf-8")

    def _fake(req, timeout=None):
        seen["body"] = json.loads(req.data.decode("utf-8"))
        seen["timeout"] = timeout
        return _R()

    monkeypatch.setattr(gemini_llm.urllib.request, "urlopen", _fake)
    return seen


def test_gemini_is_used_when_configured(_clean_env, monkeypatch):
    _clean_env.setenv("GEMINI_API_KEY", "g-key")
    seen = _gemini_ok(monkeypatch, "很好！跟我說一遍：I want an apple.")

    c = CloudLLM()
    out = c.generate_from_prompt("已組好的 prompt", target="I want an apple.")

    assert out == "很好！跟我說一遍：I want an apple."
    assert c.verified_backend() == "gemini"
    assert seen["body"]["contents"][0]["parts"][0]["text"] == "已組好的 prompt"


def test_gemini_beats_relay(_clean_env, monkeypatch):
    """使用者已裁定不用中轉：兩個都設時要走 Gemini。"""
    _clean_env.setenv("GEMINI_API_KEY", "g-key")
    _clean_env.setenv("ANTHROPIC_API_KEY", "sk-relay")
    _gemini_ok(monkeypatch, "很好！跟我說一遍：I want an apple.")

    c = CloudLLM()
    assert c.configured_backend() == "gemini"
    c.generate_from_prompt("prompt", target="I want an apple.")
    assert c.verified_backend() == "gemini"


def test_bedrock_still_beats_gemini(_clean_env, monkeypatch):
    """決賽主線不可以被開發用的 Gemini 蓋掉。"""
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    _clean_env.setenv("GEMINI_API_KEY", "g-key")
    assert CloudLLM().configured_backend() == "bedrock"


def test_available_true_with_only_gemini(_clean_env):
    _clean_env.setenv("GEMINI_API_KEY", "g-key")
    assert CloudLLM().available() is True


def test_gemini_gets_the_conversation_timeout_not_its_own_default(_clean_env, monkeypatch):
    """1.5s 的斷網驗收上界靠這個；用錯就直接破功（與 Bedrock 同理）。"""
    _clean_env.setenv("GEMINI_API_KEY", "g-key")
    seen = _gemini_ok(monkeypatch, "很好！跟我說一遍：I want an apple.")

    CloudLLM().generate_from_prompt("prompt", target="I want an apple.")

    assert seen["timeout"] == cloud_llm_mod._TIMEOUT_S


def test_gemini_failure_records_reason(_clean_env, monkeypatch):
    from server import gemini_llm

    _clean_env.setenv("GEMINI_API_KEY", "g-key")

    def _boom(req, timeout=None):
        raise TimeoutError("連線逾時")

    monkeypatch.setattr(gemini_llm.urllib.request, "urlopen", _boom)

    c = CloudLLM()
    assert c.generate_from_prompt("prompt", target="x") is None
    assert c.verified() is False
    assert "TimeoutError" in c.status_detail()
