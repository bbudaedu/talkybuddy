# -*- coding: utf-8 -*-
"""`/api/status` 的 cloud_tts 不得在沒有證據時報綠燈。

**這支測試存在的理由**（2026-07-30 實測）：金鑰設好之後，`available()` 只檢查
「金鑰與 voice_id 在不在」，於是 `/api/status` 回報 `cloud_tts=true`；但
`CLOUD_TTS_TIMEOUT_S` 預設 1.5 秒，而 `eleven_v3` 暖機後仍要約 3 秒，所以
**每一次合成都逾時、靜默降級回邊緣語音**。

自檢說綠燈、聽到的卻是邊緣音——這個狀態比沒有金鑰更糟：沒金鑰時
`preflight` 至少會老實說 `cloud_tts=false`。

修法不是把 `available()` 改成每次真的去打 API——它是 `pipeline._synth_tts`
熱路徑上的閘門，必須便宜。改成：`synth()` 記下每次的實際結果，status 依
**證據**而非**設定**回報，而且在還沒有證據時要明講「尚未驗證」，不能默認成功。
"""

from __future__ import annotations

import urllib.error

import pytest

from server import config
from server.cloud_tts import CloudTTS


class _FakeResp:
    def __init__(self, data: bytes, content_type: str = "audio/pcm"):
        self._data, self.headers = data, {"Content-Type": content_type}

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", "test-voice")
    monkeypatch.setattr(config, "CLOUD_TTS_TIMEOUT_S", 1.5)


SEGS = [("zh", "好，我們一起唸一次喔。")]


# ---------------------------------------------------------------------------
# 還沒有證據時，不准報成功
# ---------------------------------------------------------------------------

def test_configured_but_never_used_is_not_reported_as_working(keyed):
    """設定齊全 ≠ 會動。這正是當天踩到的那個狀態。"""
    tts = CloudTTS()
    assert tts.available() is True, "設定閘門仍該說設定齊全"
    assert tts.verified() is False, "但沒實際合成過就不能說它可用"
    assert "尚未" in tts.status_detail()


def test_missing_key_says_so_plainly(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "")
    tts = CloudTTS()
    assert tts.verified() is False
    assert "ELEVENLABS_API_KEY" in tts.status_detail()


# ---------------------------------------------------------------------------
# 有證據之後，要如實反映
# ---------------------------------------------------------------------------

def test_a_successful_synth_is_remembered(keyed, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda *a, **k: _FakeResp(b"\x00\x01" * 8000))
    tts = CloudTTS()
    assert tts.synth(SEGS) is not None
    assert tts.verified() is True
    assert "成功" in tts.status_detail()


def test_a_timeout_flips_it_back_to_not_working(keyed, monkeypatch):
    """逾時是當天的實際失敗模式，而且降級是靜默的——status 必須是唯一線索。"""
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    tts = CloudTTS()
    assert tts.synth(SEGS) is None
    assert tts.verified() is False
    detail = tts.status_detail()
    assert "逾時" in detail
    # 光說「逾時」不夠：要能直接看出是哪個上限卡住的，否則又要重查一輪
    assert "1.5" in detail


def test_an_http_error_reports_the_status_code(keyed, monkeypatch):
    """401（金鑰無效）與逾時是完全不同的問題，不能都顯示成同一句話。"""
    def _boom(*a, **k):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    tts = CloudTTS()
    assert tts.synth(SEGS) is None
    assert tts.verified() is False
    assert "401" in tts.status_detail()


def test_recovery_is_reflected_too(keyed, monkeypatch):
    """一次失敗不該永久標記為壞掉——網路可能只是抖了一下。"""
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    tts = CloudTTS()
    tts.synth(SEGS)
    assert tts.verified() is False

    monkeypatch.setattr("urllib.request.urlopen",
                        lambda *a, **k: _FakeResp(b"\x00\x01" * 8000))
    assert tts.synth(SEGS) is not None
    assert tts.verified() is True


# ---------------------------------------------------------------------------
# 熱路徑不得變貴
# ---------------------------------------------------------------------------

def test_the_hot_path_gate_still_does_not_touch_the_network(keyed, monkeypatch):
    """`available()` 每個回合都會被 pipeline._synth_tts 呼叫一次。

    它必須維持純設定檢查——一旦改成真的去打 API 探測，等於每回合多一次
    往返，反而害了原本要救的延遲。
    """
    def _explode(*a, **k):
        raise AssertionError("available() 不該碰網路")

    monkeypatch.setattr("urllib.request.urlopen", _explode)
    CloudTTS().available()


# ---------------------------------------------------------------------------
# /api/status 的接線
# ---------------------------------------------------------------------------

def test_status_reports_evidence_not_configuration(keyed, monkeypatch):
    """status 的 cloud_tts 要跟著證據走，並附上人看得懂的理由。"""
    from fastapi.testclient import TestClient

    import server.app as app_mod

    monkeypatch.setattr(app_mod.cloud_tts_engine, "verified", lambda: False)
    monkeypatch.setattr(app_mod.cloud_tts_engine, "status_detail",
                        lambda: "尚未驗證：設定齊全但還沒實際合成過")
    body = TestClient(app_mod.app).get("/api/status").json()

    assert body["cloud_tts"] is False, "沒有證據就不能是 true"
    assert isinstance(body["cloud_tts"], bool), "型別契約不變（見 test_e2e）"
    assert "尚未驗證" in body["cloud_tts_detail"]
