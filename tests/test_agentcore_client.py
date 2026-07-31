# -*- coding: utf-8 -*-
"""test_agentcore_client.py — AgentCore Harness 客戶端封裝。

三個 agent 改由 AgentCore Harness 執行後，共用這一層封裝。它負責：
harness ARN 解析、session 管理、InvokeHarness 呼叫、回應取文字。

與 bedrock_converse 平行：那層是「直接呼叫模型」，這層是「呼叫託管的 agent 迴圈」。
全程 monkeypatch client factory、不觸網、不需 AWS 憑證。
"""

from __future__ import annotations

import pytest

from server import agentcore

_ALL_ENV = [
    "TALKYBUDDY_AGENT_BACKEND",
    "AGENTCORE_REGION",
    "AGENTCORE_HARNESS_ORCHESTRATOR",
    "AGENTCORE_HARNESS_HOMEWORK",
    "AGENTCORE_HARNESS_REPORT",
    "AGENTCORE_MEMORY_ARN",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ALL_ENV:
        monkeypatch.delenv(name, raising=False)
    # 本檔會真的走進 agentcore.invoke，而它現在會取全域 Bedrock 節流許可
    # （每秒 1 個請求）。不擋掉的話這個檔案每呼叫一次就真的睡 1.05 秒，
    # 二十幾次 = 整個測試套件慢二十幾秒。節流器本身有 tests/test_bedrock_throttle.py
    # 驗，「invoke 有沒有過閘」則由本檔的 test_invoke_goes_through_the_bedrock_throttle
    # 驗（它會自己覆蓋這個 stub）——這裡換成 no-op 不會少測到任何東西。
    from server import bedrock_throttle

    monkeypatch.setattr(bedrock_throttle, "acquire", lambda **k: 0.0)
    return monkeypatch


class _FakeClient:
    """假 bedrock-agentcore client：記下 invoke_harness 參數並回傳預設 payload。"""

    def __init__(self, payload):
        self._payload = payload
        self.captured: dict = {}

    def invoke_harness(self, **kwargs):
        self.captured = kwargs
        return self._payload


def _ok(text: str) -> dict:
    """一輪正常結束的 InvokeHarness 回應。

    **這是 EventStream，不是 dict payload。** 舊版這裡回的是
    ``{"output": {"message": {"content": [...]}}}``——那是 bedrock-runtime.Converse
    的形狀。fake 寫錯了，於是整批測試一路全綠，卻把一個對真實 API 無效的契約
    釘死。形狀的權威來源是本機 botocore service model，見本檔最後一條
    ``test_fake_matches_the_real_botocore_service_model``。

    定義在檔案前段但實作委派到後段的 ``_stream_ok``：那一段連同註解解釋了
    為什麼形狀是這樣，放在一起讀比較清楚。
    """
    return _stream_ok(text)


# ---------------------------------------------------------------------------
# resolve_config
# ---------------------------------------------------------------------------

def test_disabled_by_default(_clean_env):
    """未選用 agentcore 後端時回 None，既有路徑零影響。"""
    assert agentcore.resolve_config("homework") is None


def test_enabled_when_backend_selected(_clean_env):
    _clean_env.setenv("TALKYBUDDY_AGENT_BACKEND", "agentcore")
    _clean_env.setenv("AGENTCORE_HARNESS_HOMEWORK", "arn:aws:...:harness/HW-1")
    cfg = agentcore.resolve_config("homework")
    assert cfg is not None
    assert cfg["harness_arn"] == "arn:aws:...:harness/HW-1"


def test_region_defaults_to_us_west_2(_clean_env):
    """競賽環境規範第 6 條指定 us-west-2，且官方 region 表列 AgentCore harness
    在 US West (Oregon) 可用，規範與可用性沒有衝突。

    先前預設新加坡：那是自有帳號時期選的（AgentCore 不在台北提供服務，
    新加坡是離台灣最近的可用 region）。理由當時成立，但規範指定 region 之後
    就不成立了——留著預設值，現場照預設跑下去就會建到規範外的 region，
    而且沒有任何東西會報錯。
    """
    _clean_env.setenv("TALKYBUDDY_AGENT_BACKEND", "agentcore")
    _clean_env.setenv("AGENTCORE_HARNESS_HOMEWORK", "arn:x")
    assert agentcore.DEFAULT_REGION == "us-west-2"
    assert agentcore.resolve_config("homework")["region"] == "us-west-2"


def test_region_env_overrides_still_win(_clean_env):
    """AGENTCORE_REGION 仍可覆蓋——現場若發現 us-west-2 有問題要能立刻改。"""
    _clean_env.setenv("TALKYBUDDY_AGENT_BACKEND", "agentcore")
    _clean_env.setenv("AGENTCORE_HARNESS_HOMEWORK", "arn:x")
    _clean_env.setenv("AGENTCORE_REGION", "ap-northeast-1")
    assert agentcore.resolve_config("homework")["region"] == "ap-northeast-1"


def test_missing_harness_arn_returns_none(_clean_env):
    """選了 agentcore 後端但沒設該角色的 harness ARN → 回 None 讓呼叫端降級，
    不可拿空字串去打 API 拿一個難懂的錯誤。"""
    _clean_env.setenv("TALKYBUDDY_AGENT_BACKEND", "agentcore")
    assert agentcore.resolve_config("homework") is None


def test_case_insensitive_backend_value(_clean_env):
    _clean_env.setenv("TALKYBUDDY_AGENT_BACKEND", "  AgentCore ")
    _clean_env.setenv("AGENTCORE_HARNESS_REPORT", "arn:y")
    assert agentcore.resolve_config("report") is not None


def test_unknown_role_returns_none(_clean_env):
    _clean_env.setenv("TALKYBUDDY_AGENT_BACKEND", "agentcore")
    assert agentcore.resolve_config("不存在的角色") is None


# ---------------------------------------------------------------------------
# invoke
# ---------------------------------------------------------------------------

def test_invoke_sends_message_and_returns_text(_clean_env, monkeypatch):
    fake = _FakeClient(_ok('{"focus":"文法"}'))
    monkeypatch.setattr(agentcore, "_build_client", lambda region, timeout_s: fake)

    cfg = {"region": "ap-southeast-1", "harness_arn": "arn:h", "memory_arn": None}
    out = agentcore.invoke(cfg, "學生說：我喜歡蘋果", session_id="sess-1", actor_id="s1")

    assert out == '{"focus":"文法"}'
    assert fake.captured["harnessArn"] == "arn:h"
    # session id 會被補齊到 API 最短長度，但必須看得出原始值（現場查 log 用）
    assert "sess-1" in fake.captured["runtimeSessionId"]
    assert fake.captured["messages"][0]["role"] == "user"


def test_actor_id_is_passed_for_memory_scoping(_clean_env, monkeypatch):
    """actorId 是 Memory 的分群鍵。漏傳會讓所有孩子共用同一份長期記憶。"""
    fake = _FakeClient(_ok("ok"))
    monkeypatch.setattr(agentcore, "_build_client", lambda region, timeout_s: fake)

    agentcore.invoke(
        {"region": "r", "harness_arn": "a", "memory_arn": "m"},
        "hi", session_id="s", actor_id="STUDENT-AMING-004",
    )
    # actorId 必須是雜湊而非明文：同一個值在 prompt 裡會被 deidentify 遮罩，
    # actorId 卻會被 AgentCore Memory 長期保存，明文送上去是自相矛盾的。
    sent = fake.captured["actorId"]
    assert "STUDENT-AMING-004" not in sent, sent
    assert sent == agentcore._hash_actor("STUDENT-AMING-004")


def test_short_session_id_is_padded_to_api_minimum(_clean_env, monkeypatch):
    """InvokeHarness 要求 runtimeSessionId 至少 33 字元（2026-07-26 實測）。

    呼叫端會很自然地傳「turn-12」「STUDENT-AMING-004」這種短字串，
    封裝層必須補齊，否則每個呼叫點都要自己記得這條規則。
    """
    fake = _FakeClient(_ok("ok"))
    monkeypatch.setattr(agentcore, "_build_client", lambda region, timeout_s: fake)

    agentcore.invoke(
        {"region": "r", "harness_arn": "a", "memory_arn": None},
        "hi", session_id="turn-12", actor_id="s1",
    )
    assert len(fake.captured["runtimeSessionId"]) >= 33


def test_same_short_session_id_maps_to_same_padded_id(_clean_env, monkeypatch):
    """補齊必須是決定性的：同一個教學循環的多次呼叫要落在同一個 session，
    Harness 的短期記憶才連貫。若每次補出不同值，記憶就斷了。"""
    seen = []

    def _spy(region, timeout_s):
        c = _FakeClient(_ok("ok"))
        seen.append(c)
        return c

    monkeypatch.setattr(agentcore, "_build_client", _spy)
    cfg = {"region": "r", "harness_arn": "a", "memory_arn": None}
    agentcore.invoke(cfg, "1", session_id="cycle-7", actor_id="s1")
    agentcore.invoke(cfg, "2", session_id="cycle-7", actor_id="s1")

    assert seen[0].captured["runtimeSessionId"] == seen[1].captured["runtimeSessionId"]


def test_different_short_session_ids_do_not_collide(_clean_env, monkeypatch):
    seen = []

    def _spy(region, timeout_s):
        c = _FakeClient(_ok("ok"))
        seen.append(c)
        return c

    monkeypatch.setattr(agentcore, "_build_client", _spy)
    cfg = {"region": "r", "harness_arn": "a", "memory_arn": None}
    agentcore.invoke(cfg, "1", session_id="cycle-7", actor_id="s1")
    agentcore.invoke(cfg, "2", session_id="cycle-8", actor_id="s1")

    assert seen[0].captured["runtimeSessionId"] != seen[1].captured["runtimeSessionId"]


def test_session_id_is_scoped_per_student(_clean_env, monkeypatch):
    """B2：同一個 session key、不同學生，必須落在不同 runtime session。

    呼叫端傳的是「orch-turn-4」「hw-2026-07-20」這種不含學生維度的字串——
    任何孩子跑到第 4 回合都會共用同一個 session，短期記憶跨童串接。
    這不是機率性碰撞，是決定性的、每次都發生。
    """
    seen = []

    def _spy(region, timeout_s):
        c = _FakeClient(_ok("ok"))
        seen.append(c)
        return c

    monkeypatch.setattr(agentcore, "_build_client", _spy)
    cfg = {"region": "r", "harness_arn": "a", "memory_arn": "m"}
    agentcore.invoke(cfg, "1", session_id="orch-turn-4", actor_id="STUDENT-A")
    agentcore.invoke(cfg, "2", session_id="orch-turn-4", actor_id="STUDENT-B")

    assert seen[0].captured["runtimeSessionId"] != seen[1].captured["runtimeSessionId"], \
        "不同學生的同名 session 必須分開"


def test_session_id_stays_stable_for_same_student(_clean_env, monkeypatch):
    """加上學生維度後，同一個孩子的同一個教學循環仍要落在同一 session。"""
    seen = []

    def _spy(region, timeout_s):
        c = _FakeClient(_ok("ok"))
        seen.append(c)
        return c

    monkeypatch.setattr(agentcore, "_build_client", _spy)
    cfg = {"region": "r", "harness_arn": "a", "memory_arn": "m"}
    agentcore.invoke(cfg, "1", session_id="orch-turn-4", actor_id="STUDENT-A")
    agentcore.invoke(cfg, "2", session_id="orch-turn-4", actor_id="STUDENT-A")

    assert seen[0].captured["runtimeSessionId"] == seen[1].captured["runtimeSessionId"]


def test_session_id_does_not_leak_student_id(_clean_env, monkeypatch):
    """學生維度用雜湊帶入：runtimeSessionId 同樣會被雲端保存，不得含明文 id。"""
    fake = _FakeClient(_ok("ok"))
    monkeypatch.setattr(agentcore, "_build_client", lambda region, timeout_s: fake)

    agentcore.invoke(
        {"region": "r", "harness_arn": "a", "memory_arn": "m"},
        "hi", session_id="hw-2026-07-20", actor_id="STUDENT-AMING-004",
    )
    sid = fake.captured["runtimeSessionId"]
    assert "STUDENT-AMING-004" not in sid, sid
    assert "AMING" not in sid, sid
    # 長度仍須符合 API 下限
    assert 33 <= len(sid) <= 64, len(sid)


def test_concatenates_multiple_text_blocks(_clean_env, monkeypatch):
    """跨多個 content block 的文字要串起來（模型分段輸出時會這樣回）。"""
    payload = _stream(
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "前"}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"text": "後"}}},
        {"messageStop": {"stopReason": "end_turn"}},
    )
    monkeypatch.setattr(
        agentcore, "_build_client", lambda region, timeout_s: _FakeClient(payload)
    )
    out = agentcore.invoke(
        {"region": "r", "harness_arn": "a", "memory_arn": None}, "u",
        session_id="s", actor_id="s1",
    )
    assert out == "前後"


