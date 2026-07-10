# -*- coding: utf-8 -*-
"""auth.py — 最簡登入：pbkdf2 密碼 + stdlib HMAC-SHA256 JWT + seed 帳號。

不加第三方套件；demo 用固定 secret（可由環境變數覆寫）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from server import store

SECRET: str = os.environ.get("TALKYBUDDY_JWT_SECRET", "dev-only-secret-change-me")
_PBKDF2_ROUNDS = 100_000


class InvalidToken(Exception):
    pass


# ---- 密碼 ----
def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt_hex, hash_hex = stored.split("$")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), _PBKDF2_ROUNDS)
    return hmac.compare_digest(dk.hex(), hash_hex)


# ---- JWT（HS256）----
def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(sub: str, role: str, ttl: int = 86400) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": sub, "role": role, "exp": int(time.time()) + ttl}
    seg = _b64url(json.dumps(header).encode()) + "." + _b64url(json.dumps(payload).encode())
    sig = hmac.new(SECRET.encode(), seg.encode(), hashlib.sha256).digest()
    return seg + "." + _b64url(sig)


def verify_token(token: str) -> dict:
    try:
        seg_h, seg_p, seg_s = token.split(".")
    except ValueError:
        raise InvalidToken("malformed")
    signing_input = f"{seg_h}.{seg_p}"
    expected = hmac.new(SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _b64url_decode(seg_s)):
        raise InvalidToken("bad signature")
    payload = json.loads(_b64url_decode(seg_p))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise InvalidToken("expired")
    return payload


# ---- 帳號（存 SQLite accounts 表）----
_SEED = [
    ("tutor@demo", "demo1234", "TUTOR-001", "tutor"),
    ("aming@demo", "demo1234", "STUDENT-AMING-004", "student"),
    ("device:GENIO-520-X992", "demo1234", "STUDENT-AMING-004", "device"),
]


def ensure_accounts() -> None:
    with store._lock:
        conn = store._get_conn()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS accounts ("
            " email TEXT PRIMARY KEY, pw_hash TEXT NOT NULL,"
            " sub TEXT NOT NULL, role TEXT NOT NULL)"
        )
        for email, pw, sub, role in _SEED:
            row = conn.execute("SELECT 1 FROM accounts WHERE email=?", (email,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO accounts (email, pw_hash, sub, role) VALUES (?,?,?,?)",
                    (email, hash_password(pw), sub, role),
                )
        conn.commit()


def authenticate(email: str, pw: str) -> dict | None:
    ensure_accounts()
    with store._lock:
        conn = store._get_conn()
        row = conn.execute(
            "SELECT pw_hash, sub, role FROM accounts WHERE email=?", (email,)
        ).fetchone()
    if row is None:
        return None
    pw_hash, sub, role = row
    if not verify_password(pw, pw_hash):
        return None
    return {"sub": sub, "role": role}
