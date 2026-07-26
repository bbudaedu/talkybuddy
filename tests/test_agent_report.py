# -*- coding: utf-8 -*-
"""test_agent_report.py — 週報 agent (server/agents/report.py) 測試套件。

TDD 第一步：測試先於實作。所有 monkeypatch 取代 converse_text，絕不觸網。

測試涵蓋：
  行為 1 — 雲端走 bedrock_converse.converse_text，cfg 用 resolve_config(role="diag")
  行為 2 — allow_cloud=False 完全不碰雲端（連 resolve_config 都不呼叫）
  行為 3 — 上雲前經 guardrails.deidentify
  行為 4 — 雲端回覆經 guardrails.passes_guardrail，不通過降級
  行為 5 — 任何例外不外拋，一律降級規則式
  行為 6 — diagnoses 為空、只有一筆、有五筆，皆不爆
  趨勢測試 — 進步 / 退步 / 持平 三種趨勢，highlights / concerns 確實不同
  週報不重複 — 同一份週報內不得有重複或幾乎重複的句子
"""

from __future__ import annotations

import json
import pytest

# ---------------------------------------------------------------------------
# fixtures：共用測試資料
# ---------------------------------------------------------------------------

_PROFILE = {
    "student_id": "TEST-001",
    "name": "小明",
    "grade": 3,
    "notes": "喜歡動物類單字",
}

def _make_diag(pron: int, flu: int, voc: int, gra: int, date: str = "2026-07-20") -> dict:
    """建立一筆診斷 dict（只填 schema 必要欄位）。"""
    return {
        "date": date,
        "scores": {
            "pronunciation": pron,
            "fluency": flu,
            "vocabulary": voc,
            "grammar": gra,
        },
        "strengths": ["願意開口嘗試"],
        "weaknesses": ["冠詞 a/an 仍漏"],
        "emotional_status": "學習態度積極。",
    }


def _diags_improving() -> list[dict]:
    """五筆診斷：四維分數整體上升（進步趨勢）。"""
    return [
        _make_diag(48, 45, 52, 42, "2026-07-16"),
        _make_diag(54, 50, 57, 47, "2026-07-17"),
        _make_diag(60, 56, 62, 53, "2026-07-18"),
        _make_diag(65, 62, 68, 59, "2026-07-19"),
        _make_diag(70, 68, 74, 65, "2026-07-20"),
    ]


def _diags_declining() -> list[dict]:
    """五筆診斷：四維分數整體下滑（退步趨勢）。"""
    return [
        _make_diag(72, 70, 76, 68, "2026-07-16"),
        _make_diag(65, 63, 70, 61, "2026-07-17"),
        _make_diag(58, 55, 63, 55, "2026-07-18"),
        _make_diag(52, 48, 57, 48, "2026-07-19"),
        _make_diag(46, 43, 51, 42, "2026-07-20"),
    ]


def _diags_stable() -> list[dict]:
    """五筆診斷：四維分數平穩（持平趨勢）。"""
    return [
        _make_diag(62, 60, 65, 58, "2026-07-16"),
        _make_diag(63, 61, 66, 59, "2026-07-17"),
        _make_diag(61, 60, 64, 57, "2026-07-18"),
        _make_diag(63, 62, 65, 59, "2026-07-19"),
        _make_diag(62, 61, 65, 58, "2026-07-20"),
    ]


