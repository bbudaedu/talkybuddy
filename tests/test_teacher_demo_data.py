# -*- coding: utf-8 -*-
"""test_teacher_demo_data.py — 教師儀表板必須看得到示範資料。

2026-08-01 現場症狀：教師端的診斷與 14 天趨勢整片空白。DB 裡的 14 筆種子
診斷一直都在，問題出在寫入端與讀取端不對稱——``add_interaction`` 會補上
``student_id``，``add_diagnosis`` 不會，而 ``list_diagnoses(student_id=...)``
正是拿 payload 裡的這個鍵在篩。教師端（tutor 角色）一律要帶 ``?student=``，
帶了就一筆都篩不到。

同一個檔也守著「重置示範資料」端點已被移除：那顆按鈕就在「重新整理」旁邊，
按錯一次就當場清光整場 demo 的互動紀錄。
"""
from __future__ import annotations

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


def _tutor_headers(client) -> dict:
    tok = client.post("/api/login",
                      json={"email": "tutor@demo", "password": "demo1234"}).json()
    return {"Authorization": "Bearer " + tok["token"]}


def test_seeded_diagnoses_are_visible_to_the_teacher(client):
    """教師端帶 ?student= 查得到那 14 筆種子診斷（14 天趨勢圖的來源）。"""
    from server import config

    headers = _tutor_headers(client)
    resp = client.get(f"/api/diagnoses?student={config.STUDENT_ID}", headers=headers)

    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 14, "教師儀表板的診斷與 14 天趨勢會整片空白"
    assert all(d.get("scores") for d in items)


def test_seeded_interactions_are_visible_to_the_teacher(client):
    from server import config

    headers = _tutor_headers(client)
    resp = client.get(
        f"/api/interactions?limit=20&student={config.STUDENT_ID}", headers=headers)

    assert resp.status_code == 200
    assert len(resp.json()) == 20


def test_add_diagnosis_stamps_the_student_id(client):
    """寫入端補 student_id，否則之後每一筆新診斷都會重演同一個 bug。"""
    from server import config, store

    store.add_diagnosis({"date": "2030-01-01", "scores": {"grammar": 60}})

    hit = [d for d in store.list_diagnoses() if d["date"] == "2030-01-01"]
    assert hit and hit[0]["student_id"] == config.STUDENT_ID


def test_legacy_diagnosis_without_student_id_still_shows_up(client):
    """``add_diagnosis`` 補欄位之前寫進去的資料不必搬遷也要看得到。

    ``diagnoses`` 用 date 當 PRIMARY KEY，本來就是單一學生的表，裡面每一列
    都只可能屬於這個 demo 學生。
    """
    from server import config, store

    # 繞過 add_diagnosis 直接寫，重現舊資料的形狀（payload 沒有 student_id）
    import json
    with store._lock:
        conn = store._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO diagnoses (date, payload) VALUES (?, ?)",
            ("2029-12-31", json.dumps({"date": "2029-12-31", "scores": {}})),
        )
        conn.commit()

    dates = [d["date"] for d in store.list_diagnoses(student_id=config.STUDENT_ID)]
    assert "2029-12-31" in dates


def test_seed_reset_endpoint_is_gone(client):
    """重置端點已移除——現場沒有第二次機會，這顆按鈕只會傷到自己。"""
    resp = client.post("/api/seed_reset", headers=_tutor_headers(client))
    assert resp.status_code == 405 or resp.status_code == 404


def test_teacher_page_has_no_reset_button():
    """前端也不能留著按鈕，否則按下去只會靜默失敗、更難查。"""
    from pathlib import Path

    src = Path("web/teacher.html").read_text(encoding="utf-8")
    assert "btnReset" not in src
    assert "/api/seed_reset" not in src
