# -*- coding: utf-8 -*-
"""跨源隔離（COOP/COEP）header 契約測試。

sherpa-onnx KWS WASM 以 emscripten `-pthread` 建置，執行時要建立 shared
WebAssembly.Memory，瀏覽器僅在 `crossOriginIsolated === true` 時才允許
（需回應帶 COOP:same-origin + COEP:require-corp）。否則 WASM init 直接丟例外，
喚醒層 degrade。全站頁面與資產皆同源，加這兩個 header 對其他資源無副作用。
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from server.app import app

pytestmark = pytest.mark.anyio

_COOP = "cross-origin-opener-policy"
_COEP = "cross-origin-embedder-policy"


async def _client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver")


async def test_student_document_is_cross_origin_isolated():
    async with await _client() as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert resp.headers.get(_COOP) == "same-origin"
    assert resp.headers.get(_COEP) == "require-corp"


async def test_teacher_document_is_cross_origin_isolated():
    async with await _client() as client:
        resp = await client.get("/teacher")
    assert resp.status_code == 200
    assert resp.headers.get(_COOP) == "same-origin"
    assert resp.headers.get(_COEP) == "require-corp"


async def test_static_assets_carry_isolation_headers():
    """同源子資源在 COEP:require-corp 下需能載入；middleware 全域套用即可。"""
    async with await _client() as client:
        resp = await client.get("/static/live-client.js")
    assert resp.status_code == 200
    assert resp.headers.get(_COOP) == "same-origin"
    assert resp.headers.get(_COEP) == "require-corp"
