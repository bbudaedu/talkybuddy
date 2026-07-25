# -*- coding: utf-8 -*-
"""test_pipeline_timeout_isolation.py — 逾時隔離回歸守門（NETCUT-02／D-03）。

本檔守護的是 09-RESEARCH.md Pitfall 2/3 記錄的結構性風險：雲端 LLM/TTS 的
「快速失敗」由各自的內層 urlopen 逾時負責（短）；`server/pipeline.py::
LLM_TIMEOUT_S` 是 cloud 與 edge **共用**的外層包裝，必須維持寬鬆，否則會
把 Phase 8 真機實測 edge LLM 單階段可達 4170ms 的生存空間一起砍掉，讓每一
次離線回覆都退化成 scaffold。這裡把「不准砍」與「必須夠短」兩件事都寫成
會在回歸時變紅的自動化測試，而不只是散落在各檔案的註解叮嚀。

同時補上 `/api/status` 的零出境證據：NETCUT-02「背景輪詢於離線視窗暫停」
對此端點不需要新的暫停邏輯，因為它本來就是純 loopback；此檔以 urlopen
間諜直接證明，而非僅靠架構推論。
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

import pytest
from starlette.testclient import TestClient

from server import cloud_llm, config
from server import pipeline as pipeline_mod
from server import scaffold
from server.app import app
from server.pipeline import VoicePipeline

from tests.test_pipeline import StubASR, StubLLM, StubTTS, _collecting_emit

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# 第 1 組 — 常數契約測試：不准砍 LLM_TIMEOUT_S、雲端逾時必須夠短
# ---------------------------------------------------------------------------


def test_llm_timeout_stays_generous_for_edge_engine():
    """pipeline.LLM_TIMEOUT_S 必須 >= 6.0，守住 edge 真機最壞 4170ms 的餘裕。

    出處：edge/EDGE_TURN_LOOP_VALIDATION.md:57 記錄真機一輪
    latency_ms={'asr': 405, 'llm': 4170, ...}；若有人日後把這個常數
    一起砍短（例如砍到 1-2 秒去湊「快速降級」的字面要求），edge 引擎
    的生成會被自己所屬的 engine 迴圈逾時截斷，每一次離線回覆都會退化
    成 scaffold 兜底文字。此斷言就是要在那件事發生時立刻變紅。
    """
    assert pipeline_mod.LLM_TIMEOUT_S >= 6.0, (
        "server/pipeline.py::LLM_TIMEOUT_S 被砍到 6.0 秒以下——"
        "這個常數是 cloud/edge 共用的外層包裝，Phase 8 真機實測 edge LLM "
        "單階段可達 4170ms（edge/EDGE_TURN_LOOP_VALIDATION.md:57），"
        "砍短它會讓每一次離線回覆都被截斷退化成 scaffold。"
    )


def test_cloud_timeouts_are_short():
    """雲端 LLM/TTS 各自的內層逾時必須 <= 2.0 秒，滿足 <1-2 秒降級門檻。"""
    assert cloud_llm._TIMEOUT_S <= 2.0, (
        "server/cloud_llm.py::_TIMEOUT_S 超過 2.0 秒——"
        "斷網時單一雲端 LLM 階段的降級延遲會超出 ROADMAP 的 <1-2 秒門檻。"
    )
    assert config.CLOUD_TTS_TIMEOUT_S <= 2.0, (
        "server/config.py::CLOUD_TTS_TIMEOUT_S 超過 2.0 秒——"
        "斷網時單一雲端 TTS 階段的降級延遲會超出 ROADMAP 的 <1-2 秒門檻。"
    )


# ---------------------------------------------------------------------------
# 第 2 組 — 行為隔離（正例 / 反例）：cloud 耗時不會吃掉 edge 自己的逾時預算
# ---------------------------------------------------------------------------


async def test_cloud_slow_then_none_does_not_starve_edge_when_llm_timeout_generous(
    monkeypatch,
):
    """cloud 耗時 0.3s 後回 None、edge 耗時 0.5s 後回可用文字，
    LLM_TIMEOUT_S 維持寬鬆（0.8）→ 回覆文字來自 edge stub。

    這證明外層逾時是 per-engine（每個引擎各自重新倒數），而非累計——
    cloud 消耗掉的 0.3 秒不會從 edge 自己的 0.8 秒預算裡先扣掉。
    """
    events: list[dict] = []
    emit = await _collecting_emit(events)
    cloud = StubLLM(reply=None, available=True, delay_s=0.3)
    edge = StubLLM(
        reply="邊緣回覆：跟我說一遍：I see a cat", available=True, delay_s=0.5
    )
    monkeypatch.setattr(pipeline_mod, "LLM_TIMEOUT_S", 0.8)
    vp = VoicePipeline(StubASR(), edge, StubTTS(available=False), cloud_llm=cloud)
    vp.network_mode = "cloud"

    result = await vp.run_turn_text("我看到一隻貓", emit)

    assert result.reply_text == "邊緣回覆：跟我說一遍：I see a cat"


async def test_llm_timeout_slashed_starves_edge_engine_regression_record(monkeypatch):
    """反例（記錄用）：若有人把 LLM_TIMEOUT_S 也一起砍短（模擬砍到 0.3），
    edge 引擎會被餓死，回覆退化成 scaffold 文字。

    此測試存在的目的**不是**要驗證正確行為，而是把「LLM_TIMEOUT_S 被砍短
    的具體後果」用可執行的方式記錄下來，讓 test_llm_timeout_stays_generous_
    for_edge_engine() 的常數契約測試在變紅時，有這裡的行為證據可以對照
    理解「為什麼不能砍」。
    """
    events: list[dict] = []
    emit = await _collecting_emit(events)
    cloud = StubLLM(reply=None, available=True, delay_s=0.3)
    edge = StubLLM(
        reply="邊緣回覆：跟我說一遍：I see a cat", available=True, delay_s=0.5
    )
    monkeypatch.setattr(pipeline_mod, "LLM_TIMEOUT_S", 0.3)
    vp = VoicePipeline(StubASR(), edge, StubTTS(available=False), cloud_llm=cloud)
    vp.network_mode = "cloud"

    text = "我看到一隻貓"
    expected_reply = scaffold.respond(text).reply_text
    result = await vp.run_turn_text(text, emit)

    assert result.reply_text == expected_reply


# ---------------------------------------------------------------------------
# 第 3 組 — /api/status 零出境證據
# ---------------------------------------------------------------------------


def test_api_status_makes_no_outbound_call(monkeypatch):
    """GET /api/status 連續兩拍輪詢（模擬 5 秒輪詢）不觸發任何真正的雲端出境
    urlopen 呼叫。

    這是 NETCUT-02「背景輪詢於離線視窗暫停」對此端點的**證據性**結論——
    不需要暫停邏輯，因為它架構上本來就是純 loopback（09-RESEARCH.md 已對
    教師儀表板輪詢做同樣的架構性認定）。

    注意：`/api/status` 會呼叫 `llm_engine.available()`（`server/llm.py::
    EdgeLLM.available()`），它對 `127.0.0.1:{LLM_SERVER_PORT}` 的
    llama-server 做 `/health` 探測——這也是透過 `urllib.request.urlopen`，
    但目的地是本機自己，不是雲端供應商。故此測試以目的地主機是否為
    loopback（127.0.0.1/localhost）分辨「本機探測」與「真正出境」，
    只對後者斷言零呼叫。
    """
    outbound_calls: list[str] = []

    def _spy_urlopen(request, *args, **kwargs):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        host = urllib.parse.urlsplit(url).hostname or ""
        if host not in ("127.0.0.1", "localhost"):
            outbound_calls.append(url)
        raise urllib.error.URLError("測試環境無真實 server，強制走既有降級路徑")

    monkeypatch.setattr(urllib.request, "urlopen", _spy_urlopen)

    client = TestClient(app)
    resp1 = client.get("/api/status")
    resp2 = client.get("/api/status")

    assert outbound_calls == [], (
        f"/api/status 輪詢觸發了非本機（真正出境）的 urlopen 呼叫：{outbound_calls}"
    )
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert "network_mode" in resp1.json()
    assert "network_mode" in resp2.json()