def test_raises_when_no_text_block(_clean_env, monkeypatch):
    """回應缺 text：明確拋錯讓呼叫端降級，不靜默回空字串。"""
    monkeypatch.setattr(
        agentcore, "_build_client",
        lambda region, timeout_s: _FakeClient(_stream(
            {"messageStart": {"role": "assistant"}},
            {"messageStop": {"stopReason": "end_turn"}},
        )),
    )
    with pytest.raises(agentcore.AgentCoreResponseError):
        agentcore.invoke(
            {"region": "r", "harness_arn": "a", "memory_arn": None}, "u",
            session_id="s", actor_id="s1",
        )


def test_missing_actor_id_raises_instead_of_silently_dropping(_clean_env, monkeypatch):
    """actor_id 為空時必須拋錯，不可靜默略過欄位。

    少了 actorId，InvokeHarness 照樣成功，但所有孩子的長期記憶會混在同一個
    actor 底下——API 不會抱怨、日誌不會留痕，是最難察覺的隱私事故。
    拋例外讓呼叫端降級回規則式，寧可這次不用雲端。
    """
    monkeypatch.setattr(
        agentcore, "_build_client",
        lambda region, timeout_s: pytest.fail("缺 actor_id 時不該送出請求"),
    )
    for bad in (None, ""):
        with pytest.raises(ValueError):
            agentcore.invoke(
                {"region": "r", "harness_arn": "a", "memory_arn": None}, "u",
                session_id="s", actor_id=bad,
            )


