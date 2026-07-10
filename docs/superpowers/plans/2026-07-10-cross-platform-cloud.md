# 跨平台雲端登入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓導師與學生跨平台（PC／平板／手機瀏覽器）登入使用；玩偶跑本地 AI、瀏覽器全語音走雲端 AI，單一身份跨終端同步、雲端為唯一真相。

**Architecture:** 沿用現有 FastAPI + WebSocket 邊緣管線，加三塊：①JWT 登入（stdlib HMAC 自製，免加依賴）②每連線一個 `VoicePipeline` 實例（共用引擎、綁 `student_id`），解單例污染 ③`POST /sync` 收玩偶上行、去重合併，store 讀寫改為 student 範圍。方案 C：同骨架、config 切 provider，鷹架引擎兩邊共用。

**Tech Stack:** Python 3.12、FastAPI/Starlette、SQLite、pytest（`starlette.testclient.TestClient`）、stdlib `hmac`/`hashlib`/`pbkdf2`。

## Global Constraints

- Python 3.12，套件相對 import（`from server import ...`）；`server/__init__.py`、`tests/__init__.py` 需存在。
- 重依賴一律 lazy import + try/except，模組提供 `available()`，import 期不得炸。
- **不新增第三方套件**：JWT 用 stdlib HMAC-SHA256 自製，密碼用 `hashlib.pbkdf2_hmac`。
- 測試指令一律：於 `talkybuddy/` 執行 `.venv/bin/python -m pytest -q`（單檔：`.venv/bin/python -m pytest tests/test_x.py -q`）。
- 測試沿用 `tests/conftest.py` 的 autouse `tmp_db`（monkeypatch `config.DB_PATH` + `store.init_db()`）。
- WebSocket 協定不得變更：維持 `text_input` / `audio_end` / `asr_result` / `reply` / `tts_audio`（見 `talkybuddy/CONTRACTS.md`）。
- Secret 一律走環境變數，絕不 commit。**上雲前先撤銷記憶中外露的舊 ElevenLabs 金鑰**（見 Task 9）。
- 每個 Task 結束都要 commit。

---

### Task 1: store 改為 student 範圍讀取

現在 `list_interactions` / `list_diagnoses` 回傳「全部」，多學生雲端會混戶。改成可依 `student_id` 過濾（省略＝維持舊行為，保邊緣單機相容）。

**Files:**
- Modify: `talkybuddy/server/store.py`（`list_interactions`、`list_diagnoses`）
- Test: `talkybuddy/tests/test_store_scope.py`（Create）

**Interfaces:**
- Consumes:（無，改動既有函式）
- Produces:
  - `list_interactions(limit: int = 50, student_id: str | None = None) -> list[dict]`
  - `list_diagnoses(student_id: str | None = None) -> list[dict]`
  - 語意：`student_id=None` → 回全部（舊行為）；給值 → 只回該生。

- [ ] **Step 1: 寫失敗測試**

```python
# talkybuddy/tests/test_store_scope.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from server import store


def test_list_interactions_filters_by_student():
    store.add_interaction({"student_text": "hi A", "student_id": "STU-A"})
    store.add_interaction({"student_text": "hi B", "student_id": "STU-B"})

    only_a = store.list_interactions(student_id="STU-A")
    assert len(only_a) == 1
    assert only_a[0]["student_id"] == "STU-A"

    all_rows = store.list_interactions()  # 省略 → 全部
    assert len(all_rows) == 2
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_store_scope.py -q`
Expected: FAIL（`list_interactions()` 尚不吃 `student_id` → TypeError 或斷言錯）

- [ ] **Step 3: 實作最小改動**

在 `store.py` 的 `list_interactions` 加參數與過濾（payload 是 JSON TEXT，於 Python 端過濾最省事、不改 schema）：

