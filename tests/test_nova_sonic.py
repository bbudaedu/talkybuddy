# -*- coding: utf-8 -*-
"""NovaSonicSession + module available() 單元測試（全程 mock SDK，不真打雲端）。"""
from __future__ import annotations

import pytest

from server import nova_sonic


def test_available_false_without_creds(monkeypatch):
    """缺 SigV4 憑證 → available() False（bidi 不吃 bearer token）。"""
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    assert nova_sonic.available() is False


def test_available_true_with_creds(monkeypatch):
    """有 SigV4 憑證且 SDK 可 import → available() True。"""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_TEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret_test")
    assert nova_sonic.available() is True


def test_session_init_stores_params():
    s = nova_sonic.NovaSonicSession(model_id="m", voice="v", region="r")
    assert (s.model_id, s.voice, s.region) == ("m", "v", "r")
