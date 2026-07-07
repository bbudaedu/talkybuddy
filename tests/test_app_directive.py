# -*- coding: utf-8 -*-
"""test_app_directive.py — B1：切 cloud 時把新診斷 directive 推進 pipeline 快取。"""

from __future__ import annotations

from starlette.testclient import TestClient

from server import app as app_mod


def test_switch_cloud_populates_pipeline_directive(monkeypatch):
    """POST /api/network_mode cloud → pipeline._directive 被填入格式化策略字串。"""
    monkeypatch.setattr(app_mod.pipeline, "_directive", None)
    client = TestClient(app_mod.app)

    resp = client.post("/api/network_mode", json={"mode": "cloud"})

    assert resp.status_code == 200
    assert resp.json()["network_mode"] == "cloud"
    # 診斷一定含 companion_directive → 格式化後推進快取
    assert app_mod.pipeline._directive is not None
    assert "【本輪教學策略】" in app_mod.pipeline._directive
    # B3 接法 A：診斷含 level_state → 推進的 directive 也帶 CEFR 難度區塊
    assert "【CEFR 難度】" in app_mod.pipeline._directive


def test_switch_edge_does_not_touch_directive(monkeypatch):
    """切回 edge → 不動 _directive（保留現值）。"""
    monkeypatch.setattr(app_mod.pipeline, "_directive", "舊策略")
    client = TestClient(app_mod.app)

    client.post("/api/network_mode", json={"mode": "edge"})

    assert app_mod.pipeline._directive == "舊策略"
