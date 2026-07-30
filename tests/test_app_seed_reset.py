# -*- coding: utf-8 -*-
"""test_app_seed_reset.py — /api/seed_reset 必須把 agent_outputs 也清掉。

`agent_outputs` 是後來才加的表，重置端點一直漏掉它。後果是：互動與診斷回到
種子狀態，教師儀表板上卻還掛著**上一場 demo** 產生的派作業／週報卡片，
而那些卡片引用的互動紀錄已經不存在了。現場會看到說不通的畫面。

用暫存 DB，不動 data/talkybuddy.db。
"""
from __future__ import annotations

import importlib

import pytest
from starlette.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """把 store 指向暫存 DB 後重新初始化，避免污染真實示範資料。"""
    from server import config, store

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    # store 以模組級單一連線快取 DB handle，換路徑後必須讓它重連。
    monkeypatch.setattr(store, "_conn", None, raising=False)
    store.init_db()

    from server.app import app
    with TestClient(app) as c:
        yield c

    monkeypatch.setattr(store, "_conn", None, raising=False)


def test_seed_reset_clears_agent_outputs(client):
    from server import store

    store.add_agent_output(
        "homework",
        {"focus": "文法（grammar）", "items": [], "source": "rule"},
        student_id="STUDENT-AMING-004",
    )
    assert store.list_agent_outputs(student_id="STUDENT-AMING-004")

    resp = client.post("/api/seed_reset")

    assert resp.status_code == 200
    assert store.list_agent_outputs(student_id="STUDENT-AMING-004") == [], (
        "重置後仍留著上一場 demo 的 agent 產出——那些卡片引用的互動已被刪除"
    )


def test_seed_reset_still_reseeds_demo_data(client):
    """清得掉不代表灌得回來：既有行為（重灌示範資料）不得被破壞。"""
    from server import store

    client.post("/api/seed_reset")

    assert store.list_interactions(limit=50), "重置後沒有重灌示範互動"
    assert store.list_diagnoses(), "重置後沒有重灌示範診斷"
