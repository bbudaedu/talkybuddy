# -*- coding: utf-8 -*-
"""/api/status live_s2s 欄 + /ws/live 端點行為測試（注入 fake NovaSonicSession）。"""
from __future__ import annotations

import os

from starlette.testclient import TestClient

from server import app as app_mod
from server import config, nova_sonic


def test_status_has_live_s2s_true(monkeypatch):
    monkeypatch.setattr(config, "LIVE_S2S_ENABLED", True)
    monkeypatch.setattr(nova_sonic, "available", lambda: True)
    client = TestClient(app_mod.app)
    body = client.get("/api/status").json()
    assert body["live_s2s"] is True


def test_status_live_s2s_false_when_flag_off(monkeypatch):
    monkeypatch.setattr(config, "LIVE_S2S_ENABLED", False)
    monkeypatch.setattr(nova_sonic, "available", lambda: True)
    client = TestClient(app_mod.app)
    assert client.get("/api/status").json()["live_s2s"] is False


def test_status_live_s2s_false_when_unavailable(monkeypatch):
    monkeypatch.setattr(config, "LIVE_S2S_ENABLED", True)
    monkeypatch.setattr(nova_sonic, "available", lambda: False)
    client = TestClient(app_mod.app)
    assert client.get("/api/status").json()["live_s2s"] is False


import json


class _FakeSession:
    """假的 NovaSonicSession：記錄呼叫、events() 吐預設事件序。"""
    last = None

    def __init__(self, *a, **k):
        self.started_with = None
        self.audio_chunks = []
        self.ended = False
        self.closed = False
        _FakeSession.last = self

    async def start(self, system_prompt):
        self.started_with = system_prompt

    async def send_audio(self, pcm16):
        self.audio_chunks.append(pcm16)

    async def end_user_turn(self):
        self.ended = True

    async def events(self):
        from server.nova_sonic import NovaEvent
        yield NovaEvent("transcript", role="USER", text="我想吃蘋果")
        yield NovaEvent("transcript", role="ASSISTANT", text="好棒！apple。")
        yield NovaEvent("audio", audio=b"\x00\x00" * 4)
        yield NovaEvent("turn_end")

    async def close(self):
        self.closed = True


def _wire_fake(monkeypatch):
    monkeypatch.setattr(config, "LIVE_S2S_ENABLED", True)
    monkeypatch.setattr(nova_sonic, "available", lambda: True)
    monkeypatch.setattr(app_mod, "_make_live_session", lambda: _FakeSession())
    # 預設關閉發音評測，避免這些非發音測試意外載入真模型；需要者自行 setattr True。
    monkeypatch.setattr(app_mod.pronunciation, "available", lambda: False)


def test_ws_live_turn_stores_interaction(monkeypatch):
    _wire_fake(monkeypatch)
    monkeypatch.setattr(app_mod.guardrails, "consent_granted", lambda: True)
    seen = {}
    monkeypatch.setattr(app_mod.store, "add_interaction",
                        lambda d: seen.update(d) or 1)
    client = TestClient(app_mod.app)
    with client.websocket_connect("/ws/live") as ws:
        ws.send_bytes(b"\x01\x02" * 8)
        ws.send_text(json.dumps({"type": "user_end"}))
        # 收到 transcript / audio / turn_end
        kinds = []
        for _ in range(4):
            m = ws.receive()
            if "text" in m and m["text"]:
                kinds.append(json.loads(m["text"]).get("type"))
            elif m.get("bytes"):
                kinds.append("audio")
        ws.send_text(json.dumps({"type": "bye"}))
    assert "live_transcript" in kinds
    assert "audio" in kinds
    # transcript 落地：USER→asr_text、ASSISTANT→reply_text
    assert seen.get("asr_text") == "我想吃蘋果"
    assert seen.get("reply_text") == "好棒！apple。"


class _FakeSessionContinuous(_FakeSession):
    """連續模式 fake：events_continuous 吐一輪後結束（None 由迭代自然收）。"""
    async def events_continuous(self):
        from server.nova_sonic import NovaEvent
        yield NovaEvent("transcript", role="USER", text="哈囉")
        yield NovaEvent("transcript", role="ASSISTANT", text="嗨呀")
        yield NovaEvent("audio", audio=b"\x00\x00" * 4)
        yield NovaEvent("turn_end")


def test_ws_live_continuous_forwards_and_ignores_user_end(monkeypatch):
    monkeypatch.setattr(config, "LIVE_S2S_ENABLED", True)
    monkeypatch.setattr(nova_sonic, "available", lambda: True)
    fake = _FakeSessionContinuous()
    monkeypatch.setattr(app_mod, "_make_live_session", lambda: fake)
    monkeypatch.setattr(app_mod.pronunciation, "available", lambda: False)
    monkeypatch.setattr(app_mod.guardrails, "consent_granted", lambda: True)
    seen = {}
    monkeypatch.setattr(app_mod.store, "add_interaction",
                        lambda d: seen.update(d) or 1)
    client = TestClient(app_mod.app)
    with client.websocket_connect("/ws/live?mode=continuous") as ws:
        ws.send_bytes(b"\x01\x02" * 8)
        ws.send_text(json.dumps({"type": "user_end"}))  # 連續模式應被忽略、不觸發收尾
        kinds = []
        for _ in range(4):
            m = ws.receive()
            if m.get("text"):
                kinds.append(json.loads(m["text"]).get("type"))
            elif m.get("bytes"):
                kinds.append("audio")
    assert "live_transcript" in kinds and "audio" in kinds and "turn_end" in kinds
    assert fake.ended is False          # user_end 未觸發 end_user_turn
    assert seen.get("asr_text") == "哈囉" and seen.get("reply_text") == "嗨呀"


