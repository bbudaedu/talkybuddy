# -*- coding: utf-8 -*-
"""test_agent_material.py — 教材提煉 agent（server/agents/material.py）測試集。

嚴格 TDD：規則式路徑先於雲端路徑測試。
"""

from __future__ import annotations


def test_rule_based_extract_finds_existing_vocab_by_chinese_key():
    """教材文字含既有詞庫的中文鍵 → 命中並回傳該詞條。"""
    from server.agents.material import _rule_based_extract

    result = _rule_based_extract("今天我們去動物園看獅子和大象。")

    assert result["source"] == "rule"
    assert result["rejected_count"] == 0
    hit_zh = {e["zh"] for e in result["entries"]}
    assert "獅子" in hit_zh
    assert "大象" in hit_zh
    assert result["accepted_count"] == len(result["entries"])


def test_rule_based_extract_finds_existing_vocab_by_english_word():
    """教材文字含既有詞庫的英文詞（不分大小寫）→ 也能命中。"""
    from server.agents.material import _rule_based_extract

    result = _rule_based_extract("Today we saw a Lion at the zoo.")

    hit_en = {e["en"] for e in result["entries"]}
    assert "lion" in hit_en


def test_rule_based_extract_never_invents_new_words():
    """規則式路徑絕不能回傳不在既有 VOCAB 裡的詞——就算文字裡有課綱外的字。"""
    from server.agents.material import _rule_based_extract
    from server.scaffold import VOCAB

    result = _rule_based_extract("我們今天學了 quokka 這個新單字，牠是一種可愛的動物。")

    for entry in result["entries"]:
        assert entry["zh"] in VOCAB, f"{entry} 不應是自創詞"
        assert VOCAB[entry["zh"]]["en"] == entry["en"]


def test_rule_based_extract_handles_no_match_without_raising():
    """教材文字完全沒有課綱詞彙 → 空 entries，不拋例外，仍是合法 schema。"""
    from server.agents.material import _rule_based_extract

    result = _rule_based_extract("這是一段完全沒有相關詞彙的中文。")

    assert result["entries"] == []
    assert result["accepted_count"] == 0
    assert result["rejected_count"] == 0
    assert isinstance(result["topic"], str) and result["topic"].strip()


def test_rule_based_extract_handles_empty_and_none_input():
    """空字串／None 輸入不拋例外。"""
    from server.agents.material import _rule_based_extract

    for bad_input in ("", None):
        result = _rule_based_extract(bad_input)
        assert result["source"] == "rule"
        assert result["entries"] == []


def test_rule_based_extract_caps_at_max_entries():
    """就算教材文字命中很多既有詞，也不超過上限（避免單次教材塞爆詞庫）。"""
    from server.agents.material import _rule_based_extract
    from server.scaffold import VOCAB

    # 把所有詞庫的中文鍵串成一段長文字，確保命中數遠超過上限
    all_keys_text = "、".join(VOCAB.keys())
    result = _rule_based_extract(all_keys_text)

    assert len(result["entries"]) <= 8


def test_rule_based_extract_avoids_substring_false_positive_chinese():
    """短詞不應因為是長詞的子字串就被誤配（例：水果不應同時導致水被匹配）。"""
    from server.agents.material import _rule_based_extract

    result = _rule_based_extract("今天我們去看水果。")

    hit_zh = {e["zh"] for e in result["entries"]}
    # 應該只匹配「水果」不匹配「水」（因為水是水果的子字串）
    assert "水果" in hit_zh
    assert "水" not in hit_zh


