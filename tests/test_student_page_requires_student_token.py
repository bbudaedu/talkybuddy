# -*- coding: utf-8 -*-
"""學生端不可以拿 tutor 的 token 自動啟動。

token 存在 localStorage，教師儀表板與學生端共用同一個 key。老師在教室電腦
開過 /teacher 之後孩子接著開學生端，若不檢查角色就會用 tutor token 啟動，
接著 /api/lesson、/api/diagnoses、/api/agent_outputs 全部 400
（tutor 需帶 ?student=），而畫面不會報錯，只是整片空白。

2026-08-01 線上實測撞到；決賽 demo 從教師儀表板切回學生端就會重現。
"""
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text(encoding="utf-8")


def test_autoboot_checks_role_is_student():
    assert 'localStorage.getItem("tb_role") === "student"' in SRC, (
        "自動啟動必須同時檢查角色，只看 token 存在會讓 tutor token 帶壞學生端"
    )


def test_login_rejects_non_student_role():
    assert 'res.role !== "student"' in SRC, (
        "老師帳號在學生端登入要當場擋下並說明，不要讓後續 API 安靜地 400"
    )