# 合法的雲端 JSON 回應
_VALID_CLOUD_RESPONSE = json.dumps({
    "period": "最近 5 次練習",
    "summary": "本週學習態度積極，整體表現有明顯進步，發音與詞彙量皆有成長。",
    "highlights": ["發音清晰度穩定提升", "能主動開口不需提示"],
    "concerns": ["文法仍需加強，冠詞偶有遺漏"],
    "suggestions": ["每天睡前陪孩子用英文說說今天做了什麼"],
    "source": "cloud",
}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# schema 驗證輔助
# ---------------------------------------------------------------------------

_REQUIRED_KEYS = {"period", "summary", "highlights", "concerns", "suggestions", "source"}


def _assert_valid_schema(report: dict, *, allow_cloud: bool = True, allow_empty_lists: bool = False) -> None:
    """斷言 report 符合公開契約 schema。

    allow_empty_lists=True：允許 highlights / concerns 為空 list
    （用於空資料情境，此時無亮點與關注點是正確行為）。
    """
    assert isinstance(report, dict), "回傳應為 dict"
    assert _REQUIRED_KEYS <= set(report.keys()), f"缺欄位：{_REQUIRED_KEYS - set(report.keys())}"
    assert isinstance(report["period"], str) and report["period"].strip(), "period 不得空"
    assert isinstance(report["summary"], str) and report["summary"].strip(), "summary 不得空"
    for key in ("highlights", "concerns", "suggestions"):
        val = report[key]
        assert isinstance(val, list), f"{key} 應為 list"
        if allow_empty_lists and key in ("highlights", "concerns"):
            # 空資料時 highlights / concerns 合法為空 list
            pass
        else:
            assert 1 <= len(val) <= 3, f"{key} 長度應在 1-3 之間，實際：{val}"
            for item in val:
                assert isinstance(item, str) and item.strip(), f"{key} 內有空字串"
    assert report["source"] in ("cloud", "rule"), f"source 非法值：{report['source']}"


# ---------------------------------------------------------------------------
# 行為 1：雲端走 bedrock_converse.converse_text
# ---------------------------------------------------------------------------

def test_cloud_calls_bedrock_converse_text(monkeypatch):
    """雲端路徑應呼叫 converse_text 並使用 resolve_config(role='diag') 的 cfg。"""
    import server.agents.report as report_mod
    import server.bedrock_converse as bc

    cfg_received = {}
    calls = []

    monkeypatch.setattr(
        bc, "resolve_config",
        lambda role=None: {"region": "us-east-1", "model_id": "test-model", "_role": role}
    )
    monkeypatch.setattr(
        bc, "converse_text",
        lambda sys, user, *, cfg, max_tokens=1024, timeout_s=12.0, **kw: (
            calls.append(cfg) or cfg_received.update(cfg) or _VALID_CLOUD_RESPONSE
        )
    )

    result = report_mod.generate_report(_PROFILE, _diags_improving(), allow_cloud=True)
    _assert_valid_schema(result)
    assert len(calls) == 1, "converse_text 應被呼叫一次"
    assert calls[0].get("_role") == "diag", "resolve_config 應以 role='diag' 呼叫"


# ---------------------------------------------------------------------------
# 行為 2：allow_cloud=False 不觸雲端
# ---------------------------------------------------------------------------

def test_allow_cloud_false_never_calls_cloud(monkeypatch):
    """allow_cloud=False 時 resolve_config 與 converse_text 均不得被呼叫。"""
    import server.agents.report as report_mod
    import server.bedrock_converse as bc

    def _should_not_call(*a, **kw):
        pytest.fail("allow_cloud=False 不應呼叫雲端函式")

    monkeypatch.setattr(bc, "resolve_config", _should_not_call)
    monkeypatch.setattr(bc, "converse_text", _should_not_call)

    result = report_mod.generate_report(_PROFILE, _diags_improving(), allow_cloud=False)
    _assert_valid_schema(result)
    assert result["source"] == "rule"


# ---------------------------------------------------------------------------
# 行為 3：上雲前呼叫 guardrails.deidentify
# ---------------------------------------------------------------------------

def test_cloud_calls_deidentify_before_upload(monkeypatch):
    """上雲前 guardrails.deidentify 應被呼叫（至少一次）。"""
    import server.agents.report as report_mod
    import server.bedrock_converse as bc
    import server.guardrails as grd

    deidentify_calls = []
    original_deidentify = grd.deidentify

    def _tracking_deidentify(text):
        deidentify_calls.append(text)
        return original_deidentify(text)

    monkeypatch.setattr(bc, "resolve_config",
                        lambda role=None: {"region": "us-east-1", "model_id": "m"})
    monkeypatch.setattr(bc, "converse_text",
                        lambda *a, **kw: _VALID_CLOUD_RESPONSE)
    monkeypatch.setattr(grd, "deidentify", _tracking_deidentify)

    report_mod.generate_report(_PROFILE, _diags_improving(), allow_cloud=True)
    assert len(deidentify_calls) > 0, "上雲前 deidentify 應至少被呼叫一次"


# ---------------------------------------------------------------------------
# 行為 4：雲端回覆不通過護欄 → 降級
# ---------------------------------------------------------------------------

def test_cloud_fails_guardrail_falls_back(monkeypatch):
    """雲端回覆不通過 passes_guardrail 時，應降級回 rule source。"""
    import server.agents.report as report_mod
    import server.bedrock_converse as bc
    import server.guardrails as grd

    monkeypatch.setattr(bc, "resolve_config",
                        lambda role=None: {"region": "us-east-1", "model_id": "m"})
    monkeypatch.setattr(bc, "converse_text",
                        lambda *a, **kw: _VALID_CLOUD_RESPONSE)
    # 強制護欄不通過
    monkeypatch.setattr(grd, "passes_guardrail", lambda text: False)

    result = report_mod.generate_report(_PROFILE, _diags_improving(), allow_cloud=True)
    _assert_valid_schema(result)
    assert result["source"] == "rule", "護欄不通過應降級為 rule"


# ---------------------------------------------------------------------------
# 行為 5：雲端例外 → 降級，不往外拋
# ---------------------------------------------------------------------------

def test_cloud_exception_falls_back_silently(monkeypatch):
    """converse_text 拋例外時，應靜默降級回規則式，不讓例外傳出。"""
    import server.agents.report as report_mod
    import server.bedrock_converse as bc

    monkeypatch.setattr(bc, "resolve_config",
                        lambda role=None: {"region": "us-east-1", "model_id": "m"})
    monkeypatch.setattr(bc, "converse_text",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("網路逾時")))

    # 不應拋例外
    result = report_mod.generate_report(_PROFILE, _diags_improving(), allow_cloud=True)
    _assert_valid_schema(result)
    assert result["source"] == "rule"


