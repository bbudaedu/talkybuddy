# -*- coding: utf-8 -*-
"""pytest 共用 fixture。

- ``tmp_db``：每個測試都把 ``server.config.DB_PATH`` monkeypatch 到 tmp_path
  下的獨立 SQLite 檔案，並呼叫 ``store.init_db()`` 建表，避免測試互相汙染、
  也避免動到正式的 data/talkybuddy.db。autouse=True，所有測試都套用。
- ``anyio_backend``：把 anyio pytest 外掛（fastapi/starlette 帶入）鎖定在
  asyncio，本專案未安裝 trio，避免 anyio 額外嘗試 trio backend 而報錯。
"""

from __future__ import annotations

import pytest

from server import config, store


def pytest_configure(config):  # noqa: F811 - pytest hook
    """註冊自訂 marker，避免 PytestUnknownMarkWarning。"""
    config.addinivalue_line("markers", "slow: 需載入重模型或長時間執行的測試")


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """把 DB_PATH 導向 tmp 目錄，並建立乾淨的資料表。"""
    db_path = tmp_path / "talkybuddy_test.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    store.init_db()
    yield db_path


@pytest.fixture(autouse=True)
def clear_active_game():
    """清掉裝置級的遊戲狀態（``server.pipeline._active_game``）。

    遊戲狀態刻意掛在行程上而不是 pipeline 實例上（老師用 ``/api/game`` 開的局，
    孩子那條 ``/ws/talk`` 連線要看得到）。代價就是它會跨測試殘留——一條測試開的
    局漏給下一條，斷言「預設沒有遊戲」的測試會莫名其妙地紅。
    """
    from server import pipeline as pipeline_mod

    pipeline_mod._active_game = None
    yield
    pipeline_mod._active_game = None


@pytest.fixture
def anyio_backend():
    """限定 anyio 測試只跑 asyncio backend（未安裝 trio）。"""
    return "asyncio"
