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

_DEV_SECRET = "dev-only-secret-change-me"
SECRET: str = os.environ.get("TALKYBUDDY_JWT_SECRET", _DEV_SECRET)
_PBKDF2_ROUNDS = 100_000

# 對外開放時綁的位址。0.0.0.0 / :: 代表「任何人都連得到」。
_PUBLIC_BINDS = ("0.0.0.0", "::", "")


def _is_publicly_bound() -> bool:
    """這個行程有沒有對外開放。讀 uvicorn 慣用的 HOST 環境變數。"""
    host = os.environ.get("TALKYBUDDY_HOST", os.environ.get("HOST", "127.0.0.1"))
    return host.strip() in _PUBLIC_BINDS


def assert_secret_is_safe() -> None:
    """對外開放卻還用預設 secret → 直接拒絕啟動。

    **為什麼要擋而不是印警告。** ``_DEV_SECRET`` 這個值在 2026-07-22 就隨
    ``origin/master`` 公開在 GitHub 上了。任何人 clone 這個 repo，就能對任何
    沒設 ``TALKYBUDDY_JWT_SECRET`` 的對外部署簽出合法 token，直接讀到教師
    儀表板與孩子的互動紀錄——那是兒童學習資料。

    決賽要交一個公開的 Live Demo 網址，而「部署時記得設環境變數」是一件
    在會場、趕死線、體力耗盡時最容易漏掉的事。所以把漏掉的代價從
    **資料外洩** 換成 **啟動失敗**：後者五秒鐘就會被發現。

    本機開發（綁 127.0.0.1）不受影響，維持零設定即可跑。
    """
    if SECRET != _DEV_SECRET or not _is_publicly_bound():
        return
    raise RuntimeError(
        "拒絕啟動：對外開放（HOST=0.0.0.0）卻仍在使用公開在 GitHub 上的預設 "
        "JWT secret。任何人都能偽造 token 讀取學生資料。\n"
        "修法：export TALKYBUDDY_JWT_SECRET=\"$(python3 -c "
        "'import secrets;print(secrets.token_urlsafe(32))')\"\n"
        "（僅綁 127.0.0.1 的本機開發不受此限制）"
    )


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
    try:
        if not hmac.compare_digest(expected, _b64url_decode(seg_s)):
            raise InvalidToken("bad signature")
        payload = json.loads(_b64url_decode(seg_p))
    except (ValueError, TypeError) as e:
        raise InvalidToken("malformed") from e
    if not isinstance(payload, dict):
        raise InvalidToken("malformed payload")
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