def test_resolve_config_exception_falls_back(monkeypatch):
    """resolve_config 拋例外時也應靜默降級回規則式。"""
    import server.agents.report as report_mod
    import server.bedrock_converse as bc

    monkeypatch.setattr(bc, "resolve_config",
                        lambda role=None: (_ for _ in ()).throw(RuntimeError("設定錯誤")))

    result = report_mod.generate_report(_PROFILE, _diags_improving(), allow_cloud=True)
    _assert_valid_schema(result)
    assert result["source"] == "rule"


# ---------------------------------------------------------------------------
# 行為 6：diagnoses 各種長度皆不爆
# ---------------------------------------------------------------------------

def test_empty_diagnoses_does_not_crash():
    """diagnoses=[] 時應能產出合法週報（highlights/concerns 可為空 list）。"""
    import server.agents.report as report_mod
    result = report_mod.generate_report(_PROFILE, [], allow_cloud=False)
    _assert_valid_schema(result, allow_empty_lists=True)
    assert result["source"] == "rule"


def test_single_diagnosis_does_not_crash():
    """diagnoses 只有一筆時應能產出合法週報（算不出趨勢也不爆）。"""
    import server.agents.report as report_mod
    result = report_mod.generate_report(_PROFILE, [_make_diag(60, 55, 62, 50)], allow_cloud=False)
    _assert_valid_schema(result)
    assert result["source"] == "rule"


def test_five_diagnoses_does_not_crash():
    """diagnoses 有五筆時正常產出。"""
    import server.agents.report as report_mod
    result = report_mod.generate_report(_PROFILE, _diags_improving(), allow_cloud=False)
    _assert_valid_schema(result)


def test_none_profile_does_not_crash():
    """profile=None 時也不應爆炸。"""
    import server.agents.report as report_mod
    result = report_mod.generate_report(None, _diags_improving(), allow_cloud=False)  # type: ignore[arg-type]
    _assert_valid_schema(result)


def test_none_diagnoses_does_not_crash():
    """diagnoses=None 時也不應爆炸。"""
    import server.agents.report as report_mod
    result = report_mod.generate_report(_PROFILE, None, allow_cloud=False)  # type: ignore[arg-type]
    _assert_valid_schema(result, allow_empty_lists=True)


# ---------------------------------------------------------------------------
# 趨勢分析：進步 / 退步 / 持平 三種趨勢，highlights / concerns 確實不同
# ---------------------------------------------------------------------------