```python
def list_interactions(limit: int = 50, student_id: str | None = None) -> list[dict]:
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT seq, payload, synced FROM interactions ORDER BY seq DESC"
        ).fetchall()
    items = [_row_to_interaction(seq, payload, synced) for seq, payload, synced in rows]
    if student_id is not None:
        items = [it for it in items if it.get("student_id") == student_id]
    return items[:limit]


def list_diagnoses(student_id: str | None = None) -> list[dict]:
    with _lock:
        conn = _get_conn()
        rows = conn.execute("SELECT payload FROM diagnoses ORDER BY date ASC").fetchall()
    items = [json.loads(p) for (p,) in rows]
    if student_id is not None:
        items = [d for d in items if d.get("student_id") == student_id]
    return items
```

（註：`diagnoses` payload 目前未必帶 `student_id`；`generate_diagnosis` 於 Task 7 補寫入。過濾對舊 mock 資料為寬鬆——省略參數時全回，維持 demo 儀表板現況。）

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_store_scope.py -q`
Expected: PASS

- [ ] **Step 5: 回歸主要測試**

Run: `.venv/bin/python -m pytest tests/test_store.py tests/test_app_profile.py -q`
Expected: PASS（省略參數維持舊行為）

- [ ] **Step 6: Commit**

```bash
git add talkybuddy/server/store.py talkybuddy/tests/test_store_scope.py
git commit -m "feat(store): student-scoped list_interactions/list_diagnoses (opt-in filter)"
```

---

### Task 2: auth 模組（密碼雜湊 + HMAC JWT + seed 帳號）

自製 stdlib JWT 與 pbkdf2 密碼，避免加依賴。帳號存新表 `accounts`。

**Files:**
- Create: `talkybuddy/server/auth.py`
- Test: `talkybuddy/tests/test_auth.py`（Create）

**Interfaces:**
- Produces:
  - `hash_password(pw: str) -> str`（回 `"pbkdf2$<salt_hex>$<hash_hex>"`）
  - `verify_password(pw: str, stored: str) -> bool`
  - `issue_token(sub: str, role: str, ttl: int = 86400) -> str`
  - `verify_token(token: str) -> dict`（回 `{"sub","role","exp"}`；失敗 raise `InvalidToken`）
  - `class InvalidToken(Exception)`
  - `ensure_accounts() -> None`（建 `accounts` 表 + seed 三筆）
  - `authenticate(email: str, pw: str) -> dict | None`（回 `{"sub","role"}` 或 None）
  - `SECRET` 取自 `os.environ["TALKYBUDDY_JWT_SECRET"]`，未設時用固定 dev 值（僅本機）。
  - Seed 帳號：`tutor@demo`(role=tutor, sub=`TUTOR-001`)、`aming@demo`(role=student, sub=`STUDENT-AMING-004`)、device 帳號 `device:GENIO-520-X992`(role=device, sub=`STUDENT-AMING-004`)。密碼皆 `demo1234`。

- [ ] **Step 1: 寫失敗測試**

```python
# talkybuddy/tests/test_auth.py
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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_auth.py -q`
Expected: FAIL（`server.auth` 不存在 → ImportError）

- [ ] **Step 3: 實作 auth.py**

```python
# talkybuddy/server/auth.py
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
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_auth.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add talkybuddy/server/auth.py talkybuddy/tests/test_auth.py
git commit -m "feat(auth): stdlib JWT + pbkdf2 passwords + seeded accounts"
```

---

### Task 3: POST /api/login 端點

**Files:**
- Modify: `talkybuddy/server/app.py`（新增 `LoginBody` + `/api/login`）
- Test: `talkybuddy/tests/test_app_login.py`（Create）

**Interfaces:**
- Consumes: `auth.authenticate`, `auth.issue_token`（Task 2）
- Produces: `POST /api/login {email,password}` → 200 `{token, role, sub}` / 401 `{detail}`

- [ ] **Step 1: 寫失敗測試**

```python
# talkybuddy/tests/test_app_login.py
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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_app_login.py -q`
Expected: FAIL（404，尚無 `/api/login`）

- [ ] **Step 3: 實作端點**

在 `app.py` import 區加 `from server import auth`；在 REST 區加：

```python
class LoginBody(BaseModel):
    email: str
    password: str