def test_rule_based_extract_avoids_substring_false_positive_english():
    """英文詞邊界匹配，避免子字串誤配（例：pencil 不應導致 pen 被匹配）。"""
    from server.agents.material import _rule_based_extract

    result = _rule_based_extract("I have a pencil in my backpack.")

    hit_en = {e["en"] for e in result["entries"]}
    # 應該只匹配詞邊界內的詞，不應該從 pencil 裡分出 pen
    # 注意：假設「筆」(pen) 在 VOCAB 裡；若不在則 entries 應為空或不含 pen
    for entry in result["entries"]:
        if entry["en"] == "pen":
            # 如果 pen 被匹配了，代表這個測試用例有問題（實際應只匹配邊界詞）
            raise AssertionError(f"不應該從 pencil 裡誤配 pen，但卻匹配了：{entry}")


def test_rule_based_extract_handles_non_string_inputs():
    """非字串輸入（int、list、dict、bool、bytes 等）不拋例外，降級成空結果。"""
    from server.agents.material import _rule_based_extract

    non_string_inputs = [
        123,                    # int
        [1, 2, 3],             # list
        {"key": "value"},      # dict
        True,                  # bool
        3.14,                  # float
        b"bytes",              # bytes
    ]

    for bad_input in non_string_inputs:
        result = _rule_based_extract(bad_input)
        assert result["source"] == "rule", f"Failed for input type: {type(bad_input)}"
        assert result["entries"] == [], f"Failed for input type: {type(bad_input)}"
        assert result["accepted_count"] == 0, f"Failed for input type: {type(bad_input)}"
        assert result["rejected_count"] == 0, f"Failed for input type: {type(bad_input)}"
        assert isinstance(result["topic"], str), f"Failed for input type: {type(bad_input)}"


# ---------------------------------------------------------------------------
# extract_vocab 公開入口：allow_cloud 閘門與降級鏈
# ---------------------------------------------------------------------------

def _assert_valid_schema(result: dict, expected_source: str | None = None) -> None:
    assert isinstance(result, dict)
    assert isinstance(result.get("topic"), str) and result["topic"].strip()
    assert isinstance(result.get("entries"), list)
    assert isinstance(result.get("accepted_count"), int)
    assert isinstance(result.get("rejected_count"), int)
    assert result.get("source") in ("cloud", "rule")
    if expected_source is not None:
        assert result["source"] == expected_source


def test_allow_cloud_false_never_touches_network(monkeypatch):
    """allow_cloud=False → resolve_config／converse_text 皆不被呼叫，source='rule'。"""
    from server.agents import material
    from server import bedrock_converse

    def _should_not_call(*a, **kw):
        import pytest
        pytest.fail("allow_cloud=False 時不應呼叫雲端函式")

    monkeypatch.setattr(bedrock_converse, "resolve_config", _should_not_call)
    monkeypatch.setattr(bedrock_converse, "converse_text", _should_not_call)

    result = material.extract_vocab("我們去動物園看獅子。", allow_cloud=False)

    _assert_valid_schema(result, expected_source="rule")


def test_cloud_path_success_merges_new_entries(monkeypatch):
    """雲端回傳合法 JSON → 詞條經 register_material_vocab 驗證合併，source='cloud'。"""
    import json
    from server.agents import material
    from server import bedrock_converse, scaffold

    snapshot = {zh: dict(v) for zh, v in scaffold.VOCAB.items()}
    try:
        cloud_json = json.dumps({
            "topic": "動物園一日遊",
            "entries": [
                {"en": "koala", "zh": "無尾熊", "cat": "animal",
                 "np": "a koala", "sent": "I see a koala."},
            ],
            "source": "cloud",
        }, ensure_ascii=False)

        monkeypatch.setattr(bedrock_converse, "resolve_config",
                            lambda role=None: {"region": "ap-east-2", "model_id": "test-model"})
        monkeypatch.setattr(bedrock_converse, "converse_text",
                            lambda *a, **kw: cloud_json)

        result = material.extract_vocab("今天去動物園看了一隻無尾熊。", allow_cloud=True)

        _assert_valid_schema(result, expected_source="cloud")
        assert result["accepted_count"] == 1
        assert result["rejected_count"] == 0
        assert "無尾熊" in scaffold.VOCAB
        assert scaffold.VOCAB["無尾熊"]["en"] == "koala"
    finally:
        scaffold.VOCAB.clear()
        scaffold.VOCAB.update(snapshot)


