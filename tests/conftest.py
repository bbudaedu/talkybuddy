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


@pytest.fixture
def anyio_backend():
    """限定 anyio 測試只跑 asyncio backend（未安裝 trio）。"""
    return "asyncio"