def test_improving_trend_appears_in_highlights():
    """進步趨勢應讓 highlights 包含「進步」或「提升」相關文字。"""
    import server.agents.report as report_mod
    result = report_mod.generate_report(_PROFILE, _diags_improving(), allow_cloud=False)
    _assert_valid_schema(result)
    highlights_text = " ".join(result["highlights"])
    assert any(kw in highlights_text for kw in ("進步", "提升", "成長", "改善")), (
        f"進步趨勢的 highlights 應反映進步，實際：{result['highlights']}"
    )


def test_declining_trend_appears_in_concerns():
    """退步趨勢應讓 concerns 包含「下滑」「退步」「需要」等關注詞。"""
    import server.agents.report as report_mod
    result = report_mod.generate_report(_PROFILE, _diags_declining(), allow_cloud=False)
    _assert_valid_schema(result)
    concerns_text = " ".join(result["concerns"])
    assert any(kw in concerns_text for kw in ("下滑", "退步", "需要", "注意", "下降")), (
        f"退步趨勢的 concerns 應反映退步，實際：{result['concerns']}"
    )


def test_stable_trend_summary_differs_from_improving():
    """持平趨勢的 summary 應與進步趨勢不同（不可產出幾乎相同的報告）。"""
    import server.agents.report as report_mod
    r_stable = report_mod.generate_report(_PROFILE, _diags_stable(), allow_cloud=False)
    r_improve = report_mod.generate_report(_PROFILE, _diags_improving(), allow_cloud=False)
    _assert_valid_schema(r_stable)
    _assert_valid_schema(r_improve)
    assert r_stable["summary"] != r_improve["summary"], (
        "持平與進步趨勢的 summary 不應完全相同"
    )


def test_improving_vs_declining_highlights_differ():
    """進步趨勢的 highlights 應與退步趨勢明顯不同。"""
    import server.agents.report as report_mod
    r_up = report_mod.generate_report(_PROFILE, _diags_improving(), allow_cloud=False)
    r_dn = report_mod.generate_report(_PROFILE, _diags_declining(), allow_cloud=False)
    _assert_valid_schema(r_up)
    _assert_valid_schema(r_dn)
    assert r_up["highlights"] != r_dn["highlights"], (
        "進步與退步趨勢的 highlights 不應完全相同"
    )


def test_improving_vs_declining_concerns_differ():
    """進步趨勢的 concerns 應與退步趨勢明顯不同。"""
    import server.agents.report as report_mod
    r_up = report_mod.generate_report(_PROFILE, _diags_improving(), allow_cloud=False)
    r_dn = report_mod.generate_report(_PROFILE, _diags_declining(), allow_cloud=False)
    assert r_up["concerns"] != r_dn["concerns"], (
        "進步與退步趨勢的 concerns 不應完全相同"
    )


def test_each_dim_trend_reflected(monkeypatch):
    """四維各有明顯弱項時，concerns 應反映對應維度。"""
    import server.agents.report as report_mod

    # 文法分數極低，其他維度偏高 → concerns 應提到文法
    diags_grammar_weak = [
        _make_diag(70, 72, 74, 35, "2026-07-19"),
        _make_diag(71, 73, 75, 33, "2026-07-20"),
    ]
    result = report_mod.generate_report(_PROFILE, diags_grammar_weak, allow_cloud=False)
    _assert_valid_schema(result)
    full_text = " ".join(result["concerns"])
    assert "文法" in full_text, f"文法弱項應出現在 concerns，實際：{result['concerns']}"


def test_low_pronunciation_reflected(monkeypatch):
    """發音分數最低時，週報應在 concerns 或 highlights 中提到發音相關議題。"""
    import server.agents.report as report_mod

    diags_pron_weak = [
        _make_diag(35, 68, 70, 65, "2026-07-19"),
        _make_diag(33, 70, 72, 67, "2026-07-20"),
    ]
    result = report_mod.generate_report(_PROFILE, diags_pron_weak, allow_cloud=False)
    _assert_valid_schema(result)
    full_text = " ".join(result["concerns"] + result["highlights"])
    assert "發音" in full_text, f"發音弱項應出現在週報，實際：{result['concerns']} / {result['highlights']}"


# ---------------------------------------------------------------------------
# 不重複：同一份週報內不得有重複或幾乎重複的句子
# ---------------------------------------------------------------------------

