# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from server import auth


def test_password_roundtrip():
    h = auth.hash_password("demo1234")
    assert auth.verify_password("demo1234", h) is True
    assert auth.verify_password("wrong", h) is False


def test_jwt_roundtrip():
    tok = auth.issue_token("STUDENT-AMING-004", "student")
    claims = auth.verify_token(tok)
    assert claims["sub"] == "STUDENT-AMING-004"
    assert claims["role"] == "student"


def test_jwt_tampered_rejected():
    tok = auth.issue_token("X", "student")
    with pytest.raises(auth.InvalidToken):
        auth.verify_token(tok + "x")


def test_seed_and_authenticate():
    auth.ensure_accounts()
    ok = auth.authenticate("aming@demo", "demo1234")
    assert ok == {"sub": "STUDENT-AMING-004", "role": "student"}
    assert auth.authenticate("aming@demo", "nope") is None