@app.post("/api/login")
async def api_login(body: LoginBody):
    ident = auth.authenticate(body.email, body.password)
    if ident is None:
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")
    token = auth.issue_token(ident["sub"], ident["role"])
    return {"token": token, "role": ident["role"], "sub": ident["sub"]}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_app_login.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add talkybuddy/server/app.py talkybuddy/tests/test_app_login.py
git commit -m "feat(api): POST /api/login issues JWT"
```

---

### Task 4: 身份解析 + 保護 REST 讀取（student 範圍 / tutor 可查 ?student=）

**Files:**
- Modify: `talkybuddy/server/app.py`（identity helper + `/api/interactions`、`/api/diagnoses`）
- Test: `talkybuddy/tests/test_app_authz.py`（Create）

**Interfaces:**
- Consumes: `auth.verify_token`（Task 2）、`store.list_interactions/list_diagnoses(student_id=...)`（Task 1）
- Produces:
  - `identity_from_header(authorization: str | None) -> dict`（回 claims；缺/壞 raise 401）
  - `GET /api/interactions`：`student` 讀自己；`tutor` 可帶 `?student=<id>`（未帶則需明確錯誤）
  - `GET /api/diagnoses`：同上範圍規則

- [ ] **Step 1: 寫失敗測試**

```python
# talkybuddy/tests/test_app_authz.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from starlette.testclient import TestClient

from server import app as app_mod, auth, store


def _tok(sub, role):
    return auth.issue_token(sub, role)


def test_interactions_requires_token():
    client = TestClient(app_mod.app)
    assert client.get("/api/interactions").status_code == 401


def test_student_sees_only_own():
    store.add_interaction({"student_text": "mine", "student_id": "STUDENT-AMING-004"})
    store.add_interaction({"student_text": "other", "student_id": "STU-OTHER"})
    client = TestClient(app_mod.app)
    h = {"Authorization": f"Bearer {_tok('STUDENT-AMING-004', 'student')}"}
    rows = client.get("/api/interactions", headers=h).json()
    assert all(r["student_id"] == "STUDENT-AMING-004" for r in rows)


def test_tutor_can_query_student():
    store.add_interaction({"student_text": "mine", "student_id": "STUDENT-AMING-004"})
    client = TestClient(app_mod.app)
    h = {"Authorization": f"Bearer {_tok('TUTOR-001', 'tutor')}"}
    rows = client.get("/api/interactions?student=STUDENT-AMING-004", headers=h).json()
    assert len(rows) == 1
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_app_authz.py -q`
Expected: FAIL（目前端點無鑑權、回 200 且不過濾）

- [ ] **Step 3: 實作 identity helper 與範圍規則**

在 `app.py` 加（import 區已含 `from fastapi import Header` → 若無則補）：

```python
from fastapi import Header  # 加到既有 fastapi import