def test_no_duplicate_sentences_in_report():
    """同一份週報的 highlights + concerns + suggestions 內不得有重複字串。"""
    import server.agents.report as report_mod

    result = report_mod.generate_report(_PROFILE, _diags_improving(), allow_cloud=False)
    _assert_valid_schema(result)

    all_items = result["highlights"] + result["concerns"] + result["suggestions"]
    # 去除首尾空白後比對
    stripped = [s.strip() for s in all_items]
    assert len(stripped) == len(set(stripped)), (
        f"週報內有重複句子：{stripped}"
    )


def test_no_duplicate_sentences_declining():
    """退步趨勢週報也不得有重複句子。"""
    import server.agents.report as report_mod

    result = report_mod.generate_report(_PROFILE, _diags_declining(), allow_cloud=False)
    _assert_valid_schema(result)

    all_items = result["highlights"] + result["concerns"] + result["suggestions"]
    stripped = [s.strip() for s in all_items]
    assert len(stripped) == len(set(stripped)), (
        f"週報內有重複句子：{stripped}"
    )


# ---------------------------------------------------------------------------
# 雲端路徑：resolve_config 回 None 時降級規則式
# ---------------------------------------------------------------------------

def test_cloud_no_config_falls_back(monkeypatch):
    """resolve_config 回 None（未設定 Bedrock provider）時，應降級回規則式。"""
    import server.agents.report as report_mod
    import server.bedrock_converse as bc

    monkeypatch.setattr(bc, "resolve_config", lambda role=None: None)

    result = report_mod.generate_report(_PROFILE, _diags_improving(), allow_cloud=True)
    _assert_valid_schema(result)
    assert result["source"] == "rule"


# ---------------------------------------------------------------------------
# 雲端 JSON schema 不合法 → 降級
# ---------------------------------------------------------------------------

def test_cloud_invalid_json_falls_back(monkeypatch):
    """雲端回傳非 JSON 時應降級回規則式。"""
    import server.agents.report as report_mod
    import server.bedrock_converse as bc

    monkeypatch.setattr(bc, "resolve_config",
                        lambda role=None: {"region": "us-east-1", "model_id": "m"})
    monkeypatch.setattr(bc, "converse_text",
                        lambda *a, **kw: "這不是 JSON")

    result = report_mod.generate_report(_PROFILE, _diags_improving(), allow_cloud=True)
    _assert_valid_schema(result)
    assert result["source"] == "rule"


def test_cloud_missing_fields_falls_back(monkeypatch):
    """雲端 JSON 缺必要欄位時應降級回規則式。"""
    import server.agents.report as report_mod
    import server.bedrock_converse as bc

    bad_json = json.dumps({"period": "最近 5 次", "summary": "好"}, ensure_ascii=False)

    monkeypatch.setattr(bc, "resolve_config",
                        lambda role=None: {"region": "us-east-1", "model_id": "m"})
    monkeypatch.setattr(bc, "converse_text",
                        lambda *a, **kw: bad_json)

    result = report_mod.generate_report(_PROFILE, _diags_improving(), allow_cloud=True)
    _assert_valid_schema(result)
    assert result["source"] == "rule"


# ---------------------------------------------------------------------------
# 雲端成功回傳 → source 為 "cloud"
# ---------------------------------------------------------------------------

def test_cloud_success_returns_cloud_source(monkeypatch):
    """雲端路徑成功時 source 應為 'cloud'。"""
    import server.agents.report as report_mod
    import server.bedrock_converse as bc

    monkeypatch.setattr(bc, "resolve_config",
                        lambda role=None: {"region": "us-east-1", "model_id": "m"})
    monkeypatch.setattr(bc, "converse_text",
                        lambda *a, **kw: _VALID_CLOUD_RESPONSE)

    result = report_mod.generate_report(_PROFILE, _diags_improving(), allow_cloud=True)
    _assert_valid_schema(result)
    assert result["source"] == "cloud"


# ---------------------------------------------------------------------------
# 品質：規則式 summary 有意義（不只是數字串接）
# ---------------------------------------------------------------------------

