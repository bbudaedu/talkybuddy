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


def test_jwt_wrong_segment_count_rejected():
    with pytest.raises(auth.InvalidToken):
        auth.verify_token("not-a-jwt")


def test_jwt_invalid_base64_rejected():
    with pytest.raises(auth.InvalidToken):
        auth.verify_token("a.b.c")


def test_jwt_non_json_payload_rejected():
    # valid signature over a non-JSON payload must still map to InvalidToken
    import base64
    import hashlib
    import hmac

    def b64url(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    seg_h = b64url(b'{"alg":"HS256","typ":"JWT"}')
    seg_p = b64url(b"not-json")
    seg = f"{seg_h}.{seg_p}"
    sig = hmac.new(auth.SECRET.encode(), seg.encode(), hashlib.sha256).digest()
    tok = seg + "." + b64url(sig)
    with pytest.raises(auth.InvalidToken):
        auth.verify_token(tok)


def test_seed_and_authenticate():
    auth.ensure_accounts()
    ok = auth.authenticate("aming@demo", "demo1234")
    assert ok == {"sub": "STUDENT-AMING-004", "role": "student"}
    assert auth.authenticate("aming@demo", "nope") is None
