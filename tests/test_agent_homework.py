# -*- coding: utf-8 -*-
"""test_agent_homework.py — 派作業 agent（server/agents/homework.py）測試集。

嚴格 TDD：所有測試先於實作存在。
monkeypatch 取代 converse_text，全程不觸網。

測試涵蓋：
  1. 正常雲端路徑（allow_cloud=True，converse_text 成功）
  2. 雲端失敗降級（converse_text 拋例外 → 規則式）
  3. allow_cloud=False 完全不出境（converse_text 與 resolve_config 皆不呼叫）
  4. 護欄命中降級（converse_text 回不安全內容 → 規則式）
  5. 去識別化生效（上雲前 profile / diagnosis 自由文字經 deidentify）
  6. 雲端 + 規則式兩條路徑 schema 完全一致
  7. 規則式離線路徑也能產出合法 items（3-5 題）
  8. focus 取 diagnosis 最低分維度
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# 測試夾具（Fixtures）
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_profile() -> dict:
    """模擬學生 profile（含自由文字，用於測試去識別化）。"""
    return {
        "student_id": "STUDENT-AMING-004",
        "name": "阿明",            # 中文姓名（非個資詞，但存在）
        "interests": ["animal", "food"],
        "mastered_vocab": [{"en": "apple", "cat": "food"}],
        # 含一個 Title-case 英文名（預期被 deidentify 遮罩成 [名字]）
        "notes": "Classmate is Tommy, he likes soccer.",
    }


@pytest.fixture
def sample_diagnosis() -> dict:
    """模擬診斷（文法最低分，預期 focus = grammar 或其中文名稱）。"""
    return {
        "date": "2026-07-26",
        "scores": {
            "pronunciation": 70,
            "fluency": 68,
            "vocabulary": 65,
            "grammar": 50,    # 最低分維度
        },
        "strengths": ["願意開口嘗試"],
        "weaknesses": ["冠詞 a/an 仍不穩定"],
        "emotional_status": "穩定",
        "instructions": {"classroom": "…", "device": "…", "peer": "…"},
    }


@pytest.fixture
def cloud_response_valid() -> str:
    """合法的雲端 JSON 回應字串（符合 homework schema）。"""
    import json
    data = {
        "focus": "文法",
        "items": [
            {"target_en": "I have a dog.", "prompt_zh": "告訴我你有什麼動物？", "why": "練習冠詞 a"},
            {"target_en": "She likes cats.", "prompt_zh": "你的朋友喜歡什麼？", "why": "練習第三人稱 -s"},
            {"target_en": "We eat an apple.", "prompt_zh": "說說看你們在吃什麼？", "why": "練習 an"},
        ],
        "source": "cloud",
    }
    return json.dumps(data, ensure_ascii=False)


# ---------------------------------------------------------------------------
# schema 驗證工具（測試共用）
# ---------------------------------------------------------------------------

def _assert_valid_schema(result: dict, expected_source: str | None = None) -> None:
    """驗證 generate_homework 回傳值符合公開契約。"""
    assert isinstance(result, dict), f"回傳值應為 dict，得到 {type(result)}"
    # focus
    assert "focus" in result, "缺 focus 欄位"
    assert isinstance(result["focus"], str) and result["focus"].strip(), \
        "focus 應為非空字串"
    # items
    assert "items" in result, "缺 items 欄位"
    assert isinstance(result["items"], list), "items 應為 list"
    assert 3 <= len(result["items"]) <= 5, \
        f"items 應有 3-5 題，得到 {len(result['items'])} 題"
    for i, item in enumerate(result["items"]):
        assert isinstance(item, dict), f"items[{i}] 應為 dict"
        assert "target_en" in item and isinstance(item["target_en"], str) and item["target_en"].strip(), \
            f"items[{i}] 缺 target_en 或為空"
        assert "prompt_zh" in item and isinstance(item["prompt_zh"], str) and item["prompt_zh"].strip(), \
            f"items[{i}] 缺 prompt_zh 或為空"
        assert "why" in item and isinstance(item["why"], str) and item["why"].strip(), \
            f"items[{i}] 缺 why 或為空"
    # source
    assert "source" in result, "缺 source 欄位"
    assert result["source"] in ("cloud", "rule"), \
        f"source 應為 'cloud' 或 'rule'，得到 {result['source']!r}"
    if expected_source is not None:
        assert result["source"] == expected_source, \
            f"預期 source={expected_source!r}，得到 {result['source']!r}"


# ---------------------------------------------------------------------------
# 測試 1：正常雲端路徑
# ---------------------------------------------------------------------------

def test_cloud_path_success(monkeypatch, sample_profile, sample_diagnosis, cloud_response_valid):
    """allow_cloud=True 且 converse_text 成功 → source='cloud'，schema 合法。"""
    import json
    from server.agents import homework
    from server import bedrock_converse

    # monkeypatch resolve_config 讓雲端閘開啟
    monkeypatch.setattr(bedrock_converse, "resolve_config",
                        lambda role=None: {"region": "ap-east-2", "model_id": "test-model"})

    # monkeypatch converse_text 回合法 JSON
    monkeypatch.setattr(bedrock_converse, "converse_text",
                        lambda system, user, cfg, max_tokens=512, timeout_s=12.0: cloud_response_valid)

    result = homework.generate_homework(sample_profile, sample_diagnosis, allow_cloud=True)

    _assert_valid_schema(result, expected_source="cloud")


# ---------------------------------------------------------------------------
# 測試 2：雲端失敗降級
# ---------------------------------------------------------------------------

def test_cloud_failure_fallback_to_rule(monkeypatch, sample_profile, sample_diagnosis):
    """converse_text 拋例外 → 靜默降級，source='rule'，schema 合法。"""
    from server.agents import homework
    from server import bedrock_converse

    monkeypatch.setattr(bedrock_converse, "resolve_config",
                        lambda role=None: {"region": "ap-east-2", "model_id": "test-model"})
    monkeypatch.setattr(bedrock_converse, "converse_text",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("網路超時")))

    result = homework.generate_homework(sample_profile, sample_diagnosis, allow_cloud=True)

    _assert_valid_schema(result, expected_source="rule")


# ---------------------------------------------------------------------------
# 測試 3：allow_cloud=False 完全不出境
# ---------------------------------------------------------------------------

def test_allow_cloud_false_no_network(monkeypatch, sample_profile, sample_diagnosis):
    """allow_cloud=False → converse_text 與 resolve_config 皆不被呼叫，source='rule'。"""
    from server.agents import homework
    from server import bedrock_converse

    def _should_not_call_resolve(*a, **kw):
        pytest.fail("allow_cloud=False 時不應呼叫 resolve_config")

    def _should_not_call_converse(*a, **kw):
        pytest.fail("allow_cloud=False 時不應呼叫 converse_text")

    monkeypatch.setattr(bedrock_converse, "resolve_config", _should_not_call_resolve)
    monkeypatch.setattr(bedrock_converse, "converse_text", _should_not_call_converse)

    result = homework.generate_homework(sample_profile, sample_diagnosis, allow_cloud=False)

    _assert_valid_schema(result, expected_source="rule")


# ---------------------------------------------------------------------------
# 測試 4：護欄命中降級
# ---------------------------------------------------------------------------

def test_guardrail_hit_fallback_to_rule(monkeypatch, sample_profile, sample_diagnosis):
    """converse_text 回含禁詞的不安全內容 → 護欄攔截後降級，source='rule'。"""
    import json
    from server.agents import homework
    from server import bedrock_converse

    # 產出包含禁詞「殺」的內容，觸發 passes_guardrail=False
    unsafe_content = json.dumps({
        "focus": "文法",
        "items": [
            {"target_en": "Kill the dragon.", "prompt_zh": "殺掉它！", "why": "動詞練習"},
            {"target_en": "I have a dog.", "prompt_zh": "說說你的狗", "why": "冠詞"},
            {"target_en": "She likes cats.", "prompt_zh": "你的朋友呢？", "why": "第三人稱"},
        ],
        "source": "cloud",
    }, ensure_ascii=False)

    monkeypatch.setattr(bedrock_converse, "resolve_config",
                        lambda role=None: {"region": "ap-east-2", "model_id": "test-model"})
    monkeypatch.setattr(bedrock_converse, "converse_text",
                        lambda *a, **kw: unsafe_content)

    result = homework.generate_homework(sample_profile, sample_diagnosis, allow_cloud=True)

    # 護欄命中 → 降級到 rule，不可回傳不安全內容
    _assert_valid_schema(result, expected_source="rule")
    # items 不含禁詞
    for item in result["items"]:
        assert "殺" not in item["prompt_zh"], "禁詞不應出現在回傳 items"
        assert "Kill" not in item["target_en"] or "kill" not in item["target_en"].lower(), \
            "安全性問題：禁詞不應出現在 target_en"


# ---------------------------------------------------------------------------
# 測試 5：去識別化生效
# ---------------------------------------------------------------------------

def test_deidentify_applied_before_cloud(monkeypatch, sample_profile, sample_diagnosis):
    """上雲前 profile / diagnosis 自由文字應經 deidentify 遮罩。"""
    import json
    from server.agents import homework
    from server import bedrock_converse

    captured_prompt: list[str] = []

    def fake_converse(system, user, *, cfg, max_tokens=512, timeout_s=12.0):
        captured_prompt.append(user)
        # 回合法 JSON 讓雲端路徑走完
        return json.dumps({
            "focus": "文法",
            "items": [
                {"target_en": "I have a dog.", "prompt_zh": "說說你的狗", "why": "冠詞"},
                {"target_en": "She likes cats.", "prompt_zh": "你的朋友呢？", "why": "第三人稱"},
                {"target_en": "We eat an apple.", "prompt_zh": "說說你吃什麼", "why": "an"},
            ],
            "source": "cloud",
        }, ensure_ascii=False)

    monkeypatch.setattr(bedrock_converse, "resolve_config",
                        lambda role=None: {"region": "ap-east-2", "model_id": "test-model"})
    monkeypatch.setattr(bedrock_converse, "converse_text", fake_converse)

    homework.generate_homework(sample_profile, sample_diagnosis, allow_cloud=True)

    assert captured_prompt, "converse_text 應被呼叫"
    prompt_text = captured_prompt[0]
    # Tommy 是 Title-case 專名，應被 deidentify 遮罩成 [名字]
    assert "Tommy" not in prompt_text, \
        "上雲前應對自由文字去識別化，Tommy 應被遮罩"


# ---------------------------------------------------------------------------
# 測試 6：兩條路徑 schema 完全一致
# ---------------------------------------------------------------------------

def test_schema_consistency_cloud_vs_rule(monkeypatch, sample_profile, sample_diagnosis,
                                           cloud_response_valid):
    """雲端路徑與規則式路徑的回傳 schema keys 完全一致。"""
    import json
    from server.agents import homework
    from server import bedrock_converse

    # 雲端路徑
    monkeypatch.setattr(bedrock_converse, "resolve_config",
                        lambda role=None: {"region": "ap-east-2", "model_id": "test-model"})
    monkeypatch.setattr(bedrock_converse, "converse_text",
                        lambda *a, **kw: cloud_response_valid)
    cloud_result = homework.generate_homework(sample_profile, sample_diagnosis, allow_cloud=True)

    # 規則式路徑
    rule_result = homework.generate_homework(sample_profile, sample_diagnosis, allow_cloud=False)

    assert set(cloud_result.keys()) == set(rule_result.keys()), \
        f"兩條路徑 schema keys 不一致：cloud={set(cloud_result.keys())}，rule={set(rule_result.keys())}"

    # 子結構也一致
    for i, (c_item, r_item) in enumerate(zip(cloud_result["items"], rule_result["items"])):
        assert set(c_item.keys()) == {"target_en", "prompt_zh", "why"}, \
            f"cloud items[{i}] keys 不符 schema"
        assert set(r_item.keys()) == {"target_en", "prompt_zh", "why"}, \
            f"rule items[{i}] keys 不符 schema"


# ---------------------------------------------------------------------------
# 測試 7：規則式路徑離線也能產出合法 items
# ---------------------------------------------------------------------------

def test_rule_path_produces_valid_items(sample_profile, sample_diagnosis):
    """allow_cloud=False 時規則式路徑能產出 3-5 題合法 items，且題目來自 scaffold 詞庫。"""
    from server.agents import homework
    from server.scaffold import VOCAB

    result = homework.generate_homework(sample_profile, sample_diagnosis, allow_cloud=False)

    _assert_valid_schema(result, expected_source="rule")

    # 每題的 target_en 應含詞庫中某個英文詞，確認題目來自 scaffold，非自編
    vocab_words = {v["en"] for v in VOCAB.values()}
    for item in result["items"]:
        en_words = {w.lower() for w in item["target_en"].replace(".", "").split()}
        overlap = en_words & {w.lower() for w in vocab_words}
        assert overlap, \
            f"target_en={item['target_en']!r} 未含任何 scaffold 詞庫詞，可能是自編題庫"


# ---------------------------------------------------------------------------
# 測試 8：focus 取 diagnosis 最低分維度
# ---------------------------------------------------------------------------

def test_focus_is_lowest_dim(sample_profile):
    """focus 應反映 diagnosis 中四維最低分的維度。"""
    from server.agents import homework

    # 讓 vocabulary 分數最低
    diag = {
        "date": "2026-07-26",
        "scores": {
            "pronunciation": 80,
            "fluency": 75,
            "vocabulary": 42,  # 最低
            "grammar": 65,
        },
        "strengths": ["開口意願高"],
        "weaknesses": ["詞彙量不足"],
        "emotional_status": "穩定",
        "instructions": {"classroom": "…", "device": "…", "peer": "…"},
    }

    result = homework.generate_homework(sample_profile, diag, allow_cloud=False)

    _assert_valid_schema(result, expected_source="rule")
    # focus 應包含 vocabulary 相關描述（中文或英文皆可）
    # 接受「詞彙」或「vocabulary」或「Vocabulary」
    assert any(kw in result["focus"] for kw in ("詞彙", "vocabulary", "Vocabulary")), \
        f"focus={result['focus']!r} 應反映最低分維度 vocabulary"


# ---------------------------------------------------------------------------
# 測試 9：任何例外都不往外拋（極端輸入）
# ---------------------------------------------------------------------------

def test_no_exception_on_extreme_inputs():
    """空 profile / 空 diagnosis / 殘缺 scores → 不拋例外，永遠回合法結果。"""
    from server.agents import homework

    # 空輸入
    result1 = homework.generate_homework({}, {}, allow_cloud=False)
    _assert_valid_schema(result1)

    # scores 只有部分維度
    result2 = homework.generate_homework(
        {"student_id": "x"},
        {"scores": {"fluency": 30}},
        allow_cloud=False,
    )
    _assert_valid_schema(result2)

    # None 值
    result3 = homework.generate_homework(
        None,   # type: ignore[arg-type]
        None,   # type: ignore[arg-type]
        allow_cloud=False,
    )
    _assert_valid_schema(result3)


# ---------------------------------------------------------------------------
# 測試 10：雲端回傳 JSON 格式錯誤 → 降級而非崩潰
# ---------------------------------------------------------------------------

def test_cloud_invalid_json_fallback(monkeypatch, sample_profile, sample_diagnosis):
    """converse_text 回傳非 JSON 字串 → 降級到規則式，source='rule'。"""
    from server.agents import homework
    from server import bedrock_converse

    monkeypatch.setattr(bedrock_converse, "resolve_config",
                        lambda role=None: {"region": "ap-east-2", "model_id": "test-model"})
    monkeypatch.setattr(bedrock_converse, "converse_text",
                        lambda *a, **kw: "這不是 JSON {broken")

    result = homework.generate_homework(sample_profile, sample_diagnosis, allow_cloud=True)

    _assert_valid_schema(result, expected_source="rule")


# ---------------------------------------------------------------------------
# 測試 11：雲端回傳 items 數量不符 → 降級（schema 驗證）
# ---------------------------------------------------------------------------

def test_cloud_wrong_item_count_fallback(monkeypatch, sample_profile, sample_diagnosis):
    """converse_text 回傳 items 只有 1 題（不符 3-5 題範圍）→ 降級到規則式。"""
    import json
    from server.agents import homework
    from server import bedrock_converse

    bad_response = json.dumps({
        "focus": "文法",
        "items": [
            {"target_en": "I have a dog.", "prompt_zh": "說說你的狗", "why": "冠詞"},
        ],
        "source": "cloud",
    }, ensure_ascii=False)

    monkeypatch.setattr(bedrock_converse, "resolve_config",
                        lambda role=None: {"region": "ap-east-2", "model_id": "test-model"})
    monkeypatch.setattr(bedrock_converse, "converse_text",
                        lambda *a, **kw: bad_response)

    result = homework.generate_homework(sample_profile, sample_diagnosis, allow_cloud=True)

    _assert_valid_schema(result, expected_source="rule")


# ---------------------------------------------------------------------------
# 測試 12（D4）：四個維度兩兩比較，items 不得完全相同
# ---------------------------------------------------------------------------

def test_four_dims_produce_different_items():
    """四個弱項維度各自跑一次規則式，任意兩個維度的 items 不得完全一樣（D4 保護）。"""
    from server.agents import homework

    _ALL_DIMS = ("pronunciation", "fluency", "vocabulary", "grammar")
    results: dict[str, list[dict]] = {}
    for dim in _ALL_DIMS:
        scores = {d: 80 for d in _ALL_DIMS}
        scores[dim] = 40
        r = homework.generate_homework({}, {"scores": scores}, allow_cloud=False)
        _assert_valid_schema(r, expected_source="rule")
        # items 序列化為可比較格式
        results[dim] = [i["target_en"] for i in r["items"]]

    # 任意兩個維度不得完全相同
    dims = list(_ALL_DIMS)
    for i in range(len(dims)):
        for j in range(i + 1, len(dims)):
            d1, d2 = dims[i], dims[j]
            assert results[d1] != results[d2], (
                f"維度 {d1!r} 與 {d2!r} 產出完全相同的作業，個人化失效（D4）：\n"
                f"  {d1}: {results[d1]}\n"
                f"  {d2}: {results[d2]}"
            )


# ---------------------------------------------------------------------------
# 測試 13（D2）：任何 prompt_zh 不得包含對應 target_en 的英文單字
# ---------------------------------------------------------------------------

def test_prompt_zh_does_not_leak_target_en():
    """規則式產出的每道題，prompt_zh 不得包含 target_en 中的任何英文單字（D2 保護）。"""
    from server.agents import homework

    _ALL_DIMS = ("pronunciation", "fluency", "vocabulary", "grammar")
    for dim in _ALL_DIMS:
        scores = {d: 80 for d in _ALL_DIMS}
        scores[dim] = 40
        r = homework.generate_homework({}, {"scores": scores}, allow_cloud=False)
        for item in r["items"]:
            target_words = {
                w.lower().strip(".,!?'\"")
                for w in item["target_en"].split()
                if w.strip(".,!?'\"").isalpha()
            }
            # prompt_zh 轉小寫後不得含 target_en 的任何英文詞
            prompt_lower = item["prompt_zh"].lower()
            leaked = {w for w in target_words if w in prompt_lower}
            assert not leaked, (
                f"維度 {dim!r}：prompt_zh 洩漏了 target_en 的英文內容（D2）：\n"
                f"  target_en:  {item['target_en']!r}\n"
                f"  prompt_zh:  {item['prompt_zh']!r}\n"
                f"  洩漏的詞：  {leaked}"
            )


# ---------------------------------------------------------------------------
# 測試 14（D1）：對 VOCAB 全部詞條跑產題，斷言不出現「zh_key 黏夾」壞字串
# ---------------------------------------------------------------------------

def test_no_bad_strings_for_all_vocab_keys():
    """對 VOCAB 全部 44 個 zh_key 跑一次產題，不得出現 zh_key 直接黏在「你」和「的」
    中間的不通順字串（例如「你貓的」「你喝的」等）。（D1 保護）"""
    import re as _re
    from server.agents import homework
    from server.scaffold import VOCAB

    # 強制使用 rule，逐一讓每個詞條所在分類的維度得低分
    # 只需對全詞庫取 VOCAB 的 zh_key 跑一次，用 _build_rule_items 直接測
    from server.agents.homework import _build_rule_items, _PROMPT_TEMPLATES_BY_CAT, _PROMPT_TEMPLATES_FALLBACK

    all_items: list[tuple[str, dict]] = []
    for dim in ("pronunciation", "fluency", "vocabulary", "grammar"):
        items = _build_rule_items(dim)
        for item in items:
            all_items.append((dim, item))

    # 同時也對每個 VOCAB 詞條的所有 prompt 範本做代入展開，逐一確認
    bad_pattern = _re.compile(r"你[^\s，。！？、]{1,6}的[^\s]")  # 例：你貓的故、你喝的故
    for zh_key, info in VOCAB.items():
        cat = info["cat"]
        templates = _PROMPT_TEMPLATES_BY_CAT.get(cat, _PROMPT_TEMPLATES_FALLBACK)
        for tmpl in templates:
            rendered = tmpl.format(zh_key=zh_key)
            # 不得出現「你X的Y」夾字（X 是 zh_key 全部或首字）
            match = bad_pattern.search(rendered)
            # 允許「你每天都用到X嗎」這類正確用法，只排除「你<zh_key>的」直接黏合
            direct_bad = f"你{zh_key}的"
            assert direct_bad not in rendered, (
                f"範本代入後出現不通順字串「{direct_bad}」（D1）：\n"
                f"  zh_key={zh_key!r}, cat={cat!r}\n"
                f"  範本: {tmpl!r}\n"
                f"  結果: {rendered!r}"
            )


# ---------------------------------------------------------------------------
# 測試 15（D5）：同一份作業內 target_en 不得全部同句型
# ---------------------------------------------------------------------------

def test_items_have_varied_sentence_patterns():
    """規則式產出同一份 5 題作業，target_en 不得全部以同一個動詞或相同句型起頭（D5 保護）。"""
    from server.agents import homework

    _ALL_DIMS = ("pronunciation", "fluency", "vocabulary", "grammar")
    for dim in _ALL_DIMS:
        scores = {d: 80 for d in _ALL_DIMS}
        scores[dim] = 40
        r = homework.generate_homework({}, {"scores": scores}, allow_cloud=False)
        targets = [item["target_en"] for item in r["items"]]

        # 取每句的前兩個詞作為句型特徵（例如 "I see", "I want", "This is", "My favorite"）
        def sent_pattern(s: str) -> str:
            words = s.split()
            return " ".join(words[:2]).lower() if len(words) >= 2 else s.lower()

        patterns = [sent_pattern(t) for t in targets]
        unique_patterns = set(patterns)
        assert len(unique_patterns) >= 2, (
            f"維度 {dim!r}：{len(targets)} 題的 target_en 全部同一句型（D5）：\n"
            f"  patterns: {patterns}\n"
            f"  targets:  {targets}"
        )


# ---------------------------------------------------------------------------
# 測試 16（D6）：同一份作業內 target_en 不得重複
# ---------------------------------------------------------------------------

def test_no_duplicate_target_en_within_one_homework():
    """同一份 5 題作業的 target_en 必須全部相異（D6 保護）。

    fluency 維度的 action cat 含「去」(sent='I want to go to school.')，
    school cat 含「學校」(sent='I want to go to school.')，
    兩者 sent 一字不差 —— 挑題時必須對 target_en 去重。
    其餘三個維度一併驗證，確保去重邏輯不破壞其他維度。
    """
    from server.agents import homework

    _ALL_DIMS = ("pronunciation", "fluency", "vocabulary", "grammar")
    for dim in _ALL_DIMS:
        scores = {d: 80 for d in _ALL_DIMS}
        scores[dim] = 40
        r = homework.generate_homework({}, {"scores": scores}, allow_cloud=False)
        targets = [item["target_en"] for item in r["items"]]
        assert len(targets) == len(set(targets)), (
            f"維度 {dim!r}：同一份作業出現重複的 target_en（D6）：\n"
            f"  targets: {targets}"
        )


# ---------------------------------------------------------------------------
# 測試 17（D7）：VOCAB 全部 44 個詞條代入範本，不得出現地點被套進「用到X」
# ---------------------------------------------------------------------------

def test_no_place_in_youyongdao_template():
    """對 VOCAB 全部 44 個詞條各自展開所有 prompt 範本，
    不得出現「用到<地點名詞>」的不通順組合（D7 保護）。

    地點型詞條判斷依據：cat='school' 且 sent 含 'go to'
    （例如「學校」sent='I want to go to school.'）。
    """
    from server.agents.homework import _PROMPT_TEMPLATES_BY_CAT, _PROMPT_TEMPLATES_FALLBACK
    from server.scaffold import VOCAB

    # 地點型詞條：cat='school' 且 sent 含 'go to'（意指需要前往的地點）
    place_keys = {
        zh_key
        for zh_key, info in VOCAB.items()
        if info["cat"] == "school" and "go to" in info["sent"]
    }

    for zh_key in place_keys:
        cat = VOCAB[zh_key]["cat"]
        templates = _PROMPT_TEMPLATES_BY_CAT.get(cat, _PROMPT_TEMPLATES_FALLBACK)
        for tmpl in templates:
            rendered = tmpl.format(zh_key=zh_key)
            bad_phrase = f"用到{zh_key}"
            assert bad_phrase not in rendered, (
                f"地點詞條「{zh_key}」被套進「用到X」範本，語意不通順（D7）：\n"
                f"  範本: {tmpl!r}\n"
                f"  結果: {rendered!r}"
            )