def identity_from_header(authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少或格式錯誤的 token")
    try:
        return auth.verify_token(authorization[len("Bearer "):])
    except auth.InvalidToken:
        raise HTTPException(status_code=401, detail="token 無效或過期")


def _resolve_student(claims: dict, student_query: str | None) -> str:
    if claims["role"] == "student":
        return claims["sub"]
    # tutor / device：需明確指定學生
    if not student_query:
        raise HTTPException(status_code=400, detail="tutor 需帶 ?student=<id>")
    return student_query
```

改兩個端點：

```python
@app.get("/api/interactions")
async def api_interactions(limit: int = 50, student: str | None = None,
                           authorization: str | None = Header(default=None)):
    claims = identity_from_header(authorization)
    sid = _resolve_student(claims, student)
    return store.list_interactions(limit=limit, student_id=sid)


@app.get("/api/diagnoses")
async def api_diagnoses(student: str | None = None,
                        authorization: str | None = Header(default=None)):
    claims = identity_from_header(authorization)
    sid = _resolve_student(claims, student)
    return store.list_diagnoses(student_id=sid)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_app_authz.py -q`
Expected: PASS

- [ ] **Step 5: 回歸既有 app 測試（可能需補 token）**

Run: `.venv/bin/python -m pytest tests/test_app_directive.py tests/test_app_profile.py -q`
Expected: PASS；若既有測試打 `/api/interactions` 無 token 而紅，於該測試補 `Authorization` header（同 Step 1 寫法）。

- [ ] **Step 6: Commit**

```bash
git add talkybuddy/server/app.py talkybuddy/tests/test_app_authz.py
git commit -m "feat(api): token-scoped interactions/diagnoses (student self / tutor query)"
```

---

### Task 5: WS /ws/talk 帶身份 + 每連線 VoicePipeline 實例

解單例污染：每條 WS 連線建**獨立 `VoicePipeline`（共用引擎、綁 student_id）**，本輪互動以該身份寫入。

**Files:**
- Modify: `talkybuddy/server/app.py`（`ws_talk` 開頭解析 token、建 per-conn pipeline）
- Modify: `talkybuddy/server/pipeline.py`（`__init__` 增 `student_id` 綁定；寫互動時帶入）
- Test: `talkybuddy/tests/test_ws_identity.py`（Create）

**Interfaces:**
- Consumes: `auth.verify_token`、`VoicePipeline(asr, llm, tts, cloud_tts=None, student_id=None)`
- Produces:
  - `VoicePipeline.__init__(..., student_id: str | None = None)`；`self.student_id`
  - `ws_talk`：連線時從 query `?token=` 解身份，`websocket.accept()` 後建
    `conn_pipe = VoicePipeline(asr_engine, llm_engine, tts_engine, cloud_tts=cloud_tts_engine, student_id=sid)`；
    後續 `run_turn_*` 全用 `conn_pipe`。缺/壞 token → `close(code=1008)`。

- [ ] **Step 1: 寫失敗測試**

```python
# talkybuddy/tests/test_ws_identity.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from starlette.testclient import TestClient

from server import app as app_mod, auth, store


def test_ws_writes_interaction_under_token_student():
    tok = auth.issue_token("STU-WS-1", "student")
    client = TestClient(app_mod.app)
    with client.websocket_connect(f"/ws/talk?token={tok}") as ws:
        ws.send_json({"type": "text_input", "source": "manual", "text": "hello"})
        # 收到 reply 事件即可（不驗 TTS 內容）
        for _ in range(6):
            msg = ws.receive_json()
            if msg.get("type") == "reply":
                break
    rows = store.list_interactions(student_id="STU-WS-1")
    assert len(rows) >= 1


def test_ws_without_token_rejected():
    client = TestClient(app_mod.app)
    try:
        with client.websocket_connect("/ws/talk") as ws:
            ws.receive_json()
        rejected = False
    except Exception:
        rejected = True
    assert rejected
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_ws_identity.py -q`
Expected: FAIL（目前 WS 不解 token、互動寫死 config STUDENT_ID）

- [ ] **Step 3a: pipeline 綁 student_id**

`pipeline.py` `VoicePipeline.__init__` 末尾加參數與欄位：

```python
    def __init__(self, asr, llm, tts, cloud_tts=None, student_id=None):
        # ... 既有指派保持不變 ...
        self.student_id = student_id
```

在 `_process_text`（或實際寫入 `store.add_interaction` 之處）把身份帶進 payload：

```python
        # 寫互動紀錄時帶上本連線身份（None 時 store 退回 config 預設）
        interaction = {
            # ... 既有欄位 ...
        }
        if self.student_id is not None:
            interaction["student_id"] = self.student_id
        store.add_interaction(interaction)
```

（找到 `pipeline.py` 內既有 `store.add_interaction(...)` 呼叫點，於該 dict 補 `student_id`。）

- [ ] **Step 3b: ws_talk 解 token + per-conn pipeline**

`app.py` `ws_talk` 內，`await websocket.accept()` 之後、進入收訊迴圈之前：

```python
    token = websocket.query_params.get("token")
    try:
        claims = auth.verify_token(token) if token else None
    except auth.InvalidToken:
        claims = None
    if claims is None:
        await websocket.close(code=1008)  # policy violation
        return
    sid = claims["sub"]
    conn_pipe = VoicePipeline(
        asr_engine, llm_engine, tts_engine,
        cloud_tts=cloud_tts_engine, student_id=sid,
    )
    conn_pipe.network_mode = pipeline.network_mode  # 承接目前模式
```

把本函式內後續所有 `pipeline.run_turn_audio(...)` / `pipeline.run_turn_text(...)` 改成 `conn_pipe.run_turn_audio(...)` / `conn_pipe.run_turn_text(...)`。

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_ws_identity.py -q`
Expected: PASS

- [ ] **Step 5: 回歸管線/ e2e 測試**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py tests/test_e2e.py -q`
Expected: PASS（`student_id=None` 時行為與舊單例一致；若既有 e2e 直連 `/ws/talk`，補 `?token=`）

- [ ] **Step 6: Commit**

```bash
git add talkybuddy/server/app.py talkybuddy/server/pipeline.py talkybuddy/tests/test_ws_identity.py
git commit -m "feat(ws): per-connection VoicePipeline bound to token student_id"
```

---

### Task 6: PIPELINE_PROFILE 收斂（edge / cloud provider 綁定）

把零散旗標收斂成單一 profile；雲端佈署設 `cloud`、玩偶設 `edge`。行為以**選 TTS provider** 為 demo 的可見差異（LLM 升級留為 config 開關，不強制）。

**Files:**
- Modify: `talkybuddy/server/config.py`（新增 `PIPELINE_PROFILE` + 衍生預設）
- Test: `talkybuddy/tests/test_pipeline_profile.py`（Create）

**Interfaces:**
- Produces:
  - `config.PIPELINE_PROFILE: str`（`"edge"` | `"cloud"`，env `TALKYBUDDY_PIPELINE_PROFILE` 覆寫，預設 `edge`）
  - `config.default_network_mode() -> str`（`cloud` profile → `"cloud"`；否則 `"edge"`）

- [ ] **Step 1: 寫失敗測試**

```python
# talkybuddy/tests/test_pipeline_profile.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib

from server import config


def test_default_profile_is_edge(monkeypatch):
    monkeypatch.delenv("TALKYBUDDY_PIPELINE_PROFILE", raising=False)
    importlib.reload(config)
    assert config.PIPELINE_PROFILE == "edge"
    assert config.default_network_mode() == "edge"


def test_cloud_profile_sets_cloud_mode(monkeypatch):
    monkeypatch.setenv("TALKYBUDDY_PIPELINE_PROFILE", "cloud")
    importlib.reload(config)
    assert config.PIPELINE_PROFILE == "cloud"
    assert config.default_network_mode() == "cloud"
    monkeypatch.delenv("TALKYBUDDY_PIPELINE_PROFILE", raising=False)
    importlib.reload(config)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_pipeline_profile.py -q`
Expected: FAIL（`PIPELINE_PROFILE` 未定義）

- [ ] **Step 3: 實作 config**

`config.py` 末尾加：

```python
# ---------------------------------------------------------------------------
# 佈署 profile：edge（玩偶本地）/ cloud（瀏覽器終端，AI 在雲端）
# ---------------------------------------------------------------------------
PIPELINE_PROFILE: str = os.environ.get("TALKYBUDDY_PIPELINE_PROFILE", "edge")


def default_network_mode() -> str:
    """cloud profile 預設走雲端管線（ElevenLabs TTS 等）；否則邊緣。"""
    return "cloud" if PIPELINE_PROFILE == "cloud" else "edge"
```

- [ ] **Step 4: 讓 app 啟動採用 profile 預設模式**

`app.py` 建立 `pipeline` 之後加一行（承接 profile 預設）：

```python
pipeline.network_mode = config.default_network_mode()
```

- [ ] **Step 5: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_pipeline_profile.py tests/test_app_cloud_tts.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add talkybuddy/server/config.py talkybuddy/server/app.py talkybuddy/tests/test_pipeline_profile.py
git commit -m "feat(config): PIPELINE_PROFILE edge/cloud drives default network_mode"
```

---

### Task 7: POST /sync 端點（玩偶上行、去重合併、診斷帶 student_id）

**Files:**
- Modify: `talkybuddy/server/app.py`（`/api/sync`）
- Modify: `talkybuddy/server/store.py`（`add_interaction` 去重輔助）
- Test: `talkybuddy/tests/test_app_sync.py`（Create）

**Interfaces:**
- Consumes: `identity_from_header`（Task 4）、`store.add_interaction`
- Produces:
  - `store.interaction_exists(student_id: str, device_id: str, client_ts: str) -> bool`
  - `POST /api/sync`（device token）body `{"interactions": [ ... ]}` →
    去重（同 `student_id`+`device_id`+`client_ts` 不重複插入）→ 回 `{"accepted": n, "skipped": m}`

- [ ] **Step 1: 寫失敗測試**

```python
# talkybuddy/tests/test_app_sync.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from starlette.testclient import TestClient

from server import app as app_mod, auth, store


def _device_tok():
    # device 帳號 sub 綁 STUDENT-AMING-004
    return auth.issue_token("STUDENT-AMING-004", "device")


def test_sync_accepts_and_dedups():
    client = TestClient(app_mod.app)
    h = {"Authorization": f"Bearer {_device_tok()}"}
    batch = {"interactions": [
        {"student_text": "doll turn 1", "device_id": "GENIO-520-X992", "client_ts": "2026-07-10T10:00:00"},
        {"student_text": "doll turn 2", "device_id": "GENIO-520-X992", "client_ts": "2026-07-10T10:01:00"},
    ]}
    r1 = client.post("/api/sync", json=batch, headers=h)
    assert r1.json() == {"accepted": 2, "skipped": 0}

    # 重送同批 → 全部跳過
    r2 = client.post("/api/sync", json=batch, headers=h)
    assert r2.json() == {"accepted": 0, "skipped": 2}

    rows = store.list_interactions(student_id="STUDENT-AMING-004")
    assert len(rows) == 2
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_app_sync.py -q`
Expected: FAIL（無 `/api/sync`）

- [ ] **Step 3a: store 去重輔助**

`store.py` 加：

```python
def interaction_exists(student_id: str, device_id: str, client_ts: str) -> bool:
    for it in list_interactions(limit=100000, student_id=student_id):
        if it.get("device_id") == device_id and it.get("client_ts") == client_ts:
            return True
    return False
```

- [ ] **Step 3b: /api/sync 端點**

`app.py` 加：

```python
class SyncBody(BaseModel):
    interactions: list[dict]


@app.post("/api/sync")
async def api_sync(body: SyncBody, authorization: str | None = Header(default=None)):
    claims = identity_from_header(authorization)
    sid = claims["sub"]  # device token 綁定的 student
    accepted = skipped = 0
    for it in body.interactions:
        rec = dict(it)
        rec["student_id"] = sid
        dev = str(rec.get("device_id", ""))
        cts = str(rec.get("client_ts", ""))
        if cts and store.interaction_exists(sid, dev, cts):
            skipped += 1
            continue
        store.add_interaction(rec)
        accepted += 1
    return {"accepted": accepted, "skipped": skipped}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_app_sync.py -q`
Expected: PASS

- [ ] **Step 5: 診斷帶 student_id（供 Task 1 的 diagnoses 過濾）**

於 `diagnose.generate_diagnosis` 產出的 dict 補 `student_id`（若函式簽名無 student，於 `app.py` `api_network_mode` 寫入前補：`new_diag["student_id"] = store._student_id()`）。加一行即可，維持既有測試。

- [ ] **Step 6: Commit**

```bash
git add talkybuddy/server/app.py talkybuddy/server/store.py talkybuddy/tests/test_app_sync.py
git commit -m "feat(api): POST /api/sync ingests doll interactions with dedup"
```

---

### Task 8: 玩偶上行同步 client + 前端登入接線

**Files:**
- Create: `talkybuddy/server/sync_client.py`（玩偶端：讀 `synced=0` → POST /api/sync → `mark_all_synced`）
- Modify: `talkybuddy/web/index.html`（登入畫面→存 token→ WS 帶 `?token=`、API 帶 Bearer）
- Modify: `talkybuddy/web/teacher.html`（登入→帶 token 讀 `/api/interactions?student=`、`/api/diagnoses?student=`）
- Test: `talkybuddy/tests/test_sync_client.py`（Create）

**Interfaces:**
- Consumes: `store.list_interactions`、`store.mark_all_synced`、`auth.issue_token`（device）
- Produces: `sync_client.push_pending(base_url: str, token: str, http_post) -> dict`
  （`http_post(url, json, headers)->obj` 注入以利測試；回 `{"accepted","skipped"}`）

- [ ] **Step 1: 寫失敗測試（注入假 http_post，不打真網路）**

```python
# talkybuddy/tests/test_sync_client.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from server import store, sync_client


def test_push_pending_sends_unsynced_and_marks():
    store.add_interaction({"student_text": "t1", "device_id": "D1", "client_ts": "2026-07-10T10:00:00"})
    sent = {}

    def fake_post(url, json, headers):
        sent["url"] = url
        sent["count"] = len(json["interactions"])
        return {"accepted": sent["count"], "skipped": 0}

    res = sync_client.push_pending("http://cloud", "tok", fake_post)
    assert sent["count"] == 1
    assert res["accepted"] == 1
    assert store.pending_count() == 0  # 已 mark_all_synced
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_sync_client.py -q`
Expected: FAIL（無 `server.sync_client`）

- [ ] **Step 3: 實作 sync_client.py**

```python
# talkybuddy/server/sync_client.py
# -*- coding: utf-8 -*-
"""玩偶端上行同步：把本地未同步互動批次送上雲端。"""
from __future__ import annotations

from server import store


def push_pending(base_url: str, token: str, http_post) -> dict:
    pending = [it for it in store.list_interactions(limit=100000) if not it.get("synced")]
    if not pending:
        return {"accepted": 0, "skipped": 0}
    payload = {"interactions": pending}
    headers = {"Authorization": f"Bearer {token}"}
    resp = http_post(f"{base_url}/api/sync", payload, headers)
    if resp.get("accepted", 0) or resp.get("skipped", 0):
        store.mark_all_synced()
    return resp
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_sync_client.py -q`
Expected: PASS

- [ ] **Step 5: 前端登入接線（手動驗收，無自動測試）**

`web/index.html`：加一個登入區塊（email/password → `POST /api/login` → `localStorage.token`）；WS 連線 URL 改 `` `${wsBase}/ws/talk?token=${token}` ``；所有 `/api/*` fetch 加 `headers:{Authorization:'Bearer '+token}`。
`web/teacher.html`：同法登入，讀取改 `` `/api/interactions?student=STUDENT-AMING-004` `` 與 `/api/diagnoses?student=...` 並帶 Bearer。
手動驗收：`.venv/bin/python -m uvicorn server.app:app` → 瀏覽器登入 `aming@demo/demo1234` → 能語音對話；`teacher@demo` → 能看到阿明資料。

- [ ] **Step 6: Commit**

```bash
git add talkybuddy/server/sync_client.py talkybuddy/web/index.html talkybuddy/web/teacher.html talkybuddy/tests/test_sync_client.py
git commit -m "feat: doll sync client + browser login wiring (student & teacher)"
```

---

### Task 9: 金鑰撤銷、環境變數注入、雲端 VM 佈署文件

**Files:**
- Create: `talkybuddy/docs/DEPLOY_CLOUD.md`
- Modify: `talkybuddy/.gitignore`（確保 `.env` 類不入庫）

**Interfaces:** 無程式碼；為佈署與安全前置。

- [ ] **Step 1: 撤銷外露金鑰（人工，最優先）**

在 ElevenLabs 後台**撤銷記憶中外露的舊金鑰**並重新產生；新金鑰只放雲端 VM 環境變數，絕不 commit。（本步驟無自動測試，屬安全前置；未完成不得上雲。）

- [ ] **Step 2: 寫 DEPLOY_CLOUD.md**

內容涵蓋：
- 環境變數清單：`TALKYBUDDY_JWT_SECRET`、`TALKYBUDDY_PIPELINE_PROFILE=cloud`、`ELEVENLABS_API_KEY`（新）、`PICOVOICE_ACCESS_KEY`、`TALKYBUDDY_CONSENT_GRANTED`。
- 啟動指令：`TALKYBUDDY_PIPELINE_PROFILE=cloud .venv/bin/python -m uvicorn server.app:app --host 0.0.0.0 --port 8000`。
- WSS：前置 nginx/caddy 反代 + TLS（瀏覽器麥克風需 HTTPS/WSS）。
- 玩偶端設 `TALKYBUDDY_PIPELINE_PROFILE=edge` 並定期呼叫 `sync_client.push_pending`（cron 或背景 task）。

- [ ] **Step 3: 驗證 .gitignore**

確認 `.env`、`*.key` 已被忽略：`git check-ignore .env || echo "ADD .env to .gitignore"`。

- [ ] **Step 4: 全套件回歸**

Run: `.venv/bin/python -m pytest -q`（於 `talkybuddy/`）
Expected: PASS（全綠）

- [ ] **Step 5: Commit**

```bash
git add talkybuddy/docs/DEPLOY_CLOUD.md talkybuddy/.gitignore
git commit -m "docs(deploy): cloud VM env/TLS guide + key-rotation prerequisite"
```

---

## Self-Review

**Spec coverage：**
- §3 拓撲（薄客戶端、玩偶本地、雲端後端）→ Task 5/6/8/9
- §4 身份登入（JWT、角色、取代寫死值、導師視角）→ Task 2/3/4
- §5 雲端管線（profile 切 provider、每連線 session、協定不變）→ Task 5/6
- §6 同步（玩偶上行、去重合併、雲端真相、導師讀取）→ Task 1/7/8
- §7 錯誤降級（consent gate、token 失效、tts_unavailable）→ 沿用既有；Task 4/5 補 401/1008
- §8 實作順序（5 步）→ Task 1→8 對應；§10 前置（金鑰、session 化、VM）→ Task 5/9
- §11 YAGNI 排除 → 計畫未納入（正確）

**Placeholder scan：** 無 TBD/TODO；每個 code step 附完整程式碼。前端接線（Task 8 Step 5）為手動驗收（HTML 大幅改動不宜逐行硬列），已明確步驟與驗收方式。

**Type consistency：** `list_interactions(limit, student_id)`、`verify_token→claims{sub,role,exp}`、`identity_from_header→claims`、`VoicePipeline(...,student_id=None)`、`push_pending(base_url,token,http_post)` 跨 Task 一致。

**已知取捨：** 去重用 Python 端線性掃描（demo 資料量小，YAGNI）；正式版應加 DB 索引欄。
