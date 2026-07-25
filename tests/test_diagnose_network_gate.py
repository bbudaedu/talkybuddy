# -*- coding: utf-8 -*-
"""test_diagnose_network_gate.py — allow_cloud 第三道出境閘門（NETCUT-02／D-03）。

涵蓋 09-RESEARCH.md Pitfall 4：`VoicePipeline._refresh_directive()`（每 5 個
成功回合背景觸發）呼叫的 `diagnose.generate_diagnosis()`，其雲端分支過去
只受憑證與家長同意兩道閘門，完全不看 network_mode——network_mode 為 edge
（D-01 kill-switch 已切斷雲端）時，這是唯一一條會繞過 kill-switch 的背景
出境呼叫。本檔驗證：

1. `generate_diagnosis(..., allow_cloud=False)`：即使有憑證+consent，
   `_call_anthropic_api` 也不被呼叫。
2. `generate_diagnosis(..., allow_cloud=True)`（預設）：行為與現況一致。
3. `VoicePipeline._refresh_directive()` 依進入當下的 `network_mode`
   把 allow_cloud 正確傳給 generate_diagnosis（edge=False／cloud=True）。
4. edge 模式下本地規則式刷新仍運作（directive 與 store 皆有更新），
   離線的 B1 導師更新功能不因本次修補而消失。
"""

from __future__ import annotations

import pytest

from server import diagnose, store
from server.pipeline import VoicePipeline

pytestmark = pytest.mark.anyio


class _StubTTS:
    def available(self) -> bool:
        return False


def _fake_resolve_config():
    return {"url": "https://example.invalid/v1/messages", "headers": {}, "model": "fake"}


# ---------------------------------------------------------------------------
# 1-2. generate_diagnosis(allow_cloud=...) 閘門本身
# ---------------------------------------------------------------------------


def test_generate_diagnosis_allow_cloud_false_never_calls_cloud_api(monkeypatch):
    """有憑證 + 有同意，但 allow_cloud=False → 出境間諜零呼叫，仍回完整診斷 dict。"""
    monkeypatch.setattr(diagnose.anthropic_relay, "resolve_config", _fake_resolve_config)
    calls = {"n": 0}

    def _spy_call(*args, **kwargs):
        calls["n"] += 1
        raise AssertionError("allow_cloud=False 時不得呼叫 _call_anthropic_api")

    monkeypatch.setattr(diagnose, "_call_anthropic_api", _spy_call)

    result = diagnose.generate_diagnosis([], None, allow_cloud=False)

    assert calls["n"] == 0
    assert result is not None
    assert result.get("companion_directive")
    assert "scores" in result


def test_generate_diagnosis_allow_cloud_true_attempts_cloud_api(monkeypatch):
    """有憑證 + 有同意，allow_cloud=True（預設）→ 間諜被呼叫一次（現況行為一致）。"""
    monkeypatch.setattr(diagnose.anthropic_relay, "resolve_config", _fake_resolve_config)
    calls = {"n": 0}

    def _spy_call(interactions, prev, cfg):
        calls["n"] += 1
        raise RuntimeError("模擬雲端失敗 → fallback 本地")

    monkeypatch.setattr(diagnose, "_call_anthropic_api", _spy_call)

    result = diagnose.generate_diagnosis([], None, allow_cloud=True)

    assert calls["n"] == 1
    assert result is not None


# ---------------------------------------------------------------------------
# 3-4. VoicePipeline._refresh_directive() 依 network_mode 傳參
# ---------------------------------------------------------------------------


async def test_refresh_directive_edge_mode_passes_allow_cloud_false(monkeypatch):
    """network_mode == "edge" 時 _refresh_directive → allow_cloud=False，
    但本地規則式刷新仍運作：directive 與 store 皆有更新。
    """
    captured: dict = {}
    original = diagnose.generate_diagnosis

    def _wrapper(interactions, prev, profile=None, allow_cloud=True):
        captured["allow_cloud"] = allow_cloud
        return original(interactions, prev, profile, allow_cloud=allow_cloud)

    monkeypatch.setattr(diagnose, "generate_diagnosis", _wrapper)

    vp = VoicePipeline(asr=None, llm=None, tts=_StubTTS())
    vp.network_mode = "edge"

    await vp._refresh_directive()

    assert captured["allow_cloud"] is False
    assert vp._directive is not None
    assert len(store.list_diagnoses()) >= 1


async def test_refresh_directive_cloud_mode_passes_allow_cloud_true(monkeypatch):
    """network_mode == "cloud" 時 _refresh_directive → allow_cloud=True。"""
    captured: dict = {}
    original = diagnose.generate_diagnosis

    def _wrapper(interactions, prev, profile=None, allow_cloud=True):
        captured["allow_cloud"] = allow_cloud
        return original(interactions, prev, profile, allow_cloud=allow_cloud)

    monkeypatch.setattr(diagnose, "generate_diagnosis", _wrapper)

    vp = VoicePipeline(asr=None, llm=None, tts=_StubTTS())
    vp.network_mode = "cloud"

    await vp._refresh_directive()

    assert captured["allow_cloud"] is True