def test_ws_live_continuous_forwards_interrupt(monkeypatch):
    """連續模式 downlink 收 NovaEvent('interrupt') → emit {type:'interrupt'}（barge-in）。"""
    class _FakeInterrupt(_FakeSession):
        async def events_continuous(self):
            from server.nova_sonic import NovaEvent
            yield NovaEvent("audio", audio=b"\x00\x00" * 4)
            yield NovaEvent("interrupt")
    monkeypatch.setattr(config, "LIVE_S2S_ENABLED", True)
    monkeypatch.setattr(nova_sonic, "available", lambda: True)
    monkeypatch.setattr(app_mod, "_make_live_session", lambda: _FakeInterrupt())
    monkeypatch.setattr(app_mod.pronunciation, "available", lambda: False)
    monkeypatch.setattr(app_mod.guardrails, "consent_granted", lambda: True)
    client = TestClient(app_mod.app)
    with client.websocket_connect("/ws/live?mode=continuous") as ws:
        kinds = []
        for _ in range(2):
            m = ws.receive()
            if m.get("text"):
                kinds.append(json.loads(m["text"]).get("type"))
            elif m.get("bytes"):
                kinds.append("audio")
    assert "interrupt" in kinds


def test_ws_live_denied_without_consent(monkeypatch):
    _wire_fake(monkeypatch)
    monkeypatch.setattr(app_mod.guardrails, "consent_granted", lambda: False)
    client = TestClient(app_mod.app)
    with client.websocket_connect("/ws/live") as ws:
        m = ws.receive()
        payload = json.loads(m["text"])
        assert payload["type"] == "live_error"
        assert payload["reason"] == "consent_required"


def test_ws_live_denied_when_unavailable(monkeypatch):
    _wire_fake(monkeypatch)
    monkeypatch.setattr(nova_sonic, "available", lambda: False)
    monkeypatch.setattr(app_mod.guardrails, "consent_granted", lambda: True)
    client = TestClient(app_mod.app)
    with client.websocket_connect("/ws/live") as ws:
        payload = json.loads(ws.receive()["text"])
        assert payload["type"] == "live_error"
        assert payload["reason"] == "unavailable"


# ---------------------------------------------------------------------------
# 階段2 發音評測接線：_store_live_turn(pron=) 與 _pcm_to_pron_score 助手
# ---------------------------------------------------------------------------

def test_store_live_turn_writes_pronunciation_score(monkeypatch):
    """pron 有值 → scores["pronunciation"] 寫入。"""
    seen = {}
    monkeypatch.setattr(app_mod.store, "add_interaction",
                        lambda d: seen.update(d) or 1)
    app_mod._store_live_turn(["hello"], ["hi"], pron=73.0)
    assert seen["scores"]["pronunciation"] == 73.0


def test_store_live_turn_empty_scores_when_pron_none(monkeypatch):
    """pron=None（現況/降級）→ scores 維持空、向後相容。"""
    seen = {}
    monkeypatch.setattr(app_mod.store, "add_interaction",
                        lambda d: seen.update(d) or 1)
    app_mod._store_live_turn(["hello"], ["hi"], pron=None)
    assert seen["scores"] == {}


def test_pcm_to_pron_score_none_when_unavailable(monkeypatch):
    monkeypatch.setattr(app_mod.pronunciation, "available", lambda: False)
    assert app_mod._pcm_to_pron_score(b"\x00\x00" * 100, "hello") is None


def test_pcm_to_pron_score_none_on_empty_pcm(monkeypatch):
    monkeypatch.setattr(app_mod.pronunciation, "available", lambda: True)
    assert app_mod._pcm_to_pron_score(b"", "hello") is None


def test_ws_live_turn_scores_pronunciation(monkeypatch):
    """turn-based：使用者 PCM 被 tee，turn_end 背景評分後把 pron 寫進該輪 interaction。"""
    _wire_fake(monkeypatch)
    monkeypatch.setattr(app_mod.guardrails, "consent_granted", lambda: True)
    monkeypatch.setattr(app_mod.pronunciation, "available", lambda: True)
    scored = {}

    def fake_score(path, ref):
        scored["ref"] = ref
        return 82.0

    monkeypatch.setattr(app_mod.pronunciation, "score", fake_score)
    seen = {}
    monkeypatch.setattr(app_mod.store, "add_interaction",
                        lambda d: seen.update(d) or 1)
    client = TestClient(app_mod.app)
    with client.websocket_connect("/ws/live") as ws:
        ws.send_bytes(b"\x01\x02" * 800)                 # 一段使用者 PCM
        ws.send_text(json.dumps({"type": "user_end"}))
        for _ in range(4):
            ws.receive()
        ws.send_text(json.dumps({"type": "bye"}))
    assert seen.get("scores", {}).get("pronunciation") == 82.0
    assert scored.get("ref")                              # 對該場跟讀 target 句評分


