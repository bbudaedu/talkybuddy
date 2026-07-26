# -*- coding: utf-8 -*-
"""test_api_agent_outputs.py — GET /api/agent_outputs（教師儀表板讀 agent 產出）。

三個 agent 的產出存進 store 之後，還要有端點讀得到，儀表板才顯示得出來。
權限比照 /api/diagnoses：JWT 必要，student 讀自己，tutor/device 需帶 ?student=。
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from server import auth, store
from server.app import app

pytestmark = pytest.mark.anyio

_SID = "STUDENT-AMING-004"
_STUDENT_AUTH = {"Authorization": f"Bearer {auth.issue_token(_SID, 'student')}"}

_HOMEWORK = {"focus": "文法", "items": [], "source": "rule"}
_REPORT = {"period": "最近 3 次", "summary": "s", "highlights": [],
           "concerns": [], "suggestions": [], "source": "rule"}


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_empty_when_no_outputs(tmp_db):
    async with await _client() as client:
        resp = await client.get("/api/agent_outputs", headers=_STUDENT_AUTH)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_returns_stored_outputs_new_to_old(tmp_db):
    store.add_agent_output("homework", _HOMEWORK, student_id=_SID)
    store.add_agent_output("report", _REPORT, student_id=_SID)

    async with await _client() as client:
        resp = await client.get("/api/agent_outputs", headers=_STUDENT_AUTH)

    body = resp.json()
    assert [r["kind"] for r in body] == ["report", "homework"]


async def test_kind_filter(tmp_db):
    """儀表板要能分頁顯示作業與週報，混在一起沒有意義。"""
    store.add_agent_output("homework", _HOMEWORK, student_id=_SID)
    store.add_agent_output("report", _REPORT, student_id=_SID)

    async with await _client() as client:
        resp = await client.get("/api/agent_outputs?kind=homework", headers=_STUDENT_AUTH)

    body = resp.json()
    assert len(body) == 1 and body[0]["kind"] == "homework"


async def test_requires_token(tmp_db):
    """沒帶 token → 401。agent 產出含孩子的學習弱項，不可裸奔。"""
    async with await _client() as client:
        resp = await client.get("/api/agent_outputs")
    assert resp.status_code == 401


async def test_student_cannot_read_another_students_outputs(tmp_db):
    """學生 token 只讀得到自己的產出，帶 ?student= 也不能越權。"""
    store.add_agent_output("homework", dict(_HOMEWORK, focus="別人的"),
                           student_id="STUDENT-OTHER-999")
    store.add_agent_output("homework", _HOMEWORK, student_id=_SID)

    async with await _client() as client:
        resp = await client.get(
            "/api/agent_outputs?student=STUDENT-OTHER-999", headers=_STUDENT_AUTH
        )

    body = resp.json()
    assert all(r["student_id"] == _SID for r in body), body