def test_rule_summary_is_prose_not_numbers():
    """規則式 summary 應是有意義的敘述句，不能只是數字堆砌。"""
    import server.agents.report as report_mod

    result = report_mod.generate_report(_PROFILE, _diags_improving(), allow_cloud=False)
    summary = result["summary"]
    # 應含有中文字（敘述性文字）
    cjk_count = sum(1 for c in summary if "\u4e00" <= c <= "\u9fff")
    assert cjk_count >= 10, f"summary 中文字太少（{cjk_count}字），可能只是數字串：{summary!r}"


def test_rule_period_describes_count():
    """period 應描述涵蓋期間，含有「次」或「天」等時間概念。"""
    import server.agents.report as report_mod

    result = report_mod.generate_report(_PROFILE, _diags_improving(), allow_cloud=False)
    period = result["period"]
    assert any(kw in period for kw in ("次", "天", "週", "筆", "期間")), (
        f"period 應描述期間，實際：{period!r}"
    )


def test_rule_suggestions_are_actionable():
    """規則式 suggestions 應包含家長可操作的建議（含動詞或動作描述）。"""
    import server.agents.report as report_mod

    result = report_mod.generate_report(_PROFILE, _diags_improving(), allow_cloud=False)
    suggestions_text = " ".join(result["suggestions"])
    # 應含有至少一個常見動作詞
    action_words = ("陪", "練習", "聽", "說", "讀", "看", "玩", "鼓勵", "帶")
    assert any(w in suggestions_text for w in action_words), (
        f"suggestions 應包含具體行動建議，實際：{result['suggestions']}"
    )


# ---------------------------------------------------------------------------
# R4：誠信問題 — 空資料 / 單筆資料時不得宣稱趨勢
# ---------------------------------------------------------------------------

# 趨勢字眼（任何一個出現在空資料/單筆的 summary 都是謊報）
_TREND_WORDS = ("穩定", "進步", "下滑", "退步", "持平", "成長", "下降", "起伏", "向上", "趨勢")


def test_empty_diagnoses_highlights_empty():
    """diagnoses=[] 時，highlights 必須是空 list（沒有練習紀錄，無亮點可言）。"""
    import server.agents.report as report_mod
    result = report_mod.generate_report({}, [], allow_cloud=False)
    assert result["highlights"] == [], (
        f"空資料時 highlights 應為空 list，實際：{result['highlights']}"
    )


def test_empty_diagnoses_concerns_empty():
    """diagnoses=[] 時，concerns 必須是空 list。"""
    import server.agents.report as report_mod
    result = report_mod.generate_report({}, [], allow_cloud=False)
    assert result["concerns"] == [], (
        f"空資料時 concerns 應為空 list，實際：{result['concerns']}"
    )


def test_empty_diagnoses_summary_no_trend_claims():
    """diagnoses=[] 時，summary 不得包含任何趨勢宣稱字眼。"""
    import server.agents.report as report_mod
    result = report_mod.generate_report({}, [], allow_cloud=False)
    summary = result["summary"]
    found = [w for w in _TREND_WORDS if w in summary]
    assert not found, (
        f"空資料的 summary 不得包含趨勢字眼 {found}，實際：{summary!r}"
    )


def test_empty_diagnoses_suggestions_general_only():
    """diagnoses=[] 時，suggestions 不得描述「孩子的表現如何」。"""
    import server.agents.report as report_mod
    result = report_mod.generate_report({}, [], allow_cloud=False)
    suggestions_text = " ".join(result["suggestions"])
    # 不應宣稱觀察到任何表現
    forbidden = ("孩子的表現", "表現良好", "表現優秀", "表現仍需", "孩子目前")
    found = [f for f in forbidden if f in suggestions_text]
    assert not found, (
        f"空資料的 suggestions 不得描述孩子表現，出現禁詞 {found}，實際：{result['suggestions']}"
    )


def test_single_diagnosis_summary_no_trend_claims():
    """只有一筆診斷時，summary 不得宣稱任何趨勢（一筆資料算不出趨勢）。"""
    import server.agents.report as report_mod
    result = report_mod.generate_report({}, [_make_diag(70, 65, 68, 60)], allow_cloud=False)
    summary = result["summary"]
    found = [w for w in _TREND_WORDS if w in summary]
    assert not found, (
        f"單筆資料的 summary 不得包含趨勢字眼 {found}，實際：{summary!r}"
    )