def test_module_import_does_not_load_boto3(_clean_env):
    """import 期不得載入 boto3（沿用 bedrock_converse / nova_sonic 慣例，
    保護 edge 端啟動時間——邊緣裝置根本不會用到 AgentCore）。"""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(agentcore.__file__).read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    assert not any(n.startswith("boto") for n in names), names


def test_invoke_never_forwards_a_skills_override(_clean_env, monkeypatch):
    """InvokeHarness 的 skills 欄位不得由呼叫端決定。

    官方 harness 文件的安全注意事項：skill 內容（含它帶的腳本）會被當成
    **可信輸入**注入 agent context，而且**沒有 IAM condition key 能限制
    per-invocation 的 skills 欄位**——invoke 時同名的 skill 會覆蓋 harness
    上掛好的那份。也就是說，只要有一條路徑能把外部輸入帶到這個欄位，
    就等於讓外部指定 agent 讀什麼指令。

    本層的參數是固定組出來的，沒有 kwargs 透傳。這條測試把「參數只有這
    四個鍵」釘死，避免日後有人為了方便加一個 **extra 就把洞開了。
    """
    fake = _FakeClient(_ok("ok"))
    monkeypatch.setattr(agentcore, "_build_client", lambda region, timeout_s: fake)

    agentcore.invoke(
        {"region": "r", "harness_arn": "a", "memory_arn": "m"},
        "hi", session_id="s", actor_id="s1",
    )

    assert set(fake.captured) == {"harnessArn", "runtimeSessionId", "messages", "actorId"}, \
        f"送出的參數多了：{set(fake.captured) - {'harnessArn', 'runtimeSessionId', 'messages', 'actorId'}}"
    assert "skills" not in fake.captured
    assert "systemPrompt" not in fake.captured


