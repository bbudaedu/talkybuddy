# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib

from server import config


def test_default_profile_is_edge(monkeypatch):
    monkeypatch.delenv("TALKYBUDDY_PIPELINE_PROFILE", raising=False)
    importlib.reload(config)
    assert config.PIPELINE_PROFILE == "edge"
    assert config.default_network_mode() == "edge"


def test_cloud_profile_sets_cloud_mode(monkeypatch):
    monkeypatch.setenv("TALKYBUDDY_PIPELINE_PROFILE", "cloud")
    importlib.reload(config)
    assert config.PIPELINE_PROFILE == "cloud"
    assert config.default_network_mode() == "cloud"
    monkeypatch.delenv("TALKYBUDDY_PIPELINE_PROFILE", raising=False)
    importlib.reload(config)