def test_cloud_response_with_invalid_entries_reports_rejected(monkeypatch):
    """雲端提議的詞條裡有不合法的（分類錯誤）→ accepted/rejected 誠實回報。"""
    import json
    from server.agents import material
    from server import bedrock_converse, scaffold

    snapshot = {zh: dict(v) for zh, v in scaffold.VOCAB.items()}
    try:
        cloud_json = json.dumps({
            "topic": "動物園一日遊",
            "entries": [
                {"en": "koala", "zh": "無尾熊", "cat": "animal",
                 "np": "a koala", "sent": "I see a koala."},
                {"en": "robot", "zh": "機器人", "cat": "toy",  # 不合法分類
                 "np": "a robot", "sent": "I see a robot."},
            ],
            "source": "cloud",
        }, ensure_ascii=False)

        monkeypatch.setattr(bedrock_converse, "resolve_config",
                            lambda role=None: {"region": "ap-east-2", "model_id": "test-model"})
        monkeypatch.setattr(bedrock_converse, "converse_text",
                            lambda *a, **kw: cloud_json)

        result = material.extract_vocab("動物園教材", allow_cloud=True)

        assert result["accepted_count"] == 1
        assert result["rejected_count"] == 1
        assert "機器人" not in scaffold.VOCAB
    finally:
        scaffold.VOCAB.clear()
        scaffold.VOCAB.update(snapshot)


def test_cloud_failure_falls_back_to_rule(monkeypatch):
    """converse_text 拋例外 → 靜默降級，source='rule'。"""
    from server.agents import material
    from server import bedrock_converse

    monkeypatch.setattr(bedrock_converse, "resolve_config",
                        lambda role=None: {"region": "ap-east-2", "model_id": "test-model"})
    monkeypatch.setattr(bedrock_converse, "converse_text",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("網路超時")))

    result = material.extract_vocab("動物園教材", allow_cloud=True)

    _assert_valid_schema(result, expected_source="rule")


def test_cloud_invalid_json_falls_back_to_rule(monkeypatch):
    """converse_text 回傳非 JSON → 降級到規則式，不拋例外。"""
    from server.agents import material
    from server import bedrock_converse

    monkeypatch.setattr(bedrock_converse, "resolve_config",
                        lambda role=None: {"region": "ap-east-2", "model_id": "test-model"})
    monkeypatch.setattr(bedrock_converse, "converse_text",
                        lambda *a, **kw: "這不是 JSON {broken")

    result = material.extract_vocab("動物園教材", allow_cloud=True)

    _assert_valid_schema(result, expected_source="rule")


def test_guardrail_hit_falls_back_to_rule(monkeypatch):
    """雲端回覆含禁詞 → 護欄攔截後降級，source='rule'。"""
    import json
    from server.agents import material
    from server import bedrock_converse

    unsafe = json.dumps({
        "topic": "動物園",
        "entries": [{"en": "kill", "zh": "殺", "cat": "action",
                     "np": "kill", "sent": "Kill the monster."}],
        "source": "cloud",
    }, ensure_ascii=False)

    monkeypatch.setattr(bedrock_converse, "resolve_config",
                        lambda role=None: {"region": "ap-east-2", "model_id": "test-model"})
    monkeypatch.setattr(bedrock_converse, "converse_text", lambda *a, **kw: unsafe)

    result = material.extract_vocab("動物園教材", allow_cloud=True)

    _assert_valid_schema(result, expected_source="rule")


def test_no_cloud_backend_configured_falls_back_to_rule(monkeypatch):
    """allow_cloud=True 但沒有任何雲端後端設定（resolve_config 回 None）→ 規則式。"""
    from server.agents import material
    from server import bedrock_converse

    monkeypatch.setattr(bedrock_converse, "resolve_config", lambda role=None: None)

    result = material.extract_vocab("動物園教材", allow_cloud=True)

    _assert_valid_schema(result, expected_source="rule")


