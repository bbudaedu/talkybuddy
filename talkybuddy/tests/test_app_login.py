# -*- coding: utf-8 -*-
from __future__ import annotations

from starlette.testclient import TestClient

from server import app as app_mod


def test_login_success_returns_token():
    client = TestClient(app_mod.app)
    r = client.post("/api/login", json={"email": "aming@demo", "password": "demo1234"})
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "student"
    assert body["sub"] == "STUDENT-AMING-004"
    assert body["token"].count(".") == 2


def test_login_bad_password_401():
    client = TestClient(app_mod.app)
    r = client.post("/api/login", json={"email": "aming@demo", "password": "nope"})
    assert r.status_code == 401