# ---------------------------------------------------------------------------
# R2：suggestions 必須隨趨勢變化
# ---------------------------------------------------------------------------

def test_suggestions_differ_improving_vs_declining():
    """進步趨勢與退步趨勢的 suggestions 不得完全相同。"""
    import server.agents.report as report_mod
    ru = report_mod.generate_report({}, _diags_improving(), allow_cloud=False)
    rd = report_mod.generate_report({}, _diags_declining(), allow_cloud=False)
    assert ru["suggestions"] != rd["suggestions"], (
        f"進步 vs 退步的 suggestions 不得一字不差，\n進步：{ru['suggestions']}\n退步：{rd['suggestions']}"
    )


def test_suggestions_differ_improving_vs_stable():
    """進步趨勢與持平趨勢的 suggestions 不得完全相同。"""
    import server.agents.report as report_mod
    ru = report_mod.generate_report({}, _diags_improving(), allow_cloud=False)
    rf = report_mod.generate_report({}, _diags_stable(), allow_cloud=False)
    assert ru["suggestions"] != rf["suggestions"], (
        f"進步 vs 持平的 suggestions 不得一字不差，\n進步：{ru['suggestions']}\n持平：{rf['suggestions']}"
    )


def test_suggestions_differ_declining_vs_stable():
    """退步趨勢與持平趨勢的 suggestions 不得完全相同。"""
    import server.agents.report as report_mod
    rd = report_mod.generate_report({}, _diags_declining(), allow_cloud=False)
    rf = report_mod.generate_report({}, _diags_stable(), allow_cloud=False)
    assert rd["suggestions"] != rf["suggestions"], (
        f"退步 vs 持平的 suggestions 不得一字不差，\n退步：{rd['suggestions']}\n持平：{rf['suggestions']}"
    )


# ---------------------------------------------------------------------------
# R3：concerns 在不同趨勢下必須有差異
# ---------------------------------------------------------------------------

def test_concerns_differ_improving_vs_declining():
    """進步趨勢與退步趨勢的 concerns 不得完全相同。"""
    import server.agents.report as report_mod
    ru = report_mod.generate_report({}, _diags_improving(), allow_cloud=False)
    rd = report_mod.generate_report({}, _diags_declining(), allow_cloud=False)
    assert ru["concerns"] != rd["concerns"], (
        f"進步 vs 退步的 concerns 不得一字不差"
    )


def test_concerns_differ_improving_vs_stable():
    """進步趨勢與持平趨勢的 concerns 不得完全相同。"""
    import server.agents.report as report_mod
    ru = report_mod.generate_report({}, _diags_improving(), allow_cloud=False)
    rf = report_mod.generate_report({}, _diags_stable(), allow_cloud=False)
    assert ru["concerns"] != rf["concerns"], (
        f"進步 vs 持平的 concerns 不得一字不差"
    )


def test_concerns_differ_declining_vs_stable():
    """退步趨勢與持平趨勢的 concerns 不得完全相同。"""
    import server.agents.report as report_mod
    rd = report_mod.generate_report({}, _diags_declining(), allow_cloud=False)
    rf = report_mod.generate_report({}, _diags_stable(), allow_cloud=False)
    assert rd["concerns"] != rf["concerns"], (
        f"退步 vs 持平的 concerns 不得一字不差"
    )


# ---------------------------------------------------------------------------
# R1：summary 不得「表現良好」與「最需要持續關注」並排
# ---------------------------------------------------------------------------

def test_summary_no_contradiction_good_yet_concern():
    """summary 不得在同一維度同時說「表現良好/優秀」又說「最需要持續關注」。"""
    import server.agents.report as report_mod

    for diags in (_diags_improving(), _diags_declining(), _diags_stable()):
        result = report_mod.generate_report({}, diags, allow_cloud=False)
        summary = result["summary"]
        # 若包含「表現良好」或「表現優秀」，就不得同時包含「最需要持續關注」
        has_positive = any(p in summary for p in ("表現良好", "表現優秀"))
        has_concern_label = "最需要持續關注" in summary
        assert not (has_positive and has_concern_label), (
            f"summary 矛盾：正面評語與「最需要持續關注」並排，實際：{summary!r}"
        )
