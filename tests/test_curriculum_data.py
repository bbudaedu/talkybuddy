# -*- coding: utf-8 -*-
"""test_curriculum_data.py — 教育部英語文領綱官方資料的查詢層。

這些測試同時是**資料完整性的守門**：課綱 JSON 若被誤刪欄位、抽取腳本
改壞、或有人手改內容，這裡會先紅。決賽要拿它回答「教材依據是什麼」，
資料本身就得是可驗證的，不能只是「檔案存在」。
"""

from __future__ import annotations

import pytest

from server import curriculum_data as cd


# ---------------------------------------------------------------------------
# 資料完整性
# ---------------------------------------------------------------------------

def test_source_is_traceable():
    """出處必須帶官方網址與檔案雜湊——「我們參考了課綱」不是答案。"""
    meta = cd.source_meta()
    assert meta["url"].startswith("https://www.naer.edu.tw/"), meta.get("url")
    assert len(meta["sha256"]) == 64
    assert meta["published"] == "2018-04-16"
    assert "教育部" in cd.source_citation()


def test_vocab_tables_match_the_official_counts():
    """官方稱基本 1,200 字、其他常用 800 字。條目數會因詞組略有出入，
    但差太多就代表抽取壞了。"""
    assert 1150 <= len(cd.basic_vocab()) <= 1260, len(cd.basic_vocab())
    assert 760 <= len(cd.extra_vocab()) <= 840, len(cd.extra_vocab())


def test_appendix_lists_are_present():
    assert len(cd.topics()) == 40           # 附錄三 主題
    assert len(cd.genres()) >= 15           # 附錄三 體裁
    assert len(cd.communicative_functions()) >= 40   # 附錄四
    assert len(cd.junior_high_grammar()) >= 100      # 附錄六
    assert "Animals" in cd.topics()
    assert any(f.startswith("Greeting") for f in cd.communicative_functions())


def test_vocab_has_no_extraction_junk():
    """抽取的常見壞法：把中文說明、分節標題「A-」、或斷字混進字彙表。"""
    for word in cd.basic_vocab() + cd.extra_vocab():
        assert word, "不得有空字串"
        assert not any("一" <= c <= "鿿" for c in word), f"混進中文：{word!r}"
        assert not word.endswith("-") or len(word) > 2, f"分節標題混入：{word!r}"
        assert len(word) <= 60, f"疑似整段文字：{word!r}"


def test_parenthesised_alternatives_stay_together():
    """`airplane (plane)` 是一筆，不該被逗號切成兩筆。"""
    assert "airplane (plane)" in cd.basic_vocab()
    assert "autumn (fall)" in cd.basic_vocab()
    # 括號內的別寫也要查得到
    assert cd.is_basic("plane")
    assert cd.is_basic("fall")


def test_elementary_targets_match_the_syllabus_text():
    """領綱本文：國小畢業口語至少 300 字、書寫至少 180 字。"""
    assert cd.elementary_targets() == {"spoken_words": 300, "written_words": 180}


# ---------------------------------------------------------------------------
# 查詢行為
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("word", ["apple", "Apple", " dog ", "school"])
def test_is_basic_accepts_common_words(word):
    assert cd.is_basic(word) is True


@pytest.mark.parametrize("word", ["", None, "supercalifragilistic", "backpack"])
def test_is_basic_rejects_non_basic(word):
    # backpack 是真的不在基本 1,200 字內（它在其他常用 800 字）
    assert cd.is_basic(word) is False


def test_backpack_is_in_the_extended_list_not_the_basic_one():
    """把「不在基本表」和「不在課綱裡」分清楚——兩者差很多。"""
    assert cd.is_basic("backpack") is False
    assert "backpack" in [w.lower() for w in cd.extra_vocab()]


def test_vocab_for_topic_filters_by_level():
    basic = cd.vocab_for_topic("Food & drinks")
    everything = cd.vocab_for_topic("Food & drinks", basic_only=False)
    assert "apple" in basic
    assert len(everything) > len(basic)
    assert all(cd.is_basic(w) for w in basic)


def test_unknown_topic_returns_empty_not_error():
    assert cd.vocab_for_topic("沒有這個主題") == ()


def test_vocab_for_band_widens_at_band_4():
    """低 band 只給基本字，高 band 才放行加深加廣的 800 字。"""
    assert len(cd.vocab_for_band(1)) == len(cd.basic_vocab())
    assert len(cd.vocab_for_band(5)) == len(cd.basic_vocab()) + len(cd.extra_vocab())
    assert len(cd.vocab_for_band("壞掉")) == len(cd.basic_vocab())


# ---------------------------------------------------------------------------
# 我們自己的題庫站不站得住腳
# ---------------------------------------------------------------------------

def test_our_scaffold_vocab_is_almost_entirely_official():
    """scaffold.VOCAB 的每個詞都要能在課綱字彙表裡找到。

    這條是**回歸守門**：日後往題庫加詞時，加了課綱外的字會當場紅，
    而不是等到決賽被問「這個字哪來的」才發現。
    """
    from server import scaffold

    words = [v["en"] for v in scaffold.VOCAB.values()]
    result = cd.coverage(words)
    assert result["ratio"] >= 0.95, result

    official = set(w.lower() for w in cd.basic_vocab() + cd.extra_vocab())
    for word in result["outside"]:
        head = word.split()[0].lower()
        assert head in official or word.lower() in official, \
            f"{word!r} 不在教育部參考字彙表（2,000 字）內"


def test_coverage_reports_the_outliers():
    result = cd.coverage(["apple", "dog", "zzzznotaword"])
    assert result["total"] == 3
    assert result["in_basic_1200"] == 2
    assert result["outside"] == ["zzzznotaword"]


def test_coverage_survives_garbage():
    assert cd.coverage(None)["total"] == 0
    assert cd.coverage([])["ratio"] == 0.0
    assert cd.coverage([None, "", "  "])["total"] == 0


def test_load_never_raises_when_the_file_is_missing(monkeypatch, tmp_path):
    """資料檔缺失時退化成空資料，不得讓出題與診斷中斷。"""
    cd.load.cache_clear()
    monkeypatch.setattr(cd, "DATA_PATH", tmp_path / "不存在.json")
    try:
        assert cd.load() == {}
        assert cd.source_citation() == "（課綱資料未載入）"
    finally:
        cd.load.cache_clear()


# ---------------------------------------------------------------------------
# /api/curriculum：現場佐證「教材依據」的端點
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_curriculum_endpoint_returns_a_verifiable_citation():
    """回應必須帶得走：官方網址 + 檔案雜湊 + 我們題庫的對照數字。"""
    from httpx import ASGITransport, AsyncClient

    from server.app import app

    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as client:
        resp = await client.get("/api/curriculum")

    assert resp.status_code == 200
    body = resp.json()
    assert body["source"]["url"].startswith("https://www.naer.edu.tw/")
    assert len(body["source"]["sha256"]) == 64
    assert body["counts"]["basic_1200"] > 1000
    assert body["our_vocab_coverage"]["ratio"] >= 0.95
