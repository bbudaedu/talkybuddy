# -*- coding: utf-8 -*-
"""test_e2e.py — httpx + ASGITransport 打 FastAPI app 的端對端測試。

涵蓋 CONTRACTS.md app.py 契約中的 REST API（不需真模型）：
- GET /            → 學生端頁面（web/index.html）
- GET /api/status  → {"asr","llm","tts","network_mode","pending"}
- GET /api/diagnoses
- POST /api/network_mode  mode="cloud" → mark_all_synced + generate_diagnosis
  → 回應含 new_diagnosis
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from server import auth, store
from server.app import app, pipeline

_STUDENT_AUTH = {"Authorization": f"Bearer {auth.issue_token('STUDENT-AMING-004', 'student')}"}

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _reset_network_mode():
    """app.py 的 pipeline 是模組級單例，跨測試共用；每個測試前後重設為 edge，避免互相汙染。"""
    pipeline.network_mode = "edge"
    yield
    pipeline.network_mode = "edge"


async def _client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver")


async def test_get_index_page_returns_html():
    """GET / 應回傳學生端 index.html（200，內容非空）。"""
    async with await _client() as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert len(resp.text) > 0


async def test_get_teacher_page_returns_html():
    """GET /teacher 應回傳教師端 teacher.html（200）。"""
    async with await _client() as client:
        resp = await client.get("/teacher")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


async def test_get_api_status_shape():
    """GET /api/status 回傳欄位齊全，network_mode 預設 edge，pending 為 int。"""
    async with await _client() as client:
        resp = await client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "asr", "llm", "tts", "cloud_tts", "cloud_llm", "cloud_provider",
        "network_mode", "pending", "live_s2s",
    }
    # cloud_provider：雲端大腦實際會走的後端，供現場佐證「大腦在 Bedrock」
    assert body["cloud_provider"] in {"bedrock", "relay", "none"}
    assert isinstance(body["live_s2s"], bool)
    assert isinstance(body["asr"], bool)
    assert isinstance(body["llm"], bool)
    assert isinstance(body["tts"], bool)
    assert isinstance(body["cloud_tts"], bool)
    assert isinstance(body["cloud_llm"], bool)
    assert body["network_mode"] == "edge"
    assert isinstance(body["pending"], int)


async def test_get_api_diagnoses_empty_when_no_data():
    """空 DB（tmp_db fixture 只 init_db 未 seed）時 /api/diagnoses 回空陣列。"""
    async with await _client() as client:
        resp = await client.get("/api/diagnoses", headers=_STUDENT_AUTH)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_get_api_interactions_empty_when_no_data():
    """空 DB 時 /api/interactions 回空陣列。"""
    async with await _client() as client:
        resp = await client.get("/api/interactions", headers=_STUDENT_AUTH)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_post_network_mode_edge_returns_no_diagnosis():
    """POST /api/network_mode mode=edge → 只切模式，不觸發同步/診斷。"""
    async with await _client() as client:
        resp = await client.post("/api/network_mode", json={"mode": "edge"}, headers=_STUDENT_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"network_mode": "edge", "synced": 0, "new_diagnosis": None}


async def test_post_network_mode_cloud_returns_new_diagnosis():
    """POST /api/network_mode mode=cloud → 觸發 mark_all_synced + generate_diagnosis，
    回應必須含非 None 的 new_diagnosis（符合 diagnosis dict 契約欄位）。
    """
    async with await _client() as client:
        resp = await client.post("/api/network_mode", json={"mode": "cloud"}, headers=_STUDENT_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["network_mode"] == "cloud"
    assert body["synced"] == 0  # 空 DB，沒有待同步紀錄
    assert body["new_diagnosis"] is not None
    diag = body["new_diagnosis"]
    assert set(diag.keys()) == {
        "date", "scores", "strengths", "weaknesses",
        "emotional_status", "instructions",
        "companion_directive",  # B1：新增陪聊策略欄位（向後相容）
        "level_state",          # B3：新增 CEFR 難度階梯欄位（向後相容）
        "source",               # TCLOUD-02：來源標記（"cloud" | "rule"）
    }
    assert diag["source"] in ("cloud", "rule")
    assert diag["companion_directive"]["difficulty"] in ("up", "hold", "down")
    assert set(diag["scores"].keys()) == {"pronunciation", "fluency", "vocabulary", "grammar"}

    # 該診斷應已寫入 DB：改用 store 直接驗證持久化，而非現已被 token 保護的
    # HTTP 端點（無 token 會 401、且 401 body {"detail":...} 的 len 恰為 1，
    # 會讓斷言假通過、不再驗證任何東西）。
    assert len(store.list_diagnoses()) == 1


async def test_post_network_mode_cloud_marks_pending_interactions_synced():
    """先寫入未同步互動，再切 cloud → synced 應等於待同步筆數，且都被標記完成。"""
    store.add_interaction(
        {
            "network_mode": "edge",
            "student_text": "hi",
            "asr_confidence": 0.9,
            "ai_response_text": "hello",
            "scores": {"fluency": 50, "vocabulary": 50, "grammar": 50},
            "latency_ms": {"asr": 1, "llm": 1, "tts_first": 1, "round_total": 3},
            "synced": False,
        }
    )
    assert store.pending_count() == 1

    async with await _client() as client:
        resp = await client.post("/api/network_mode", json={"mode": "cloud"}, headers=_STUDENT_AUTH)
    body = resp.json()
    assert body["synced"] == 1
    assert body["new_diagnosis"] is not None
    assert store.pending_count() == 0


async def test_post_network_mode_invalid_mode_returns_400():
    """mode 不是 edge/cloud → 400。"""
    async with await _client() as client:
        resp = await client.post("/api/network_mode", json={"mode": "wifi"}, headers=_STUDENT_AUTH)
    assert resp.status_code == 400


async def test_post_network_mode_requires_token():
    """無 Authorization header → 401，且不改變 pipeline.network_mode。"""
    pipeline.network_mode = "cloud"  # 先設一個非目標值，確認 401 請求完全不動它
    async with await _client() as client:
        resp = await client.post("/api/network_mode", json={"mode": "edge"})
    assert resp.status_code == 401
    assert pipeline.network_mode == "cloud"  # 未被改動


async def test_post_network_mode_invalid_token_returns_401():
    """格式錯誤/無效 token → 401。"""
    async with await _client() as client:
        resp = await client.post(
            "/api/network_mode",
            json={"mode": "edge"},
            headers={"Authorization": "Bearer not-a-real-token"},
        )
    assert resp.status_code == 401


def test_ws_talk_text_input_full_flow(monkeypatch):
    """WS /ws/talk：text_input 快速語句應依序收到 state/reply/tts_unavailable。

    使用 starlette TestClient（同步、內建支援 websocket_connect），因為
    httpx.ASGITransport 不支援 WebSocket 協定，僅能測 HTTP。
    monkeypatch 掉 app.py 的全域引擎單例（llm/tts/asr）的 available()，
    強制降級路徑，確保這條測試「不需真模型」、快速且結果確定性。
    """
    from starlette.testclient import TestClient

    from server import app as app_module

    monkeypatch.setattr(app_module.llm_engine, "available", lambda: False)
    monkeypatch.setattr(app_module.tts_engine, "available", lambda: False)
    monkeypatch.setattr(app_module.asr_engine, "available", lambda: False)

    tok = auth.issue_token("STUDENT-AMING-004", "student")
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/talk?token={tok}") as ws:
            ws.send_json({"type": "text_input", "text": "我要一個蘋果"})

            msg_types = []
            reply_msg = None
            for _ in range(6):
                data = ws.receive_json()
                msg_types.append(data["type"])
                if data["type"] == "reply":
                    reply_msg = data
                if data["type"] in ("tts_audio", "tts_unavailable"):
                    break

            assert "reply" in msg_types
            assert "tts_unavailable" in msg_types  # tts 已被 monkeypatch 為不可用
            assert reply_msg is not None
            assert reply_msg["fallback"] is False
            assert reply_msg["seq"] >= 1
            assert set(reply_msg["scores"].keys()) == {"fluency", "vocabulary", "grammar"}


class _CountingLLM:
    """可區分雲端/邊緣回覆的計數 stub，符合 EdgeLLM/CloudLLM 的 available()/generate() 契約。"""

    def __init__(self, label: str):
        self.label = label
        self.calls = 0

    def available(self) -> bool:
        return True

    def generate(self, student_text, scaffold_result, directive=None):
        self.calls += 1
        return f"{self.label}回覆：跟我說一遍：I see a cat"


def test_network_mode_switch_affects_live_ws_session(monkeypatch):
    """NETCUT-01 活體 WS 回歸測試：不重整頁面、不重連 WS，切換飛航模式後下一回合真的走 edge。

    修復前（conn_pipe.network_mode 只在連線當下複製一次）此測試必為 RED——第二回合
    仍會呼叫 cloud stub；修復後（每回合 dispatch 前重新從全域 pipeline.network_mode
    同步）轉 GREEN。
    """
    from starlette.testclient import TestClient

    from server import app as app_module

    cloud_stub = _CountingLLM("雲端")
    edge_stub = _CountingLLM("邊緣")
    monkeypatch.setattr(app_module, "cloud_llm_engine", cloud_stub)
    monkeypatch.setattr(app_module, "llm_engine", edge_stub)
    monkeypatch.setattr(app_module.tts_engine, "available", lambda: False)

    tok = auth.issue_token("STUDENT-AMING-004", "student")
    auth_headers = {"Authorization": f"Bearer {tok}"}

    with TestClient(app_module.app) as client:
        # 先切 cloud，開 WS，跑第一回合
        resp = client.post("/api/network_mode", json={"mode": "cloud"}, headers=auth_headers)
        assert resp.status_code == 200

        with client.websocket_connect(f"/ws/talk?token={tok}") as ws:
            ws.send_json({"type": "text_input", "text": "我要一個蘋果"})
            for _ in range(6):
                data = ws.receive_json()
                if data["type"] in ("tts_audio", "tts_unavailable"):
                    break
            assert cloud_stub.calls == 1
            assert edge_stub.calls == 0

            # 不關閉這條 WS，切到 edge（在 WS session 開著時發第二個 POST；starlette
            # TestClient 的 websocket portal 支援此併發，見 09-RESEARCH.md Code Examples）
            resp2 = client.post("/api/network_mode", json={"mode": "edge"}, headers=auth_headers)
            assert resp2.status_code == 200

            # 同一條 WS 上送第二次 text_input：應改走 edge，cloud 不再被呼叫
            ws.send_json({"type": "text_input", "text": "我要一個蘋果"})
            for _ in range(6):
                data = ws.receive_json()
                if data["type"] in ("tts_audio", "tts_unavailable"):
                    break

    assert cloud_stub.calls == 1  # 沒有再被呼叫
    assert edge_stub.calls >= 1

    rows = store.list_interactions(limit=10)
    assert rows[0]["network_mode"] == "edge"  # 第二回合寫入的那筆