# ---------------------------------------------------------------------------
# InvokeHarness 的真實回應是 EventStream，不是 dict
# ---------------------------------------------------------------------------
#
# 這一段是本檔最重要的部分。原本 `_extract_text` 解的是
# `payload["output"]["message"]["content"]`，那是 **bedrock-runtime.Converse**
# 的形狀，不是 **bedrock-agentcore.InvokeHarness** 的。用本機 botocore service
# model 離線驗證：InvokeHarness 的 output shape 只有一個成員 `stream`，
# 而且標了 `{"eventstream": True}`。
#
# 也就是說：這條路一次都沒成功過。而上面那批測試用的 fake 回的是 dict，
# 於是它們一路全綠，把一個對真實 API 無效的契約釘死了。假的越像真的越好，
# 這裡改成 yield 事件。


def _stream(*events) -> dict:
    """模擬 botocore EventStream：可迭代物件，每次 yield 一個單鍵 dict。"""
    return {"stream": iter(events)}


def _delta(text: str) -> dict:
    return {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": text}}}


def _stream_ok(text: str) -> dict:
    """一輪完整、正常結束的 Harness 串流。"""
    return _stream(
        {"messageStart": {"role": "assistant"}},
        _delta(text),
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"usage": {"inputTokens": 10, "outputTokens": 5}}},
    )


