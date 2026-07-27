# -*- coding: utf-8 -*-
"""test_student_identity.py — 驗證 D-05：教師儀表板學生姓名真實化（非 mock）。

涵蓋三塊：
1. `store.student_display_name()` 的三層解析（config 有值 / 被覆寫 / config
   缺屬性時回退），鏡像 `_student_id()` 的既有形態。
2. `GET /api/student_profile` 端點——沿用既有 `identity_from_header()` +
   `_resolve_student()` 授權模型，不新增授權面。
3. D-05 與 D-04 的邊界：姓名不得進上傳白名單，`project_for_upload()` 的
   輸出永遠不含姓名值。
"""

from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from server import app as app_mod, auth, config, store, sync_client


def _tok(sub, role):
    return auth.issue_token(sub, role)


def test_student_display_name_returns_nonempty_by_default():
    """未設任何覆寫時，回傳非空字串（demo 預設姓名）。"""
    assert store.student_display_name()


def test_student_display_name_reflects_config_override(monkeypatch):
    """monkeypatch config.STUDENT_NAME 後，回傳被覆寫的值。"""
    monkeypatch.setattr(config, "STUDENT_NAME", "王小明")
    assert store.student_display_name() == "王小明"


def test_student_display_name_falls_back_when_config_attr_missing(monkeypatch):
    """config 缺少 STUDENT_NAME 屬性時，回退到 _FALLBACK_STUDENT_NAME，不拋例外。"""
    monkeypatch.delattr(config, "STUDENT_NAME", raising=False)
    assert store.student_display_name() == store._FALLBACK_STUDENT_NAME


def test_student_display_name_accepts_any_student_id_without_raising():
    """student_id 參數目前只是介面預留（單一學生 demo），任何值都不應拋例外。"""
    assert store.student_display_name(student_id=None)
    assert store.student_display_name(student_id="STUDENT-AMING-004")
    assert store.student_display_name(student_id="anything-not-registered")


# ---------------------------------------------------------------------------
# GET /api/student_profile — 沿用既有 identity_from_header + _resolve_student
# 授權模型（Task 2）
# ---------------------------------------------------------------------------

def test_student_profile_requires_token():
    """無 Authorization header → 401。"""
    client = TestClient(app_mod.app)
    assert client.get("/api/student_profile").status_code == 401


def test_student_profile_rejects_bad_token():
    """壞掉的 token → 401。"""
    client = TestClient(app_mod.app)
    h = {"Authorization": "Bearer not-a-real-token"}
    assert client.get("/api/student_profile", headers=h).status_code == 401


def test_student_profile_tutor_without_student_query_400():
    """tutor token 但未帶 ?student= → 400（沿用 _resolve_student 既有行為）。"""
    client = TestClient(app_mod.app)
    h = {"Authorization": f"Bearer {_tok('TUTOR-001', 'tutor')}"}
    assert client.get("/api/student_profile", headers=h).status_code == 400


def test_student_profile_tutor_with_student_query_returns_identity_fields():
    """tutor token 帶 ?student= → 200，回應含 student_id / display_name / device_id 三個鍵。"""
    client = TestClient(app_mod.app)
    h = {"Authorization": f"Bearer {_tok('TUTOR-001', 'tutor')}"}
    resp = client.get("/api/student_profile?student=STUDENT-AMING-004", headers=h)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"student_id", "display_name", "device_id"}
    assert body["student_id"] == "STUDENT-AMING-004"
    assert body["display_name"] == store.student_display_name()
    assert body["device_id"] == config.DEVICE_ID


def test_student_profile_student_role_sees_only_own_id():
    """student token（不帶 query）→ 200，且回應的 student_id 等於 token 的 sub（不能讀到別人的）。"""
    client = TestClient(app_mod.app)
    h = {"Authorization": f"Bearer {_tok('STUDENT-AMING-004', 'student')}"}
    resp = client.get("/api/student_profile", headers=h)
    assert resp.status_code == 200
    assert resp.json()["student_id"] == "STUDENT-AMING-004"


# ---------------------------------------------------------------------------
# D-05 與 D-04 的邊界（Task 3 隱私核心）：姓名不得進上傳白名單
# ---------------------------------------------------------------------------

def test_student_display_name_not_in_upload_projection():
    """把姓名值塞進一筆互動的欄位，project_for_upload() 的輸出仍不含姓名。

    這條測試存在的意義：日後有人為了方便把姓名塞進上傳 payload 時會當場失敗。
    """
    name = store.student_display_name()
    item = {
        "student_id": "STUDENT-AMING-004",
        "display_name": name,
        "student_name": name,
        "name": name,
        "student_text": f"{name}說今天天氣很好",
    }
    out = sync_client.project_for_upload(item)
    assert all(v != name for v in out.values())
    assert not ({"display_name", "student_name", "name"} & set(sync_client.UPLOAD_FIELDS))
    assert not ({"display_name", "student_name", "name"} & set(out.keys()))


def test_teacher_html_has_no_hardcoded_student_name():
    """讀 web/teacher.html 原始碼，斷言硬編姓名字串已不存在於檔案中。"""
    html_path = Path(__file__).resolve().parent.parent / "web" / "teacher.html"
    src = html_path.read_text(encoding="utf-8")
    assert "阿明" not in src
