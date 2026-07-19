# -*- coding: utf-8 -*-
"""test_llm_n_ctx_profile.py — LLM context 視窗 profile-driven 驗證（EDGE-01）。

比照 tests/test_pipeline_profile.py 的 monkeypatch.setenv/delenv +
importlib.reload(config) 範式，驗證：
- 預設（未設 profile）/ edge profile → config.LLM_N_CTX == 512
- cloud profile → config.LLM_N_CTX == 1024
- TALKYBUDDY_LLM_N_CTX env 覆寫任何 profile 的預設值
- server/llm.py::EdgeLLM._get_model() 以 config.LLM_N_CTX 建構 Llama 的 n_ctx 參數
"""

from __future__ import annotations

import importlib

import pytest

from server import config
from server.llm import EdgeLLM


def _reset_config_env(monkeypatch):
    monkeypatch.delenv("TALKYBUDDY_PIPELINE_PROFILE", raising=False)
    monkeypatch.delenv("TALKYBUDDY_LLM_N_CTX", raising=False)


@pytest.fixture(autouse=True)
def _cleanup_config_reload():
    """每個測試結束後 reload(config) 清理，避免污染其他測試模組層級 import。"""
    yield
    importlib.reload(config)


def test_default_profile_llm_n_ctx_is_512(monkeypatch):
    """未設 TALKYBUDDY_PIPELINE_PROFILE（預設 edge）→ LLM_N_CTX == 512。"""
    _reset_config_env(monkeypatch)
    importlib.reload(config)
    assert config.PIPELINE_PROFILE == "edge"
    assert config.LLM_N_CTX == 512


def test_edge_profile_llm_n_ctx_is_512(monkeypatch):
    """明確設 edge profile → LLM_N_CTX == 512。"""
    _reset_config_env(monkeypatch)
    monkeypatch.setenv("TALKYBUDDY_PIPELINE_PROFILE", "edge")
    importlib.reload(config)
    assert config.LLM_N_CTX == 512


def test_cloud_profile_llm_n_ctx_is_1024(monkeypatch):
    """cloud profile → LLM_N_CTX == 1024。"""
    _reset_config_env(monkeypatch)
    monkeypatch.setenv("TALKYBUDDY_PIPELINE_PROFILE", "cloud")
    importlib.reload(config)
    assert config.LLM_N_CTX == 1024


def test_env_override_wins_over_edge_default(monkeypatch):
    """TALKYBUDDY_LLM_N_CTX 覆寫 edge 預設值。"""
    _reset_config_env(monkeypatch)
    monkeypatch.setenv("TALKYBUDDY_PIPELINE_PROFILE", "edge")
    monkeypatch.setenv("TALKYBUDDY_LLM_N_CTX", "768")
    importlib.reload(config)
    assert config.LLM_N_CTX == 768


def test_env_override_wins_over_cloud_default(monkeypatch):
    """覆寫優先於 profile 預設：即使 profile=cloud，仍以 env 值為準。"""
    _reset_config_env(monkeypatch)
    monkeypatch.setenv("TALKYBUDDY_PIPELINE_PROFILE", "cloud")
    monkeypatch.setenv("TALKYBUDDY_LLM_N_CTX", "768")
    importlib.reload(config)
    assert config.LLM_N_CTX == 768


class _FakeGguf:
    """假 gguf 路徑：exists() 恆回 True，避免依賴真實模型檔。"""

    def exists(self) -> bool:
        return True

    def __str__(self) -> str:
        return "/fake/qwen2.5-1.5b-instruct-q4_k_m.gguf"


class _FakeLlama:
    """攔截 Llama(...) 建構參數的假類別，不真的載入權重。"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_get_model_uses_config_llm_n_ctx(monkeypatch):
    """EdgeLLM._get_model() 建構 Llama 時的 n_ctx 參數取自 config.LLM_N_CTX。"""
    _reset_config_env(monkeypatch)
    monkeypatch.setenv("TALKYBUDDY_PIPELINE_PROFILE", "cloud")
    monkeypatch.setenv("TALKYBUDDY_LLM_N_CTX", "999")
    importlib.reload(config)
    assert config.LLM_N_CTX == 999

    from server import llm as llm_mod

    monkeypatch.setattr(llm_mod, "_get_gguf_path", lambda: _FakeGguf())
    monkeypatch.setattr("llama_cpp.Llama", _FakeLlama)
    monkeypatch.setattr(EdgeLLM, "_model", None)
    monkeypatch.setattr(EdgeLLM, "_model_failed", False)

    edge = EdgeLLM()
    model = edge._get_model()

    assert model is not None
    assert model.kwargs["n_ctx"] == 999