def test_extract_text_consumes_event_stream(_clean_env, monkeypatch):
    """真實 InvokeHarness 回 EventStream；文字要從 contentBlockDelta 累積。"""
    monkeypatch.setattr(
        agentcore, "_build_client",
        lambda region, timeout_s: _FakeClient(_stream_ok('{"focus":"文法"}')),
    )
    out = agentcore.invoke(
        {"region": "r", "harness_arn": "a", "memory_arn": None}, "u",
        session_id="s", actor_id="s1",
    )
    assert out == '{"focus":"文法"}'


def test_extract_text_concatenates_deltas_in_order(_clean_env, monkeypatch):
    """一段 JSON 會被切成很多個 delta 送回來，順序錯了就解不出 JSON。"""
    payload = _stream(
        {"messageStart": {"role": "assistant"}},
        _delta('{"focus":'), _delta('"文法"'), _delta("}"),
        {"messageStop": {"stopReason": "end_turn"}},
    )
    monkeypatch.setattr(
        agentcore, "_build_client", lambda region, timeout_s: _FakeClient(payload)
    )
    out = agentcore.invoke(
        {"region": "r", "harness_arn": "a", "memory_arn": None}, "u",
        session_id="s", actor_id="s1",
    )
    assert out == '{"focus":"文法"}'


def test_reasoning_content_is_not_mixed_into_the_answer(_clean_env, monkeypatch):
    """`delta.reasoningContent` 是思考過程，不是答案，混進去 JSON 就爛了。

    這個專案已經被同型的坑咬過一次（Gemini thinking token 截斷回覆）。
    delta 是個 union-ish 結構，text / toolUse / toolResult / reasoningContent
    是平行的鍵——只取 text，其他一律略過。
    """
    payload = _stream(
        {"contentBlockDelta": {"delta": {"reasoningContent": {"text": "讓我想想…"}}}},
        _delta('{"focus":"文法"}'),
        {"messageStop": {"stopReason": "end_turn"}},
    )
    monkeypatch.setattr(
        agentcore, "_build_client", lambda region, timeout_s: _FakeClient(payload)
    )
    out = agentcore.invoke(
        {"region": "r", "harness_arn": "a", "memory_arn": None}, "u",
        session_id="s", actor_id="s1",
    )
    assert out == '{"focus":"文法"}', "思考過程不得混入答案"


@pytest.mark.parametrize("err_event", [
    "validationException", "internalServerException", "runtimeClientError",
])
def test_error_event_in_stream_raises(_clean_env, monkeypatch, err_event):
    """錯誤是**串流中的事件**，不是 boto3 例外——不主動檢查就會靜默吞掉。

    症狀會是「拿到半截 JSON → schema 驗證失敗 → 降級回規則式」，
    現場看起來像「雲端品質不好」，而不是「雲端根本失敗了」。
    """
    payload = _stream(
        _delta('{"focus":'),
        {err_event: {"message": "boom"}},
    )
    monkeypatch.setattr(
        agentcore, "_build_client", lambda region, timeout_s: _FakeClient(payload)
    )
    with pytest.raises(agentcore.AgentCoreResponseError):
        agentcore.invoke(
            {"region": "r", "harness_arn": "a", "memory_arn": None}, "u",
            session_id="s", actor_id="s1",
        )


def test_stream_without_any_text_raises(_clean_env, monkeypatch):
    """串流跑完一個 text 都沒有 → 拋錯讓呼叫端降級，不回空字串。"""
    payload = _stream(
        {"messageStart": {"role": "assistant"}},
        {"messageStop": {"stopReason": "end_turn"}},
    )
    monkeypatch.setattr(
        agentcore, "_build_client", lambda region, timeout_s: _FakeClient(payload)
    )
    with pytest.raises(agentcore.AgentCoreResponseError):
        agentcore.invoke(
            {"region": "r", "harness_arn": "a", "memory_arn": None}, "u",
            session_id="s", actor_id="s1",
        )