class _FakeSessionContinuousLateTurnEnd(_FakeSession):
    """真機情境 fake：最後一輪 turn_end 仍在串流佇列，uplink 先因 bye 收尾。

    turn_end 只在 close()（teardown 開始）被呼叫後才吐出，重現「連線提早關閉→
    發音評測跑不到 turn_end」。正確行為＝teardown 應 drain 掉最後一輪（含發音
    評測落地），而非直接 cancel downlink。
    """

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        import asyncio
        self._audio_seen = asyncio.Event()
        self._close_called = asyncio.Event()

    async def send_audio(self, pcm16):
        self.audio_chunks.append(pcm16)
        self._audio_seen.set()

    async def events_continuous(self):
        from server.nova_sonic import NovaEvent
        await self._audio_seen.wait()                    # 確保 uplink 已 tee pcm
        yield NovaEvent("transcript", role="USER", text="I want an apple")
        await self._close_called.wait()                  # teardown 開始才放行
        yield NovaEvent("turn_end")                      # drain 階段才吐 → 應被處理

    async def close(self):
        self.closed = True
        self._close_called.set()


def test_ws_live_continuous_drains_final_turn_pron_on_bye(monkeypatch):
    """continuous：最後一輪 turn_end 在 bye 收尾後才到，teardown 應 drain 並落地發音評測。"""
    monkeypatch.setattr(config, "LIVE_S2S_ENABLED", True)
    monkeypatch.setattr(nova_sonic, "available", lambda: True)
    fake = _FakeSessionContinuousLateTurnEnd()
    monkeypatch.setattr(app_mod, "_make_live_session", lambda: fake)
    monkeypatch.setattr(app_mod.guardrails, "consent_granted", lambda: True)
    monkeypatch.setattr(app_mod.pronunciation, "available", lambda: True)
    monkeypatch.setattr(app_mod.pronunciation, "score", lambda path, ref: 82.0)
    seen = {}
    monkeypatch.setattr(app_mod.store, "add_interaction",
                        lambda d: seen.update(d) or 1)
    client = TestClient(app_mod.app)
    got_turn_end = False
    with client.websocket_connect("/ws/live?mode=continuous") as ws:
        ws.send_bytes(b"\x01\x02" * 800)                 # 一段使用者 PCM（被 tee）
        ws.send_text(json.dumps({"type": "bye"}))        # uplink 收尾 → 觸發 teardown
        # 讀到最後一輪 turn_end 才離開 with（連線存活期間 server drain 完該輪）；
        # 舊碼直接 cancel downlink → 不會送 turn_end、連線被關 → receive 丟例外。
        try:
            for _ in range(6):
                m = ws.receive()
                if m.get("type") == "websocket.close":
                    break
                if m.get("text") and json.loads(m["text"]).get("type") == "turn_end":
                    got_turn_end = True
                    break
        except Exception:
            pass
    # 最後一輪應被 drain：turn_end 送達、發音評測落地、逐字稿存下
    assert got_turn_end
    assert seen.get("asr_text") == "I want an apple"
    assert seen.get("scores", {}).get("pronunciation") == 82.0


def test_pcm_to_pron_score_builds_wav_scores_and_deletes(monkeypatch):
    """組 16k mono wav → pronunciation.score → 回分數；暫存 wav 事後刪除（隱私）。"""
    monkeypatch.setattr(app_mod.pronunciation, "available", lambda: True)
    captured = {}

    def fake_score(path, ref):
        captured["path"] = path
        captured["exists_during"] = os.path.exists(path)
        captured["ref"] = ref
        return 77.0

    monkeypatch.setattr(app_mod.pronunciation, "score", fake_score)
    out = app_mod._pcm_to_pron_score(b"\x01\x02" * 1600, "I want an apple.")
    assert out == 77.0
    assert captured["ref"] == "I want an apple."
    assert captured["exists_during"] is True            # 評分時 wav 在
    assert not os.path.exists(captured["path"])          # 評分後刪除（用後即刪）


# ---------------------------------------------------------------------------
# Task 4：ws_live 連線用 build_lesson 組教學 prompt（_build_live_prompt 助手）
# ---------------------------------------------------------------------------

def test_build_live_prompt_uses_lesson(monkeypatch):
    from server import lesson
    monkeypatch.setattr(app_mod.store, "list_diagnoses", lambda: [])
    monkeypatch.setattr(app_mod.store, "get_profile", lambda: None)
    monkeypatch.setattr(
        lesson, "build_lesson",
        lambda diags, prof: lesson.Lesson("food", "I want to eat an apple.",
                                          "單字", "【策略】多鼓勵。"))
    p = app_mod._build_live_prompt()
    assert "I want to eat an apple." in p
    assert "food" in p
    assert "【策略】多鼓勵。" in p
