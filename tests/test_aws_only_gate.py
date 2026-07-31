# -*- coding: utf-8 -*-
"""競賽合規閘門：執行期不得有非 AWS 的雲端出境。

規範原文（2026 雲湧智生黑客松，AWS 完整開發環境路線）：

    競賽僅限使用 Amazon Bedrock、SageMaker AI 所提供之基礎模型、Kiro，
    及 AWS 相關雲端服務進行系統與功能建置

專案在開發期接了三個非 AWS 後端（Gemini / Anthropic relay / ElevenLabs），
三個都能跑、也都驗過真機。**用任何一個都是違規。**

這裡的測試守的是「擋得住」這件事本身。特別注意 `configured_backend()` 與
`generate_from_prompt()` **各自獨立解析設定**——只擋其中一邊，真正送出去的
仍會是 Gemini。那是最危險的假合規：`/api/status` 顯示合規，實際仍在出境。
"""

from __future__ import annotations

import pytest

from server import aws_only


@pytest.fixture(autouse=True)
def _default_competition_mode(monkeypatch):
    """預設不設環境變數，驗證「預設就是合規模式」。"""
    monkeypatch.delenv("TALKYBUDDY_AWS_ONLY", raising=False)
    yield


# ---------------------------------------------------------------------------
# 預設值：忘記設定不能等於違規
# ---------------------------------------------------------------------------

def test_competition_mode_is_on_by_default():
    """忘記開的代價是失格，忘記關的代價只是雲端沒接上。預設站在安全那邊。"""
    assert aws_only.enabled() is True


def test_it_can_be_turned_off_for_local_development(monkeypatch):
    for off in ("0", "false", "no", "off"):
        monkeypatch.setenv("TALKYBUDDY_AWS_ONLY", off)
        assert aws_only.enabled() is False, f"{off!r} 應該關掉合規模式"


def test_bedrock_is_always_allowed():
    """Bedrock 是 AWS 服務，任何模式下都不該被擋。"""
    assert aws_only.llm_backend_allowed("bedrock") is True


def test_non_aws_backends_are_blocked():
    for backend in aws_only.NON_AWS_LLM_BACKENDS:
        assert aws_only.llm_backend_allowed(backend) is False, f"{backend} 沒被擋住"


def test_elevenlabs_is_blocked():
    assert aws_only.cloud_tts_allowed() is False


# ---------------------------------------------------------------------------
# 真正的出境路徑：兩個入口都要擋
# ---------------------------------------------------------------------------

def _force_gemini_and_relay(monkeypatch):
    """假裝 Gemini 與 relay 都設定齊全、Bedrock 沒設定。"""
    from server import anthropic_relay, bedrock_converse, gemini_llm

    monkeypatch.setattr(bedrock_converse, "resolve_config", lambda *a, **k: None)
    monkeypatch.setattr(gemini_llm, "resolve_config", lambda *a, **k: {"fake": "gemini"})
    monkeypatch.setattr(anthropic_relay, "resolve_config", lambda *a, **k: {"fake": "relay"})


def test_status_never_claims_a_non_aws_backend(monkeypatch):
    """/api/status 的資料來源不能說「會走 gemini」——那是對外宣告違規。"""
    from server import cloud_llm

    _force_gemini_and_relay(monkeypatch)
    assert cloud_llm.CloudLLM().configured_backend() == "none"


def test_the_actual_send_path_is_blocked_too(monkeypatch):
    """**最重要的一條。**

    `generate_from_prompt()` 自己解析設定，不看 `configured_backend()`。
    只擋 status 那邊的話，畫面顯示合規、封包照樣飛去 Google——
    那比不擋還糟，因為它讓人以為安全。
    """
    from server import anthropic_relay, cloud_llm, gemini_llm

    _force_gemini_and_relay(monkeypatch)

    def _boom(*a, **kw):
        raise AssertionError("競賽模式下不該呼叫非 AWS 後端")

    monkeypatch.setattr(gemini_llm, "generate_from_prompt", _boom, raising=False)
    monkeypatch.setattr(anthropic_relay, "post_messages", _boom, raising=False)

    out = cloud_llm.CloudLLM().generate_from_prompt(
        [{"role": "user", "content": "hello"}], target=None
    )
    assert out is None, "被擋下時應回 None，讓 pipeline 走既有降級鏈"


def test_turning_the_gate_off_restores_the_backends(monkeypatch):
    """賽後要能一行恢復，程式碼零損失。"""
    from server import cloud_llm

    _force_gemini_and_relay(monkeypatch)
    monkeypatch.setenv("TALKYBUDDY_AWS_ONLY", "0")
    assert cloud_llm.CloudLLM().configured_backend() == "gemini"


def test_cloud_tts_reports_unavailable_in_competition_mode(monkeypatch):
    """ElevenLabs 金鑰就算設著，競賽模式下也要回報不可用。"""
    from server import cloud_tts

    assert cloud_tts.CloudTTS().available() is False