def test_response_without_stream_key_raises(_clean_env, monkeypatch):
    """回應根本沒有 stream 鍵（API 形狀漂移）→ 拋錯，不要 TypeError 到外面。"""
    monkeypatch.setattr(
        agentcore, "_build_client", lambda region, timeout_s: _FakeClient({"output": {}})
    )
    with pytest.raises(agentcore.AgentCoreResponseError):
        agentcore.invoke(
            {"region": "r", "harness_arn": "a", "memory_arn": None}, "u",
            session_id="s", actor_id="s1",
        )


# ---------------------------------------------------------------------------
# 競賽規範：Bedrock 每秒 1 個請求以下
# ---------------------------------------------------------------------------

def test_invoke_goes_through_the_bedrock_throttle(_clean_env, monkeypatch):
    """InvokeHarness 也是打到 Bedrock，必須過同一道全域節流閘。

    規範第 1 條限的是「Amazon Bedrock 的請求」，Harness 在雲端跑的就是模型
    推理迴圈。四個直呼端已經收斂在 bedrock_converse 那道閘；AgentCore 是
    **第五個**呼叫端，走的是另一支 API，不主動接上去就會繞過節流——
    而超速的症狀（ThrottlingException → 靜默降級）在現場只會看起來像
    「雲端很慢」，沒人會想到是自己違規。
    """
    from server import bedrock_throttle

    order: list[str] = []
    monkeypatch.setattr(
        bedrock_throttle, "acquire", lambda **k: order.append("throttle") or 0.0
    )

    class _RecordingClient(_FakeClient):
        def invoke_harness(self, **kwargs):
            order.append("invoke")
            return super().invoke_harness(**kwargs)

    monkeypatch.setattr(
        agentcore, "_build_client",
        lambda region, timeout_s: _RecordingClient(_ok("ok")),
    )
    agentcore.invoke(
        {"region": "r", "harness_arn": "a", "memory_arn": None}, "u",
        session_id="s", actor_id="s1",
    )

    assert order == ["throttle", "invoke"], "必須先取得節流許可才送出請求"


def test_throttle_timeout_propagates_as_failure(_clean_env, monkeypatch):
    """排隊太久時放棄本次呼叫——呼叫端的 except 會降級到 Bedrock 再到規則式。"""
    from server import bedrock_throttle

    def _timeout(**k):
        raise bedrock_throttle.ThrottleTimeout("排太久")

    monkeypatch.setattr(bedrock_throttle, "acquire", _timeout)
    monkeypatch.setattr(
        agentcore, "_build_client",
        lambda region, timeout_s: pytest.fail("節流未放行時不得送出請求"),
    )
    with pytest.raises(bedrock_throttle.ThrottleTimeout):
        agentcore.invoke(
            {"region": "r", "harness_arn": "a", "memory_arn": None}, "u",
            session_id="s", actor_id="s1",
        )


def test_fake_matches_the_real_botocore_service_model(_clean_env):
    """把上面那些 fake 的形狀釘到本機 botocore 的真實 service model 上。

    這條測試存在的唯一理由：**上一版的 fake 是憑想像寫的，而且憑想像寫錯了。**
    離線、不需憑證、不觸網，卻能在本機當場抓到 API 形狀漂移——
    而不是在決賽現場才發現。
    """
    botocore_session = pytest.importorskip("botocore.session")
    model = botocore_session.get_session().get_service_model("bedrock-agentcore")
    op = model.operation_model("InvokeHarness")

    # 1. output 只有 stream，而且是 event stream
    assert set(op.output_shape.members) == {"stream"}, \
        "InvokeHarness 的回應不是 dict payload，是 EventStream"
    stream_shape = op.output_shape.members["stream"]
    assert stream_shape.serialization.get("eventstream") is True

    # 2. 我們消費的事件與欄位真的存在
    assert "contentBlockDelta" in stream_shape.members
    delta = stream_shape.members["contentBlockDelta"].members["delta"]
    assert "text" in delta.members
    assert "reasoningContent" in delta.members, "reasoningContent 是平行鍵，不能當成 text"

    # 3. 我們檢查的錯誤事件真的是串流事件（不是 boto3 例外）
    for name in ("validationException", "internalServerException", "runtimeClientError"):
        assert name in stream_shape.members, name

    # 4. 我們送出的參數名稱都存在（harnessArn 而非 harness_arn 之類的低級錯）
    for name in ("harnessArn", "runtimeSessionId", "messages", "actorId"):
        assert name in op.input_shape.members, name
