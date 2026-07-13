# -*- coding: utf-8 -*-
"""test_app_cloud_llm.py — /api/status 回報 cloud_llm 引擎可用性欄位。"""
from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport

from server.app import app

pytestmark = pytest.mark.anyio


async def test_status_reports_cloud_llm_bool():
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    assert "cloud_llm" in data
    assert isinstance(data["cloud_llm"], bool)