def test_no_exception_on_extreme_inputs():
    """None／空字串輸入不拋例外。"""
    from server.agents import material

    for bad in (None, ""):
        result = material.extract_vocab(bad, allow_cloud=False)
        _assert_valid_schema(result)


def test_agentcore_branch_calls_invoke_with_truthy_actor_id(monkeypatch):
    """AgentCore 分支被走到時，actor_id 必須是真值（非 None／非空字串）。

    agentcore.invoke() 對非真值 actor_id 一律拋 ValueError（見
    server/agentcore.py 的守門：漏傳會讓所有孩子共用同一份長期記憶）。教材
    提煉不分學生，但仍要傳一個固定的非個人化 sentinel 滿足這道守門——
    否則 AgentCore 分支即使正確設定也會每次都在第一步被守門擋下、
    silently 降級到 Bedrock，agent_backends.chain("material") 回報的鏈
    就變成一句謊言。
    """
    import json
    from server.agents import material
    from server import agent_backends, agentcore

    captured: dict = {}

    def fake_invoke(cfg, user_message, *, actor_id=None, session_id=None, timeout_s=None):
        captured["actor_id"] = actor_id
        return json.dumps({
            "topic": "動物園一日遊",
            "entries": [],
            "source": "cloud",
        }, ensure_ascii=False)

    monkeypatch.setattr(
        agent_backends, "resolve",
        lambda role: ({"region": "us-west-2", "harness_arn": "arn:material"}, None),
    )
    monkeypatch.setattr(agentcore, "invoke", fake_invoke)

    result = material.extract_vocab("動物園教材", allow_cloud=True)

    assert "actor_id" in captured, "AgentCore 分支應被走到（agentcore.invoke 應被呼叫）"
    assert captured["actor_id"], "actor_id 必須是真值，不能是 None 或空字串"
    assert captured["actor_id"] != "None"
    _assert_valid_schema(result, expected_source="cloud")


def test_agentcore_session_id_differs_per_text(monkeypatch):
    """不同教材文字 → 不同 session_id，避免所有老師/所有次上傳共用同一個
    Harness session（一旦設定 AGENTCORE_MEMORY_ARN 就是無上限成長、彼此
    污染的共用對話歷史）。actor_id 已是固定的非個人化 sentinel，
    session_id 就必須由呼叫本身的輸入推導，兩者不能同時是常數。
    """
    import json
    from server.agents import material
    from server import agent_backends, agentcore

    captured_session_ids: list[str] = []

    def fake_invoke(cfg, user_message, *, actor_id=None, session_id=None, timeout_s=None):
        captured_session_ids.append(session_id)
        return json.dumps({
            "topic": "教材",
            "entries": [],
            "source": "cloud",
        }, ensure_ascii=False)

    monkeypatch.setattr(
        agent_backends, "resolve",
        lambda role: ({"region": "us-west-2", "harness_arn": "arn:material"}, None),
    )
    monkeypatch.setattr(agentcore, "invoke", fake_invoke)

    material.extract_vocab("今天我們去動物園看獅子。", allow_cloud=True)
    material.extract_vocab("今天我們去海邊看魚。", allow_cloud=True)

    assert len(captured_session_ids) == 2
    assert all(captured_session_ids), "session_id 不得是 None 或空字串"
    assert captured_session_ids[0] != captured_session_ids[1], \
        f"不同教材文字應產生不同 session_id，得到相同值：{captured_session_ids}"

    # 決定性：同一份教材重複送，session_id 應相同（可重現，不是隨機的）
    captured_session_ids.clear()
    material.extract_vocab("今天我們去動物園看獅子。", allow_cloud=True)
    material.extract_vocab("今天我們去動物園看獅子。", allow_cloud=True)
    assert captured_session_ids[0] == captured_session_ids[1], \
        "同一份教材文字重複送，session_id 應相同（決定性）"
