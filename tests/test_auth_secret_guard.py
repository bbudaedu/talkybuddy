# -*- coding: utf-8 -*-
"""對外開放時，公開的預設 JWT secret 必須擋住啟動。

`dev-only-secret-change-me` 這個值在 2026-07-22 就隨 `origin/master` 公開在
GitHub 上了。任何人 clone 這個 repo，就能對任何沒設 `TALKYBUDDY_JWT_SECRET`
的對外部署簽出合法 token，直接讀到教師儀表板與孩子的互動紀錄。

**為什麼是拒絕啟動而不是印警告。** 決賽要交一個公開的 Live Demo 網址，而
「部署時記得設環境變數」正是在會場、趕死線、體力耗盡時最容易漏掉的事。
警告會被捲過去，啟動失敗五秒鐘就會被發現。

這幾條測試守的就是這個守衛本身——它被「順手」拿掉時要當場紅。
"""

from __future__ import annotations

import importlib

import pytest


def _auth_with(monkeypatch, *, secret=None, host=None):
    """用指定的環境變數重載 auth 模組（SECRET 是模組層常數，要重載才會變）。"""
    monkeypatch.delenv("TALKYBUDDY_JWT_SECRET", raising=False)
    monkeypatch.delenv("TALKYBUDDY_HOST", raising=False)
    monkeypatch.delenv("HOST", raising=False)
    if secret is not None:
        monkeypatch.setenv("TALKYBUDDY_JWT_SECRET", secret)
    if host is not None:
        monkeypatch.setenv("TALKYBUDDY_HOST", host)
    from server import auth
    return importlib.reload(auth)


@pytest.fixture(autouse=True)
def _restore_auth():
    """測試改過環境變數，最後把模組重載回乾淨狀態，不污染其他測試。"""
    yield
    from server import auth
    importlib.reload(auth)


def test_public_bind_with_the_leaked_default_secret_refuses_to_start(monkeypatch):
    """0.0.0.0 + 預設 secret = 門沒鎖，必須拒絕啟動。"""
    auth = _auth_with(monkeypatch, host="0.0.0.0")
    with pytest.raises(RuntimeError) as exc:
        auth.assert_secret_is_safe()
    assert "TALKYBUDDY_JWT_SECRET" in str(exc.value), "錯誤訊息要直接告訴人怎麼修"


def test_ipv6_any_address_is_also_public(monkeypatch):
    auth = _auth_with(monkeypatch, host="::")
    with pytest.raises(RuntimeError):
        auth.assert_secret_is_safe()


def test_a_real_secret_lets_a_public_deployment_start(monkeypatch):
    auth = _auth_with(monkeypatch, secret="a-real-random-secret", host="0.0.0.0")
    auth.assert_secret_is_safe()


def test_local_development_still_needs_zero_configuration(monkeypatch):
    """綁 127.0.0.1 的本機開發不該被擋——否則大家會為了跑起來而拿掉守衛。"""
    auth = _auth_with(monkeypatch, host="127.0.0.1")
    auth.assert_secret_is_safe()


def test_no_host_set_at_all_is_treated_as_local(monkeypatch):
    """沒設 HOST 時 uvicorn 預設綁 127.0.0.1，維持零設定可跑。"""
    auth = _auth_with(monkeypatch)
    auth.assert_secret_is_safe()


def test_the_guard_is_actually_wired_into_startup():
    """守衛沒被呼叫就等於不存在——釘住它真的接在 lifespan 上。"""
    import inspect

    from server import app as app_module

    src = inspect.getsource(app_module.lifespan)
    assert "assert_secret_is_safe" in src, "啟動流程沒有呼叫 secret 守衛"
