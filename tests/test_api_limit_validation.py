# -*- coding: utf-8 -*-
"""test_api_limit_validation.py — 列表端點的 limit 參數驗證（code review W5）。

`?limit=-1` 在 SQLite 的 `LIMIT -1` 等於「無上限」——一個未驗證的查詢參數
就能把整張表撈出來。這兩個端點回的是孩子的互動逐字稿與學習弱項，
與診斷同級的個資，不能讓外部輸入決定回傳量。

上限訂在 200：儀表板一頁最多顯示幾十筆，200 已經寬鬆。
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from server import auth, store
from server.app import app

pytestmark = pytest.mark.anyio

_SID = "STUDENT-AMING-004"
_AUTH = {"Authorization": f"Bearer {auth.issue_token(_SID, 'student')}"}

_HOMEWORK = {"focus": "文法", "items": [], "source": "rule"}


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize("path", ["/api/agent_outputs", "/api/interactions"])
@pytest.mark.parametrize("bad", ["-1", "0", "999999", "abc"])
async def test_invalid_limit_is_rejected(tmp_db, path, bad):
    async with await _client() as client:
        resp = await client.get(f"{path}?limit={bad}", headers=_AUTH)
    assert resp.status_code == 422, f"{path}?limit={bad} 應被拒絕，實得 {resp.status_code}"


@pytest.mark.parametrize("path", ["/api/agent_outputs", "/api/interactions"])
async def test_valid_limit_still_works(tmp_db, path):
    store.add_agent_output("homework", _HOMEWORK, student_id=_SID)
    async with await _client() as client:
        resp = await client.get(f"{path}?limit=5", headers=_AUTH)
    assert resp.status_code == 200


async def test_negative_limit_would_return_everything_without_the_guard(tmp_db):
    """記錄這條缺陷為什麼危險：SQLite 的 LIMIT -1 是無上限。

    直接打 store 層驗證——端點擋住了，但底層仍不該把負數當成「全部」。
    """
    for i in range(25):
        store.add_agent_output("homework", dict(_HOMEWORK, focus=f"f{i}"), student_id=_SID)

    rows = store.list_agent_outputs(limit=-1, student_id=_SID)
    assert len(rows) <= 20, f"limit=-1 撈出了 {len(rows)} 筆，等於無上限"
